"""
promptfoo custom assertion for summarize_rank evaluations.

Checks that the model output parses as a valid RankResult.
Parse failures are counted as a named score for reporting.
"""

import json
import re


def get_assert(output: str, context: dict) -> dict:
    stripped = output.strip()
    fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()

    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return _result(passed=False, score_key="parse_error", reason="Output is not valid JSON.")

    # Validate required RankResult fields
    required = {"summary", "topics", "importance_score", "importance_reason"}
    missing = required - set(parsed.keys())
    if missing:
        return _result(
            passed=False,
            score_key="parse_error",
            reason=f"Missing required fields: {', '.join(sorted(missing))}",
        )

    if not isinstance(parsed.get("importance_score"), (int, float)):
        return _result(
            passed=False,
            score_key="parse_error",
            reason="importance_score is not a number.",
        )

    if not isinstance(parsed.get("topics"), list):
        return _result(
            passed=False,
            score_key="parse_error",
            reason="topics is not a list.",
        )

    return _result(passed=True, score_key="ok", reason="Output parses as RankResult.")


_SCORE_KEYS = ("ok", "parse_error")


def _result(*, passed: bool, score_key: str, reason: str) -> dict:
    named_scores = {k: 0 for k in _SCORE_KEYS}
    named_scores[score_key] = 1
    return {
        "pass": passed,
        "score": 1 if passed else 0,
        "reason": reason,
        "namedScores": named_scores,
    }
