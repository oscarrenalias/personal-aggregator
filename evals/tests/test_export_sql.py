"""Tests for evals/clusterer_merge/export.sql safety and structure."""

import re
from pathlib import Path

SQL_PATH = Path(__file__).parent.parent / "clusterer_merge" / "export.sql"

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

# Top-level JSON keys expected in the SELECT output per spec §2
EXPECTED_JSON_KEYS = {
    "pair_id",
    "first_seen",
    "thread_a",
    "thread_b",
    "shadow_verdicts",
}

# Keys inside thread objects per spec §2
EXPECTED_THREAD_KEYS = {
    "id",
    "title",
    "summary",
    "member_count",
    "recent_articles",
}

# Keys inside recent_articles per spec §2
EXPECTED_ARTICLE_KEYS = {
    "title",
    "source",
    "assigned_at",
    "summary",
}


def _load_sql() -> str:
    return SQL_PATH.read_text(encoding="utf-8")


def test_sql_file_exists():
    assert SQL_PATH.exists(), f"export.sql not found at {SQL_PATH}"


def test_no_forbidden_keywords():
    sql = _load_sql()
    # Strip SQL comments before checking
    stripped = re.sub(r"--[^\n]*", "", sql)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    tokens = set(re.findall(r"\b\w+\b", stripped, re.IGNORECASE))
    tokens_upper = {t.upper() for t in tokens}
    for keyword in FORBIDDEN_KEYWORDS:
        # read_only is case-sensitive in the prod-db guard; check as-is too
        assert keyword.upper() not in tokens_upper, (
            f"Forbidden keyword {keyword!r} found in export.sql"
        )
        assert keyword not in stripped, (
            f"Forbidden keyword {keyword!r} found in export.sql"
        )


def test_expected_top_level_json_keys_present():
    sql = _load_sql()
    for key in EXPECTED_JSON_KEYS:
        assert f"'{key}'" in sql or f'"{key}"' in sql, (
            f"Expected JSON key {key!r} not found as a string literal in export.sql"
        )


def test_expected_thread_json_keys_present():
    sql = _load_sql()
    for key in EXPECTED_THREAD_KEYS:
        assert f"'{key}'" in sql or f'"{key}"' in sql, (
            f"Expected thread key {key!r} not found as a string literal in export.sql"
        )


def test_expected_article_json_keys_present():
    sql = _load_sql()
    for key in EXPECTED_ARTICLE_KEYS:
        assert f"'{key}'" in sql or f'"{key}"' in sql, (
            f"Expected article key {key!r} not found as a string literal in export.sql"
        )


def test_sql_starts_with_select_or_with():
    sql = _load_sql().strip()
    # Strip comments from the beginning
    sql_no_comments = re.sub(r"--[^\n]*\n?", "", sql).strip()
    assert re.match(r"^(SELECT|WITH)\b", sql_no_comments, re.IGNORECASE), (
        "export.sql should start with SELECT or WITH (read-only)"
    )
