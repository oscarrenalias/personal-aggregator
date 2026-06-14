"""Regression tests for the Today view master-detail layout.

Verifies:
- GET /today returns a brief CARD LIST (not the full brief crammed in the list pane).
- GET /brief/{id} returns the full detail fragment with topics.
- GET /brief/{id} returns 404 for a missing id.
- POST /today/refresh enqueues a pending brief and returns generating state.
- Internal article refs in the detail target #reader-pane (not #article-detail).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from aggregator_common.models import Brief, BriefTopic


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc)


def make_brief(
    session: Session,
    *,
    status: str = "ready",
    headline: str = "Test Brief Headline",
    intro: str | None = None,
    model: str | None = None,
    generated_at: datetime | None = None,
    origin: str = "auto",
    days_offset: int = 0,
) -> Brief:
    base = _NOW + timedelta(days=days_offset)
    day_start = base.replace(hour=0, minute=0, second=0, microsecond=0)
    brief = Brief(
        status=status,
        headline=headline,
        intro=intro,
        model=model,
        generated_at=generated_at or (base if status == "ready" else None),
        origin=origin,
        period_start=day_start,
        period_end=day_start + timedelta(days=1),
    )
    session.add(brief)
    session.flush()
    session.commit()
    session.refresh(brief)
    return brief


def make_brief_topic(
    session: Session,
    *,
    brief_id: int,
    position: int = 0,
    headline: str = "Topic Headline",
    what_happened: str = "Something happened.",
    why_it_matters: str = "It matters because.",
    historical_context: str | None = None,
    topic_refs: list | None = None,
) -> BriefTopic:
    topic = BriefTopic(
        brief_id=brief_id,
        position=position,
        headline=headline,
        what_happened=what_happened,
        why_it_matters=why_it_matters,
        historical_context=historical_context,
        topic_refs=topic_refs or [],
    )
    session.add(topic)
    session.flush()
    session.commit()
    session.refresh(topic)
    return topic


# ---------------------------------------------------------------------------
# GET /today — list pane
# ---------------------------------------------------------------------------


def test_get_today_returns_brief_cards_not_full_brief(db_session, client):
    """Regression: GET /today must return a list of brief cards in the list pane,
    not the full brief body crammed into the 380px-wide middle pane.

    Pre-fix: the response contained the entire .today-brief with all topics inline.
    Post-fix: the response contains .brief-card elements and no inline topic content.
    """
    brief = make_brief(db_session, headline="AI Roundup")
    make_brief_topic(db_session, brief_id=brief.id, headline="AI Topic")

    response = client.get("/today")
    assert response.status_code == 200
    html = response.text

    # Must contain a brief card (not the full detail)
    assert "brief-card" in html
    assert "AI Roundup" in html

    # Must NOT contain the full detail-specific markup (topics are in the reader pane)
    assert "today-topic-section" not in html
    assert "What happened" not in html
    assert "Why it matters" not in html


def test_get_today_returns_multiple_brief_cards(db_session, client):
    """GET /today returns all ready briefs as cards (newest first, up to 30)."""
    make_brief(db_session, headline="Brief One", days_offset=0)
    make_brief(db_session, headline="Brief Two", days_offset=1)

    response = client.get("/today")
    assert response.status_code == 200
    html = response.text

    assert "Brief One" in html
    assert "Brief Two" in html
    assert html.count("brief-card") >= 2


def test_get_today_shows_generating_banner_when_pending(db_session, client):
    """GET /today shows a generating banner when a brief is pending/generating."""
    make_brief(db_session, status="pending")

    response = client.get("/today")
    assert response.status_code == 200
    assert "brief-generating-banner" in response.text
    assert "Generating your daily brief" in response.text


def test_get_today_shows_generating_banner_with_polling(db_session, client):
    """The generating banner must poll GET /today every 5s via hx-trigger."""
    make_brief(db_session, status="generating")

    response = client.get("/today")
    assert response.status_code == 200
    html = response.text
    assert "hx-get=\"/today\"" in html
    assert "every 5s" in html


def test_get_today_empty_state_when_no_briefs(client):
    """GET /today with no briefs shows the empty state with Generate button."""
    response = client.get("/today")
    assert response.status_code == 200
    assert "today-empty" in response.text
    assert "No brief yet" in response.text


def test_get_today_list_has_brief_list_header_with_refresh(client):
    """GET /today always renders the brief-list-header with a Refresh button."""
    response = client.get("/today")
    assert response.status_code == 200
    html = response.text
    assert "brief-list-header" in html
    assert "Refresh" in html
    assert 'hx-post="/today/refresh"' in html


def test_get_today_brief_card_shows_topic_count(db_session, client):
    """Brief card must display the number of topics."""
    brief = make_brief(db_session)
    make_brief_topic(db_session, brief_id=brief.id, position=0, headline="T1")
    make_brief_topic(db_session, brief_id=brief.id, position=1, headline="T2")

    response = client.get("/today")
    assert response.status_code == 200
    assert "2 topics" in response.text


def test_get_today_brief_card_singular_topic(db_session, client):
    """Brief card with 1 topic shows '1 topic' (not '1 topics')."""
    brief = make_brief(db_session)
    make_brief_topic(db_session, brief_id=brief.id, headline="Only Topic")

    response = client.get("/today")
    assert response.status_code == 200
    assert "1 topic" in response.text
    assert "1 topics" not in response.text


# ---------------------------------------------------------------------------
# GET /brief/{id} — reader pane detail
# ---------------------------------------------------------------------------


def test_get_brief_detail_returns_full_content(db_session, client):
    """GET /brief/{id} returns the full detail fragment with headline, intro, topics."""
    brief = make_brief(
        db_session,
        headline="Detailed Brief",
        intro="This is the intro.",
        model="gpt-4o",
    )
    make_brief_topic(
        db_session,
        brief_id=brief.id,
        headline="Topic One",
        what_happened="Things happened.",
        why_it_matters="They matter.",
    )

    response = client.get(f"/brief/{brief.id}")
    assert response.status_code == 200
    html = response.text

    assert "Detailed Brief" in html
    assert "This is the intro." in html
    assert "Topic One" in html
    assert "Things happened." in html
    assert "They matter." in html
    assert "gpt-4o" in html


def test_get_brief_detail_404_for_missing_id(client):
    """GET /brief/99999 returns 404 when no such brief exists."""
    response = client.get("/brief/99999")
    assert response.status_code == 404


def test_get_brief_detail_internal_refs_target_reader_pane(db_session, client):
    """Regression: internal article refs in the detail must use hx-target=\"#reader-content\",
    not #article-detail (old list-pane target) and not #reader-pane (replaced by
    #reader-content so the .reader-close-btn chrome persists through HTMX swaps)."""
    brief = make_brief(db_session)
    make_brief_topic(
        db_session,
        brief_id=brief.id,
        topic_refs=[{"internal": True, "article_id": 42, "url": None, "title": "Ref Article"}],
    )

    response = client.get(f"/brief/{brief.id}")
    assert response.status_code == 200
    html = response.text

    assert 'hx-target="#reader-content"' in html
    assert 'hx-target="#article-detail"' not in html
    assert 'hx-target="#reader-pane"' not in html
    assert "Ref Article" in html


