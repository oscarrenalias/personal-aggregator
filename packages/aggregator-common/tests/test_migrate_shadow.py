"""Migration round-trip test for d6e7f8a9b0c1 (shadow evaluation storage).

AC-6: Migration upgrades cleanly.
AC-7 (migration): Downgrade removes all added columns and new table.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

_PACKAGE_ROOT = Path(__file__).parent.parent
_ALEMBIC_INI = _PACKAGE_ROOT / "alembic.ini"
_MIGRATIONS_DIR = _PACKAGE_ROOT / "src" / "aggregator_common" / "migrations"


def _ensure_docker_host() -> None:
    if "DOCKER_HOST" in os.environ:
        return
    if Path("/var/run/docker.sock").exists():
        os.environ["DOCKER_HOST"] = "unix:///var/run/docker.sock"
        return
    orbstack = Path.home() / ".orbstack" / "run" / "docker.sock"
    if orbstack.exists():
        os.environ["DOCKER_HOST"] = f"unix://{orbstack}"
        return
    raise RuntimeError(
        "No Docker socket found. Ensure Docker or OrbStack is running. "
        "OrbStack socket expected at ~/.orbstack/run/docker.sock."
    )


@pytest.fixture(scope="module")
def shadow_roundtrip_db():
    """Isolated container for migration round-trip; does not share with other fixtures."""
    from testcontainers.postgres import PostgresContainer

    _ensure_docker_host()

    original = os.environ.get("DATABASE_URL")
    with PostgresContainer("postgres:16") as postgres:
        raw_url = postgres.get_connection_url()
        db_url = raw_url.replace("postgresql+psycopg2://", "postgresql+psycopg://")
        os.environ["DATABASE_URL"] = db_url
        yield db_url

    if original is not None:
        os.environ["DATABASE_URL"] = original
    else:
        os.environ.pop("DATABASE_URL", None)


def _make_alembic_cfg(db_url: str) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_shadow_migration_upgrade_adds_columns_and_table(shadow_roundtrip_db):
    """AC-6: d6e7f8a9b0c1 upgrade adds four columns to llm_calls and creates llm_shadow_results."""
    cfg = _make_alembic_cfg(shadow_roundtrip_db)

    # Upgrade to exactly d6e7f8a9b0c1 so the test doesn't depend on later migrations
    command.upgrade(cfg, "d6e7f8a9b0c1")

    engine = create_engine(shadow_roundtrip_db)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "llm_shadow_results" in tables, "upgrade should create llm_shadow_results table"

        llm_call_cols = {c["name"] for c in inspector.get_columns("llm_calls")}
        for col in ("experiment", "arm", "reasoning_effort", "reasoning_tokens"):
            assert col in llm_call_cols, f"upgrade should add column {col!r} to llm_calls"

        shadow_cols = {c["name"] for c in inspector.get_columns("llm_shadow_results")}
        for col in ("id", "created_at", "experiment", "service", "operation", "ref_id",
                    "arm", "model", "prompt_hash", "status", "attempts", "params",
                    "output", "raw_output", "error", "request_ids"):
            assert col in shadow_cols, f"llm_shadow_results should have column {col!r}"
    finally:
        engine.dispose()


def test_shadow_migration_downgrade_removes_columns_and_table(shadow_roundtrip_db):
    """AC-7 (migration): d6e7f8a9b0c1 downgrade removes all added columns and drops the table."""
    cfg = _make_alembic_cfg(shadow_roundtrip_db)

    # Should already be at d6e7f8a9b0c1 from the previous test (module-scoped fixture)
    # Downgrade to the previous revision (c6d7e8f9a0b1)
    command.downgrade(cfg, "c6d7e8f9a0b1")

    engine = create_engine(shadow_roundtrip_db)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "llm_shadow_results" not in tables, "downgrade should drop llm_shadow_results"

        llm_call_cols = {c["name"] for c in inspector.get_columns("llm_calls")}
        for col in ("experiment", "arm", "reasoning_effort", "reasoning_tokens"):
            assert col not in llm_call_cols, f"downgrade should remove column {col!r} from llm_calls"
    finally:
        engine.dispose()

    # Restore head for a clean state
    command.upgrade(cfg, "head")
