"""Tests for evals/clusterer_merge/assert_same_story.py.

Covers all 7 cases from the acceptance criteria:
  1. correct merge   – label=merge, same_story=true  → pass=True,  correct=1
  2. correct separate – label=separate, same_story=false → pass=True, correct=1
  3. wrong_merge      – label=separate, same_story=true  → pass=False, wrong_merge=1
  4. missed_merge     – label=merge, same_story=false → pass=False, missed_merge=1
  5. fenced JSON      – output wrapped in ```json ... ``` → parsed correctly
  6. invalid JSON     → pass=False, parse_error=1
  7. missing/non-boolean same_story → pass=False, parse_error=1
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "clusterer_merge"))
import assert_same_story  # noqa: E402

_SCORE_KEYS = ("correct", "wrong_merge", "missed_merge", "parse_error")


def _context(label: str) -> dict:
    return {"vars": {"label": label}}


def _output(same_story: bool, reason: str = "some reason") -> str:
    return json.dumps({"same_story": same_story, "reason": reason})


def _assert_single_named_score(result: dict, expected_key: str) -> None:
    """Check exactly one namedScores key is 1 and it is the expected one."""
    named = result["namedScores"]
    assert set(named.keys()) == set(_SCORE_KEYS), (
        f"namedScores keys mismatch: {set(named.keys())} != {set(_SCORE_KEYS)}"
    )
    for k, v in named.items():
        if k == expected_key:
            assert v == 1, f"Expected namedScores[{k!r}] == 1, got {v}"
        else:
            assert v == 0, f"Expected namedScores[{k!r}] == 0, got {v}"


# ---------------------------------------------------------------------------
# Case 1: correct merge
# ---------------------------------------------------------------------------

def test_correct_merge():
    result = assert_same_story.get_assert(
        _output(same_story=True),
        _context("merge"),
    )
    assert result["pass"] is True
    _assert_single_named_score(result, "correct")


# ---------------------------------------------------------------------------
# Case 2: correct separate
# ---------------------------------------------------------------------------

def test_correct_separate():
    result = assert_same_story.get_assert(
        _output(same_story=False),
        _context("separate"),
    )
    assert result["pass"] is True
    _assert_single_named_score(result, "correct")


# ---------------------------------------------------------------------------
# Case 3: wrong_merge (label=separate, model says same_story=true)
# ---------------------------------------------------------------------------

def test_wrong_merge():
    result = assert_same_story.get_assert(
        _output(same_story=True),
        _context("separate"),
    )
    assert result["pass"] is False
    _assert_single_named_score(result, "wrong_merge")


# ---------------------------------------------------------------------------
# Case 4: missed_merge (label=merge, model says same_story=false)
# ---------------------------------------------------------------------------

def test_missed_merge():
    result = assert_same_story.get_assert(
        _output(same_story=False),
        _context("merge"),
    )
    assert result["pass"] is False
    _assert_single_named_score(result, "missed_merge")


# ---------------------------------------------------------------------------
# Case 5: fenced JSON (```json ... ```) is parsed correctly
# ---------------------------------------------------------------------------

def test_fenced_json_correct_merge():
    raw = json.dumps({"same_story": True, "reason": "clearly related"})
    fenced = f"```json\n{raw}\n```"
    result = assert_same_story.get_assert(fenced, _context("merge"))
    assert result["pass"] is True
    _assert_single_named_score(result, "correct")


def test_fenced_json_no_language_tag():
    """Fence without language tag (``` ... ```) should also be parsed."""
    raw = json.dumps({"same_story": False, "reason": "unrelated"})
    fenced = f"```\n{raw}\n```"
    result = assert_same_story.get_assert(fenced, _context("separate"))
    assert result["pass"] is True
    _assert_single_named_score(result, "correct")


# ---------------------------------------------------------------------------
# Case 6: invalid JSON → parse_error
# ---------------------------------------------------------------------------

def test_invalid_json():
    result = assert_same_story.get_assert("not json at all", _context("merge"))
    assert result["pass"] is False
    _assert_single_named_score(result, "parse_error")


def test_truncated_json():
    result = assert_same_story.get_assert('{"same_story": tru', _context("merge"))
    assert result["pass"] is False
    _assert_single_named_score(result, "parse_error")


# ---------------------------------------------------------------------------
# Case 7a: missing same_story → parse_error
# ---------------------------------------------------------------------------

def test_missing_same_story():
    output = json.dumps({"reason": "just a reason, no same_story"})
    result = assert_same_story.get_assert(output, _context("merge"))
    assert result["pass"] is False
    _assert_single_named_score(result, "parse_error")


# ---------------------------------------------------------------------------
# Case 7b: non-boolean same_story → parse_error
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_value", ["true", 1, 0, None, "yes"])
def test_non_boolean_same_story(bad_value):
    output = json.dumps({"same_story": bad_value, "reason": "bad type"})
    result = assert_same_story.get_assert(output, _context("merge"))
    assert result["pass"] is False
    _assert_single_named_score(result, "parse_error")
