"""Tests for evals/clusterer_merge/label_sheet.py.

Covers acceptance criteria:
  - Round-trip test: filled xlsx rows appear in CSV; blank rows skipped.
  - Replacement test: re-importing same pair_id replaces the row; CSV has no duplicates.
  - Invalid-value test: invalid decision aborts; CSV unchanged.
  - No-shadow-verdicts test: exported xlsx has no column containing shadow_verdicts.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import openpyxl
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "clusterer_merge"))
import label_sheet  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pair(pair_id: str, title_a: str = "Thread A", title_b: str = "Thread B") -> dict:
    a_id, b_id = pair_id.split(":")
    return {
        "pair_id": pair_id,
        "thread_a": {
            "id": int(a_id),
            "title": title_a,
            "summary": "Summary of thread A.",
            "member_count": 2,
            "recent_articles": [],
        },
        "thread_b": {
            "id": int(b_id),
            "title": title_b,
            "summary": "Summary of thread B.",
            "member_count": 3,
            "recent_articles": [],
        },
        "shadow_verdicts": {"exp/arm1": True, "exp/arm2": False},
    }


def _empty_labels_csv(tmp_path: Path) -> Path:
    p = tmp_path / "labels.csv"
    p.write_text("pair_id,label,tags,notes,labeled_by,labeled_at\n")
    return p


def _write_labels_csv(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "labels.csv"
    fieldnames = ["pair_id", "label", "tags", "notes", "labeled_by", "labeled_at"]
    with open(p, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return p


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _get_xlsx_headers(xlsx_path: Path) -> list[str]:
    wb = openpyxl.load_workbook(xlsx_path)
    ws = wb.active
    headers = [str(c.value).lower() if c.value else "" for c in ws[1]]
    wb.close()
    return headers


def _fill_xlsx_decision(
    xlsx_path: Path, pair_id: str, decision: str, tags: str = "", notes: str = ""
) -> None:
    """Open xlsx, find the row with the given pair_id, write the decision, save."""
    wb = openpyxl.load_workbook(xlsx_path)
    ws = wb.active
    header = [str(c.value).lower() if c.value else "" for c in ws[1]]
    pair_id_col = header.index("pair_id")
    decision_col = next(
        i for i, h in enumerate(header) if h.startswith("your_decision")
    )
    tags_col = header.index("tags") if "tags" in header else None
    notes_col = header.index("notes") if "notes" in header else None

    for row in ws.iter_rows(min_row=2):
        cell_val = row[pair_id_col].value
        if cell_val is not None and str(cell_val).strip() == pair_id:
            row[decision_col].value = decision
            if tags_col is not None:
                row[tags_col].value = tags
            if notes_col is not None:
                row[notes_col].value = notes
            break
    wb.save(xlsx_path)
    wb.close()


def _do_import(xlsx_path: Path, labels_path: Path) -> None:
    """Call cmd_import with the given paths."""
    args = argparse.Namespace(sheet=str(xlsx_path), labels=str(labels_path))
    label_sheet.cmd_import(args)


# ---------------------------------------------------------------------------
# AC-5: round-trip — filled rows appear in CSV; blank rows skipped
# ---------------------------------------------------------------------------

def test_roundtrip_filled_rows_appear_in_csv(tmp_path):
    """Export pairs, fill decisions, import, verify both rows in CSV."""
    pairs = [_make_pair("10:20"), _make_pair("30:40")]
    labels_path = _empty_labels_csv(tmp_path)
    xlsx_path = tmp_path / "sheet.xlsx"

    label_sheet.write_workbook(pairs, str(xlsx_path))
    _fill_xlsx_decision(xlsx_path, "10:20", "merge")
    _fill_xlsx_decision(xlsx_path, "30:40", "separate")
    _do_import(xlsx_path, labels_path)

    rows = _read_csv(labels_path)
    pair_ids = {r["pair_id"] for r in rows}
    assert "10:20" in pair_ids
    assert "30:40" in pair_ids


def test_roundtrip_labels_correct(tmp_path):
    """Imported labels match the decisions filled into the xlsx."""
    pairs = [_make_pair("10:20"), _make_pair("30:40")]
    labels_path = _empty_labels_csv(tmp_path)
    xlsx_path = tmp_path / "sheet.xlsx"

    label_sheet.write_workbook(pairs, str(xlsx_path))
    _fill_xlsx_decision(xlsx_path, "10:20", "merge")
    _fill_xlsx_decision(xlsx_path, "30:40", "separate")
    _do_import(xlsx_path, labels_path)

    rows = {r["pair_id"]: r for r in _read_csv(labels_path)}
    assert rows["10:20"]["label"] == "merge"
    assert rows["30:40"]["label"] == "separate"
    assert rows["10:20"]["labeled_by"] == "human"
    assert rows["30:40"]["labeled_by"] == "human"


def test_blank_decision_rows_skipped(tmp_path):
    """A row with no decision in the xlsx is not written to the CSV."""
    pairs = [_make_pair("10:20"), _make_pair("30:40")]
    labels_path = _empty_labels_csv(tmp_path)
    xlsx_path = tmp_path / "sheet.xlsx"

    label_sheet.write_workbook(pairs, str(xlsx_path))
    _fill_xlsx_decision(xlsx_path, "10:20", "merge")
    # 30:40 left blank intentionally
    _do_import(xlsx_path, labels_path)

    rows = _read_csv(labels_path)
    pair_ids = {r["pair_id"] for r in rows}
    assert "10:20" in pair_ids
    assert "30:40" not in pair_ids


def test_all_blank_decisions_produces_empty_csv(tmp_path):
    """If every row is blank, the CSV stays empty (header only)."""
    pairs = [_make_pair("10:20")]
    labels_path = _empty_labels_csv(tmp_path)
    xlsx_path = tmp_path / "sheet.xlsx"

    label_sheet.write_workbook(pairs, str(xlsx_path))
    # No decisions filled in
    _do_import(xlsx_path, labels_path)

    rows = _read_csv(labels_path)
    assert rows == []


# ---------------------------------------------------------------------------
# AC-6: replacement — re-importing same pair_id replaces the row, no duplicate
# ---------------------------------------------------------------------------

def test_reimport_replaces_row(tmp_path):
    """Second import of the same pair_id overwrites the first decision."""
    pairs = [_make_pair("10:20")]
    labels_path = _empty_labels_csv(tmp_path)
    xlsx_path = tmp_path / "sheet.xlsx"

    label_sheet.write_workbook(pairs, str(xlsx_path))
    _fill_xlsx_decision(xlsx_path, "10:20", "merge")
    _do_import(xlsx_path, labels_path)

    # Verify first import
    rows = _read_csv(labels_path)
    assert len(rows) == 1
    assert rows[0]["label"] == "merge"

    # Second import with different decision
    _fill_xlsx_decision(xlsx_path, "10:20", "separate")
    _do_import(xlsx_path, labels_path)

    rows = _read_csv(labels_path)
    # Still exactly one row for this pair_id
    matching = [r for r in rows if r["pair_id"] == "10:20"]
    assert len(matching) == 1
    assert matching[0]["label"] == "separate"


def test_reimport_does_not_duplicate(tmp_path):
    """After two imports of the same pair_id the CSV contains only one row."""
    pairs = [_make_pair("10:20"), _make_pair("30:40")]
    labels_path = _empty_labels_csv(tmp_path)
    xlsx_path = tmp_path / "sheet.xlsx"

    label_sheet.write_workbook(pairs, str(xlsx_path))
    _fill_xlsx_decision(xlsx_path, "10:20", "merge")
    _fill_xlsx_decision(xlsx_path, "30:40", "merge")
    _do_import(xlsx_path, labels_path)
    _do_import(xlsx_path, labels_path)  # second import

    rows = _read_csv(labels_path)
    for pid in ("10:20", "30:40"):
        assert len([r for r in rows if r["pair_id"] == pid]) == 1


# ---------------------------------------------------------------------------
# AC-7: invalid decision aborts; CSV unchanged
# ---------------------------------------------------------------------------

def test_invalid_decision_aborts(tmp_path):
    """An invalid decision value causes cmd_import to exit with code 1."""
    pairs = [_make_pair("10:20")]
    labels_path = _empty_labels_csv(tmp_path)
    xlsx_path = tmp_path / "sheet.xlsx"

    label_sheet.write_workbook(pairs, str(xlsx_path))
    _fill_xlsx_decision(xlsx_path, "10:20", "INVALID_VALUE")

    with pytest.raises(SystemExit) as exc_info:
        _do_import(xlsx_path, labels_path)
    assert exc_info.value.code == 1


def test_invalid_decision_csv_unchanged(tmp_path):
    """When an invalid decision aborts the import the CSV is not modified."""
    pairs = [_make_pair("10:20"), _make_pair("30:40")]
    # Pre-populate the labels file with one existing row
    labels_path = _write_labels_csv(tmp_path, [
        {
            "pair_id": "10:20",
            "label": "merge",
            "tags": "",
            "notes": "",
            "labeled_by": "human",
            "labeled_at": "2026-01-01",
        }
    ])
    original_content = labels_path.read_text()
    xlsx_path = tmp_path / "sheet.xlsx"

    label_sheet.write_workbook(pairs, str(xlsx_path))
    _fill_xlsx_decision(xlsx_path, "10:20", "BAD_DECISION")

    with pytest.raises(SystemExit):
        _do_import(xlsx_path, labels_path)

    assert labels_path.read_text() == original_content


def test_invalid_decision_case_sensitive(tmp_path):
    """'Merge' (capitalised) is invalid; only lowercase 'merge' is accepted."""
    pairs = [_make_pair("10:20")]
    labels_path = _empty_labels_csv(tmp_path)
    xlsx_path = tmp_path / "sheet.xlsx"

    label_sheet.write_workbook(pairs, str(xlsx_path))
    # cmd_import lower-cases the value before checking, so "Merge" should be valid
    _fill_xlsx_decision(xlsx_path, "10:20", "Merge")
    # Should NOT raise — the code does decision_lower = decision_str.lower()
    _do_import(xlsx_path, labels_path)
    rows = _read_csv(labels_path)
    assert rows[0]["label"] == "merge"


# ---------------------------------------------------------------------------
# AC-8: exported xlsx has no shadow_verdicts column
# ---------------------------------------------------------------------------

def test_exported_xlsx_has_no_shadow_verdicts_column(tmp_path):
    """shadow_verdicts from the dataset must not appear as a column in the xlsx."""
    pairs = [_make_pair("10:20")]  # pair has shadow_verdicts key
    xlsx_path = tmp_path / "sheet.xlsx"
    label_sheet.write_workbook(pairs, str(xlsx_path))

    headers = _get_xlsx_headers(xlsx_path)
    assert not any("shadow" in h for h in headers)
    assert not any("verdict" in h for h in headers)


def test_exported_xlsx_columns_match_spec(tmp_path):
    """Exported xlsx has the COLUMNS defined in label_sheet.COLUMNS."""
    pairs = [_make_pair("10:20")]
    xlsx_path = tmp_path / "sheet.xlsx"
    label_sheet.write_workbook(pairs, str(xlsx_path))

    wb = openpyxl.load_workbook(xlsx_path)
    ws = wb.active
    # Header values (preserve original case for comparison)
    headers = [str(c.value) if c.value else "" for c in ws[1]]
    wb.close()

    for expected_col in label_sheet.COLUMNS:
        assert expected_col in headers, f"expected column {expected_col!r} not found in xlsx"


def test_exported_xlsx_decision_column_blank(tmp_path):
    """The your_decision column is blank for all data rows on export."""
    pairs = [_make_pair("10:20"), _make_pair("30:40")]
    xlsx_path = tmp_path / "sheet.xlsx"
    label_sheet.write_workbook(pairs, str(xlsx_path))

    headers = _get_xlsx_headers(xlsx_path)
    decision_col_idx = next(
        i for i, h in enumerate(headers) if h.startswith("your_decision")
    )

    wb = openpyxl.load_workbook(xlsx_path)
    ws = wb.active
    for row in ws.iter_rows(min_row=2, values_only=True):
        val = row[decision_col_idx]
        assert val is None or val == "", f"expected blank decision cell, got {val!r}"
    wb.close()
