"""Tests for evals/clusterer_merge/build_dataset.py."""

import json

import pytest

import build_dataset  # noqa: E402  (evals/ added to sys.path by conftest)

VALID_RECORD = {
    "pair_id": "100:200",
    "first_seen": "2026-11-15T10:00:00Z",
    "thread_a": {
        "id": 100,
        "title": "Thread A title",
        "summary": "Thread A summary",
        "member_count": 2,
        "recent_articles": [],
    },
    "thread_b": {
        "id": 200,
        "title": "Thread B title",
        "summary": "Thread B summary",
        "member_count": 3,
        "recent_articles": [],
    },
    "shadow_verdicts": {},
}

VALID_RECORD_2 = {
    "pair_id": "300:400",
    "first_seen": "2026-11-16T10:00:00Z",
    "thread_a": {
        "id": 300,
        "title": "Another thread",
        "summary": "Summary here",
        "member_count": 1,
        "recent_articles": [],
    },
    "thread_b": {
        "id": 400,
        "title": "Another thread B",
        "summary": "Summary B",
        "member_count": 1,
        "recent_articles": [],
    },
    "shadow_verdicts": {},
}


def test_valid_round_trip(tmp_path):
    """Valid input is written sorted by pair_id with exported_at added."""
    # deliberately out of order to test sorting
    input_file = tmp_path / "input.jsonl"
    input_file.write_text(
        json.dumps(VALID_RECORD_2) + "\n" + json.dumps(VALID_RECORD),
        encoding="utf-8",
    )

    out = tmp_path / "pairs.jsonl"
    build_dataset.main(["--out", str(out), "--input", str(input_file)])

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    rec1 = json.loads(lines[1])
    # sorted by pair_id
    assert rec0["pair_id"] == "100:200"
    assert rec1["pair_id"] == "300:400"
    # exported_at added
    assert "exported_at" in rec0
    assert "exported_at" in rec1
    # original keys preserved
    assert rec0["thread_a"]["id"] == 100
    assert rec1["thread_b"]["id"] == 400


def test_duplicate_drop_last_wins(tmp_path):
    """Duplicate pair_ids: last occurrence wins."""
    record_first = json.loads(json.dumps(VALID_RECORD))
    record_first["thread_a"]["title"] = "first version"

    record_last = json.loads(json.dumps(VALID_RECORD))
    record_last["thread_a"]["title"] = "last version"

    input_file = tmp_path / "input.jsonl"
    input_file.write_text(
        json.dumps(record_first) + "\n" + json.dumps(record_last),
        encoding="utf-8",
    )

    out = tmp_path / "pairs.jsonl"
    build_dataset.main(["--out", str(out), "--input", str(input_file)])

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["thread_a"]["title"] == "last version"


def test_bad_pair_id_format_aborts(tmp_path):
    """Bad pair_id format fails with line number; nothing is written."""
    bad = json.loads(json.dumps(VALID_RECORD))
    bad["pair_id"] = "not-valid"
    bad["thread_a"]["id"] = 999

    input_file = tmp_path / "input.jsonl"
    input_file.write_text(json.dumps(bad), encoding="utf-8")

    out = tmp_path / "pairs.jsonl"
    with pytest.raises(SystemExit) as exc_info:
        build_dataset.main(["--out", str(out), "--input", str(input_file)])

    assert exc_info.value.code == 1
    assert not out.exists()


def test_out_of_order_ids_aborts(tmp_path):
    """pair_id with thread_a.id > thread_b.id fails."""
    bad = json.loads(json.dumps(VALID_RECORD))
    bad["pair_id"] = "200:100"
    bad["thread_a"]["id"] = 200
    bad["thread_b"]["id"] = 100

    input_file = tmp_path / "input.jsonl"
    input_file.write_text(json.dumps(bad), encoding="utf-8")

    out = tmp_path / "pairs.jsonl"
    with pytest.raises(SystemExit) as exc_info:
        build_dataset.main(["--out", str(out), "--input", str(input_file)])

    assert exc_info.value.code == 1
    assert not out.exists()


def test_equal_ids_aborts(tmp_path):
    """pair_id where both ids are equal fails (not strictly less than)."""
    bad = json.loads(json.dumps(VALID_RECORD))
    bad["pair_id"] = "100:100"
    bad["thread_a"]["id"] = 100
    bad["thread_b"]["id"] = 100

    input_file = tmp_path / "input.jsonl"
    input_file.write_text(json.dumps(bad), encoding="utf-8")

    out = tmp_path / "pairs.jsonl"
    with pytest.raises(SystemExit) as exc_info:
        build_dataset.main(["--out", str(out), "--input", str(input_file)])

    assert exc_info.value.code == 1
    assert not out.exists()


def test_missing_required_key_aborts(tmp_path):
    """Record missing a required top-level key aborts; nothing is written."""
    bad = json.loads(json.dumps(VALID_RECORD))
    del bad["shadow_verdicts"]

    input_file = tmp_path / "input.jsonl"
    input_file.write_text(json.dumps(bad), encoding="utf-8")

    out = tmp_path / "pairs.jsonl"
    with pytest.raises(SystemExit) as exc_info:
        build_dataset.main(["--out", str(out), "--input", str(input_file)])

    assert exc_info.value.code == 1
    assert not out.exists()


def test_missing_thread_key_aborts(tmp_path):
    """Record missing a required thread sub-key aborts."""
    bad = json.loads(json.dumps(VALID_RECORD))
    del bad["thread_a"]["member_count"]

    input_file = tmp_path / "input.jsonl"
    input_file.write_text(json.dumps(bad), encoding="utf-8")

    out = tmp_path / "pairs.jsonl"
    with pytest.raises(SystemExit) as exc_info:
        build_dataset.main(["--out", str(out), "--input", str(input_file)])

    assert exc_info.value.code == 1
    assert not out.exists()


def test_overwrite_refusal(tmp_path):
    """Refuses to overwrite an existing output file."""
    out = tmp_path / "pairs.jsonl"
    out.write_text("existing content", encoding="utf-8")

    input_file = tmp_path / "input.jsonl"
    input_file.write_text(json.dumps(VALID_RECORD), encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        build_dataset.main(["--out", str(out), "--input", str(input_file)])

    assert exc_info.value.code == 1
    # File unchanged
    assert out.read_text(encoding="utf-8") == "existing content"


def test_blank_lines_skipped(tmp_path):
    """Blank lines in input are silently skipped."""
    input_file = tmp_path / "input.jsonl"
    input_file.write_text(
        "\n" + json.dumps(VALID_RECORD) + "\n\n",
        encoding="utf-8",
    )

    out = tmp_path / "pairs.jsonl"
    build_dataset.main(["--out", str(out), "--input", str(input_file)])

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_error_message_contains_line_number(tmp_path, capsys):
    """Error messages include the line number of the offending record."""
    good = json.dumps(VALID_RECORD)
    bad = json.loads(json.dumps(VALID_RECORD_2))
    del bad["first_seen"]

    input_file = tmp_path / "input.jsonl"
    input_file.write_text(good + "\n" + json.dumps(bad), encoding="utf-8")

    out = tmp_path / "pairs.jsonl"
    with pytest.raises(SystemExit):
        build_dataset.main(["--out", str(out), "--input", str(input_file)])

    captured = capsys.readouterr()
    assert "line 2" in captured.err
