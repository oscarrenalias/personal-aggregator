"""Unit tests for aggregator_processor.image — all priority levels independent."""

from __future__ import annotations

import pytest

from aggregator_processor.image import select_header_image

# ---------------------------------------------------------------------------
# HTML stubs for page_html
# ---------------------------------------------------------------------------

_OG_AND_TWITTER = b"""
<html><head>
  <meta property="og:image" content="https://cdn.example.com/og.jpg">
  <meta name="twitter:image" content="https://cdn.example.com/twitter.jpg">
</head><body><img src="https://cdn.example.com/body-img.jpg"></body></html>
"""

_TWITTER_ONLY = b"""
<html><head>
  <meta name="twitter:image" content="https://cdn.example.com/twitter-only.jpg">
</head><body><img src="https://cdn.example.com/body-img.jpg"></body></html>
"""

_IMG_ONLY = b"""
<html><body>
  <p>Some text.</p>
  <img src="https://cdn.example.com/first-img.jpg">
  <img src="https://cdn.example.com/second-img.jpg">
</body></html>
"""

_NO_IMAGES = b"<html><body><p>text only, no images</p></body></html>"


# ---------------------------------------------------------------------------
# Priority 1: og:image from page HTML
# ---------------------------------------------------------------------------


class TestOgImage:
    def test_og_image_returned_when_present(self):
        result = select_header_image(_OG_AND_TWITTER, {}, None)
        assert result == "https://cdn.example.com/og.jpg"

    def test_og_image_as_bytes_input(self):
        html = b'<meta property="og:image" content="https://cdn.example.com/og-bytes.png">'
        result = select_header_image(html, {}, None)
        assert result == "https://cdn.example.com/og-bytes.png"

    def test_og_image_as_str_input(self):
        html = '<meta property="og:image" content="https://cdn.example.com/og-str.png">'
        result = select_header_image(html, {}, None)
        assert result == "https://cdn.example.com/og-str.png"

    def test_og_image_invalid_url_skipped(self):
        html = b'<meta property="og:image" content="not-a-url">'
        result = select_header_image(html, {}, "https://cdn.example.com/default.jpg")
        assert result == "https://cdn.example.com/default.jpg"


# ---------------------------------------------------------------------------
# Priority 2: twitter:image (when og:image absent)
# ---------------------------------------------------------------------------


class TestTwitterImage:
    def test_twitter_image_returned_when_no_og(self):
        result = select_header_image(_TWITTER_ONLY, {}, None)
        assert result == "https://cdn.example.com/twitter-only.jpg"

    def test_og_image_beats_twitter_image(self):
        result = select_header_image(_OG_AND_TWITTER, {}, None)
        assert result == "https://cdn.example.com/og.jpg"


# ---------------------------------------------------------------------------
# Priority 3: feed media (page_html may be None)
# ---------------------------------------------------------------------------


class TestFeedMedia:
    def test_media_content_with_image_medium(self):
        feed = {"media_content": [{"url": "https://cdn.example.com/media.jpg", "medium": "image"}]}
        result = select_header_image(_NO_IMAGES, feed, None)
        assert result == "https://cdn.example.com/media.jpg"

    def test_media_content_image_url_by_extension(self):
        feed = {"media_content": [{"url": "https://cdn.example.com/photo.png", "medium": ""}]}
        result = select_header_image(_NO_IMAGES, feed, None)
        assert result == "https://cdn.example.com/photo.png"

    def test_media_thumbnail(self):
        feed = {"media_thumbnail": [{"url": "https://cdn.example.com/thumb.jpg"}]}
        result = select_header_image(_NO_IMAGES, feed, None)
        assert result == "https://cdn.example.com/thumb.jpg"

    def test_image_enclosure(self):
        feed = {"enclosures": [{"url": "https://cdn.example.com/enc.jpg", "type": "image/jpeg"}]}
        result = select_header_image(_NO_IMAGES, feed, None)
        assert result == "https://cdn.example.com/enc.jpg"

    def test_feed_media_returned_when_page_html_none(self):
        feed = {"media_content": [{"url": "https://cdn.example.com/feed-media.webp", "medium": "image"}]}
        result = select_header_image(None, feed, None)
        assert result == "https://cdn.example.com/feed-media.webp"

    def test_non_image_enclosure_ignored(self):
        feed = {"enclosures": [{"url": "https://cdn.example.com/audio.mp3", "type": "audio/mpeg"}]}
        result = select_header_image(_NO_IMAGES, feed, "https://cdn.example.com/default.jpg")
        assert result == "https://cdn.example.com/default.jpg"


# ---------------------------------------------------------------------------
# Priority 4: first <img> src — page first, then feed HTML
# ---------------------------------------------------------------------------


class TestFirstImg:
    def test_first_img_from_page_html(self):
        result = select_header_image(_IMG_ONLY, {}, None)
        assert result == "https://cdn.example.com/first-img.jpg"

    def test_first_img_from_feed_content_when_page_none(self):
        feed = {
            "content": [{"value": '<img src="https://cdn.example.com/feed-content-img.jpg">'}]
        }
        result = select_header_image(None, feed, None)
        assert result == "https://cdn.example.com/feed-content-img.jpg"

    def test_first_img_from_feed_summary_when_page_none(self):
        feed = {"summary": '<p><img src="https://cdn.example.com/summary-img.png"></p>'}
        result = select_header_image(None, feed, None)
        assert result == "https://cdn.example.com/summary-img.png"

    def test_feed_content_checked_before_feed_summary(self):
        feed = {
            "content": [{"value": '<img src="https://cdn.example.com/content-wins.jpg">'}],
            "summary": '<img src="https://cdn.example.com/summary-loses.jpg">',
        }
        result = select_header_image(None, feed, None)
        assert result == "https://cdn.example.com/content-wins.jpg"


# ---------------------------------------------------------------------------
# Priority 5 & 6: default_image_url and final None
# ---------------------------------------------------------------------------


class TestDefaultAndNone:
    def test_default_image_url_used_as_fallback(self):
        result = select_header_image(_NO_IMAGES, {}, "https://cdn.example.com/default.jpg")
        assert result == "https://cdn.example.com/default.jpg"

    def test_none_returned_when_everything_absent(self):
        result = select_header_image(_NO_IMAGES, {}, None)
        assert result is None

    def test_none_returned_when_page_none_and_no_feed_or_default(self):
        result = select_header_image(None, {}, None)
        assert result is None

    def test_page_none_does_not_prevent_default(self):
        result = select_header_image(None, {}, "https://cdn.example.com/default.jpg")
        assert result == "https://cdn.example.com/default.jpg"
