from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Generator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

import aggregator_common.queries as queries
from aggregator_common.models import Article, Brief, BriefTopic, Category, InterestProfile, Source, Thread, ThreadMembership
from aggregator_common.state import ArticleStatus

_NOW = datetime.now(tz=timezone.utc)
_TODAY_START = _NOW.replace(hour=0, minute=0, second=0, microsecond=0)
_YESTERDAY = _TODAY_START - timedelta(days=1)


@pytest.fixture
def session(db_session_factory) -> Generator[Session, None, None]:
    s = db_session_factory()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _make_source(session: Session, suffix: str = "") -> Source:
    src = Source(
        name=f"QTest Source{suffix}",
        feed_url=f"https://qtest{suffix}.example.com/feed.xml",
    )
    session.add(src)
    session.flush()
    return src


def _make_ready_article(
    session: Session,
    source_id: int,
    dedup_key: str,
    *,
    title: str = "Test Article",
    importance_score: int | None = None,
    is_read: bool = False,
    is_saved: bool = False,
    is_hidden: bool = False,
    categories: list | None = None,
    feed_published_at: datetime | None = None,
    raw_payload: dict | None = None,
) -> Article:
    article = Article(
        source_id=source_id,
        dedup_key=dedup_key,
        status=ArticleStatus.ready,
        clean_title=title,
        importance_score=importance_score,
        is_read=is_read,
        is_saved=is_saved,
        is_hidden=is_hidden,
        categories=categories,
        feed_published_at=feed_published_at or _NOW,
        raw_payload=raw_payload or {"link": f"https://example.com/{dedup_key}"},
        retrieved_at=_NOW,
    )
    session.add(article)
    session.flush()
    return article


def _index_article(session: Session, article_id: int, text_content: str) -> None:
    session.execute(
        text(
            "UPDATE articles SET search_vector = to_tsvector('english', :txt) WHERE id = :id"
        ),
        {"txt": text_content, "id": article_id},
    )
    session.flush()