def test_get_brief_detail_has_correct_container(db_session, client):
    """GET /brief/{id} response must use .brief-detail container (not .today-brief)."""
    brief = make_brief(db_session)
    response = client.get(f"/brief/{brief.id}")
    assert response.status_code == 200
    assert "brief-detail" in response.text
    # Old full-brief container must not appear in the detail fragment
    assert 'class="today-brief"' not in response.text


def test_get_brief_detail_historical_context_rendered_when_present(db_session, client):
    """Historical context is rendered when provided."""
    brief = make_brief(db_session)
    make_brief_topic(
        db_session,
        brief_id=brief.id,
        historical_context="Long history here.",
    )

    response = client.get(f"/brief/{brief.id}")
    assert response.status_code == 200
    assert "Background" in response.text
    assert "Long history here." in response.text


# ---------------------------------------------------------------------------
# POST /today/refresh — enqueue + generating state
# ---------------------------------------------------------------------------


def test_post_today_refresh_enqueues_pending_brief(db_session, client):
    """POST /today/refresh creates a pending brief when none is in-flight."""
    response = client.post("/today/refresh")
    assert response.status_code == 200

    from sqlalchemy import select as sa_select
    pending = db_session.execute(
        sa_select(Brief).where(Brief.status == "pending")
    ).scalar_one_or_none()
    assert pending is not None
    assert pending.origin == "manual"


def test_post_today_refresh_returns_generating_state(client):
    """POST /today/refresh response must show the generating banner."""
    response = client.post("/today/refresh")
    assert response.status_code == 200
    html = response.text
    assert "brief-generating-banner" in html
    assert "Generating your daily brief" in html


def test_post_today_refresh_does_not_duplicate_if_already_pending(db_session, client):
    """POST /today/refresh must not create a second pending brief when one exists."""
    existing = make_brief(db_session, status="pending")

    response = client.post("/today/refresh")
    assert response.status_code == 200

    from sqlalchemy import select as sa_select, func
    count = db_session.execute(
        sa_select(func.count()).select_from(Brief).where(Brief.status == "pending")
    ).scalar()
    assert count == 1


def test_post_today_refresh_returns_existing_ready_briefs_in_list(db_session, client):
    """POST /today/refresh shows existing ready briefs alongside the generating banner."""
    make_brief(db_session, headline="Old Ready Brief")

    response = client.post("/today/refresh")
    assert response.status_code == 200
    html = response.text

    assert "Old Ready Brief" in html
    assert "brief-generating-banner" in html


# ---------------------------------------------------------------------------
# Alpine component registration
# ---------------------------------------------------------------------------


def test_app_js_registers_brief_list_component(client):
    """app.js must register the briefList Alpine component for the Today view."""
    response = client.get("/static/app.js")
    assert response.status_code == 200
    js = response.text

    assert "briefList" in js
    assert "Alpine.data('briefList'" in js or "window.Alpine.data('briefList'" in js


