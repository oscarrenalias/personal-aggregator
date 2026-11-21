"""Tests for the disk response cache in evals/shared/litellm_provider.py.

All tests are deterministic and require no network access.
"""

import json
from pathlib import Path
from unittest import mock

import pytest


def _make_response(text: str = "MERGE"):
    usage = mock.MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5
    usage.total_tokens = 15

    choice = mock.MagicMock()
    choice.message.content = text

    resp = mock.MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


def _call(
    *,
    model: str = "gpt-4.1-mini",
    messages: list | None = None,
    params: dict | None = None,
    repeat_index: int = 0,
):
    if messages is None:
        messages = [{"role": "user", "content": "merge?"}]
    opts = {"config": {"model": model, "params": params or {}}}
    ctx = {"repeatIndex": repeat_index}
    import shared.litellm_provider as prov

    return prov.call_api(json.dumps(messages), opts, ctx)


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect the cache dir to a tmp dir and clear EVAL_NO_CACHE."""
    monkeypatch.delenv("EVAL_NO_CACHE", raising=False)
    import shared.litellm_provider as prov

    monkeypatch.setattr(prov, "_CACHE_DIR", tmp_path / "litellm")
    return tmp_path / "litellm"


# ---------------------------------------------------------------------------
# Cache key uniqueness
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_different_models(self):
        from shared.litellm_provider import _cache_key

        msgs = [{"role": "user", "content": "x"}]
        assert _cache_key("gpt-4.1-mini", msgs, {}, 0) != _cache_key("gpt-4.1", msgs, {}, 0)

    def test_different_messages(self):
        from shared.litellm_provider import _cache_key

        k1 = _cache_key("m", [{"role": "user", "content": "a"}], {}, 0)
        k2 = _cache_key("m", [{"role": "user", "content": "b"}], {}, 0)
        assert k1 != k2

    def test_different_params(self):
        from shared.litellm_provider import _cache_key

        msgs = [{"role": "user", "content": "x"}]
        assert _cache_key("m", msgs, {"temperature": 0.0}, 0) != _cache_key(
            "m", msgs, {"temperature": 1.0}, 0
        )

    def test_different_repeat_indices(self):
        from shared.litellm_provider import _cache_key

        msgs = [{"role": "user", "content": "x"}]
        assert _cache_key("m", msgs, {}, 0) != _cache_key("m", msgs, {}, 1)

    def test_same_inputs_stable_key(self):
        from shared.litellm_provider import _cache_key

        msgs = [{"role": "user", "content": "x"}]
        assert _cache_key("m", msgs, {"t": 0.5}, 2) == _cache_key("m", msgs, {"t": 0.5}, 2)


# ---------------------------------------------------------------------------
# Cache hit / miss behaviour
# ---------------------------------------------------------------------------


class TestCacheHitMiss:
    def test_first_call_invokes_litellm(self):
        resp = _make_response("MERGE")
        with mock.patch("litellm.completion", return_value=resp) as m:
            with mock.patch("litellm.completion_cost", return_value=0.001):
                result = _call()
        m.assert_called_once()
        assert result["output"] == "MERGE"
        assert not result.get("cached", False)

    def test_second_call_hits_cache_no_litellm(self):
        resp = _make_response("SEPARATE")
        with mock.patch("litellm.completion", return_value=resp):
            with mock.patch("litellm.completion_cost", return_value=0.001):
                _call()

        with mock.patch("litellm.completion") as m2:
            result = _call()

        m2.assert_not_called()
        assert result["output"] == "SEPARATE"
        assert result["cached"] is True

    def test_different_repeat_indices_are_independent(self):
        """Repeat 0 and repeat 1 must cache separately — repeats are independent samples."""
        resp0 = _make_response("MERGE")
        resp1 = _make_response("SEPARATE")

        with mock.patch("litellm.completion", return_value=resp0):
            with mock.patch("litellm.completion_cost", return_value=0.0):
                _call(repeat_index=0)

        with mock.patch("litellm.completion", return_value=resp1):
            with mock.patch("litellm.completion_cost", return_value=0.0):
                _call(repeat_index=1)

        with mock.patch("litellm.completion") as m_no_call:
            r0 = _call(repeat_index=0)
            r1 = _call(repeat_index=1)

        m_no_call.assert_not_called()
        assert r0["output"] == "MERGE"
        assert r1["output"] == "SEPARATE"
        assert r0["cached"] is True
        assert r1["cached"] is True


# ---------------------------------------------------------------------------
# Errors are never cached
# ---------------------------------------------------------------------------


class TestErrorsNotCached:
    def test_error_response_not_written_to_cache(self):
        with mock.patch("litellm.completion", side_effect=RuntimeError("API error")):
            result = _call()
        assert "error" in result

        resp = _make_response("OK")
        with mock.patch("litellm.completion", return_value=resp) as m2:
            with mock.patch("litellm.completion_cost", return_value=0.0):
                result2 = _call()
        m2.assert_called_once()
        assert result2["output"] == "OK"


# ---------------------------------------------------------------------------
# EVAL_NO_CACHE disables both reads and writes
# ---------------------------------------------------------------------------


class TestEvalNoCache:
    def test_disables_cache_reads(self, monkeypatch):
        resp = _make_response("MERGE")
        with mock.patch("litellm.completion", return_value=resp):
            with mock.patch("litellm.completion_cost", return_value=0.0):
                _call()

        monkeypatch.setenv("EVAL_NO_CACHE", "1")
        with mock.patch("litellm.completion", return_value=resp) as m2:
            with mock.patch("litellm.completion_cost", return_value=0.0):
                result = _call()

        m2.assert_called_once()
        assert not result.get("cached", False)

    def test_disables_cache_writes(self, monkeypatch, isolated_cache):
        monkeypatch.setenv("EVAL_NO_CACHE", "1")
        resp = _make_response("MERGE")
        with mock.patch("litellm.completion", return_value=resp):
            with mock.patch("litellm.completion_cost", return_value=0.0):
                _call()

        cache_files = list(isolated_cache.glob("*.json")) if isolated_cache.exists() else []
        assert cache_files == []


# ---------------------------------------------------------------------------
# Lazy litellm import: cache hit must not import litellm
# ---------------------------------------------------------------------------


class TestLazyLitellmImport:
    def test_cache_hit_does_not_import_litellm(self, isolated_cache):
        """A cache hit must not trigger the litellm import."""
        import sys
        import shared.litellm_provider as prov

        # Pre-populate the cache directly (bypasses litellm entirely)
        messages = [{"role": "user", "content": "lazy-import-test"}]
        key = prov._cache_key("gpt-4.1-mini", messages, {}, 0)
        isolated_cache.mkdir(parents=True, exist_ok=True)
        (isolated_cache / f"{key}.json").write_text(
            json.dumps({
                "output": "CACHED",
                "tokenUsage": {"prompt": 1, "completion": 1, "total": 2},
            })
        )

        # Remove litellm from sys.modules to simulate it not having been imported
        saved_litellm = sys.modules.pop("litellm", None)
        try:
            opts = {"config": {"model": "gpt-4.1-mini", "params": {}}}
            ctx = {"repeatIndex": 0}
            result = prov.call_api(json.dumps(messages), opts, ctx)

            assert result.get("cached") is True
            assert "litellm" not in sys.modules
        finally:
            if saved_litellm is not None:
                sys.modules["litellm"] = saved_litellm
