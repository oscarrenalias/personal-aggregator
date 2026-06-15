"""Tests confirming MCP get_thread does NOT stamp last_viewed_at."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

import pytest
from sqlalchemy.orm import Session

from aggregator_common.models import Source, Thread

_NOW = datetime.now(tz=timezone.utc)


@pytest.fixture(autouse=True)
def patch_get_session(session: Session, monkeypatch) -> None:
    @contextmanager
    def _mock_get_session() -> Generator[Session, None, None]:
        yield session

    import aggregator_mcp.server as srv  # noqa: PLC0415

    monkeypatch.setattr(srv, "get_session", _mock_get_session)


def _make_thread(session: Session, *, last_viewed_at: datetime | None = None) -> Thread:
    thread = Thread(
        representative_title="MCP LVA Test Thread",
        first_seen=_NOW,
        last_updated=_NOW,
        status="active",
        source_list=[],
        known_facts=[],
        deltas=[],
        last_viewed_at=last_viewed_at,
    )
    session.add(thread)
    session.flush()
    return thread


class TestMcpGetThreadDoesNotStampLastViewedAt:
    def test_get_thread_leaves_last_viewed_at_none(self, session: Session):
        """MCP get_thread must not call mark_thread_viewed; last_viewed_at stays None."""
        import aggregator_mcp.server as srv

        thread = _make_thread(session, last_viewed_at=None)

        result = srv.get_thread(thread_id=thread.id)

        assert "error" not in result
        session.expire(thread)
        session.refresh(thread)
        assert thread.last_viewed_at is None  # type: ignore[attr-defined]

    def test_get_thread_leaves_existing_stamp_unchanged(self, session: Session):
        """MCP get_thread must not overwrite an existing last_viewed_at."""
        import aggregator_mcp.server as srv

        existing_stamp = _NOW
        thread = _make_thread(session, last_viewed_at=existing_stamp)

        srv.get_thread(thread_id=thread.id)

        session.expire(thread)
        session.refresh(thread)
        assert thread.last_viewed_at == existing_stamp  # type: ignore[attr-defined]

    def test_get_thread_returns_has_updates_field(self, session: Session):
        """MCP get_thread result includes has_updates in the thread dict."""
        import aggregator_mcp.server as srv

        thread = _make_thread(session, last_viewed_at=None)

        result = srv.get_thread(thread_id=thread.id)

        assert "thread" in result
        assert "has_updates" in result["thread"]

    def test_get_thread_has_updates_true_for_never_viewed(self, session: Session):
        """MCP get_thread returns has_updates=True when last_viewed_at is None."""
        import aggregator_mcp.server as srv

        thread = _make_thread(session, last_viewed_at=None)

        result = srv.get_thread(thread_id=thread.id)

        assert result["thread"]["has_updates"] is True
