import pytest

from aggregator_common.llm_params import apply_reasoning_params


def _base_kwargs() -> dict:
    return {"model": "gpt-4.1", "max_tokens": 1024, "temperature": 0.3}


def test_none_returns_equal_dict_not_same_object():
    kwargs = _base_kwargs()
    result = apply_reasoning_params(kwargs, None)
    assert result == kwargs
    assert result is not kwargs


def test_none_effort_keeps_temperature():
    kwargs = _base_kwargs()
    result = apply_reasoning_params(kwargs, "none")
    assert result["temperature"] == 0.3


def test_none_effort_adds_reasoning_effort_key():
    result = apply_reasoning_params(_base_kwargs(), "none")
    assert result["reasoning_effort"] == "none"


def test_none_effort_replaces_max_tokens_with_max_completion_tokens():
    result = apply_reasoning_params(_base_kwargs(), "none")
    assert "max_tokens" not in result
    assert result["max_completion_tokens"] == 1024


def test_none_effort_adds_allowed_openai_params():
    result = apply_reasoning_params(_base_kwargs(), "none")
    assert result["allowed_openai_params"] == ["reasoning_effort"]


def test_low_effort_removes_temperature():
    result = apply_reasoning_params(_base_kwargs(), "low")
    assert "temperature" not in result


def test_low_effort_adds_reasoning_effort_key():
    result = apply_reasoning_params(_base_kwargs(), "low")
    assert result["reasoning_effort"] == "low"


def test_low_effort_replaces_max_tokens_with_max_completion_tokens():
    result = apply_reasoning_params(_base_kwargs(), "low")
    assert "max_tokens" not in result
    assert result["max_completion_tokens"] == 1024


def test_low_effort_adds_allowed_openai_params():
    result = apply_reasoning_params(_base_kwargs(), "low")
    assert result["allowed_openai_params"] == ["reasoning_effort"]


def test_existing_allowed_openai_params_preserved():
    kwargs = {**_base_kwargs(), "allowed_openai_params": ["some_param"]}
    result = apply_reasoning_params(kwargs, "medium")
    assert "some_param" in result["allowed_openai_params"]
    assert "reasoning_effort" in result["allowed_openai_params"]


def test_reasoning_effort_not_duplicated_if_already_present():
    kwargs = {**_base_kwargs(), "allowed_openai_params": ["reasoning_effort"]}
    result = apply_reasoning_params(kwargs, "low")
    assert result["allowed_openai_params"].count("reasoning_effort") == 1


def test_existing_max_completion_tokens_wins():
    kwargs = {**_base_kwargs(), "max_completion_tokens": 2048}
    result = apply_reasoning_params(kwargs, "low")
    assert result["max_completion_tokens"] == 2048
    assert "max_tokens" not in result


def test_invalid_effort_raises_value_error():
    with pytest.raises(ValueError, match="Invalid reasoning_effort"):
        apply_reasoning_params(_base_kwargs(), "ultra")


def test_kwargs_not_mutated():
    kwargs = _base_kwargs()
    original = dict(kwargs)
    apply_reasoning_params(kwargs, "low")
    assert kwargs == original