class TestListArticlesViews:
    def test_all_returns_ready_articles(self, session: Session):
        src = _make_source(session, "-all")
        a1 = _make_ready_article(session, src.id, "all-1")
        a2 = _make_ready_article(session, src.id, "all-2")

        results = queries.list_articles(session, "all")
        ids = {r.id for r in results}

        assert a1.id in ids
        assert a2.id in ids

    def test_all_excludes_hidden_articles(self, session: Session):
        src = _make_source(session, "-allhid")
        hidden = _make_ready_article(session, src.id, "allhid-hidden", is_hidden=True)
        visible = _make_ready_article(session, src.id, "allhid-visible")

        results = queries.list_articles(session, "all")
        ids = {r.id for r in results}

        assert visible.id in ids
        assert hidden.id not in ids

    def test_unread_returns_only_unread(self, session: Session):
        src = _make_source(session, "-unread")
        unread = _make_ready_article(session, src.id, "unread-no", is_read=False)
        read = _make_ready_article(session, src.id, "unread-yes", is_read=True)

        results = queries.list_articles(session, "unread")
        ids = {r.id for r in results}

        assert unread.id in ids
        assert read.id not in ids

    def test_important_returns_high_score_only(self, session: Session):
        src = _make_source(session, "-imp")
        high = _make_ready_article(session, src.id, "imp-high", importance_score=85)
        low = _make_ready_article(session, src.id, "imp-low", importance_score=30)
        no_score = _make_ready_article(session, src.id, "imp-none")

        results = queries.list_articles(session, "important", important_threshold=70)
        ids = {r.id for r in results}

        assert high.id in ids
        assert low.id not in ids
        assert no_score.id not in ids

    def test_saved_returns_only_saved(self, session: Session):
        src = _make_source(session, "-saved")
        saved = _make_ready_article(session, src.id, "saved-yes", is_saved=True)
        unsaved = _make_ready_article(session, src.id, "saved-no", is_saved=False)

        results = queries.list_articles(session, "saved")
        ids = {r.id for r in results}

        assert saved.id in ids
        assert unsaved.id not in ids

    def test_uncategorized_returns_no_category_and_empty_array(self, session: Session):
        src = _make_source(session, "-uncat")
        no_cat = _make_ready_article(session, src.id, "uncat-none", categories=None)
        empty_cat = _make_ready_article(session, src.id, "uncat-empty", categories=[])
        has_cat = _make_ready_article(session, src.id, "uncat-has", categories=["tech"])

        results = queries.list_articles(session, "uncategorized")
        ids = {r.id for r in results}

        assert no_cat.id in ids
        assert empty_cat.id in ids
        assert has_cat.id not in ids

    def test_today_returns_only_today_articles(self, session: Session):
        src = _make_source(session, "-today")
        today_art = _make_ready_article(
            session, src.id, "today-yes",
            feed_published_at=_TODAY_START + timedelta(hours=6),
        )
        yesterday_art = _make_ready_article(
            session, src.id, "today-no",
            feed_published_at=_YESTERDAY,
        )

        results = queries.list_articles(session, "today")
        ids = {r.id for r in results}

        assert today_art.id in ids
        assert yesterday_art.id not in ids

    def test_unknown_view_raises_value_error(self, session: Session):
        with pytest.raises(ValueError, match="Unknown view"):
            queries.list_articles(session, "bogus_view")

    def test_category_filter(self, session: Session):
        src = _make_source(session, "-catf")
        tech = _make_ready_article(session, src.id, "catf-tech", categories=["tech"])
        news = _make_ready_article(session, src.id, "catf-news", categories=["news"])

        results = queries.list_articles(session, "all", category="tech")
        ids = {r.id for r in results}

        assert tech.id in ids
        assert news.id not in ids

    def test_source_id_filter(self, session: Session):
        src1 = _make_source(session, "-srcf1")
        src2 = _make_source(session, "-srcf2")
        a1 = _make_ready_article(session, src1.id, "srcf-a1")
        a2 = _make_ready_article(session, src2.id, "srcf-a2")

        results = queries.list_articles(session, "all", source_id=src1.id)
        ids = {r.id for r in results}

        assert a1.id in ids
        assert a2.id not in ids

    def test_unread_only_flag(self, session: Session):
        src = _make_source(session, "-uo")
        unread = _make_ready_article(session, src.id, "uo-unread", is_read=False)
        read = _make_ready_article(session, src.id, "uo-read", is_read=True)

        results = queries.list_articles(session, "all", unread_only=True)
        ids = {r.id for r in results}

        assert unread.id in ids
        assert read.id not in ids

    def test_empty_view_returns_list(self, session: Session):
        results = queries.list_articles(session, "saved")
        assert isinstance(results, list)


class TestSearchArticles:
    def test_matching_articles_returned(self, session: Session):
        src = _make_source(session, "-srch")
        article = _make_ready_article(session, src.id, "srch-match")
        _index_article(session, article.id, "quantum computing breakthrough research")

        results = queries.search_articles(session, "quantum computing")
        ids = {r.id for r in results}

        assert article.id in ids

    def test_no_match_returns_empty(self, session: Session):
        src = _make_source(session, "-srchempty")
        article = _make_ready_article(session, src.id, "srch-nomatch")
        _index_article(session, article.id, "cooking recipes food")

        results = queries.search_articles(session, "zzxnomatchxyz")

        assert results == []

    def test_category_filter(self, session: Session):
        src = _make_source(session, "-srchcat")
        tech = _make_ready_article(session, src.id, "srchcat-t", categories=["tech"])
        news = _make_ready_article(session, src.id, "srchcat-n", categories=["news"])
        for art in [tech, news]:
            _index_article(session, art.id, "python programming language")

        results = queries.search_articles(session, "python programming", category="tech")
        ids = {r.id for r in results}

        assert tech.id in ids
        assert news.id not in ids

    def test_source_id_filter(self, session: Session):
        src1 = _make_source(session, "-srchsrc1")
        src2 = _make_source(session, "-srchsrc2")
        a1 = _make_ready_article(session, src1.id, "srchsrc-a1")
        a2 = _make_ready_article(session, src2.id, "srchsrc-a2")
        for art in [a1, a2]:
            _index_article(session, art.id, "machine learning artificial intelligence")

        results = queries.search_articles(session, "machine learning", source_id=src1.id)
        ids = {r.id for r in results}

        assert a1.id in ids
        assert a2.id not in ids

    def test_since_filter(self, session: Session):
        src = _make_source(session, "-srchsince")
        recent = _make_ready_article(
            session, src.id, "srchsince-new", feed_published_at=_NOW
        )
        old = _make_ready_article(
            session, src.id, "srchsince-old",
            feed_published_at=_NOW - timedelta(days=14),
        )
        for art in [recent, old]:
            _index_article(session, art.id, "blockchain distributed ledger")

        since = _NOW - timedelta(days=1)
        results = queries.search_articles(session, "blockchain distributed", since=since)
        ids = {r.id for r in results}

        assert recent.id in ids
        assert old.id not in ids


