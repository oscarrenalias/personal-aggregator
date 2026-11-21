#!/usr/bin/env python3
"""
report.py <results.json> [--exclude-tag T]... [--only-tag T]...

Reads a promptfoo eval JSON output file (promptfoo eval -o) and prints
an accuracy score table with one row per prompt × provider combination.

Columns: prompt, provider, n, correct, accuracy, wrong_merge, missed_merge,
         parse_error

Rows sorted by accuracy descending. With tag filters, recomputes from the
per-test metadata.tags field. Warns when judge-labeled tests are present.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def _load_results(path: Path) -> list[dict]:
    """Return the flat list of test results from a promptfoo eval JSON file.

    promptfoo -o can produce two shapes:
      {"results": {"results": [...], ...}, ...}  — version-3 wrapped
      {"results": [...], ...}                    — flat list
    """
    data = json.loads(path.read_text())
    results_field = data.get("results", [])
    if isinstance(results_field, dict):
        return results_field.get("results") or []
    if isinstance(results_field, list):
        return results_field
    return []


def _get_metadata(result: dict) -> dict:
    """Extract test metadata, checking testCase.metadata then top-level metadata."""
    test_case = result.get("testCase") or {}
    return test_case.get("metadata") or result.get("metadata") or {}


def _get_tags(metadata: dict) -> set[str]:
    """Return normalised tag set from metadata.tags (string or list)."""
    raw = metadata.get("tags", "")
    if not raw:
        return set()
    if isinstance(raw, list):
        return {t.strip().lower() for t in raw if t.strip()}
    return {t.strip().lower() for t in str(raw).split(";") if t.strip()}


def _get_named_scores(result: dict) -> dict:
    grading = result.get("gradingResult") or {}
    return grading.get("namedScores") or {}


_SCORE_KEYS = ("correct", "wrong_merge", "missed_merge", "parse_error")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print an accuracy score table from a promptfoo eval JSON output file."
    )
    parser.add_argument(
        "results_file",
        metavar="results.json",
        help="Path to promptfoo eval output JSON file",
    )
    parser.add_argument(
        "--exclude-tag",
        metavar="T",
        action="append",
        dest="exclude_tags",
        default=[],
        help="Exclude tests tagged with T (may be repeated)",
    )
    parser.add_argument(
        "--only-tag",
        metavar="T",
        action="append",
        dest="only_tags",
        default=[],
        help="Include only tests tagged with T (may be repeated; all must match)",
    )
    args = parser.parse_args()

    path = Path(args.results_file)
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    all_results = _load_results(path)
    if not all_results:
        print("warning: no results found in file", file=sys.stderr)

    exclude_tags = {t.lower() for t in args.exclude_tags}
    only_tags = {t.lower() for t in args.only_tags}
    has_filters = bool(exclude_tags or only_tags)

    # Warn when any test in the file (before filtering) came from a judge label
    if any(_get_metadata(r).get("labeled_by") == "judge" for r in all_results):
        print(
            "WARNING: judge-labeled tests are present in the results; "
            "accuracy may be inflated."
        )

    if has_filters:
        parts = []
        if only_tags:
            parts.append(f"only-tag: {', '.join(sorted(only_tags))}")
        if exclude_tags:
            parts.append(f"exclude-tag: {', '.join(sorted(exclude_tags))}")
        print(f"Filters: {'; '.join(parts)}")

    # Aggregate by (prompt_label, provider_label)
    agg: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"n": 0, "correct": 0, "wrong_merge": 0, "missed_merge": 0, "parse_error": 0}
    )

    # Dedup set: (prompt_label, provider_label, pair_id, repeat_index)
    # Only deduplicate when pair_id is available; keeps the first occurrence.
    seen: set[tuple[str, str, str, int]] = set()

    for result in all_results:
        metadata = _get_metadata(result)
        tags = _get_tags(metadata)

        if only_tags and not only_tags.issubset(tags):
            continue
        if exclude_tags and exclude_tags.intersection(tags):
            continue

        prompt_label = (result.get("prompt") or {}).get("label") or ""
        provider_label = (result.get("provider") or {}).get("label") or ""

        pair_id = metadata.get("pair_id") or (result.get("testCase") or {}).get("vars", {}).get("pair_id")
        if pair_id is not None:
            repeat_index = result.get("repeatIndex", 0)
            dedup_key = (prompt_label, provider_label, str(pair_id), int(repeat_index))
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

        key = (prompt_label, provider_label)
        named_scores = _get_named_scores(result)
        agg[key]["n"] += 1
        for score_key in _SCORE_KEYS:
            agg[key][score_key] += int(named_scores.get(score_key, 0))

    if not agg:
        print("(no results after filtering)")
        return

    # Build rows and sort by accuracy descending, then prompt, then provider
    rows = []
    for (prompt_label, provider_label), counts in agg.items():
        n = counts["n"]
        correct = counts["correct"]
        accuracy = correct / n if n > 0 else 0.0
        rows.append(
            {
                "prompt": prompt_label,
                "provider": provider_label,
                "n": n,
                "correct": correct,
                "accuracy": accuracy,
                "wrong_merge": counts["wrong_merge"],
                "missed_merge": counts["missed_merge"],
                "parse_error": counts["parse_error"],
            }
        )

    rows.sort(key=lambda r: (-r["accuracy"], r["prompt"], r["provider"]))

    # Format accuracy as integer percentage string (e.g. "88%")
    def _fmt_acc(a: float) -> str:
        return f"{round(a * 100)}%"

    # Determine column widths
    headers = ["prompt", "provider", "n", "correct", "accuracy",
               "wrong_merge", "missed_merge", "parse_error"]
    col_w: dict[str, int] = {h: len(h) for h in headers}
    for row in rows:
        col_w["prompt"] = max(col_w["prompt"], len(row["prompt"]))
        col_w["provider"] = max(col_w["provider"], len(row["provider"]))
        col_w["n"] = max(col_w["n"], len(str(row["n"])))
        col_w["correct"] = max(col_w["correct"], len(str(row["correct"])))
        col_w["accuracy"] = max(col_w["accuracy"], len(_fmt_acc(row["accuracy"])))
        col_w["wrong_merge"] = max(col_w["wrong_merge"], len(str(row["wrong_merge"])))
        col_w["missed_merge"] = max(col_w["missed_merge"], len(str(row["missed_merge"])))
        col_w["parse_error"] = max(col_w["parse_error"], len(str(row["parse_error"])))

    def _fmt_row(values: list[str]) -> str:
        return "  ".join(v.ljust(col_w[h]) for h, v in zip(headers, values))

    header_line = _fmt_row([h for h in headers])
    print(header_line)
    print("-" * len(header_line))

    for row in rows:
        print(
            _fmt_row(
                [
                    row["prompt"],
                    row["provider"],
                    str(row["n"]),
                    str(row["correct"]),
                    _fmt_acc(row["accuracy"]),
                    str(row["wrong_merge"]),
                    str(row["missed_merge"]),
                    str(row["parse_error"]),
                ]
            )
        )


if __name__ == "__main__":
    main()
