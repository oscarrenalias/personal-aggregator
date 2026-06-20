import datetime

import pytest

from aggregator_retriever.parse import parse_feed

_RSS2_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test RSS Feed</title>
    <link>https://rss.example.com</link>
    <description>A test RSS 2.0 feed</description>
    <item>
      <title>Article One</title>
      <link>https://rss.example.com/1</link>
      <guid>https://rss.example.com/guid/1</guid>
      <description>Summary of article one</description>
      <pubDate>Mon, 01 Jan 2024 12:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Article Two</title>
      <link>https://rss.example.com/2</link>
      <guid>https://rss.example.com/guid/2</guid>
      <description>Summary of article two</description>
      <pubDate>Tue, 02 Jan 2024 12:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""

_ATOM_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test Atom Feed</title>
  <link href="https://atom.example.com"/>
  <id>https://atom.example.com</id>
  <entry>
    <title>Atom Article One</title>
    <link href="https://atom.example.com/1"/>
    <id>urn:uuid:atom-article-1</id>
    <summary>Atom summary one</summary>
    <updated>2024-01-01T12:00:00Z</updated>
  </entry>
  <entry>
    <title>Atom Article Two</title>
    <link href="https://atom.example.com/2"/>
    <id>urn:uuid:atom-article-2</id>
    <summary>Atom summary two</summary>
    <updated>2024-01-02T08:00:00Z</updated>
  </entry>
</feed>
"""

_FEED_NO_ID = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>No-ID Feed</title>
    <link>https://noid.example.com</link>
    <description>Feed with no ids</description>
    <item>
      <title>No Guid Item</title>
      <link>https://noid.example.com/article</link>
      <description>Has a link but no guid</description>
    </item>
  </channel>
</rss>
"""

_FEED_NO_DEDUP = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Bare Feed</title>
    <link>https://bare.example.com</link>
    <description>Entries with no id, link, title, or date</description>
    <item>
      <description>Just a description, no id/link/title</description>
    </item>
    <item>
      <title>Good Item</title>
      <link>https://bare.example.com/good</link>
      <guid>bare-good-1</guid>
    </item>
  </channel>
</rss>
"""


class TestParseFeedRSS2:
    def test_parses_two_entries(self):
        entries = parse_feed(_RSS2_FEED, source_id=1)
        assert len(entries) == 2

    def test_dedup_key_is_guid(self):
        entries = parse_feed(_RSS2_FEED, source_id=1)
        keys = {e.dedup_key for e in entries}
        assert "https://rss.example.com/guid/1" in keys
        assert "https://rss.example.com/guid/2" in keys

    def test_feed_url_populated(self):
        entries = parse_feed(_RSS2_FEED, source_id=1)
        urls = {e.feed_url for e in entries}
        assert "https://rss.example.com/1" in urls

    def test_feed_title_populated(self):
        entries = parse_feed(_RSS2_FEED, source_id=1)
        titles = {e.feed_title for e in entries}
        assert "Article One" in titles

    def test_feed_published_at_parsed(self):
        entries = parse_feed(_RSS2_FEED, source_id=1)
        for e in entries:
            assert isinstance(e.feed_published_at, datetime.datetime)
            assert e.feed_published_at.tzinfo is not None

    def test_raw_payload_is_dict(self):
        entries = parse_feed(_RSS2_FEED, source_id=1)
        for e in entries:
            assert isinstance(e.raw_payload, dict)

    def test_raw_payload_json_safe(self):
        import json

        entries = parse_feed(_RSS2_FEED, source_id=1)
        for e in entries:
            json.dumps(e.raw_payload)  # must not raise


class TestParseFeedAtom:
    def test_parses_two_entries(self):
        entries = parse_feed(_ATOM_FEED, source_id=2)
        assert len(entries) == 2

    def test_dedup_key_is_atom_id(self):
        entries = parse_feed(_ATOM_FEED, source_id=2)
        keys = {e.dedup_key for e in entries}
        assert "urn:uuid:atom-article-1" in keys
        assert "urn:uuid:atom-article-2" in keys

    def test_feed_summary_populated(self):
        entries = parse_feed(_ATOM_FEED, source_id=2)
        summaries = {e.feed_summary for e in entries}
        assert "Atom summary one" in summaries

    def test_feed_published_at_parsed(self):
        entries = parse_feed(_ATOM_FEED, source_id=2)
        for e in entries:
            assert isinstance(e.feed_published_at, datetime.datetime)

    def test_raw_payload_json_safe(self):
        import json

        entries = parse_feed(_ATOM_FEED, source_id=2)
        for e in entries:
            json.dumps(e.raw_payload)


class TestParseFeedEdgeCases:
    def test_no_guid_falls_back_to_link(self):
        entries = parse_feed(_FEED_NO_ID, source_id=3)
        assert len(entries) == 1
        assert entries[0].dedup_key == "https://noid.example.com/article"

    def test_entry_without_dedup_key_is_skipped(self):
        entries = parse_feed(_FEED_NO_DEDUP, source_id=4)
        # The underiable entry (description-only) is skipped; the good item passes
        assert len(entries) == 1
        assert entries[0].dedup_key == "bare-good-1"

    def test_feed_published_at_none_when_unparseable(self):
        feed = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>No Date Feed</title>
    <link>https://nodate.example.com</link>
    <item>
      <title>No Date</title>
      <link>https://nodate.example.com/1</link>
      <guid>nodate-1</guid>
    </item>
  </channel>
</rss>"""
        entries = parse_feed(feed, source_id=5)
        assert len(entries) == 1
        assert entries[0].feed_published_at is None

    def test_malformed_entry_isolated_good_entries_persist(self):
        """An entry that raises during dedup_key construction is skipped; others continue."""
        # feedparser itself is robust; simulate a malformed entry by passing body with
        # one parseable and one gracefully-skipped item. The real isolation path is that
        # the try/except in parse_feed catches any per-entry exception.
        # We verify by parsing a feed where one entry has no dedup_key derivable.
        feed = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Mixed Feed</title>
    <link>https://mixed.example.com</link>
    <item>
      <description>Only description, no id/link/title/date</description>
    </item>
    <item>
      <title>Good Article</title>
      <link>https://mixed.example.com/good</link>
      <guid>mixed-good-1</guid>
      <description>Valid entry</description>
    </item>
  </channel>
