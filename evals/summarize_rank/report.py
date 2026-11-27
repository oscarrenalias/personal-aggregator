#!/usr/bin/env python3
"""
report.py <results.json> [--threshold N] [--json] [--entity-sheet <path.csv>]
          [--sample-size N] [--seed N]

Reads a promptfoo eval JSON output file and prints metrics for the
summarize_rank v2 vs v3 comparison.

Metrics printed:
  n, parse failure rate per variant
  v3-vs-v2 mean absolute score difference (mae)
  mean signed difference (bias)
  Spearman rho (rank correlation)
  flip rate across threshold (default 66, --threshold flag)
  flip direction counts
  v3 entity coverage (% with >= 1 entity), median entity count, distribution
  v2-vs-stored-production mae (noise floor)

PASS/FAIL against spec-499532a1 criteria:
  parse failure  <= 0.5%
  mae            <= 4
  flip_rate      <= 5%
  rho            >= 0.90
  entity_coverage >= 85%
  median_entities in [2, 6]

--json flag outputs machine-readable JSON.
--entity-sheet <path.csv> writes a random sample (default 30) of v3 outputs
  with article title + entities + empty ok/notes columns for human review.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from pathlib import Path

DEFAULT_THRESHOLD = 66
DEFAULT_SAMPLE_SIZE = 30


# ---------------------------------------------------------------------------
# Results loading
# ---------------------------------------------------------------------------

def _load_results(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    results_field = data.get("results", [])
    if isinstance(results_field, dict):
        return results_field.get("results") or []
    if isinstance(results_field, list):
        return results_field
    return []


def _get_metadata(result: dict) -> dict:
    test_case = result.get("testCase") or {}
    return test_case.get("metadata") or result.get("metadata") or {}


def _parse_output(output: str | None) -> dict | None:
    if not output:
        return None
    import re
    stripped = str(output).strip()
    fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Spearman rho (no scipy dependency)
# ---------------------------------------------------------------------------

def _rank_list(values: list[float]) -> list[float]:
    """Return fractional ranks for a list of values (ascending, ties averaged)."""
    n = len(values)
    indexed = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and values[indexed[j + 1]] == values[indexed[j]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg_rank
        i = j + 1
    return ranks


def _spearman_rho(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 2:
        return float("nan")
    rx = _rank_list(x)
    ry = _rank_list(y)
    d2 = sum((rx[i] - ry[i]) ** 2 for i in range(n))
    return 1.0 - 6.0 * d2 / (n * (n * n - 1))


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def _collect_paired_scores(results: list[dict]) -> tuple[
    dict[int, dict],  # article_id -> {v2_score, v3_score, v3_entities, v3_parsed, title, production_score}
    dict[str, int],   # variant -> parse_error_count
    dict[str, int],   # variant -> total_count
]:
    per_article: dict[int, dict] = {}
    parse_errors: dict[str, int] = {"v2": 0, "v3": 0}
    totals: dict[str, int] = {"v2": 0, "v3": 0}

    for result in results:
        metadata = _get_metadata(result)
        article_id = metadata.get("article_id")
        if article_id is None:
            # fall back to vars
            article_id = (result.get("testCase") or {}).get("vars", {}).get("article_id")
        if article_id is None:
            continue

        article_id = int(article_id)
        prompt_label = (result.get("prompt") or {}).get("label") or ""
        if prompt_label not in ("v2", "v3"):
            continue

        raw_output = result.get("response", {})
        if isinstance(raw_output, dict):
            output_text = raw_output.get("output")
        else:
            output_text = str(raw_output) if raw_output else None

        totals[prompt_label] = totals.get(prompt_label, 0) + 1

        parsed = _parse_output(output_text)
        if parsed is None or "importance_score" not in parsed:
            parse_errors[prompt_label] = parse_errors.get(prompt_label, 0) + 1
            continue

        score = int(parsed.get("importance_score", 0))
        entities = parsed.get("entities", [])

        if article_id not in per_article:
            per_article[article_id] = {
                "production_score": metadata.get("production_score"),
                "title": (result.get("testCase") or {}).get("vars", {}).get("title"),
            }

        entry = per_article[article_id]
        if prompt_label == "v2":
            entry["v2_score"] = score
            entry["v2_parsed"] = parsed
        else:
            entry["v3_score"] = score
            entry["v3_entities"] = entities if isinstance(entities, list) else []
            entry["v3_parsed"] = parsed

    return per_article, parse_errors, totals


def _analyze(
    per_article: dict[int, dict],
    parse_errors: dict[str, int],
    totals: dict[str, int],
    threshold: int,
) -> dict:
    paired = [
        (aid, entry)
        for aid, entry in per_article.items()
        if "v2_score" in entry and "v3_score" in entry
    ]

    n_paired = len(paired)

    # --- score metrics ---
    if n_paired > 0:
        v2_scores = [e["v2_score"] for _, e in paired]
        v3_scores = [e["v3_score"] for _, e in paired]
        prod_scores = [e.get("production_score") for _, e in paired]

        diffs = [v3_scores[i] - v2_scores[i] for i in range(n_paired)]
        mae_v3_v2 = sum(abs(d) for d in diffs) / n_paired
        mean_diff = sum(diffs) / n_paired
        rho = _spearman_rho(v2_scores, v3_scores)

        # noise floor: v2 vs production
        prod_valid = [(v2_scores[i], prod_scores[i]) for i in range(n_paired) if prod_scores[i] is not None]
        if prod_valid:
            mae_v2_prod = sum(abs(a - b) for a, b in prod_valid) / len(prod_valid)
        else:
            mae_v2_prod = float("nan")

        # flip rate
        flips_v2_above = sum(
            1 for i in range(n_paired)
            if (v2_scores[i] >= threshold) != (v3_scores[i] >= threshold)
            and v2_scores[i] >= threshold
        )
        flips_v3_above = sum(
            1 for i in range(n_paired)
            if (v2_scores[i] >= threshold) != (v3_scores[i] >= threshold)
            and v3_scores[i] >= threshold
        )
        total_flips = flips_v2_above + flips_v3_above
        flip_rate = total_flips / n_paired
    else:
        mae_v3_v2 = mae_v2_prod = mean_diff = rho = flip_rate = float("nan")
        total_flips = flips_v2_above = flips_v3_above = 0
        v2_scores = v3_scores = []

    # --- entity metrics ---
    v3_entries = [e for _, e in per_article.items() if "v3_entities" in e]
    n_v3 = len(v3_entries)
    if n_v3 > 0:
        counts = [len(e.get("v3_entities", [])) for e in v3_entries]
        entity_coverage = sum(1 for c in counts if c >= 1) / n_v3
        sorted_counts = sorted(counts)
        mid = n_v3 // 2
        median_entities = (
            sorted_counts[mid] if n_v3 % 2 == 1
            else (sorted_counts[mid - 1] + sorted_counts[mid]) / 2
        )
        distribution = {
            "0": counts.count(0),
            "1-2": sum(1 for c in counts if 1 <= c <= 2),
            "3-5": sum(1 for c in counts if 3 <= c <= 5),
            "6-8": sum(1 for c in counts if 6 <= c <= 8),
            ">8": sum(1 for c in counts if c > 8),
        }
    else:
        entity_coverage = median_entities = float("nan")
        distribution = {}

    # --- parse failure rates ---
    v2_total = totals.get("v2", 0)
    v3_total = totals.get("v3", 0)
    v2_parse_fail_rate = (parse_errors.get("v2", 0) / v2_total) if v2_total > 0 else float("nan")
    v3_parse_fail_rate = (parse_errors.get("v3", 0) / v3_total) if v3_total > 0 else float("nan")

    return {
        "n_paired": n_paired,
        "n_v3_entity": n_v3,
        "v2_total": v2_total,
        "v3_total": v3_total,
        "v2_parse_errors": parse_errors.get("v2", 0),
        "v3_parse_errors": parse_errors.get("v3", 0),
        "v2_parse_fail_rate": v2_parse_fail_rate,
        "v3_parse_fail_rate": v3_parse_fail_rate,
        "mae_v3_v2": mae_v3_v2,
        "mean_diff": mean_diff,
        "rho": rho,
        "threshold": threshold,
        "flip_rate": flip_rate,
        "flips_total": total_flips,
        "flips_v2_above_v3_below": flips_v2_above,
        "flips_v3_above_v2_below": flips_v3_above,
        "entity_coverage": entity_coverage,
        "median_entities": median_entities,
        "entity_distribution": distribution,
        "mae_v2_prod": mae_v2_prod,
    }


def _pass_fail(metrics: dict) -> dict[str, bool]:
    def _ok(val: float) -> bool:
        return not (math.isnan(val) if isinstance(val, float) else False)

    results = {}
    results["parse_fail_v2"] = (
        _ok(metrics["v2_parse_fail_rate"]) and metrics["v2_parse_fail_rate"] <= 0.005
    )
    results["parse_fail_v3"] = (
        _ok(metrics["v3_parse_fail_rate"]) and metrics["v3_parse_fail_rate"] <= 0.005
    )
    results["mae"] = _ok(metrics["mae_v3_v2"]) and metrics["mae_v3_v2"] <= 4.0
    results["flip_rate"] = _ok(metrics["flip_rate"]) and metrics["flip_rate"] <= 0.05
    results["rho"] = _ok(metrics["rho"]) and metrics["rho"] >= 0.90
    results["entity_coverage"] = (
        _ok(metrics["entity_coverage"]) and metrics["entity_coverage"] >= 0.85
    )
    median = metrics["median_entities"]
    results["median_entities"] = (
        _ok(median) and isinstance(median, (int, float)) and 2 <= median <= 6
    )
    return results


def _print_report(metrics: dict, pf: dict[str, bool]) -> None:
    def _fmt(v: float | int, fmt: str = ".2f") -> str:
        if isinstance(v, float) and math.isnan(v):
            return "n/a"
        return format(v, fmt)

    def _pct(v: float) -> str:
        if isinstance(v, float) and math.isnan(v):
            return "n/a"
        return f"{v * 100:.1f}%"

    def _flag(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    print(f"\n=== summarize_rank eval report ===")
    print(f"n paired (v2 & v3):        {metrics['n_paired']}")
    print(f"v2 total / parse errors:   {metrics['v2_total']} / {metrics['v2_parse_errors']}  ({_pct(metrics['v2_parse_fail_rate'])})  [{_flag(pf['parse_fail_v2'])}]")
    print(f"v3 total / parse errors:   {metrics['v3_total']} / {metrics['v3_parse_errors']}  ({_pct(metrics['v3_parse_fail_rate'])})  [{_flag(pf['parse_fail_v3'])}]")
    print()
    print(f"v3 vs v2 score metrics (n={metrics['n_paired']}):")
    print(f"  mae:                     {_fmt(metrics['mae_v3_v2'])}    [{_flag(pf['mae'])}]  (target ≤4)")
    print(f"  mean signed diff:        {_fmt(metrics['mean_diff'])}")
    print(f"  Spearman rho:            {_fmt(metrics['rho'])}    [{_flag(pf['rho'])}]  (target ≥0.90)")
    print(f"  flip rate @{metrics['threshold']}:         {_pct(metrics['flip_rate'])}    [{_flag(pf['flip_rate'])}]  (target ≤5%)")
    print(f"    v2≥{metrics['threshold']} → v3<{metrics['threshold']}:        {metrics['flips_v2_above_v3_below']}")
    print(f"    v3≥{metrics['threshold']} → v2<{metrics['threshold']}:        {metrics['flips_v3_above_v2_below']}")
    print(f"  v2 vs production mae:    {_fmt(metrics['mae_v2_prod'])}  (noise floor)")
    print()
    print(f"v3 entity metrics (n={metrics['n_v3_entity']}):")
    print(f"  coverage (≥1 entity):   {_pct(metrics['entity_coverage'])}    [{_flag(pf['entity_coverage'])}]  (target ≥85%)")
    print(f"  median entity count:     {_fmt(metrics['median_entities'])}    [{_flag(pf['median_entities'])}]  (target 2–6)")
    dist = metrics["entity_distribution"]
    if dist:
        print(f"  distribution:           0:{dist.get('0',0)}  1-2:{dist.get('1-2',0)}  3-5:{dist.get('3-5',0)}  6-8:{dist.get('6-8',0)}  >8:{dist.get('>8',0)}")
    print()
    overall = all(pf.values())
    print(f"Overall: {'PASS' if overall else 'FAIL'}")
    print()


def _write_entity_sheet(
    per_article: dict[int, dict],
    path: str,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: int | None = None,
) -> None:
    v3_entries = [
        (aid, entry)
        for aid, entry in per_article.items()
        if "v3_entities" in entry
    ]
    rng = random.Random(seed)
    if len(v3_entries) > sample_size:
        v3_entries = rng.sample(v3_entries, sample_size)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["article_id", "title", "entities", "ok", "notes"])
        writer.writeheader()
        for aid, entry in sorted(v3_entries, key=lambda x: x[0]):
            writer.writerow({
                "article_id": aid,
                "title": entry.get("title") or "",
                "entities": "; ".join(entry.get("v3_entities", [])),
                "ok": "",
                "notes": "",
            })

    print(f"wrote entity sheet ({len(v3_entries)} rows) to {path}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Print a metrics report from a promptfoo summarize_rank eval results file."
    )
    parser.add_argument("results_file", metavar="results.json")
    parser.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD,
        help=f"Importance score threshold for flip-rate metric (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument("--json", dest="json_output", action="store_true", default=False)
    parser.add_argument("--entity-sheet", metavar="PATH", default=None)
    parser.add_argument(
        "--sample-size",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help=f"Number of rows in entity sheet sample (default: {DEFAULT_SAMPLE_SIZE})",
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed for entity sheet sampling")
    args = parser.parse_args(argv)

    path = Path(args.results_file)
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    all_results = _load_results(path)
    if not all_results:
        print("warning: no results found in file", file=sys.stderr)

    per_article, parse_errors, totals = _collect_paired_scores(all_results)
    metrics = _analyze(per_article, parse_errors, totals, args.threshold)
    pf = _pass_fail(metrics)

    if args.json_output:
        output = {
            "metrics": metrics,
            "pass_fail": pf,
            "overall": all(pf.values()),
        }
        print(json.dumps(output, indent=2))
    else:
        _print_report(metrics, pf)

    if args.entity_sheet:
        _write_entity_sheet(per_article, args.entity_sheet, args.sample_size, args.seed)

    overall = all(pf.values())
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
