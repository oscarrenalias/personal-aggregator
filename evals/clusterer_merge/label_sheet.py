"""label_sheet.py -- export pairs to an xlsx labelling sheet.

CLI:
  label_sheet.py export --dataset <file> --labels <file> --out <sheet.xlsx>
                        [--only-unlabeled] [--limit N] [--seed S]

Reads a JSONL dataset and CSV labels file, filters, shuffles, and writes
an openpyxl workbook ready for a human labeller.

Columns:
  pair_id | thread_a_title | thread_a_summary | thread_a_recent_articles
  | thread_a_member_count | thread_b_title | thread_b_summary
  | thread_b_recent_articles | thread_b_member_count
  | your_decision (merge/separate) | tags | notes

shadow_verdicts is intentionally omitted. Existing human labels are never
pre-filled in the decision column.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import random
import sys
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

COLUMNS = [
    "pair_id",
    "thread_a_title",
    "thread_a_summary",
    "thread_a_recent_articles",
    "thread_a_member_count",
    "thread_b_title",
    "thread_b_summary",
    "thread_b_recent_articles",
    "thread_b_member_count",
    "your_decision (merge/separate)",
    "tags",
    "notes",
]

DECISION_COL_INDEX = COLUMNS.index("your_decision (merge/separate)") + 1  # 1-based

# Light yellow highlight for the decision column
DECISION_FILL = PatternFill(start_color="FFFFE0", end_color="FFFFE0", fill_type="solid")

HEADER_FONT = Font(bold=True)

# Column widths (characters)
COL_WIDTHS = {
    "pair_id": 20,
    "thread_a_title": 36,
    "thread_a_summary": 48,
    "thread_a_recent_articles": 60,
    "thread_a_member_count": 18,
    "thread_b_title": 36,
    "thread_b_summary": 48,
    "thread_b_recent_articles": 60,
    "thread_b_member_count": 18,
    "your_decision (merge/separate)": 28,
    "tags": 20,
    "notes": 40,
}

DEFAULT_SEED = 42


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_articles(articles: list[dict]) -> str:
    """Format recent_articles as 'date · source: title — summary', one per line."""
    lines = []
    for a in articles:
        assigned_at = a.get("assigned_at", "")
        date = assigned_at[:10] if assigned_at else ""
        source = a.get("source", "")
        title = a.get("title", "")
        summary = a.get("summary", "")
        lines.append(f"{date} · {source}: {title} — {summary}")
    return "\n".join(lines)


def load_dataset(path: str) -> dict[str, dict]:
    dataset: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            dataset[record["pair_id"]] = record
    return dataset


def load_labels(path: str) -> dict[str, dict]:
    """Return a dict of pair_id -> row for rows with a human label."""
    labeled: dict[str, dict] = {}
    if not Path(path).exists():
        return labeled
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = row.get("pair_id", "")
            label = row.get("label", "").strip()
            labeled_by = row.get("labeled_by", "").strip()
            if pid and label and labeled_by == "human":
                labeled[pid] = row
    return labeled


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def build_rows(
    dataset: dict[str, dict],
    labeled: dict[str, dict],
    only_unlabeled: bool,
    seed: int,
    limit: int | None,
) -> list[dict]:
    """Build, filter, shuffle, and limit the row list."""
    pairs = list(dataset.values())

    if only_unlabeled:
        pairs = [p for p in pairs if p["pair_id"] not in labeled]

    rng = random.Random(seed)
    rng.shuffle(pairs)

    if limit is not None:
        pairs = pairs[:limit]

    return pairs


def write_workbook(pairs: list[dict], out_path: str) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Labels"

    # Header row
    for col_idx, name in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=name)
        cell.font = HEADER_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    # Freeze header row
    ws.freeze_panes = "A2"

    # Highlight the decision column header too
    ws.cell(row=1, column=DECISION_COL_INDEX).fill = DECISION_FILL

    # Data rows
    for row_idx, pair in enumerate(pairs, start=2):
        a = pair.get("thread_a", {}) or {}
        b = pair.get("thread_b", {}) or {}

        values = [
            pair.get("pair_id", ""),
            a.get("title") or "",
            a.get("summary") or "",
            _format_articles(a.get("recent_articles") or []),
            a.get("member_count", ""),
            b.get("title") or "",
            b.get("summary") or "",
            _format_articles(b.get("recent_articles") or []),
            b.get("member_count", ""),
            "",   # your_decision — intentionally blank
            "",   # tags
            "",   # notes
        ]

        for col_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.alignment = Alignment(wrap_text=True, vertical="top")

        # Highlight decision cell
        ws.cell(row=row_idx, column=DECISION_COL_INDEX).fill = DECISION_FILL

    # Column widths
    for col_idx, name in enumerate(COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = COL_WIDTHS.get(name, 20)

    wb.save(out_path)


def _load_all_labels(path: str) -> dict[str, dict]:
    """Return every row from the labels CSV keyed by pair_id (all labeled_by values)."""
    rows: dict[str, dict] = {}
    if not Path(path).exists():
        return rows
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = row.get("pair_id", "")
            if pid:
                rows[pid] = dict(row)
    return rows


def _pair_id_sort_key(row: dict) -> tuple:
    pid = row.get("pair_id", "")
    try:
        a, b = pid.split(":")
        return (int(a), int(b))
    except (ValueError, AttributeError):
        return (float("inf"), float("inf"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_export(args: argparse.Namespace) -> None:
    dataset = load_dataset(args.dataset)
    labeled = load_labels(args.labels)

    pairs = build_rows(
        dataset=dataset,
        labeled=labeled,
        only_unlabeled=args.only_unlabeled,
        seed=args.seed,
        limit=args.limit,
    )

    write_workbook(pairs, args.out)
    print(f"wrote {len(pairs)} rows to {args.out}")


def cmd_import(args: argparse.Namespace) -> None:
    wb = openpyxl.load_workbook(args.sheet, read_only=True, data_only=True)
    ws = wb.active
    assert ws is not None

    # Discover column indices from header row (case-insensitive)
    header_row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))

    pair_id_col: int | None = None
    decision_col: int | None = None
    tags_col: int | None = None
    notes_col: int | None = None

    for i, h in enumerate(header_row):
        if h is None:
            continue
        h_lower = str(h).strip().lower()
        if h_lower == "pair_id":
            pair_id_col = i
        elif h_lower.startswith("your_decision"):
            decision_col = i
        elif h_lower == "tags":
            tags_col = i
        elif h_lower == "notes":
            notes_col = i

    if pair_id_col is None or decision_col is None:
        print("error: could not find required columns (pair_id, your_decision)", file=sys.stderr)
        wb.close()
        sys.exit(1)

    # First pass: read and validate all data rows; collect valid ones
    today = datetime.date.today().isoformat()
    pending: list[tuple[str, str, str, str]] = []  # (pair_id, label, tags, notes)
    n_skipped = 0

    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        raw_pid = row[pair_id_col]
        pair_id = str(raw_pid).strip() if raw_pid is not None else ""
        if not pair_id:
            continue

        raw_decision = row[decision_col]
        decision_str = str(raw_decision).strip() if raw_decision is not None else ""

        raw_tags = row[tags_col] if tags_col is not None else None
        tags = str(raw_tags).strip() if raw_tags is not None else ""

        raw_notes = row[notes_col] if notes_col is not None else None
        notes = str(raw_notes).strip() if raw_notes is not None else ""

        if not decision_str:
            n_skipped += 1
            continue

        decision_lower = decision_str.lower()
        if decision_lower not in ("merge", "separate"):
            print(f"error: row {row_idx}: invalid decision value {decision_str!r} (must be merge, separate, or blank)")
            wb.close()
            sys.exit(1)

        pending.append((pair_id, decision_lower, tags, notes))

    wb.close()

    # Load existing labels (all rows, not just human)
    existing = _load_all_labels(args.labels)

    # Apply changes
    n_added = 0
    n_replaced = 0

    for pair_id, label, tags, notes in pending:
        new_row = {
            "pair_id": pair_id,
            "label": label,
            "tags": tags,
            "notes": notes,
            "labeled_by": "human",
            "labeled_at": today,
        }
        if pair_id in existing:
            existing[pair_id] = new_row
            n_replaced += 1
        else:
            existing[pair_id] = new_row
            n_added += 1

    # Write sorted CSV
    fieldnames = ["pair_id", "label", "tags", "notes", "labeled_by", "labeled_at"]
    sorted_rows = sorted(existing.values(), key=_pair_id_sort_key)

    with open(args.labels, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted_rows)

    print(f"{n_added} added, {n_replaced} replaced, {n_skipped} skipped")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="label_sheet: labelling worksheet tools."
    )
    sub = parser.add_subparsers(dest="command")

    export_p = sub.add_parser("export", help="Export pairs to xlsx labelling sheet.")
    export_p.add_argument("--dataset", required=True, help="JSONL dataset file")
    export_p.add_argument("--labels", required=True, help="CSV labels file")
    export_p.add_argument("--out", required=True, help="Output .xlsx path")
    export_p.add_argument(
        "--only-unlabeled",
        action="store_true",
        default=False,
        help="Exclude pairs that already have a human label in the labels file",
    )
    export_p.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Cap the number of rows written (applied after shuffle)",
    )
    export_p.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        metavar="S",
        help=f"Random seed for shuffling (default: {DEFAULT_SEED})",
    )

    import_p = sub.add_parser("import", help="Import decisions from a filled xlsx labelling sheet.")
    import_p.add_argument("--sheet", required=True, help="Filled .xlsx sheet to import")
    import_p.add_argument("--labels", required=True, help="CSV labels file to update")

    args = parser.parse_args()

    if args.command == "export":
        cmd_export(args)
    elif args.command == "import":
        cmd_import(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