class TestGetArticle:
    def test_happy_path_resolves_source_name(self, session: Session):
        src = _make_source(session, "-getart")
        article = _make_ready_article(
            session, src.id, "getart-1",
            title="Get This One",
            raw_payload={"link": "https://example.com/getart-1"},
        )

        result = queries.get_article(session, article.id)

        assert result.id == article.id
        assert result.title == "Get This One"
        assert result.url == "https://example.com/getart-1"
        assert result.source_name == src.name

    def test_unknown_id_raises_value_error(self, session: Session):
        with pytest.raises(ValueError, match="not found"):
            queries.get_article(session, 999_999_001)


class TestGetInterestProfile:
    def test_returns_empty_string_when_no_row(self, session: Session):
        existing = session.get(InterestProfile, True)
        if existing:
            session.delete(existing)
            session.flush()

        result = queries.get_interest_profile(session)

        assert result == ""

    def test_returns_profile_text_when_set(self, session: Session):
        existing = session.get(InterestProfile, True)
        if existing:
            session.delete(existing)
            session.flush()

        profile = InterestProfile(profile_text="I enjoy technology and science news.")
        session.add(profile)
        session.flush()

        result = queries.get_interest_profile(session)

        assert result == "I enjoy technology and science news."


class TestListCategoriesAndSources:
    def test_list_categories_returns_only_enabled(self, session: Session):
        enabled = Category(name="q-enabled-cat", enabled=True, sort_order=10)
        disabled = Category(name="q-disabled-cat", enabled=False, sort_order=11)
        session.add_all([enabled, disabled])
        session.flush()

        results = queries.list_categories(session)
        names = {r.name for r in results}

        assert "q-enabled-cat" in names
        assert "q-disabled-cat" not in names

    def test_list_sources_returns_only_enabled(self, session: Session):
        enabled_src = Source(
            name="q-enabled-src",
            feed_url="https://q-enabled-src.example.com/feed.xml",
            enabled=True,
        )
        disabled_src = Source(
            name="q-disabled-src",
            feed_url="https://q-disabled-src.example.com/feed.xml",
            enabled=False,
        )
        session.add_all([enabled_src, disabled_src])
        session.flush()

        results = queries.list_sources(session)
        names = {r.name for r in results}

        assert "q-enabled-src" in names
        assert "q-disabled-src" not in names


class TestMutationHelpers:
    def test_mark_read_sets_is_read_true(self, session: Session):
        src = _make_source(session, "-mr")
        article = _make_ready_article(session, src.id, "mr-art1", is_read=False)

        result = queries.mark_read(session, article.id)

        assert result["is_read"] is True

    def test_mark_unread_sets_is_read_false(self, session: Session):
        src = _make_source(session, "-mu")
        article = _make_ready_article(session, src.id, "mu-art1", is_read=True)

        result = queries.mark_unread(session, article.id)

        assert result["is_read"] is False

    def test_save_article_sets_is_saved_true(self, session: Session):
        src = _make_source(session, "-sv")
        article = _make_ready_article(session, src.id, "sv-art1", is_saved=False)

        result = queries.save_article(session, article.id)

        assert result["is_saved"] is True

    def test_unsave_article_sets_is_saved_false(self, session: Session):
        src = _make_source(session, "-us")
        article = _make_ready_article(session, src.id, "us-art1", is_saved=True)

        result = queries.unsave_article(session, article.id)

        assert result["is_saved"] is False

    def test_mark_read_unknown_id_raises(self, session: Session):
        with pytest.raises(ValueError, match="not found"):
            queries.mark_read(session, 999_999_002)

    def test_mark_unread_unknown_id_raises(self, session: Session):
        with pytest.raises(ValueError, match="not found"):
            queries.mark_unread(session, 999_999_003)

    def test_save_article_unknown_id_raises(self, session: Session):
        with pytest.raises(ValueError, match="not found"):
            queries.save_article(session, 999_999_004)

    def test_unsave_article_unknown_id_raises(self, session: Session):
        with pytest.raises(ValueError, match="not found"):
            queries.unsave_article(session, 999_999_005)


