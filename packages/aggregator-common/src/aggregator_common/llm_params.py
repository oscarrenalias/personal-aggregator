REASONING_EFFORTS: frozenset[str] = frozenset({"none", "minimal", "low", "medium", "high"})


def apply_reasoning_params(kwargs: dict, reasoning_effort: str | None) -> dict:
    """Apply reasoning_effort-related params to an LLM call kwargs dict.

    Returns a new dict. When reasoning_effort is None the returned dict is an equal
    copy with no other mutations.

    For non-None effort:
    - reasoning_effort key is set.
    - max_tokens is moved to max_completion_tokens (existing max_completion_tokens wins).
    - temperature is removed unless effort == 'none'.
    - 'reasoning_effort' is appended to allowed_openai_params (created if absent;
      deduplicated if already present).

    Raises ValueError for an effort value outside REASONING_EFFORTS.
    """
    if reasoning_effort is None:
        return dict(kwargs)

    if reasoning_effort not in REASONING_EFFORTS:
        raise ValueError(
            f"Invalid reasoning_effort {reasoning_effort!r}; "
            f"must be one of {sorted(REASONING_EFFORTS)}"
        )

    result = dict(kwargs)

    result["reasoning_effort"] = reasoning_effort

    if "max_tokens" in result:
        moved_value = result.pop("max_tokens")
        if "max_completion_tokens" not in result:
            result["max_completion_tokens"] = moved_value

    if reasoning_effort != "none":
        result.pop("temperature", None)

    existing = list(result.get("allowed_openai_params", []))
    if "reasoning_effort" not in existing:
        existing.append("reasoning_effort")
    result["allowed_openai_params"] = existing

    return result
