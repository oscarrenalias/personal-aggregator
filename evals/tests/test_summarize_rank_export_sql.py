"""Tests for evals/summarize_rank/export.sql safety and structure."""

import re
from pathlib import Path

SQL_PATH = Path(__file__).parent.parent / "summarize_rank" / "export.sql"

FORBIDDEN_KEYWORDS = {
    "SET",
    "INSERT",
    "UPDATE",
    "DELETE",
    "CREATE",
    "DROP",
    "TRUNCATE",
    "read_only",
}

# Top-level JSON keys expected in the SELECT output
EXPECTED_JSON_KEYS = {
    "article_id",
    "source_name",
    "title",
    "content_text",
    "production_score",
    "interest_profile_text",
    "categories",
}


def _load_sql() -> str:
    return SQL_PATH.read_text(encoding="utf-8")


def test_sql_file_exists():
    assert SQL_PATH.exists(), f"export.sql not found at {SQL_PATH}"


def test_no_forbidden_keywords():
    sql = _load_sql()
    stripped = re.sub(r"--[^\n]*", "", sql)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    tokens_upper = {t.upper() for t in re.findall(r"\b\w+\b", stripped, re.IGNORECASE)}
    for keyword in FORBIDDEN_KEYWORDS:
        assert keyword.upper() not in tokens_upper, (
            f"Forbidden keyword {keyword!r} found in export.sql"
        )


def test_sql_starts_with_select_or_with():
    sql = _load_sql().strip()
    sql_no_comments = re.sub(r"--[^\n]*\n?", "", sql).strip()
    assert re.match(r"^(SELECT|WITH)\b", sql_no_comments, re.IGNORECASE), (
        "export.sql should start with SELECT or WITH (read-only)"
    )


def test_expected_top_level_json_keys_present():
    sql = _load_sql()
    for key in EXPECTED_JSON_KEYS:
        assert f"'{key}'" in sql or f'"{key}"' in sql, (
            f"Expected JSON key {key!r} not found as a string literal in export.sql"
        )


def test_interest_profile_column_is_profile_text():
    """export.sql must select profile_text, not the nonexistent 'text' column."""
    sql = _load_sql()
    sql_no_comments = re.sub(r"--[^\n]*", "", sql)
    # Must reference profile_text
    assert re.search(r"\bprofile_text\b", sql_no_comments), (
        "export.sql must SELECT profile_text from interest_profile (not 'text')"
    )
    # Must not reference the wrong column name bare (outside comments/strings)
    # 'text' may appear as a type-cast keyword; check specifically for
    # "SELECT text" which would reference the nonexistent column
    assert not re.search(
        r"\bSELECT\s+text\b", sql_no_comments, re.IGNORECASE
    ), "export.sql must not SELECT the nonexistent 'text' column from interest_profile"