def test_app_js_brief_list_has_select_brief_method(client):
    """briefList() component must expose selectBrief() for card click handling."""
    response = client.get("/static/app.js")
    assert response.status_code == 200
    assert "selectBrief" in response.text


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------


def test_styles_css_has_brief_card_rule(client):
    """styles.css must define .brief-card for the list-pane card."""
    response = client.get("/static/styles.css")
    assert response.status_code == 200
    assert ".brief-card" in response.text


def test_styles_css_has_brief_list_header_rule(client):
    """styles.css must define .brief-list-header."""
    response = client.get("/static/styles.css")
    assert response.status_code == 200
    assert ".brief-list-header" in response.text


def test_styles_css_has_brief_detail_rule(client):
    """styles.css must define .brief-detail for the reader-pane container."""
    response = client.get("/static/styles.css")
    assert response.status_code == 200
    assert ".brief-detail" in response.text


# ---------------------------------------------------------------------------
# Regression: same-day multiple briefs — issue #1
# ---------------------------------------------------------------------------


def test_today_shows_both_same_day_briefs(db_session, client):
    """Regression: two ready briefs with the same period_start (same calendar day)
    must BOTH appear in the Today list.

    Pre-fix concern: the second brief might overwrite or shadow the first.
    Post-fix: the query returns all ready briefs regardless of period_start;
    both cards are rendered.
    """
    day_start = _NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    auto_brief = Brief(
        status="ready",
        origin="auto",
        headline="Auto Brief",
        generated_at=_NOW,
        period_start=day_start,
        period_end=day_start + timedelta(days=1),
    )
    manual_brief = Brief(
        status="ready",
        origin="manual",
        headline="Manual Refresh Brief",
        generated_at=_NOW + timedelta(minutes=30),
        period_start=day_start,
        period_end=day_start + timedelta(days=1),
    )
    db_session.add(auto_brief)
    db_session.add(manual_brief)
    db_session.flush()
    db_session.commit()

    response = client.get("/today")
    assert response.status_code == 200
    html = response.text

    assert "Auto Brief" in html
    assert "Manual Refresh Brief" in html
    assert html.count("brief-card") >= 2


def test_brief_card_date_uses_period_start_not_generated_at(db_session, client):
    """Regression: the brief card date must reflect period_start, not generated_at.

    Pre-fix: the card used brief.generated_at which could be on a different UTC day
    than the period it covers (e.g., a UTC+2 auto-brief generated before UTC midnight
    would show 'Jun 13' even though it covered the Jun 14 local day).
    Post-fix: period_start drives the date label.
    """
    day_start = datetime(2025, 6, 14, 0, 0, 0, tzinfo=timezone.utc)
    brief = Brief(
        status="ready",
        origin="auto",
        headline="Timezone Test Brief",
        generated_at=datetime(2025, 6, 13, 22, 30, 0, tzinfo=timezone.utc),
        period_start=day_start,
        period_end=day_start + timedelta(days=1),
    )
    db_session.add(brief)
    db_session.flush()
    db_session.commit()

    card_response = client.get("/today")
    assert card_response.status_code == 200
    html = card_response.text

    assert "14 Jun 2025" in html
    assert "13 Jun 2025" not in html


def test_brief_detail_date_uses_period_start_not_generated_at(db_session, client):
    """Regression: the brief detail date must reflect period_start, not generated_at."""
    day_start = datetime(2025, 6, 14, 0, 0, 0, tzinfo=timezone.utc)
    brief = Brief(
        status="ready",
        origin="auto",
        headline="Detail Date Test",
        generated_at=datetime(2025, 6, 13, 22, 30, 0, tzinfo=timezone.utc),
        period_start=day_start,
        period_end=day_start + timedelta(days=1),
    )
    db_session.add(brief)
    db_session.flush()
    db_session.commit()

    detail_response = client.get(f"/brief/{brief.id}")
    assert detail_response.status_code == 200
    html = detail_response.text

    assert "14 Jun 2025" in html
    assert "13 Jun 2025" not in html


def test_today_refresh_period_uses_local_midnight(client, db_session):
    """POST /today/refresh creates a brief whose period_start is local-timezone midnight,
    not UTC midnight — matching the period boundaries used by the auto-brief scheduler.

    In a UTC-only deployment (the test default) both are identical; the test verifies
    that period_start is the start of the day (00:00:00) and period_end is 24h later.
    """
    from sqlalchemy import select as sa_select

    response = client.post("/today/refresh")
    assert response.status_code == 200

    pending = db_session.execute(
        sa_select(Brief).where(Brief.status == "pending")
    ).scalar_one_or_none()
    assert pending is not None
    assert pending.origin == "manual"
    assert pending.period_start.hour == 0
    assert pending.period_start.minute == 0
    assert pending.period_start.second == 0
    period_duration = (pending.period_end - pending.period_start).total_seconds()
    assert period_duration == 86400
