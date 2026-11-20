"""
render_tests.py -- dataset + labels -> promptfoo test cases

CLI:
  render_tests.py --dataset <file> --labels <file> --out <file>
                  [--include-judge]
                  [--only-tag T]...
                  [--exclude-tag T]...

Reads a JSONL dataset and CSV labels file, joins on pair_id, filters to
usable labels, applies tag filters, and writes promptfoo test cases as YAML.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

import yaml


def _parse_tags(tags_str: str) -> list[str]:
    """Parse a semicolon-separated tags string into a list of non-empty tags."""
    if not tags_str:
        return []
    return [t.strip() for t in tags_str.split(";") if t.strip()]


def load_dataset(path: str) -> dict[str, dict]:
    """Load JSONL dataset, returning a dict keyed by pair_id."""
    dataset: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            dataset[record["pair_id"]] = record
    return dataset


def load_labels(path: str) -> list[dict]:
    """Load labels CSV, returning a list of row dicts."""
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def build_test_cases(
    dataset: dict[str, dict],
    labels: list[dict],
    include_judge: bool,
    only_tags: list[str],
    exclude_tags: list[str],
) -> tuple[list[dict], int]:
    """
    Build promptfoo test cases from the joined dataset + labels.

    Returns (test_cases, missing_count) where missing_count is the number of
    label rows whose pair_id was not found in the dataset.
    """
    test_cases = []
    missing_count = 0

    for label_row in labels:
        pair_id = label_row["pair_id"]
        labeled_by = label_row.get("labeled_by", "")
        label = label_row.get("label", "")
        tags = _parse_tags(label_row.get("tags", ""))

        if not include_judge and labeled_by == "judge":
            continue

        if pair_id not in dataset:
            missing_count += 1
            print(
                f"WARNING: label pair_id {pair_id!r} not found in dataset",
                file=sys.stderr,
            )
            continue

        if only_tags and not any(t in tags for t in only_tags):
            continue

        if exclude_tags and any(t in tags for t in exclude_tags):
            continue

        pair = dataset[pair_id]
        thread_a = pair["thread_a"]
        thread_b = pair["thread_b"]

        test_case = {
            "description": f"{pair_id} ({label})",
            "vars": {
                "pair_id": pair_id,
                "thread_a_id": thread_a["id"],
                "thread_a_title": thread_a.get("title"),
                "thread_a_summary": thread_a.get("summary"),
                "thread_b_id": thread_b["id"],
                "thread_b_title": thread_b.get("title"),
                "thread_b_summary": thread_b.get("summary"),
                "label": label,
                "tags": tags,
            },
            "metadata": {
                "pair_id": pair_id,
                "label": label,
                "tags": tags,
                "labeled_by": labeled_by,
            },
        }
        test_cases.append(test_case)

    return test_cases, missing_count


def _null_safe_representer(dumper: yaml.Dumper, _value: None) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:null", "null")


def write_yaml(test_cases: list[dict], out_path: str) -> None:
    """Write test cases as YAML."""
    dumper = yaml.Dumper
    dumper.add_representer(type(None), _null_safe_representer)
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(test_cases, f, Dumper=dumper, allow_unicode=True, sort_keys=False)


def write_json(test_cases: list[dict], out_path: str) -> None:
    """Write test cases as JSON."""
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(test_cases, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert dataset + labels into promptfoo test cases."
    )
    parser.add_argument("--dataset", required=True, help="JSONL dataset file")
    parser.add_argument("--labels", required=True, help="CSV labels file")
    parser.add_argument(
        "--out", required=True, help="Output file (.yaml/.yml or .json)"
    )
    parser.add_argument(
        "--include-judge",
        action="store_true",
        default=False,
        help="Include judge-labeled rows (excluded by default)",
    )
    parser.add_argument(
        "--only-tag",
        dest="only_tags",
        action="append",
        default=[],
        metavar="T",
        help="Keep only pairs whose tags include T (repeatable)",
    )
    parser.add_argument(
        "--exclude-tag",
        dest="exclude_tags",
        action="append",
        default=[],
        metavar="T",
        help="Drop pairs whose tags include T (repeatable)",
    )
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    labels = load_labels(args.labels)

    test_cases, missing_count = build_test_cases(
        dataset=dataset,
        labels=labels,
        include_judge=args.include_judge,
        only_tags=args.only_tags,
        exclude_tags=args.exclude_tags,
    )

    out_path = args.out
    suffix = Path(out_path).suffix.lower()
    if suffix in (".yaml", ".yml"):
        write_yaml(test_cases, out_path)
    else:
        write_json(test_cases, out_path)

    print(f"wrote {len(test_cases)} test cases to {out_path}")
    if missing_count:
        print(
            f"WARNING: {missing_count} label pair_id(s) not found in dataset",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
