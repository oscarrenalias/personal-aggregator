import hashlib
import time

import pytest

from aggregator_retriever.normalize import dedup_key, normalize_url, serialize_entry


class TestNormalizeUrl:
    @pytest.mark.parametrize("url,expected", [
        # default port dropped
        ("http://example.com:80/foo", "http://example.com/foo"),
        ("https://example.com:443/foo", "https://example.com/foo"),
        # non-default port retained
        ("http://example.com:8080/foo", "http://example.com:8080/foo"),
        ("https://example.com:8443/foo", "https://example.com:8443/foo"),
        # fragment stripped
        ("https://example.com/foo#section", "https://example.com/foo"),
        ("https://example.com/foo?q=1#hash", "https://example.com/foo?q=1"),
        # tracking params removed
        ("https://example.com/?fbclid=abc", "https://example.com/"),
        ("https://example.com/?gclid=abc", "https://example.com/"),
        ("https://example.com/?mc_eid=abc", "https://example.com/"),
        ("https://example.com/?igshid=abc", "https://example.com/"),
        # utm_* variants
        ("https://example.com/?utm_source=rss&utm_medium=feed", "https://example.com/"),
        ("https://example.com/?utm_campaign=X&q=1", "https://example.com/?q=1"),
        # query sort
        ("https://example.com/?z=1&a=2", "https://example.com/?a=2&z=1"),
        # trailing slash stripped except root
        ("https://example.com/foo/", "https://example.com/foo"),
        ("https://example.com/", "https://example.com/"),
        # scheme + host lowercased
        ("HTTP://EXAMPLE.COM/foo", "http://example.com/foo"),
    ])
    def test_normalize(self, url, expected):
        assert normalize_url(url) == expected

    def test_idempotent(self):
        url = "https://example.com/foo?a=1&utm_source=x#frag"
        once = normalize_url(url)
        twice = normalize_url(once)
        assert once == twice

    def test_mixed_tracking_and_real_params_sorted(self):
        url = "https://example.com/path?z=9&utm_source=email&a=1&fbclid=x"
        result = normalize_url(url)
        assert result == "https://example.com/path?a=1&z=9"


class TestDedupKey:
    def test_id_present_returns_id(self):
        entry = {"id": "tag:example.com,2024:1", "link": "https://example.com/1"}
        key = dedup_key(entry, "42")
        assert key == "tag:example.com,2024:1"

    def test_id_absent_link_present_returns_normalized_link(self):
        entry = {"link": "https://example.com/foo?utm_source=rss#section"}
        key = dedup_key(entry, "42")
        assert key == "https://example.com/foo"

    def test_id_and_link_absent_title_published_returns_sha256(self):
        entry = {"title": "My Title", "published": "Mon, 01 Jan 2024 00:00:00 +0000"}
        key = dedup_key(entry, "7")
        expected = hashlib.sha256(
            "7\nMy Title\nMon, 01 Jan 2024 00:00:00 +0000".encode()
        ).hexdigest()
        assert key == expected

    def test_title_only_returns_sha256(self):
        entry = {"title": "Only Title"}
        key = dedup_key(entry, "5")
        expected = hashlib.sha256("5\nOnly Title\n".encode()).hexdigest()
        assert key == expected

    def test_all_absent_returns_none(self):
        key = dedup_key({}, "1")
        assert key is None

    def test_object_with_attrs(self):
        class FakeEntry:
            id = "entry-id-123"
            link = "https://example.com/ignored"

        key = dedup_key(FakeEntry(), "99")
        assert key == "entry-id-123"

    def test_empty_id_falls_through_to_link(self):
        entry = {"id": "", "link": "https://example.com/article"}
        key = dedup_key(entry, "1")
        assert key == "https://example.com/article"


class TestSerializeEntry:
    def test_struct_time_converted_to_iso(self):
        st = time.strptime("2024-01-15 12:30:00", "%Y-%m-%d %H:%M:%S")
        entry = {"published_parsed": st, "title": "Test"}
        result = serialize_entry(entry)
        assert result["published_parsed"] == "2024-01-15T12:30:00+00:00"
        assert result["title"] == "Test"

    def test_bytes_converted_to_base64(self):
        import base64

        entry = {"content_blob": b"hello bytes"}
        result = serialize_entry(entry)
        assert result["content_blob"] == base64.b64encode(b"hello bytes").decode("ascii")

    def test_scalar_types_pass_through(self):
        entry = {"title": "T", "count": 5, "score": 1.5, "flag": True, "missing": None}
        result = serialize_entry(entry)
        assert result == {"title": "T", "count": 5, "score": 1.5, "flag": True, "missing": None}

    def test_nested_dict_serialized(self):
        st = time.strptime("2024-01-01 00:00:00", "%Y-%m-%d %H:%M:%S")
        entry = {"meta": {"published_parsed": st, "label": "x"}}
        result = serialize_entry(entry)
        assert result["meta"]["published_parsed"] == "2024-01-01T00:00:00+00:00"
        assert result["meta"]["label"] == "x"

    def test_result_is_json_safe(self):
        import json
        import time as _time

        st = _time.strptime("2024-06-01 00:00:00", "%Y-%m-%d %H:%M:%S")
        entry = {"ts": st, "data": b"\x00\xff", "title": "ok"}
        result = serialize_entry(entry)
        json.dumps(result)  # must not raise

    def test_non_serializable_dropped(self):
        class Unpicklable:
            pass

        entry = {"title": "good", "bad": Unpicklable()}
        result = serialize_entry(entry)
        assert "title" in result
        assert result.get("bad") is None
