"""
Export output -> frozen JSONL dataset.

Usage:
    python build_dataset.py --out <path> [--input <file>|-]

Reads one JSON object per line from stdin or a file, validates each record,
deduplicates by pair_id (last occurrence wins), adds exported_at (UTC now),
sorts by pair_id, and writes the JSONL. Refuses to overwrite an existing file.
Prints wrote/label counts. Invalid records abort without writing anything.
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_KEYS = {"pair_id", "first_seen", "thread_a", "thread_b", "shadow_verdicts"}
REQUIRED_THREAD_KEYS = {"id", "title", "summary", "member_count", "recent_articles"}


def _validate_record(record: dict, lineno: int) -> None:
    """Validate a single record; raise ValueError with line number on failure."""
    missing = REQUIRED_KEYS - record.keys()
    if missing:
        raise ValueError(f"line {lineno}: missing required keys: {', '.join(sorted(missing))}")

    pair_id = record["pair_id"]
    if not isinstance(pair_id, str):
        raise ValueError(f"line {lineno}: pair_id must be a string, got {type(pair_id).__name__}")

    parts = pair_id.split(":")
    if len(parts) != 2:
        raise ValueError(f"line {lineno}: pair_id must have format '<int>:<int>', got {pair_id!r}")
    try:
        a_id = int(parts[0])
        b_id = int(parts[1])
    except ValueError:
        raise ValueError(f"line {lineno}: pair_id parts must be integers, got {pair_id!r}")

    if a_id >= b_id:
        raise ValueError(
            f"line {lineno}: pair_id requires thread_a.id < thread_b.id, got {pair_id!r}"
        )

    for thread_key in ("thread_a", "thread_b"):
        thread = record[thread_key]
        if not isinstance(thread, dict):
            raise ValueError(f"line {lineno}: {thread_key} must be an object")
        missing_thread = REQUIRED_THREAD_KEYS - thread.keys()
        if missing_thread:
            raise ValueError(
                f"line {lineno}: {thread_key} missing keys: {', '.join(sorted(missing_thread))}"
            )
        if not isinstance(thread.get("id"), int):
            raise ValueError(f"line {lineno}: {thread_key}.id must be an integer")

    if record["thread_a"]["id"] != a_id:
        raise ValueError(
            f"line {lineno}: pair_id {pair_id!r} does not match thread_a.id={record['thread_a']['id']}"
        )
    if record["thread_b"]["id"] != b_id:
        raise ValueError(
            f"line {lineno}: pair_id {pair_id!r} does not match thread_b.id={record['thread_b']['id']}"
        )


def _load_labels(labels_path: Path) -> set:
    """Return set of pair_ids that have human labels in the CSV."""
    if not labels_path.exists():
        return set()
    labeled = set()
    with labels_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("labeled_by", "").strip() == "human":
                labeled.add(row["pair_id"].strip())
    return labeled


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a frozen JSONL dataset from export.sql output."
    )
    parser.add_argument("--out", required=True, help="Output JSONL file path")
    parser.add_argument(
        "--input",
        default="-",
        help="Input file path or - for stdin (default: stdin)",
    )
    args = parser.parse_args(argv)

    out_path = Path(args.out)
    if out_path.exists():
        print(
            f"error: output file already exists: {out_path}\n"
            "Datasets are immutable; choose a new filename.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.input == "-":
        lines = sys.stdin.read().splitlines()
    else:
        with open(args.input, encoding="utf-8") as f:
            lines = f.read().splitlines()

    records_by_pair_id: dict[str, dict] = {}
    for lineno, raw in enumerate(lines, start=1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"error: line {lineno}: invalid JSON: {exc}", file=sys.stderr)
            sys.exit(1)

        try:
            _validate_record(record, lineno)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

        records_by_pair_id[record["pair_id"]] = record

    exported_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sorted_records = sorted(
        records_by_pair_id.values(),
        key=lambda r: tuple(int(x) for x in r["pair_id"].split(":")),
    )

    for record in sorted_records:
        record["exported_at"] = exported_at

    labels_path = Path(__file__).parent / "labels" / "merge-labels.csv"
    labeled_pair_ids = _load_labels(labels_path)
    labeled_count = sum(1 for r in sorted_records if r["pair_id"] in labeled_pair_ids)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for record in sorted_records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    n = len(sorted_records)
    print(f"wrote {n} pairs to {out_path}")
    print(f"{labeled_count} of {n} pairs have a human label in {labels_path}")


if __name__ == "__main__":
    main()