_BRIEF_PERIOD_START = _TODAY_START
_BRIEF_PERIOD_END = _TODAY_START + timedelta(days=1)


def _make_brief(
    session: Session,
    *,
    status: str = "ready",
    headline: str = "Brief Headline",
    intro: str = "Brief intro.",
    model: str = "gpt-4.1",
    generated_at: datetime | None = None,
) -> Brief:
    brief = Brief(
        status=status,
        origin="manual",
        period_start=_BRIEF_PERIOD_START,
        period_end=_BRIEF_PERIOD_END,
        headline=headline,
        intro=intro,
        model=model,
        generated_at=generated_at or _NOW,
    )
    session.add(brief)
    session.flush()
    return brief


def _make_brief_topic(
    session: Session,
    brief_id: int,
    position: int,
    *,
    headline: str = "Topic Headline",
    what_happened: str = "Something happened.",
    why_it_matters: str = "It matters because.",
    historical_context: str | None = None,
    refs: list | None = None,
) -> BriefTopic:
    topic = BriefTopic(
        brief_id=brief_id,
        position=position,
        headline=headline,
        what_happened=what_happened,
        why_it_matters=why_it_matters,
        historical_context=historical_context,
        topic_refs=refs or [],
    )
    session.add(topic)
    session.flush()
    return topic


class TestBriefQueries:
    def test_get_latest_brief_returns_none_when_no_ready_brief(self, session: Session):
        result = queries.get_latest_brief(session)
        assert result is None

    def test_get_latest_brief_returns_ready_brief_with_topics(self, session: Session):
        brief = _make_brief(session, headline="Today's Brief", intro="An intro.")
        _make_brief_topic(
            session,
            brief.id,
            1,
            headline="Topic A",
            what_happened="A happened.",
            why_it_matters="A matters.",
            refs=[{"article_id": 1, "title": "Ref Article"}],
        )
        _make_brief_topic(
            session,
            brief.id,
            2,
            headline="Topic B",
            what_happened="B happened.",
            why_it_matters="B matters.",
            historical_context="B background.",
        )

        result = queries.get_latest_brief(session)

        assert result is not None
        assert result.id == brief.id
        assert result.headline == "Today's Brief"
        assert result.intro == "An intro."
        assert result.model == "gpt-4.1"
        assert result.generated_at is not None
        assert len(result.topics) == 2
        assert result.topics[0].position == 1
        assert result.topics[0].headline == "Topic A"
        assert result.topics[0].refs == [{"article_id": 1, "title": "Ref Article"}]
        assert result.topics[1].position == 2
        assert result.topics[1].historical_context == "B background."

    def test_get_latest_brief_topics_ordered_by_position(self, session: Session):
        brief = _make_brief(session)
        _make_brief_topic(session, brief.id, 3, headline="Third")
        _make_brief_topic(session, brief.id, 1, headline="First")
        _make_brief_topic(session, brief.id, 2, headline="Second")

        result = queries.get_latest_brief(session)

        assert result is not None
        positions = [t.position for t in result.topics]
        assert positions == [1, 2, 3]

    def test_get_latest_brief_ignores_non_ready_briefs(self, session: Session):
        _make_brief(session, status="pending", headline="Pending Brief")
        _make_brief(session, status="failed", headline="Failed Brief")

        result = queries.get_latest_brief(session)
        assert result is None

    def test_enqueue_brief_inserts_when_none_pending(self, session: Session):
        result = queries.enqueue_brief(session)
        assert result == {"status": "queued"}

    def test_enqueue_brief_returns_already_pending_when_pending_exists(self, session: Session):
        _make_brief(session, status="pending", headline="In-flight Brief")

        result = queries.enqueue_brief(session)
        assert result == {"status": "already_pending"}

    def test_enqueue_brief_returns_already_pending_when_generating_exists(self, session: Session):
        _make_brief(session, status="generating", headline="Generating Brief")

        result = queries.enqueue_brief(session)
        assert result == {"status": "already_pending"}


