"""Tests for evals/clusterer_merge/report.py.

Covers acceptance criteria:
  - Counts, accuracy, sorting verified against fixture data.
  - Tag filters (--only-tag, --exclude-tag) recompute correctly.
  - --repeat N handling: fixture with N copies of same test → n multiplied.
  - Judge warning printed when any test has labeled_by=judge in metadata.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "clusterer_merge"))
import report  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result(
    prompt_label: str = "v1",
    provider_label: str = "gpt-4.1-mini",
    correct: int = 1,
    wrong_merge: int = 0,
    missed_merge: int = 0,
    parse_error: int = 0,
    tags: str = "",
    labeled_by: str = "human",
) -> dict:
    return {
        "prompt": {"label": prompt_label},
        "provider": {"label": provider_label},
        "testCase": {
            "metadata": {
                "labeled_by": labeled_by,
                "tags": tags,
            }
        },
        "gradingResult": {
            "namedScores": {
                "correct": correct,
                "wrong_merge": wrong_merge,
                "missed_merge": missed_merge,
                "parse_error": parse_error,
            }
        },
    }


def _write_fixture(tmp_path: Path, results: list[dict], flat: bool = True) -> Path:
    """Write a promptfoo JSON fixture and return the path."""
    if flat:
        data = {"results": results}
    else:
        data = {"results": {"results": results}}
    p = tmp_path / "results.json"
    p.write_text(json.dumps(data))
    return p


def _invoke_main(fixture_path: Path, extra_args: list[str] | None = None) -> None:
    """Call report.main() with the given fixture as the first CLI argument."""
    old_argv = sys.argv
    sys.argv = ["report.py", str(fixture_path)] + (extra_args or [])
    try:
        report.main()
    except SystemExit:
        pass
    finally:
        sys.argv = old_argv


def _data_lines(out: str) -> list[str]:
    """Return non-empty output lines after the header and separator."""
    lines = out.splitlines()
    # Skip header (starts with "prompt") and separator (starts with "-")
    data = []
    past_separator = False
    for line in lines:
        if line.startswith("-"):
            past_separator = True
            continue
        if past_separator and line.strip():
            data.append(line)
    return data


# ---------------------------------------------------------------------------
# AC-1: counts, accuracy, row sorting
# ---------------------------------------------------------------------------

def test_single_row_counts_and_accuracy(tmp_path, capsys):
    """3 results for one (prompt, provider): 2 correct, 1 wrong_merge → 67%."""
    results = [
        _result("v1", "model-a", correct=1),
        _result("v1", "model-a", correct=1),
        _result("v1", "model-a", correct=0, wrong_merge=1),
    ]
    _invoke_main(_write_fixture(tmp_path, results))
    out, _ = capsys.readouterr()
    data = _data_lines(out)
    assert len(data) == 1
    row = data[0]
    # n=3, correct=2, accuracy=67%
    assert "3" in row
    assert "2" in row
    assert "67%" in row


def test_accuracy_100_percent(tmp_path, capsys):
    results = [_result("v1", "model-a", correct=1)]
    _invoke_main(_write_fixture(tmp_path, results))
    out, _ = capsys.readouterr()
    assert "100%" in out


def test_accuracy_0_percent(tmp_path, capsys):
    results = [_result("v1", "model-a", correct=0, wrong_merge=1)]
    _invoke_main(_write_fixture(tmp_path, results))
    out, _ = capsys.readouterr()
    assert "0%" in out


def test_multiple_rows_sorted_by_accuracy_descending(tmp_path, capsys):
    """Higher-accuracy row appears above lower-accuracy row in the output."""
    results = [
        _result("v2", "model-a", correct=0, wrong_merge=1),  # 0%
        _result("v1", "model-a", correct=1),                  # 100%
    ]
    _invoke_main(_write_fixture(tmp_path, results))
    out, _ = capsys.readouterr()
    data = _data_lines(out)
    assert len(data) == 2
    # v1 (100%) should appear first
    assert "v1" in data[0]
    assert "v2" in data[1]


def test_error_score_columns_aggregated(tmp_path, capsys):
    """wrong_merge, missed_merge, parse_error are summed across results."""
    results = [
        _result("v1", "m", correct=1),
        _result("v1", "m", correct=0, missed_merge=1),
        _result("v1", "m", correct=0, parse_error=1),
    ]
    _invoke_main(_write_fixture(tmp_path, results))
    out, _ = capsys.readouterr()
    data = _data_lines(out)
    assert len(data) == 1
    # n=3, correct=1, missed_merge=1, parse_error=1
    row = data[0]
    assert "3" in row
    assert "33%" in row


def test_wrapped_format_loads_correctly(tmp_path, capsys):
    """Version-3 nested format {"results": {"results": [...]}} is handled."""
    results = [_result("v1", "model-a", correct=1)]
    _invoke_main(_write_fixture(tmp_path, results, flat=False))
    out, _ = capsys.readouterr()
    assert "v1" in out
    assert "model-a" in out
    assert "100%" in out


def test_multiple_providers_separate_rows(tmp_path, capsys):
    """Different providers produce separate rows."""
    results = [
        _result("v1", "model-a", correct=1),
        _result("v1", "model-b", correct=0, wrong_merge=1),
    ]
    _invoke_main(_write_fixture(tmp_path, results))
    out, _ = capsys.readouterr()
    data = _data_lines(out)
    assert len(data) == 2
    providers = " ".join(data)
    assert "model-a" in providers
    assert "model-b" in providers


# ---------------------------------------------------------------------------
# AC-2: tag filters
# ---------------------------------------------------------------------------

def test_only_tag_includes_matching(tmp_path, capsys):
    """--only-tag keeps only results whose tags include the specified tag."""
    results = [
        _result("v1", "m", correct=1, tags="shopping;deals"),
        _result("v1", "m", correct=0, wrong_merge=1, tags="politics"),
    ]
    _invoke_main(_write_fixture(tmp_path, results), extra_args=["--only-tag", "shopping"])
    out, _ = capsys.readouterr()
    data = _data_lines(out)
    # Only the shopping row passes; n=1, correct=1, accuracy=100%
    assert len(data) == 1
    assert "100%" in data[0]


def test_exclude_tag_removes_matching(tmp_path, capsys):
    """--exclude-tag drops results tagged with the specified tag."""
    results = [
        _result("v1", "m", correct=1, tags="shopping"),
        _result("v1", "m", correct=0, wrong_merge=1, tags="politics"),
    ]
    _invoke_main(_write_fixture(tmp_path, results), extra_args=["--exclude-tag", "shopping"])
    out, _ = capsys.readouterr()
    data = _data_lines(out)
    # Only politics result remains: 0/1 = 0%
    assert len(data) == 1
    assert "0%" in data[0]


def test_only_tag_all_filtered_out(tmp_path, capsys):
    """--only-tag with no matching results prints the 'no results' message."""
    results = [_result("v1", "m", correct=1, tags="politics")]
    _invoke_main(_write_fixture(tmp_path, results), extra_args=["--only-tag", "shopping"])
    out, _ = capsys.readouterr()
    assert "no results" in out.lower()


def test_tag_filter_case_insensitive(tmp_path, capsys):
    """Tag matching is case-insensitive."""
    results = [
        _result("v1", "m", correct=1, tags="Shopping"),
        _result("v2", "m", correct=0, wrong_merge=1, tags="Politics"),
    ]
    _invoke_main(_write_fixture(tmp_path, results), extra_args=["--only-tag", "shopping"])
    out, _ = capsys.readouterr()
    data = _data_lines(out)
    assert len(data) == 1
    assert "v1" in data[0]


def test_multiple_only_tags_all_must_match(tmp_path, capsys):
    """--only-tag A --only-tag B: row must carry both tags."""
    results = [
        _result("v1", "m", correct=1, tags="shopping;deals"),  # has both
        _result("v2", "m", correct=1, tags="shopping"),         # missing 'deals'
    ]
    _invoke_main(
        _write_fixture(tmp_path, results),
        extra_args=["--only-tag", "shopping", "--only-tag", "deals"],
    )
    out, _ = capsys.readouterr()
    data = _data_lines(out)
    assert len(data) == 1
    assert "v1" in data[0]


def test_exclude_tag_keeps_untagged(tmp_path, capsys):
    """--exclude-tag does not remove rows with no tags."""
    results = [
        _result("v1", "m", correct=1, tags=""),
        _result("v2", "m", correct=1, tags="shopping"),
    ]
    _invoke_main(_write_fixture(tmp_path, results), extra_args=["--exclude-tag", "shopping"])
    out, _ = capsys.readouterr()
    data = _data_lines(out)
    assert len(data) == 1
    assert "v1" in data[0]


# ---------------------------------------------------------------------------
# AC-3: --repeat N handling (N copies in the fixture → n multiplied)
# ---------------------------------------------------------------------------

def test_repeat_three_copies_n_equals_three(tmp_path, capsys):
    """Fixture with 3 copies of same result (simulating promptfoo --repeat 3): n=3."""
    base = _result("v1", "model-a", correct=1)
    results = [base, base, base]
    _invoke_main(_write_fixture(tmp_path, results))
    out, _ = capsys.readouterr()
    data = _data_lines(out)
    assert len(data) == 1
    # n column should be 3
    assert "3" in data[0]
    assert "100%" in data[0]


def test_repeat_mixed_outcomes_accuracy_aggregated(tmp_path, capsys):
    """With 3 copies, 2 correct + 1 wrong → 67% accuracy, n=3."""
    results = [
        _result("v1", "model-a", correct=1),
        _result("v1", "model-a", correct=1),
        _result("v1", "model-a", correct=0, wrong_merge=1),
    ]
    _invoke_main(_write_fixture(tmp_path, results))
    out, _ = capsys.readouterr()
    data = _data_lines(out)
    assert len(data) == 1
    row = data[0]
    assert "3" in row
    assert "67%" in row


def test_repeat_five_copies(tmp_path, capsys):
    """Five copies of the same test: n=5."""
    base = _result("v1", "model-a", correct=1)
    results = [base] * 5
    _invoke_main(_write_fixture(tmp_path, results))
    out, _ = capsys.readouterr()
    data = _data_lines(out)
    assert len(data) == 1
    assert "5" in data[0]


# ---------------------------------------------------------------------------
# AC-4: judge warning
# ---------------------------------------------------------------------------

def test_judge_warning_printed_when_judge_present(tmp_path, capsys):
    """Any judge-labeled test in the file triggers the warning."""
    results = [
        _result("v1", "m", correct=1, labeled_by="human"),
        _result("v1", "m", correct=1, labeled_by="judge"),
    ]
    _invoke_main(_write_fixture(tmp_path, results))
    out, _ = capsys.readouterr()
    assert "WARNING" in out
    assert "judge" in out.lower()


def test_judge_warning_absent_when_all_human(tmp_path, capsys):
    """No judge-labeled tests → no warning."""
    results = [
        _result("v1", "m", correct=1, labeled_by="human"),
        _result("v2", "m", correct=1, labeled_by="human"),
    ]
    _invoke_main(_write_fixture(tmp_path, results))
    out, _ = capsys.readouterr()
    assert "WARNING" not in out


def test_judge_warning_fires_even_when_judge_rows_filtered_out(tmp_path, capsys):
    """Warning fires for the full file, even when judge rows are filtered by --only-tag."""
    results = [
        _result("v1", "m", correct=1, labeled_by="judge", tags="politics"),
        _result("v2", "m", correct=1, labeled_by="human", tags="sports"),
    ]
    _invoke_main(
        _write_fixture(tmp_path, results),
        extra_args=["--only-tag", "sports"],
    )
    out, _ = capsys.readouterr()
    # Warning must appear even though the judge row is excluded from the table
    assert "WARNING" in out
    assert "judge" in out.lower()


# ---------------------------------------------------------------------------
# Helper function unit tests (no I/O, no capsys)
# ---------------------------------------------------------------------------

def test_get_tags_semicolon_string():
    tags = report._get_tags({"tags": "shopping;deals;ambiguous"})
    assert tags == {"shopping", "deals", "ambiguous"}


def test_get_tags_list_lowercased():
    tags = report._get_tags({"tags": ["Shopping", "DEALS"]})
    assert tags == {"shopping", "deals"}


def test_get_tags_empty_key():
    assert report._get_tags({}) == set()


def test_get_tags_empty_string():
    assert report._get_tags({"tags": ""}) == set()


def test_get_metadata_from_test_case():
    result = {"testCase": {"metadata": {"labeled_by": "human"}}}
    assert report._get_metadata(result)["labeled_by"] == "human"


def test_get_metadata_fallback_to_top_level():
    result = {"metadata": {"labeled_by": "judge"}}
    assert report._get_metadata(result)["labeled_by"] == "judge"


def test_get_named_scores_present():
    result = {"gradingResult": {"namedScores": {"correct": 1, "wrong_merge": 0}}}
    scores = report._get_named_scores(result)
    assert scores["correct"] == 1
    assert scores["wrong_merge"] == 0


def test_get_named_scores_missing():
    assert report._get_named_scores({}) == {}
