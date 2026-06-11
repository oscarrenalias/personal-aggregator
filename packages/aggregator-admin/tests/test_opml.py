"""Pure unit tests for OPML parse and build helpers."""
from __future__ import annotations

import types
from xml.etree import ElementTree

import pytest

from aggregator_admin.opml import ParsedFeed, build_opml, parse_opml


def _make_source(name: str, url: str, id: int = 1):
    return types.SimpleNamespace(name=name, feed_url=url, id=id)


# ---------------------------------------------------------------------------
# OPML sample fixtures
# ---------------------------------------------------------------------------

_FLAT_OPML = """\
<?xml version='1.0' encoding='utf-8'?>
<opml version="2.0">
  <head><title>Test</title></head>
  <body>
    <outline type="rss" title="Feed A" text="Feed A" xmlUrl="http://a.com/feed.xml" />
    <outline type="rss" title="Feed B" text="Feed B" xmlUrl="http://b.com/feed.xml" />
  </body>
</opml>"""

_NESTED_OPML = """\
<?xml version='1.0' encoding='utf-8'?>
<opml version="2.0">
  <head><title>Test</title></head>
  <body>
    <outline text="Tech">
      <outline type="rss" title="Tech Feed" text="Tech Feed" xmlUrl="http://tech.com/feed.xml" />
      <outline text="Sub-Tech">
        <outline type="rss" title="Sub Feed" text="Sub Feed" xmlUrl="http://sub.tech.com/feed.xml" />
      </outline>
    </outline>
    <outline type="rss" title="Top Level" text="Top Level" xmlUrl="http://top.com/feed.xml" />
  </body>
</opml>"""

_DUPE_OPML = """\
<?xml version='1.0' encoding='utf-8'?>
<opml version="2.0">
  <body>
    <outline type="rss" title="Feed A" xmlUrl="http://a.com/feed.xml" />
    <outline type="rss" title="Feed A Duplicate" xmlUrl="http://a.com/feed.xml" />
    <outline type="rss" title="Feed B" xmlUrl="http://b.com/feed.xml" />
  </body>
</opml>"""

_NAME_FALLBACK_OPML = """\
<?xml version='1.0' encoding='utf-8'?>
<opml version="2.0">
  <body>
    <outline type="rss" title="Has Title" text="Has Text" xmlUrl="http://title.com/feed.xml" />
    <outline type="rss" text="Has Text Only" xmlUrl="http://text.com/feed.xml" />
    <outline type="rss" xmlUrl="http://url-only.com/feed.xml" />
  </body>
</opml>"""

_MALFORMED_XML = "this is not xml at all <<<"

_NON_OPML_ROOT = """\
<?xml version='1.0' encoding='utf-8'?>
<rss version="2.0"><channel /></rss>"""


# ---------------------------------------------------------------------------
# parse_opml tests
# ---------------------------------------------------------------------------

class TestParseOpml:
    def test_flat_feeds_returns_all(self):
        feeds = parse_opml(_FLAT_OPML)
        urls = {f.url for f in feeds}
        assert urls == {"http://a.com/feed.xml", "http://b.com/feed.xml"}

    def test_nested_folders_are_flattened(self):
        feeds = parse_opml(_NESTED_OPML)
        urls = {f.url for f in feeds}
        assert urls == {
            "http://tech.com/feed.xml",
            "http://sub.tech.com/feed.xml",
            "http://top.com/feed.xml",
        }
        assert len(feeds) == 3

    def test_nested_name_derived_from_title(self):
        feeds = parse_opml(_NESTED_OPML)
        by_url = {f.url: f.name for f in feeds}
        assert by_url["http://tech.com/feed.xml"] == "Tech Feed"
        assert by_url["http://sub.tech.com/feed.xml"] == "Sub Feed"
        assert by_url["http://top.com/feed.xml"] == "Top Level"

    def test_dedup_within_file(self):
        feeds = parse_opml(_DUPE_OPML)
        urls = [f.url for f in feeds]
        assert urls.count("http://a.com/feed.xml") == 1
        assert len(feeds) == 2

    def test_name_fallback_prefers_title(self):
        feeds = parse_opml(_NAME_FALLBACK_OPML)
        by_url = {f.url: f.name for f in feeds}
        assert by_url["http://title.com/feed.xml"] == "Has Title"

    def test_name_fallback_uses_text_when_no_title(self):
        feeds = parse_opml(_NAME_FALLBACK_OPML)
        by_url = {f.url: f.name for f in feeds}
        assert by_url["http://text.com/feed.xml"] == "Has Text Only"

    def test_name_fallback_uses_url_when_no_title_or_text(self):
        feeds = parse_opml(_NAME_FALLBACK_OPML)
        by_url = {f.url: f.name for f in feeds}
        assert by_url["http://url-only.com/feed.xml"] == "http://url-only.com/feed.xml"

    def test_malformed_xml_raises_value_error(self):
        with pytest.raises(ValueError, match="Malformed XML"):
            parse_opml(_MALFORMED_XML)

    def test_non_opml_root_raises_value_error(self):
        with pytest.raises(ValueError, match="Expected root element"):
            parse_opml(_NON_OPML_ROOT)

    def test_returns_parsed_feed_instances(self):
        feeds = parse_opml(_FLAT_OPML)
        assert all(isinstance(f, ParsedFeed) for f in feeds)
        assert all(hasattr(f, "url") and hasattr(f, "name") for f in feeds)


