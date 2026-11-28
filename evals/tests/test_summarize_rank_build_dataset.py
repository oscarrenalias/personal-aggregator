"""Tests for evals/summarize_rank/build_dataset.py."""

import json
import pytest

# conftest.py adds evals/ to sys.path; use package import to avoid name
# collision with evals/clusterer_merge/build_dataset.py.
from summarize_rank import build_dataset as sr_build


VALID_RECORD = {
    "article_id": 100,
    "source_name": "TechCrunch",
    "clean_title": "AI takes over the world",
    "feed_title": None,
    "title": "AI takes over the world",
    "content_text": "An AI model scored highest on every benchmark.",
    "excerpt": None,
    "feed_summary": None,
    "production_score": 80,
    "interest_profile_text": "I am interested in AI.",
    "categories": [{"name": "Tech", "description": "Technology news"}],
}

VALID_RECORD_2 = {
    "article_id": 200,
    "source_name": "Reuters",
    "clean_title": "Stock markets close higher",
    "feed_title": None,
    "title": "Stock markets close higher",
    "content_text": "Markets rallied on optimism.",
    "excerpt": None,
    "feed_summary": None,
    "production_score": 30,
    "interest_profile_text": "I am interested in AI.",
    "categories": [],
}


def test_valid_round_trip(tmp_path):
    input_file = tmp_path / "input.jsonl"
    input_file.write_text(
        json.dumps(VALID_RECORD_2) + "\n" + json.dumps(VALID_RECORD),
        encoding="utf-8",
    )

    out = tmp_path / "articles.jsonl"
    sr_build.main(["--out", str(out), "--input", str(input_file)])

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rec0 = json.loads(lines[0])
    rec1 = json.loads(lines[1])
    # sorted by article_id ascending
    assert rec0["article_id"] == 100
    assert rec1["article_id"] == 200
    # exported_at added
    assert "exported_at" in rec0
    assert "exported_at" in rec1


def test_duplicate_drop_last_wins(tmp_path):
    record_first = json.loads(json.dumps(VALID_RECORD))
    record_first["clean_title"] = "first version"

    record_last = json.loads(json.dumps(VALID_RECORD))
    record_last["clean_title"] = "last version"

    input_file = tmp_path / "input.jsonl"
    input_file.write_text(
        json.dumps(record_first) + "\n" + json.dumps(record_last),
        encoding="utf-8",
    )

    out = tmp_path / "articles.jsonl"
    sr_build.main(["--out", str(out), "--input", str(input_file)])

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["clean_title"] == "last version"


def test_missing_required_key_aborts(tmp_path):
    bad = json.loads(json.dumps(VALID_RECORD))
    del bad["production_score"]

    input_file = tmp_path / "input.jsonl"
    input_file.write_text(json.dumps(bad), encoding="utf-8")

    out = tmp_path / "articles.jsonl"
    with pytest.raises(SystemExit) as exc_info:
        sr_build.main(["--out", str(out), "--input", str(input_file)])

    assert exc_info.value.code == 1
    assert not out.exists()


def test_invalid_article_id_type_aborts(tmp_path):
    bad = json.loads(json.dumps(VALID_RECORD))
    bad["article_id"] = "not-an-int"

    input_file = tmp_path / "input.jsonl"
    input_file.write_text(json.dumps(bad), encoding="utf-8")

    out = tmp_path / "articles.jsonl"
    with pytest.raises(SystemExit) as exc_info:
        sr_build.main(["--out", str(out), "--input", str(input_file)])

    assert exc_info.value.code == 1


def test_overwrite_refusal(tmp_path):
    out = tmp_path / "articles.jsonl"
    out.write_text("existing", encoding="utf-8")

    input_file = tmp_path / "input.jsonl"
    input_file.write_text(json.dumps(VALID_RECORD), encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        sr_build.main(["--out", str(out), "--input", str(input_file)])

    assert exc_info.value.code == 1
    assert out.read_text(encoding="utf-8") == "existing"


def test_overwrite_zero_byte_allowed(tmp_path):
    """A 0-byte output file (from a failed export) must be overwritable."""
    out = tmp_path / "articles.jsonl"
    out.write_bytes(b"")  # empty file left by a failed export run

    input_file = tmp_path / "input.jsonl"
    input_file.write_text(json.dumps(VALID_RECORD), encoding="utf-8")

    sr_build.main(["--out", str(out), "--input", str(input_file)])

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["article_id"] == 100


def test_blank_lines_skipped(tmp_path):
    input_file = tmp_path / "input.jsonl"
    input_file.write_text(
        "\n" + json.dumps(VALID_RECORD) + "\n\n",
        encoding="utf-8",
    )

    out = tmp_path / "articles.jsonl"
    sr_build.main(["--out", str(out), "--input", str(input_file)])

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
