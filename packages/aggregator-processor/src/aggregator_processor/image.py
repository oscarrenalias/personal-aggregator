from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urlparse


def _is_valid_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _looks_like_image_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"))


class _MetaAndImgParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.og_image: str | None = None
        self.twitter_image: str | None = None
        self.first_img: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k.lower(): (v or "") for k, v in attrs}

        if tag == "meta":
            prop = attr_dict.get("property", "").lower()
            name = attr_dict.get("name", "").lower()
            content = attr_dict.get("content", "")

            if not self.og_image and prop == "og:image" and _is_valid_url(content):
                self.og_image = content

            if not self.twitter_image and name == "twitter:image" and _is_valid_url(content):
                self.twitter_image = content

        elif tag == "img" and self.first_img is None:
            src = attr_dict.get("src", "")
            if _is_valid_url(src):
                self.first_img = src


def _parse_html(html: str | bytes) -> _MetaAndImgParser:
    parser = _MetaAndImgParser()
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    try:
        parser.feed(html)
    except Exception:
        pass
    return parser


def _feed_media_url(feed_payload: dict) -> str | None:
    # media:content (feedparser normalises the colon to underscore)
    for item in feed_payload.get("media_content") or []:
        url = item.get("url") or ""
        medium = item.get("medium") or ""
        if url and (medium == "image" or _looks_like_image_url(url)) and _is_valid_url(url):
            return url

    # media:thumbnail
    for item in feed_payload.get("media_thumbnail") or []:
        url = item.get("url") or ""
        if url and _is_valid_url(url):
            return url

    # image enclosure
    for enc in feed_payload.get("enclosures") or []:
        url = enc.get("url") or ""
        enc_type = enc.get("type") or ""
        if url and enc_type.startswith("image/") and _is_valid_url(url):
            return url

    return None


def _first_img_from_feed(feed_payload: dict) -> str | None:
    candidates: list[str] = []

    # content is a list of dicts with a 'value' key in feedparser
    for item in feed_payload.get("content") or []:
        if isinstance(item, dict):
            v = item.get("value") or ""
            if v:
                candidates.append(v)

    summary = feed_payload.get("summary") or ""
    if summary:
        candidates.append(summary)

    for html_content in candidates:
        parsed = _parse_html(html_content)
        if parsed.first_img:
            return parsed.first_img

    return None


def select_header_image(
    page_html: str | bytes | None,
    feed_payload: dict,
    default_image_url: str | None,
) -> str | None:
    """Return the best header-image URL using the priority chain.

    1. og:image from page_html  (skipped when page_html is None)
    2. twitter:image from page_html  (skipped when page_html is None)
    3. Feed media: media:content / media:thumbnail / image enclosure
    4. First <img> src in page_html, then in feed content
    5. default_image_url
    6. None
    """
    page_parsed: _MetaAndImgParser | None = None
    if page_html is not None:
        page_parsed = _parse_html(page_html)

        if page_parsed.og_image:
            return page_parsed.og_image

        if page_parsed.twitter_image:
            return page_parsed.twitter_image

    feed_media = _feed_media_url(feed_payload)
    if feed_media:
        return feed_media

    if page_parsed is not None and page_parsed.first_img:
        return page_parsed.first_img

    feed_img = _first_img_from_feed(feed_payload)
    if feed_img:
        return feed_img

    if default_image_url:
        return default_image_url

    return None
