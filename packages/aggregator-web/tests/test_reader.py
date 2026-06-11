"""Tests for aggregator_web.reader — unit tests using a live Postgres container."""

from __future__ import annotations

import pytest

from aggregator_common.models import Article
from aggregator_web.reader import (
    ArticleNotFoundError,
    FeedSpec,
    mark_all_read,
    mark_read,
    mark_unread,
    save,
    unsave,
)
from conftest import make_article, make_source


# ---------------------------------------------------------------------------
# mark_read / mark_unread
# ---------------------------------------------------------------------------


def test_mark_read_sets_flag(db_session):
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_read=False)
    result = mark_read(db_session, article.id)
    assert result.is_read is True
    assert result.read_at is not None


def test_mark_read_then_mark_unread(db_session):
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_read=False)
    mark_read(db_session, article.id)
    result = mark_unread(db_session, article.id)
    assert result.is_read is False
    assert result.read_at is None


def test_mark_read_not_found(db_session):
    with pytest.raises(ArticleNotFoundError):
        mark_read(db_session, 99999)


def test_mark_unread_not_found(db_session):
    with pytest.raises(ArticleNotFoundError):
        mark_unread(db_session, 99999)


# ---------------------------------------------------------------------------
# save / unsave
# ---------------------------------------------------------------------------


def test_save_sets_flag(db_session):
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_saved=False)
    result = save(db_session, article.id)
    assert result.is_saved is True


def test_save_then_unsave(db_session):
    src = make_source(db_session)
    article = make_article(db_session, source_id=src.id, is_saved=False)
    save(db_session, article.id)
    result = unsave(db_session, article.id)
    assert result.is_saved is False


def test_save_not_found(db_session):
    with pytest.raises(ArticleNotFoundError):
        save(db_session, 99999)


def test_unsave_not_found(db_session):
    with pytest.raises(ArticleNotFoundError):
        unsave(db_session, 99999)


# ---------------------------------------------------------------------------
# mark_all_read — smart views
# ---------------------------------------------------------------------------


def test_mark_all_read_smart_all(db_session):
    src = make_source(db_session)
    a1 = make_article(db_session, source_id=src.id, dedup_key="k1", is_read=False)
    a2 = make_article(db_session, source_id=src.id, dedup_key="k2", is_read=False)
    count = mark_all_read(db_session, FeedSpec(type="smart", value="all"))
    assert count == 2
    db_session.expire_all()
    for aid in (a1.id, a2.id):
        a = db_session.get(Article, aid)
        assert a.is_read is True
        assert a.read_at is not None


def test_mark_all_read_smart_unread(db_session):
    src = make_source(db_session)
    a1 = make_article(db_session, source_id=src.id, dedup_key="k1", is_read=False)
    a2 = make_article(db_session, source_id=src.id, dedup_key="k2", is_read=True)
    count = mark_all_read(db_session, FeedSpec(type="smart", value="unread"))
    # "unread" feed spec marks ALL ready articles (not just currently-unread).
    assert count >= 1
    db_session.expire_all()
    assert db_session.get(Article, a1.id).is_read is True
    assert db_session.get(Article, a2.id).is_read is True


def test_mark_all_read_smart_saved(db_session):
    src = make_source(db_session)
    saved = make_article(db_session, source_id=src.id, dedup_key="k1", is_saved=True)
    unsaved = make_article(db_session, source_id=src.id, dedup_key="k2", is_saved=False)
    mark_all_read(db_session, FeedSpec(type="smart", value="saved"))
    db_session.expire_all()
    assert db_session.get(Article, saved.id).is_read is True
    assert db_session.get(Article, unsaved.id).is_read is False


def test_mark_all_read_smart_important(db_session):
    src = make_source(db_session)
    important = make_article(
        db_session, source_id=src.id, dedup_key="k1", importance_score=80
    )
    low = make_article(db_session, source_id=src.id, dedup_key="k2", importance_score=40)
    mark_all_read(db_session, FeedSpec(type="smart", value="important"), important_threshold=70)
    db_session.expire_all()
    assert db_session.get(Article, important.id).is_read is True
    assert db_session.get(Article, low.id).is_read is False


def test_mark_all_read_smart_uncategorized(db_session):
    src = make_source(db_session)
    no_cats = make_article(db_session, source_id=src.id, dedup_key="k1", categories=None)
    categorized = make_article(
        db_session, source_id=src.id, dedup_key="k2", categories=["tech"]
    )
    mark_all_read(db_session, FeedSpec(type="smart", value="uncategorized"))
    db_session.expire_all()
    assert db_session.get(Article, no_cats.id).is_read is True
    assert db_session.get(Article, categorized.id).is_read is False


# ---------------------------------------------------------------------------
# mark_all_read — category and source feeds
# ---------------------------------------------------------------------------


def test_mark_all_read_category(db_session):
    src = make_source(db_session)
    tech = make_article(
        db_session, source_id=src.id, dedup_key="k1", categories=["tech"]
    )
    sports = make_article(
        db_session, source_id=src.id, dedup_key="k2", categories=["sports"]
    )
    mark_all_read(db_session, FeedSpec(type="category", value="tech"))
    db_session.expire_all()
    assert db_session.get(Article, tech.id).is_read is True
    assert db_session.get(Article, sports.id).is_read is False


def test_mark_all_read_source(db_session):
    src1 = make_source(db_session, name="S1", url="http://s1.example.com/feed")
    src2 = make_source(db_session, name="S2", url="http://s2.example.com/feed")
    a1 = make_article(db_session, source_id=src1.id, dedup_key="k1")
    a2 = make_article(db_session, source_id=src2.id, dedup_key="k2")
    mark_all_read(db_session, FeedSpec(type="source", value=src1.id))
    db_session.expire_all()
    assert db_session.get(Article, a1.id).is_read is True
    assert db_session.get(Article, a2.id).is_read is False


def test_mark_all_read_does_not_affect_non_ready(db_session):
    from aggregator_common.state import ArticleStatus

    src = make_source(db_session)
    ready = make_article(db_session, source_id=src.id, dedup_key="k1")
    pending = make_article(
        db_session,
        source_id=src.id,
        dedup_key="k2",
        status=ArticleStatus.pending_processing,
    )
    mark_all_read(db_session, FeedSpec(type="smart", value="all"))
    db_session.expire_all()
    assert db_session.get(Article, ready.id).is_read is True
    assert db_session.get(Article, pending.id).is_read is False
