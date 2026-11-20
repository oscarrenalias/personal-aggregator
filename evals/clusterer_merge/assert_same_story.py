"""
promptfoo custom assertion for clusterer.merge evaluations.

Parses the model's JSON output and checks whether same_story matches
the expected label (merge → true, separate → false).
"""

import json
import re


def get_assert(output: str, context: dict) -> dict:
    """
    GradingResult assertion for promptfoo.

    Args:
        output: Raw model output string (may contain a ```json ... ``` fence).
        context: promptfoo context dict; context['vars']['label'] is 'merge' or 'separate'.

    Returns:
        GradingResult dict with keys: pass, score, reason, namedScores.
    """
    label = context["vars"]["label"]
    expected_same_story = label == "merge"

    # Strip optional ```json ... ``` fence
    stripped = output.strip()
    fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()

    # Parse JSON
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return _result(
            passed=False,
            score_key="parse_error",
            reason="Output is not valid JSON.",
        )

    # Validate same_story field
    same_story = parsed.get("same_story", _MISSING)
    if same_story is _MISSING:
        return _result(
            passed=False,
            score_key="parse_error",
            reason="same_story field is missing.",
        )
    if not isinstance(same_story, bool):
        return _result(
            passed=False,
            score_key="parse_error",
            reason=f"same_story is not a boolean (got {type(same_story).__name__}).",
        )

    model_reason = parsed.get("reason", "")

    if same_story == expected_same_story:
        return _result(
            passed=True,
            score_key="correct",
            reason=model_reason,
        )

    if same_story and not expected_same_story:
        # Model said merge, label says separate
        return _result(
            passed=False,
            score_key="wrong_merge",
            reason=model_reason,
        )

    # same_story is False but expected True: missed merge
    return _result(
        passed=False,
        score_key="missed_merge",
        reason=model_reason,
    )


# Sentinel for missing dict key (avoids collision with None)
_MISSING = object()

_SCORE_KEYS = ("correct", "wrong_merge", "missed_merge", "parse_error")


def _result(*, passed: bool, score_key: str, reason: str) -> dict:
    named_scores = {k: 0 for k in _SCORE_KEYS}
    named_scores[score_key] = 1
    return {
        "pass": passed,
        "score": 1 if passed else 0,
        "reason": reason,
        "namedScores": named_scores,
    }
