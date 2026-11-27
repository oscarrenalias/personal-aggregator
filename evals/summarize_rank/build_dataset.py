"""
Export output -> frozen JSONL dataset for summarize_rank evals.

Usage:
    python build_dataset.py --out <path> [--input <file>|-]

Reads one JSON object per line from stdin or a file, validates each record,
deduplicates by article_id (last occurrence wins), adds exported_at (UTC now),
sorts by article_id, and writes the JSONL. Refuses to overwrite an existing file.
Prints wrote/count. Invalid records abort without writing anything.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


REQUIRED_KEYS = {
    "article_id",
    "source_name",
    "title",
    "content_text",
    "production_score",
    "interest_profile_text",
    "categories",
}


def _validate_record(record: dict, lineno: int) -> None:
    missing = REQUIRED_KEYS - record.keys()
    if missing:
        raise ValueError(f"line {lineno}: missing required keys: {', '.join(sorted(missing))}")

    article_id = record["article_id"]
    if not isinstance(article_id, int):
        raise ValueError(f"line {lineno}: article_id must be an integer, got {type(article_id).__name__}")

    production_score = record["production_score"]
    if not isinstance(production_score, (int, float)):
        raise ValueError(
            f"line {lineno}: production_score must be a number, got {type(production_score).__name__}"
        )

    if not isinstance(record.get("categories"), list):
        raise ValueError(f"line {lineno}: categories must be a list")


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

    records_by_id: dict[int, dict] = {}
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

        records_by_id[record["article_id"]] = record

    exported_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sorted_records = sorted(records_by_id.values(), key=lambda r: r["article_id"])

    for record in sorted_records:
        record["exported_at"] = exported_at

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for record in sorted_records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    n = len(sorted_records)
    print(f"wrote {n} articles to {out_path}")


if __name__ == "__main__":
    main()
