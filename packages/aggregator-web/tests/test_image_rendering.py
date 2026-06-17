"""Tests for image selection logic and template rendering.

Covers:
- _pick_topic_image unit tests (highest importance, recency tie-break,
  external refs ignored, empty refs, missing image_url)
- _attach_brief_images: single batched query for all refs across all briefs
- Thread card template: thumbnail rendered when image_url present, absent when not
- Thread detail template: hero rendered when image_url present, absent when not
- Brief detail template: topic image rendered when present, absent when not
- loading=lazy and onerror fallback attributes present on rendered images
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from aggregator_common.models import Article, Brief, BriefTopic, Source, Thread, ThreadMembership
from aggregator_common.state import ArticleStatus

_NOW = datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(session: Session, name: str = "Img Render Source") -> Source:
    src = Source(name=name, feed_url=f"https://{name.lower().replace(' ', '-')}.example.com/feed.xml")
    session.add(src)
    session.flush()
    session.commit()
    session.refresh(src)
    return src


def _make_article(
    session: Session,
    source_id: int,
    dedup_key: str,
    *,
    importance_score: int | None = None,
    header_image_url: str | None = None,
    published_at: datetime | None = None,
    title: str = "Test Article",
) -> Article:
    art = Article(
        source_id=source_id,
        dedup_key=dedup_key,
        status=ArticleStatus.ready,
        raw_payload={"link": f"https://example.com/{dedup_key}"},
        retrieved_at=_NOW,
        clean_title=title,
        feed_published_at=published_at or _NOW,
        importance_score=importance_score,
        header_image_url=header_image_url,
    )
    session.add(art)
    session.flush()
    session.commit()
    session.refresh(art)
    return art


def _make_thread(
    session: Session,
    *,
    title: str = "Test Thread",
    surfaced: bool = True,
    top_grade: int | None = 75,
    rolling_summary: str | None = None,
) -> Thread:
    thread = Thread(
        representative_title=title,
        first_seen=_NOW,
        last_updated=_NOW,
        status="active",
        surfaced=surfaced,
        top_grade=top_grade,
        source_list=[],
        known_facts=[],
        deltas=[],
        rolling_summary=rolling_summary,
    )
    session.add(thread)
    session.flush()
    session.commit()
    session.refresh(thread)
    return thread


def _make_membership(
    session: Session,
    thread_id: int,
    article_id: int,
    *,
    suppressed: bool = False,
) -> ThreadMembership:
    tm = ThreadMembership(
        thread_id=thread_id,
        article_id=article_id,
        suppressed=suppressed,
        assigned_at=_NOW,
    )
    session.add(tm)
    session.flush()
    session.commit()
    session.refresh(tm)
    return tm


def _make_brief(
    session: Session,
    *,
    headline: str = "Test Brief",
    status: str = "ready",
) -> Brief:
    day_start = _NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    brief = Brief(
        status=status,
        headline=headline,
        origin="auto",
        generated_at=_NOW if status == "ready" else None,
        period_start=day_start,
        period_end=day_start + timedelta(days=1),
    )
    session.add(brief)
    session.flush()
    session.commit()
    session.refresh(brief)
    return brief


def _make_brief_topic(
    session: Session,
    brief_id: int,
    *,
    headline: str = "Topic",
    topic_refs: list | None = None,
) -> BriefTopic:
    topic = BriefTopic(
        brief_id=brief_id,
        position=0,
        headline=headline,
        what_happened="Something happened.",
        why_it_matters="It matters.",
        topic_refs=topic_refs or [],
    )
    session.add(topic)
    session.flush()
    session.commit()
    session.refresh(topic)
    return topic


# ---------------------------------------------------------------------------
# Unit tests for _pick_topic_image
# ---------------------------------------------------------------------------


class TestPickTopicImage:
    def test_returns_none_for_empty_refs(self):
        from aggregator_web.app import _pick_topic_image

        assert _pick_topic_image([], {}) is None

    def test_returns_none_when_image_map_empty(self):
        from aggregator_web.app import _pick_topic_image

        refs = [{"internal": True, "article_id": 1}]
        assert _pick_topic_image(refs, {}) is None

    def test_ignores_external_refs(self):
        from aggregator_web.app import _pick_topic_image

        refs = [{"internal": False, "article_id": 1, "url": "https://external.example.com"}]
        image_map = {
            1: {
                "header_image_url": "https://example.com/ext.jpg",
                "importance_score": 90,
                "feed_published_at": None,
            }
        }
        assert _pick_topic_image(refs, image_map) is None

    def test_picks_highest_importance_article(self):
        from aggregator_web.app import _pick_topic_image

        refs = [
            {"internal": True, "article_id": 1},
            {"internal": True, "article_id": 2},
        ]
        image_map = {
            1: {"header_image_url": "https://example.com/low.jpg", "importance_score": 20, "feed_published_at": None},
            2: {"header_image_url": "https://example.com/high.jpg", "importance_score": 90, "feed_published_at": None},
        }
        assert _pick_topic_image(refs, image_map) == "https://example.com/high.jpg"

    def test_recency_tiebreak_when_importance_equal(self):
        from aggregator_web.app import _pick_topic_image

        refs = [
            {"internal": True, "article_id": 1},
            {"internal": True, "article_id": 2},
        ]
        image_map = {
            1: {
                "header_image_url": "https://example.com/older.jpg",
                "importance_score": 50,
                "feed_published_at": _NOW - timedelta(hours=5),
            },
            2: {
                "header_image_url": "https://example.com/newer.jpg",
                "importance_score": 50,
                "feed_published_at": _NOW - timedelta(hours=1),
            },
        }
        assert _pick_topic_image(refs, image_map) == "https://example.com/newer.jpg"

    def test_returns_none_when_article_has_no_image(self):
        from aggregator_web.app import _pick_topic_image

        refs = [{"internal": True, "article_id": 1}]
        image_map = {
            1: {"header_image_url": None, "importance_score": 80, "feed_published_at": None}
        }
        assert _pick_topic_image(refs, image_map) is None

    def test_returns_none_for_empty_string_image(self):
        from aggregator_web.app import _pick_topic_image

        refs = [{"internal": True, "article_id": 1}]
        image_map = {
            1: {"header_image_url": "", "importance_score": 80, "feed_published_at": None}
        }
        assert _pick_topic_image(refs, image_map) is None


# ---------------------------------------------------------------------------
# _attach_brief_images batches to a single query
# ---------------------------------------------------------------------------


class TestAttachBriefImagesBatching:
    def test_issues_single_query_for_multiple_briefs(self, db_session):
        """_attach_brief_images issues one DB query regardless of brief/topic count."""
        from aggregator_web.app import _attach_brief_images

        src = Source(name="Batch Query Source", feed_url="https://bqs.example.com/feed.xml")
        db_session.add(src)
        db_session.flush()

        a1 = Article(
            source_id=src.id, dedup_key="bqs-art-1", status="ready",
            raw_payload={}, retrieved_at=_NOW,
            header_image_url="https://example.com/bqs1.jpg",
            importance_score=80,
        )
        a2 = Article(
            source_id=src.id, dedup_key="bqs-art-2", status="ready",
            raw_payload={}, retrieved_at=_NOW,
            header_image_url="https://example.com/bqs2.jpg",
            importance_score=70,
        )
        db_session.add_all([a1, a2])
        db_session.flush()
        db_session.commit()
        db_session.refresh(a1)
        db_session.refresh(a2)

        # Use SimpleNamespace to avoid triggering ORM lazy-loads during _attach_brief_images
        topic1 = SimpleNamespace(topic_refs=[{"internal": True, "article_id": a1.id}])
        topic2 = SimpleNamespace(topic_refs=[{"internal": True, "article_id": a2.id}])
        brief1 = SimpleNamespace(topics=[topic1])
        brief2 = SimpleNamespace(topics=[topic2])

        execute_calls = []
        original_execute = db_session.execute

        def counting_execute(*args, **kwargs):
            execute_calls.append(1)
            return original_execute(*args, **kwargs)

        db_session.execute = counting_execute
        try:
            _attach_brief_images([brief1, brief2], db_session)
        finally:
            db_session.execute = original_execute

        # One query for all article ids across both briefs
        assert len(execute_calls) == 1

    def test_empty_refs_produces_no_query(self, db_session):
        """_attach_brief_images issues no query when no refs exist."""
        from aggregator_web.app import _attach_brief_images

        topic = SimpleNamespace(topic_refs=[])
        brief = SimpleNamespace(topics=[topic])

        execute_calls = []
        original_execute = db_session.execute

        def counting_execute(*args, **kwargs):
            execute_calls.append(1)
            return original_execute(*args, **kwargs)

        db_session.execute = counting_execute
        try:
            _attach_brief_images([brief], db_session)
        finally:
            db_session.execute = original_execute

        assert len(execute_calls) == 0

    def test_attaches_correct_images_to_topics(self, db_session):
        """Images are correctly assigned to the right topics across multiple briefs."""
        from aggregator_web.app import _attach_brief_images

        src = Source(name="Attach Img Source", feed_url="https://ais.example.com/feed.xml")
        db_session.add(src)
        db_session.flush()

        a1 = Article(
            source_id=src.id, dedup_key="ais-art-1", status="ready",
            raw_payload={}, retrieved_at=_NOW,
            header_image_url="https://example.com/ais1.jpg",
            importance_score=80,
        )
        a2 = Article(
            source_id=src.id, dedup_key="ais-art-2", status="ready",
            raw_payload={}, retrieved_at=_NOW,
            header_image_url="https://example.com/ais2.jpg",
            importance_score=70,
        )
        db_session.add_all([a1, a2])
        db_session.flush()
        db_session.commit()
        db_session.refresh(a1)
        db_session.refresh(a2)

        topic1 = SimpleNamespace(topic_refs=[{"internal": True, "article_id": a1.id}])
        topic2 = SimpleNamespace(topic_refs=[{"internal": True, "article_id": a2.id}])
        brief1 = SimpleNamespace(topics=[topic1])
        brief2 = SimpleNamespace(topics=[topic2])

        _attach_brief_images([brief1, brief2], db_session)

        assert topic1.image_url == "https://example.com/ais1.jpg"
        assert topic2.image_url == "https://example.com/ais2.jpg"

    def test_topic_image_none_when_no_internal_refs(self, db_session):
        """topic.image_url is None when topics have only external refs (no hero image is set)."""
        from aggregator_web.app import _attach_brief_images

        topic = SimpleNamespace(
            topic_refs=[{"internal": False, "url": "https://external.example.com/article", "title": "Ext"}]
        )
        brief = SimpleNamespace(topics=[topic])

        _attach_brief_images([brief], db_session)

        assert topic.image_url is None


# ---------------------------------------------------------------------------
# Thread card template rendering
# ---------------------------------------------------------------------------


class TestThreadCardImageRendering:
    def test_thread_card_shows_card_image_when_image_url_set(self, client, db_session):
        src = _make_source(db_session, "Card Image Source")
        thread = _make_thread(db_session, title="Thread With Image")
        art = _make_article(
            db_session, src.id, "card-img-1",
            importance_score=80,
            header_image_url="https://example.com/card-img.jpg",
        )
        _make_membership(db_session, thread.id, art.id)

        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        html = response.text
        assert "card-image" in html
        assert "https://example.com/card-img.jpg" in html

    def test_thread_card_no_image_container_when_image_url_absent(self, client, db_session):
        _make_thread(db_session, title="Thread No Image")

        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "card-image" not in response.text

    def test_thread_card_image_has_loading_lazy(self, client, db_session):
        src = _make_source(db_session, "Lazy Image Source")
        thread = _make_thread(db_session, title="Lazy Load Image Thread")
        art = _make_article(
            db_session, src.id, "lazy-img-1",
            importance_score=80,
            header_image_url="https://example.com/lazy-img.jpg",
        )
        _make_membership(db_session, thread.id, art.id)

        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "loading=\"lazy\"" in response.text

    def test_thread_card_image_has_onerror_fallback(self, client, db_session):
        src = _make_source(db_session, "Onerror Image Source")
        thread = _make_thread(db_session, title="Onerror Image Thread")
        art = _make_article(
            db_session, src.id, "onerror-img-1",
            importance_score=80,
            header_image_url="https://example.com/onerror-img.jpg",
        )
        _make_membership(db_session, thread.id, art.id)

        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "onerror" in response.text
        assert "remove()" in response.text


# ---------------------------------------------------------------------------
# Thread detail template rendering
# ---------------------------------------------------------------------------


class TestThreadDetailImageRendering:
    def test_thread_detail_shows_hero_when_image_url_set(self, client, db_session):
        src = _make_source(db_session, "Detail Hero Source")
        thread = _make_thread(db_session, title="Thread With Hero")
        art = _make_article(
            db_session, src.id, "detail-hero-1",
            importance_score=80,
            header_image_url="https://example.com/hero.jpg",
        )
        _make_membership(db_session, thread.id, art.id)

        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        html = response.text
        assert "detail-hero" in html
        assert "https://example.com/hero.jpg" in html

    def test_thread_detail_no_hero_when_image_url_absent(self, client, db_session):
        thread = _make_thread(db_session, title="Thread No Hero")

        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "detail-hero" not in response.text

    def test_thread_detail_hero_has_loading_lazy(self, client, db_session):
        src = _make_source(db_session, "Hero Lazy Source")
        thread = _make_thread(db_session, title="Hero Lazy Thread")
        art = _make_article(
            db_session, src.id, "hero-lazy-1",
            importance_score=80,
            header_image_url="https://example.com/hero-lazy.jpg",
        )
        _make_membership(db_session, thread.id, art.id)

        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "loading=\"lazy\"" in response.text

    def test_thread_detail_hero_has_onerror_removes_container(self, client, db_session):
        src = _make_source(db_session, "Hero Onerror Source")
        thread = _make_thread(db_session, title="Hero Onerror Thread")
        art = _make_article(
            db_session, src.id, "hero-onerror-1",
            importance_score=80,
            header_image_url="https://example.com/hero-onerror.jpg",
        )
        _make_membership(db_session, thread.id, art.id)

        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        # onerror must reference closest('.detail-hero').remove() to clear the container
        assert "detail-hero" in response.text
        assert "onerror" in response.text
        assert "remove()" in response.text

    def test_thread_detail_suppressed_article_image_not_shown(self, client, db_session):
        src = _make_source(db_session, "Supp Hero Source")
        thread = _make_thread(db_session, title="Suppressed Article Hero Thread")
        suppressed_art = _make_article(
            db_session, src.id, "supp-hero-1",
            importance_score=99,
            header_image_url="https://example.com/suppressed-hero.jpg",
        )
        _make_membership(db_session, thread.id, suppressed_art.id, suppressed=True)

        response = client.get(f"/threads/{thread.id}", headers={"HX-Request": "true"})
        assert response.status_code == 200
        # The suppressed article's image must not appear as the hero
        assert "https://example.com/suppressed-hero.jpg" not in response.text


# ---------------------------------------------------------------------------
# Brief detail template rendering
# ---------------------------------------------------------------------------


class TestBriefDetailImageRendering:
    def test_brief_detail_shows_topic_image_when_present(self, client, db_session):
        src = _make_source(db_session, "Brief Topic Img Source")
        art = _make_article(
            db_session, src.id, "brief-topic-img-1",
            importance_score=80,
            header_image_url="https://example.com/topic-img.jpg",
        )
        brief = _make_brief(db_session, headline="Brief With Topic Image")
        _make_brief_topic(
            db_session,
            brief.id,
            headline="Topic With Image",
            topic_refs=[{"internal": True, "article_id": art.id, "title": "Ref Article"}],
        )

        response = client.get(f"/brief/{brief.id}")
        assert response.status_code == 200
        html = response.text
        assert "today-topic-image" in html
        assert "https://example.com/topic-img.jpg" in html

    def test_brief_detail_no_topic_image_when_refs_external(self, client, db_session):
        brief = _make_brief(db_session, headline="Brief External Refs Only")
        _make_brief_topic(
            db_session,
            brief.id,
            headline="External Ref Topic",
            topic_refs=[{"internal": False, "url": "https://external.example.com", "title": "Ext"}],
        )

        response = client.get(f"/brief/{brief.id}")
        assert response.status_code == 200
        assert "today-topic-image" not in response.text

    def test_brief_detail_no_topic_image_when_no_refs(self, client, db_session):
        brief = _make_brief(db_session, headline="Brief No Refs")
        _make_brief_topic(db_session, brief.id, headline="No Refs Topic", topic_refs=[])

        response = client.get(f"/brief/{brief.id}")
        assert response.status_code == 200
        assert "today-topic-image" not in response.text

    def test_brief_detail_topic_image_has_loading_lazy(self, client, db_session):
        src = _make_source(db_session, "Brief Img Lazy Source")
        art = _make_article(
            db_session, src.id, "brief-lazy-1",
            importance_score=80,
            header_image_url="https://example.com/brief-lazy.jpg",
        )
        brief = _make_brief(db_session, headline="Brief Lazy Image")
        _make_brief_topic(
            db_session,
            brief.id,
            topic_refs=[{"internal": True, "article_id": art.id, "title": "Ref"}],
        )

        response = client.get(f"/brief/{brief.id}")
        assert response.status_code == 200
        assert "loading=\"lazy\"" in response.text

    def test_brief_detail_topic_image_has_onerror_fallback(self, client, db_session):
        src = _make_source(db_session, "Brief Onerror Source")
        art = _make_article(
            db_session, src.id, "brief-onerror-1",
            importance_score=80,
            header_image_url="https://example.com/brief-onerror.jpg",
        )
        brief = _make_brief(db_session, headline="Brief Onerror")
        _make_brief_topic(
            db_session,
            brief.id,
            topic_refs=[{"internal": True, "article_id": art.id, "title": "Ref"}],
        )

        response = client.get(f"/brief/{brief.id}")
        assert response.status_code == 200
        assert "onerror" in response.text
        assert "remove()" in response.text

    def test_brief_detail_no_hero_when_no_images(self, client, db_session):
        brief = _make_brief(db_session, headline="Brief No Hero")
        _make_brief_topic(db_session, brief.id, headline="No Image Topic", topic_refs=[])

        response = client.get(f"/brief/{brief.id}")
        assert response.status_code == 200
        assert "detail-hero" not in response.text


# ---------------------------------------------------------------------------
# Thread card visual consistency with article card style
# ---------------------------------------------------------------------------


class TestThreadCardVisualConsistency:
    """Thread list card matches article card style: full-width card-image + rolling_summary snippet."""

    def test_thread_card_uses_card_image_not_thread_thumb(self, client, db_session):
        src = _make_source(db_session, "Style Img Source")
        thread = _make_thread(db_session, title="Thread Style Card Image")
        art = _make_article(
            db_session, src.id, "style-img-1",
            importance_score=80,
            header_image_url="https://example.com/style-img.jpg",
        )
        _make_membership(db_session, thread.id, art.id)

        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        html = response.text
        assert "card-image" in html
        assert "thread-thumb" not in html

    def test_thread_card_no_image_no_card_image_container(self, client, db_session):
        _make_thread(db_session, title="Thread No Image Container")

        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "card-image" not in response.text

    def test_thread_card_shows_rolling_summary_as_card_excerpt(self, client, db_session):
        _make_thread(
            db_session,
            title="Thread With Rolling Summary",
            rolling_summary="This is the rolling summary of the thread story.",
        )

        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        html = response.text
        assert "card-excerpt" in html
        assert "This is the rolling summary" in html

    def test_thread_card_no_excerpt_when_rolling_summary_absent(self, client, db_session):
        _make_thread(db_session, title="Thread Without Summary")

        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        assert "card-excerpt" not in response.text

    def test_thread_card_image_is_positioned_before_card_body(self, client, db_session):
        src = _make_source(db_session, "Image Order Source")
        thread = _make_thread(db_session, title="Thread Image Order")
        art = _make_article(
            db_session, src.id, "img-order-1",
            importance_score=80,
            header_image_url="https://example.com/img-order.jpg",
        )
        _make_membership(db_session, thread.id, art.id)

        response = client.get("/threads", headers={"HX-Request": "true"})
        assert response.status_code == 200
        html = response.text
        # card-image must appear before card-body in the markup
        assert html.index("card-image") < html.index("card-body")
