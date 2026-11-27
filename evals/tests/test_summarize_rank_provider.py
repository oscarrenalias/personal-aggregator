"""Tests for response_format support in evals/shared/litellm_provider.py.

Verifies that:
1. A response_format import path string is resolved correctly.
2. The resolved class is included in the cache key.
3. Existing merge configs (no response_format) are unaffected.
"""

import json
from pathlib import Path
from unittest import mock

import pytest

# conftest.py adds evals/ to sys.path
import shared.litellm_provider as prov


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("EVAL_NO_CACHE", raising=False)
    monkeypatch.setattr(prov, "_CACHE_DIR", tmp_path / "litellm")
    return tmp_path / "litellm"


class TestResponseFormatResolution:
    def test_resolve_valid_import_path(self):
        cls = prov._resolve_response_format("aggregator_summarize_rank.schema:RankResult")
        from aggregator_summarize_rank.schema import RankResult
        assert cls is RankResult

    def test_resolve_missing_colon_raises(self):
        with pytest.raises(ValueError, match="module:ClassName"):
            prov._resolve_response_format("aggregator_summarize_rank.schema.RankResult")

    def test_resolve_nonexistent_module_raises(self):
        with pytest.raises((ModuleNotFoundError, ImportError)):
            prov._resolve_response_format("nonexistent_module_xyz:SomeClass")

    def test_resolve_nonexistent_class_raises(self):
        with pytest.raises(AttributeError):
            prov._resolve_response_format("aggregator_summarize_rank.schema:NoSuchClass")


class TestCacheKeyWithResponseFormat:
    def test_response_format_path_in_cache_key(self):
        msgs = [{"role": "user", "content": "rank this"}]
        key_with = prov._cache_key("gpt-6-luna", msgs, {}, 0, "some.module:SomeClass")
        key_without = prov._cache_key("gpt-6-luna", msgs, {}, 0, None)
        assert key_with != key_without

    def test_different_format_paths_different_keys(self):
        msgs = [{"role": "user", "content": "rank this"}]
        key_a = prov._cache_key("m", msgs, {}, 0, "mod:ClassA")
        key_b = prov._cache_key("m", msgs, {}, 0, "mod:ClassB")
        assert key_a != key_b

    def test_same_inputs_stable_key(self):
        msgs = [{"role": "user", "content": "rank this"}]
        path = "aggregator_summarize_rank.schema:RankResult"
        k1 = prov._cache_key("m", msgs, {}, 0, path)
        k2 = prov._cache_key("m", msgs, {}, 0, path)
        assert k1 == k2


class TestCallApiWithResponseFormat:
    def _make_response(self, text: str = '{"summary":"s","topics":[],"importance_score":70,"importance_reason":"r"}'):
        usage = mock.MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 20
        usage.total_tokens = 30

        choice = mock.MagicMock()
        choice.message.content = text

        resp = mock.MagicMock()
        resp.choices = [choice]
        resp.usage = usage
        return resp

    def test_response_format_passed_to_litellm(self):
        from aggregator_summarize_rank.schema import RankResult

        resp = self._make_response()
        messages = [{"role": "user", "content": "test"}]
        options = {
            "config": {
                "model": "gpt-6-luna",
                "response_format": "aggregator_summarize_rank.schema:RankResult",
                "params": {"timeout": 30},
            }
        }
        ctx = {"repeatIndex": 0}

        with mock.patch("litellm.completion", return_value=resp) as m_completion:
            with mock.patch("litellm.completion_cost", return_value=0.001):
                result = prov.call_api(json.dumps(messages), options, ctx)

        assert "error" not in result
        call_kwargs = m_completion.call_args.kwargs
        assert call_kwargs.get("response_format") is RankResult

    def test_no_response_format_unchanged_behaviour(self):
        """Configs without response_format must behave exactly as before."""
        resp = self._make_response('{"same_story": false, "reason": "stub"}')
        messages = [{"role": "user", "content": "merge?"}]
        options = {
            "config": {
                "model": "gpt-6-luna",
                "params": {"reasoning_effort": "none", "max_completion_tokens": 128},
            }
        }
        ctx = {"repeatIndex": 0}

        with mock.patch("litellm.completion", return_value=resp) as m_completion:
            with mock.patch("litellm.completion_cost", return_value=0.0):
                result = prov.call_api(json.dumps(messages), options, ctx)

        assert "error" not in result
        call_kwargs = m_completion.call_args.kwargs
        assert "response_format" not in call_kwargs