# ---------------------------------------------------------------------------
# build_opml tests
# ---------------------------------------------------------------------------

class TestBuildOpml:
    def test_root_tag_and_version(self):
        result = build_opml([_make_source("Feed A", "http://a.com/feed.xml")])
        root = ElementTree.fromstring(result)
        assert root.tag == "opml"
        assert root.get("version") == "2.0"

    def test_outline_attributes(self):
        result = build_opml([_make_source("My Feed", "http://my.com/feed.xml")])
        root = ElementTree.fromstring(result)
        body = root.find("body")
        assert body is not None
        outlines = list(body)
        assert len(outlines) == 1
        ol = outlines[0]
        assert ol.get("type") == "rss"
        assert ol.get("text") == "My Feed"
        assert ol.get("title") == "My Feed"
        assert ol.get("xmlUrl") == "http://my.com/feed.xml"

    def test_deterministic_ordering_by_name_then_id(self):
        sources = [
            _make_source("Zebra Feed", "http://z.com/feed.xml", id=3),
            _make_source("alpha feed", "http://a.com/feed.xml", id=1),
            _make_source("Middle Feed", "http://m.com/feed.xml", id=2),
        ]
        result = build_opml(sources)
        root = ElementTree.fromstring(result)
        body = root.find("body")
        assert body is not None
        names = [ol.get("title") for ol in body]
        assert names == ["alpha feed", "Middle Feed", "Zebra Feed"]

    def test_special_characters_are_escaped(self):
        sources = [_make_source("Feed & <Friends>", "http://example.com/feed.xml?a=1&b=2")]
        result = build_opml(sources)
        root = ElementTree.fromstring(result)
        body = root.find("body")
        assert body is not None
        ol = list(body)[0]
        assert ol.get("text") == "Feed & <Friends>"
        assert ol.get("xmlUrl") == "http://example.com/feed.xml?a=1&b=2"

    def test_head_title_present(self):
        result = build_opml([])
        root = ElementTree.fromstring(result)
        head = root.find("head")
        assert head is not None
        title = head.find("title")
        assert title is not None
        title_text = title.text or ""
        assert "Personal Aggregator" in title_text

    def test_empty_sources_produces_valid_opml(self):
        result = build_opml([])
        root = ElementTree.fromstring(result)
        body = root.find("body")
        assert body is not None
        assert len(list(body)) == 0

    def test_xml_declaration_present(self):
        result = build_opml([])
        assert result.startswith("<?xml")


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_roundtrip_recovers_all_urls(self):
        sources = [
            _make_source("Alpha Feed", "http://alpha.com/feed.xml", id=1),
            _make_source("Beta Feed", "http://beta.com/feed.xml", id=2),
        ]
        opml_text = build_opml(sources)
        parsed = parse_opml(opml_text)
        parsed_urls = {f.url for f in parsed}
        original_urls = {s.feed_url for s in sources}
        assert parsed_urls == original_urls

    def test_roundtrip_recovers_names(self):
        sources = [_make_source("My Special Feed", "http://special.com/feed.xml")]
        opml_text = build_opml(sources)
        parsed = parse_opml(opml_text)
        assert len(parsed) == 1
        assert parsed[0].name == "My Special Feed"
        assert parsed[0].url == "http://special.com/feed.xml"