</rss>"""
        entries = parse_feed(feed, source_id=9)
        assert any(e.dedup_key == "mixed-good-1" for e in entries)

    def test_bozo_garbage_body_logs_warning(self):
        """parse_feed logs a WARNING when a non-empty body yields 0 entries and is unidentifiable.

        Mirrors the real failure mode (a body the retriever couldn't decode — e.g. an
        undecoded Content-Encoding — looks like junk to feedparser). We use plainly
        non-feed bytes, which feedparser reports with version='' (unrecognized format),
        deterministically triggering the warning. (feedparser's `bozo` flag on
        brotli-shaped bytes is not deterministic across runs, so we don't rely on it.)
        """
        from unittest.mock import patch

        from aggregator_retriever import parse as parse_mod

        garbage = b"\x00\x01\x02 this is not XML or any feed format \xff\xfe just raw bytes"

        # Patch the parse module's logger.warning directly so the assertion is immune to
        # global logging state (handlers/levels/logging.disable, pytest caplog displacement)
        # that other tests leave behind — which made handler/caplog-based capture flaky in a
        # full-suite run. This verifies the *intent*: parse_feed emits the diagnostic warning.
        with patch.object(parse_mod.logger, "warning") as mock_warning:
            entries = parse_feed(garbage, source_id=42)

        assert entries == []
        # The diagnostic warning's format string mentions "bozo"; assert it was emitted.
        assert any(
            "bozo" in str(call.args[0]).lower() for call in mock_warning.call_args_list
        ), f"expected a 'bozo' diagnostic warning; got calls: {mock_warning.call_args_list}"

    def test_empty_feed_returns_empty_list(self):
        feed = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Empty Feed</title>
    <link>https://empty.example.com</link>
  </channel>
</rss>"""
        entries = parse_feed(feed, source_id=6)
        assert entries == []


class TestCommentsUrl:
    _FEED_WITH_COMMENTS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>HN-style Feed</title>
    <link>https://hn.example.com</link>
    <description>Feed with comments links</description>
    <item>
      <title>Ask HN: Something interesting</title>
      <link>https://news.ycombinator.com/item?id=12345</link>
      <guid>https://news.ycombinator.com/item?id=12345</guid>
      <comments>https://news.ycombinator.com/item?id=12345</comments>
      <description>Discussion thread</description>
    </item>
    <item>
      <title>No comments link here</title>
      <link>https://hn.example.com/2</link>
      <guid>https://hn.example.com/guid/2</guid>
      <description>No comments element</description>
    </item>
  </channel>
</rss>
"""

    def test_comments_url_captured_when_present(self):
        entries = parse_feed(self._FEED_WITH_COMMENTS, source_id=10)
        entry = next(e for e in entries if "12345" in e.dedup_key)
        assert entry.comments_url == "https://news.ycombinator.com/item?id=12345"

    def test_comments_url_none_when_absent(self):
        entries = parse_feed(self._FEED_WITH_COMMENTS, source_id=10)
        entry = next(e for e in entries if "guid/2" in e.dedup_key)
        assert entry.comments_url is None

    def test_rss2_feed_without_comments_has_none(self):
        entries = parse_feed(_RSS2_FEED, source_id=1)
        for e in entries:
            assert e.comments_url is None


# ---------------------------------------------------------------------------
# Reddit link-post URL resolution
# ---------------------------------------------------------------------------

# Realistic Reddit RSS entry for a link post. The description contains the
# standard Reddit table markup: thumbnail anchor (href = external article URL),
# user link, [link] anchor (external), [comments] anchor (reddit comments URL).
_REDDIT_LINK_POST_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>r/worldnews</title>
    <link>https://www.reddit.com/r/worldnews/.rss</link>
    <description>The latest news from r/worldnews</description>
    <item>
      <title>Ukrainian drones hit gas storage</title>
      <link>https://www.reddit.com/r/worldnews/comments/1uatfze/ukrainian_drones/</link>
      <guid isPermaLink="false">t3_1uatfze</guid>
      <pubDate>Fri, 20 Jun 2025 10:00:00 +0000</pubDate>
      <description><![CDATA[<table><tr><td><a href="https://kyivindependent.com/ukrainian-drones-hit-gas-storage/"><img src="https://b.thumbs.redditmedia.com/t.jpg" /></a></td><td> submitted by &#32; <a href="https://www.reddit.com/user/newsuser">/u/newsuser</a> &#32; <span><a href="https://kyivindependent.com/ukrainian-drones-hit-gas-storage/">[link]</a></span> &#32; <span><a href="https://www.reddit.com/r/worldnews/comments/1uatfze/ukrainian_drones/">[comments]</a></span></td></tr></table>]]></description>
    </item>
  </channel>
</rss>
"""

# Reddit self/text post: description contains only reddit links and post body.
_REDDIT_SELF_POST_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>r/worldnews</title>
    <link>https://www.reddit.com/r/worldnews/.rss</link>
    <description>The latest news from r/worldnews</description>
    <item>
      <title>Live Thread: Breaking News</title>
      <link>https://www.reddit.com/r/worldnews/comments/live123/live_thread/</link>
      <guid isPermaLink="false">t3_live123</guid>
      <pubDate>Fri, 20 Jun 2025 09:00:00 +0000</pubDate>
      <description><![CDATA[<!-- SC_OFF --><div class="md"><p>This is a self post discussion thread.</p></div><!-- SC_ON --> submitted by <a href="https://www.reddit.com/user/moduser">/u/moduser</a> <span><a href="https://www.reddit.com/r/worldnews/comments/live123/live_thread/">[comments]</a></span>]]></description>
    </item>
  </channel>
</rss>
"""


class TestRedditLinkPostResolution:
    def test_link_post_resolves_external_article_url(self):
        """Reddit link post: feed_url becomes the external article URL, not the comments page."""
        entries = parse_feed(_REDDIT_LINK_POST_FEED, source_id=20)
        assert len(entries) == 1
        e = entries[0]
        assert e.feed_url == "https://kyivindependent.com/ukrainian-drones-hit-gas-storage/"

    def test_link_post_sets_comments_url_to_reddit_page(self):
        """Reddit link post: comments_url is the reddit comments page."""
        entries = parse_feed(_REDDIT_LINK_POST_FEED, source_id=20)
        e = entries[0]
        assert e.comments_url == "https://www.reddit.com/r/worldnews/comments/1uatfze/ukrainian_drones/"

    def test_link_post_raw_payload_link_is_external_url(self):
        """raw_payload['link'] is patched to the external URL so the processor fetches it."""
        entries = parse_feed(_REDDIT_LINK_POST_FEED, source_id=20)
        e = entries[0]
        assert e.raw_payload["link"] == "https://kyivindependent.com/ukrainian-drones-hit-gas-storage/"

    def test_self_post_keeps_reddit_link_as_feed_url(self):
        """Reddit self/text post (no external href): feed_url stays as the reddit comments URL."""
        entries = parse_feed(_REDDIT_SELF_POST_FEED, source_id=21)
        assert len(entries) == 1
        e = entries[0]
        assert e.feed_url == "https://www.reddit.com/r/worldnews/comments/live123/live_thread/"

    def test_self_post_raw_payload_link_unchanged(self):
        """Reddit self/text post: raw_payload['link'] is not patched."""
        entries = parse_feed(_REDDIT_SELF_POST_FEED, source_id=21)
        e = entries[0]
        assert e.raw_payload["link"] == "https://www.reddit.com/r/worldnews/comments/live123/live_thread/"

    def test_non_reddit_feed_is_completely_unchanged(self):
        """Non-Reddit RSS entry: feed_url, comments_url, and raw_payload are unaffected."""
        entries = parse_feed(_RSS2_FEED, source_id=1)
        assert len(entries) == 2
        for e in entries:
            assert "rss.example.com" in (e.feed_url or "")
            assert e.comments_url is None
            assert e.raw_payload.get("link", "").startswith("https://rss.example.com")

    def test_link_post_dedup_key_from_guid_not_affected(self):
        """Reddit link post: dedup_key still uses the guid (t3_...), not the resolved URL."""
        entries = parse_feed(_REDDIT_LINK_POST_FEED, source_id=20)
        e = entries[0]
        assert e.dedup_key == "t3_1uatfze"