def _make_thread(
    session: Session,
    *,
    title: str = "Test Thread",
    source_list: list | None = None,
) -> Thread:
    now = _NOW
    thread = Thread(
        representative_title=title,
        first_seen=now,
        last_updated=now,
        status="active",
        source_list=source_list,
        known_facts=[],
        deltas=[],
    )
    session.add(thread)
    session.flush()
    return thread


def _make_thread_membership(
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
    return tm


class TestListThreads:
    def test_source_count_matches_source_list_length(self, session: Session):
        thread = _make_thread(
            session,
            title="Thread With Sources",
            source_list=["Source A", "Source B", "Source C"],
        )

        results = queries.list_threads(session)
        ids = {r.id: r for r in results}

        assert thread.id in ids
        assert ids[thread.id].source_count == 3

    def test_source_count_is_zero_when_source_list_is_none(self, session: Session):
        thread = _make_thread(session, title="Thread No Sources", source_list=None)

        results = queries.list_threads(session)
        ids = {r.id: r for r in results}

        assert thread.id in ids
        assert ids[thread.id].source_count == 0

    def test_member_count_excludes_suppressed_rows(self, session: Session):
        src = _make_source(session, "-tlt2")
        thread = _make_thread(session, title="Thread With Members")
        article_active = _make_ready_article(session, src.id, "tlt2-active")
        article_suppressed = _make_ready_article(session, src.id, "tlt2-suppressed")
        _make_thread_membership(session, thread.id, article_active.id, suppressed=False)
        _make_thread_membership(session, thread.id, article_suppressed.id, suppressed=True)

        results = queries.list_threads(session)
        ids = {r.id: r for r in results}

        assert thread.id in ids
        assert ids[thread.id].member_count == 1

    def test_member_count_is_zero_when_no_memberships(self, session: Session):
        thread = _make_thread(session, title="Thread No Members")

        results = queries.list_threads(session)
        ids = {r.id: r for r in results}

        assert thread.id in ids
        assert ids[thread.id].member_count == 0

    def test_member_count_counts_all_active_members(self, session: Session):
        src = _make_source(session, "-tlt3")
        thread = _make_thread(session, title="Thread Multi Members")
        for i in range(3):
            article = _make_ready_article(session, src.id, f"tlt3-art{i}")
            _make_thread_membership(session, thread.id, article.id, suppressed=False)

        results = queries.list_threads(session)
        ids = {r.id: r for r in results}

        assert thread.id in ids
        assert ids[thread.id].member_count == 3

    def test_max_age_days_excludes_thread_older_than_cutoff(self, session: Session):
        old_time = datetime.now(tz=timezone.utc) - timedelta(days=8)
        old_thread = Thread(
            representative_title="Old Thread",
            first_seen=old_time,
            last_updated=old_time,
            status="active",
            known_facts=[],
            deltas=[],
        )
        session.add(old_thread)
        session.flush()

        results = queries.list_threads(session, max_age_days=7)
        ids = {r.id for r in results}

        assert old_thread.id not in ids

    def test_max_age_days_includes_thread_within_cutoff(self, session: Session):
        recent_time = datetime.now(tz=timezone.utc) - timedelta(days=6)
        recent_thread = Thread(
            representative_title="Recent Thread",
            first_seen=recent_time,
            last_updated=recent_time,
            status="active",
            known_facts=[],
            deltas=[],
        )
        session.add(recent_thread)
        session.flush()

        results = queries.list_threads(session, max_age_days=7)
        ids = {r.id for r in results}

        assert recent_thread.id in ids

    def test_max_age_days_none_returns_all_threads(self, session: Session):
        old_time = datetime.now(tz=timezone.utc) - timedelta(days=60)
        old_thread = Thread(
            representative_title="Very Old Thread",
            first_seen=old_time,
            last_updated=old_time,
            status="active",
            known_facts=[],
            deltas=[],
        )
        session.add(old_thread)
        session.flush()

        results = queries.list_threads(session, max_age_days=None)
        ids = {r.id for r in results}

        assert old_thread.id in ids
