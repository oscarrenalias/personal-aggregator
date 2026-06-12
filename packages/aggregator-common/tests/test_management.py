"""Integration tests for aggregator_common.management functions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Generator

import pytest
from sqlalchemy.orm import Session

from aggregator_common.errors import ConflictError, NotFoundError
from aggregator_common.management import (
    add_category,
    add_source,
    disable_category,
    disable_source,
    enable_category,
    enable_source,
    refresh_source_now,
    remove_category,
    remove_source,
    rename_category,
    set_category_description,
    set_category_order,
    set_interest_profile,
    set_source_interval,
)
from aggregator_common.models import Article, Category, Source
from aggregator_common.state import ArticleStatus

_NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def session(db_session_factory) -> Generator[Session, None, None]:
    s = db_session_factory()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


# ---------------------------------------------------------------------------
# set_interest_profile
# ---------------------------------------------------------------------------


class TestSetInterestProfile:
    def test_creates_on_first_call(self, session: Session):
        result = set_interest_profile(session, "Python, ML, cloud")
        assert result["profile_text"] == "Python, ML, cloud"
        assert result["id"] is True
        assert result["updated_at"] is not None

    def test_upsert_updates_on_second_call(self, session: Session):
        set_interest_profile(session, "first profile")
        result = set_interest_profile(session, "second profile")
        assert result["profile_text"] == "second profile"

    def test_returns_all_three_fields(self, session: Session):
        result = set_interest_profile(session, "test text")
        assert {"id", "profile_text", "updated_at"} <= set(result.keys())


# ---------------------------------------------------------------------------
# add_source / remove_source
# ---------------------------------------------------------------------------


class TestAddSource:
    def test_happy_path_returns_source_fields(self, session: Session):
        result = add_source(session, "My Blog", "https://myblog-add.example.com/feed.xml")
        assert result["name"] == "My Blog"
        assert result["feed_url"] == "https://myblog-add.example.com/feed.xml"
        assert result["enabled"] is True
        assert "id" in result
        assert "created_at" in result
        assert "updated_at" in result

    def test_duplicate_feed_url_raises_conflict(self, session: Session):
        src = Source(name="Blog A", feed_url="https://dup-src.example.com/feed.xml")
        session.add(src)
        session.flush()
        with pytest.raises(ConflictError, match="feed_url"):
            add_source(session, "Blog B", "https://dup-src.example.com/feed.xml")

    def test_custom_interval_and_priority(self, session: Session):
        result = add_source(
            session,
            "Custom Feed",
            "https://custom-src.example.com/feed.xml",
            refresh_interval_seconds=7200,
            priority=5,
        )
        assert result["refresh_interval_seconds"] == 7200
        assert result["priority"] == 5

    def test_disabled_source_created_correctly(self, session: Session):
        result = add_source(
            session,
            "Disabled Feed",
            "https://disabled-src.example.com/feed.xml",
            enabled=False,
        )
        assert result["enabled"] is False


class TestRemoveSource:
    def test_cascade_deletes_articles_and_reports_count(self, session: Session):
        src = Source(name="ToDelete", feed_url="https://del-src.example.com/feed.xml")
        session.add(src)
        session.flush()
        for i in range(3):
            session.add(Article(
                source_id=src.id,
                dedup_key=f"del-{i}",
                status=ArticleStatus.pending_processing,
                raw_payload={},
                retrieved_at=_NOW,
            ))
        session.flush()

        result = remove_source(session, src.id)

        assert result["sources_deleted"] == 1
        assert result["articles_deleted"] == 3

    def test_zero_articles_when_source_has_none(self, session: Session):
        src = Source(name="EmptySource", feed_url="https://empty-src.example.com/feed.xml")
        session.add(src)
        session.flush()

        result = remove_source(session, src.id)

        assert result["sources_deleted"] == 1
        assert result["articles_deleted"] == 0

    def test_unknown_id_raises_not_found(self, session: Session):
        with pytest.raises(NotFoundError):
            remove_source(session, 999_999_901)


# ---------------------------------------------------------------------------
# enable_source / disable_source / set_source_interval / refresh_source_now
# ---------------------------------------------------------------------------


class TestEnableSource:
    def test_enables_and_resets_failure_state(self, session: Session):
        src = Source(
            name="Disabled",
            feed_url="https://dis-src.example.com/feed.xml",
            enabled=False,
            consecutive_failures=5,
        )
        session.add(src)
        session.flush()

        result = enable_source(session, src.id)

        assert result["enabled"] is True
        assert result["consecutive_failures"] == 0
        assert result["next_check_at"] is not None

    def test_unknown_id_raises_not_found(self, session: Session):
        with pytest.raises(NotFoundError):
            enable_source(session, 999_999_902)


class TestDisableSource:
    def test_sets_enabled_false(self, session: Session):
        src = Source(name="Active", feed_url="https://act-src.example.com/feed.xml")
        session.add(src)
        session.flush()

        result = disable_source(session, src.id)

        assert result["enabled"] is False

    def test_unknown_id_raises_not_found(self, session: Session):
        with pytest.raises(NotFoundError):
            disable_source(session, 999_999_903)


class TestSetSourceInterval:
    def test_updates_interval(self, session: Session):
        src = Source(name="Interval", feed_url="https://iv-src.example.com/feed.xml")
        session.add(src)
        session.flush()

        result = set_source_interval(session, src.id, 7200)

        assert result["refresh_interval_seconds"] == 7200
        assert result["id"] == src.id

    def test_unknown_id_raises_not_found(self, session: Session):
        with pytest.raises(NotFoundError):
            set_source_interval(session, 999_999_904, 3600)


class TestRefreshSourceNow:
    def test_sets_next_check_at_close_to_now(self, session: Session):
        src = Source(name="Refresh", feed_url="https://rf-src.example.com/feed.xml")
        session.add(src)
        session.flush()

        before = datetime.now(timezone.utc)
        result = refresh_source_now(session, src.id)
        after = datetime.now(timezone.utc)

        assert result["next_check_at"] is not None
        nca = result["next_check_at"]
        if nca.tzinfo is None:
            nca = nca.replace(tzinfo=timezone.utc)
        assert before <= nca <= after

    def test_unknown_id_raises_not_found(self, session: Session):
        with pytest.raises(NotFoundError):
            refresh_source_now(session, 999_999_905)


# ---------------------------------------------------------------------------
# add_category / remove_category
# ---------------------------------------------------------------------------


class TestAddCategory:
    def test_happy_path_returns_expected_fields(self, session: Session):
        result = add_category(session, "Technology", description="Tech news", sort_order=1)
        assert result["name"] == "Technology"
        assert result["description"] == "Tech news"
        assert result["sort_order"] == 1
        assert result["enabled"] is True
        assert "id" in result
        assert "created_at" in result
        assert "updated_at" in result

    def test_duplicate_name_raises_conflict(self, session: Session):
        cat = Category(name="Politics")
        session.add(cat)
        session.flush()
        with pytest.raises(ConflictError, match="Politics"):
            add_category(session, "Politics")

    def test_defaults_enabled_true(self, session: Session):
        result = add_category(session, "DefaultEnabled-Mgmt")
        assert result["enabled"] is True


class TestRemoveCategory:
    def test_remove_by_int_id(self, session: Session):
        result_add = add_category(session, "ToRemove-Int")
        result = remove_category(session, result_add["id"])
        assert result == {"categories_deleted": 1}

    def test_remove_by_name_string(self, session: Session):
        add_category(session, "ToRemove-Name")
        result = remove_category(session, "ToRemove-Name")
        assert result == {"categories_deleted": 1}

    def test_remove_by_all_digit_string(self, session: Session):
        result_add = add_category(session, "ToRemove-Digit")
        result = remove_category(session, str(result_add["id"]))
        assert result == {"categories_deleted": 1}

    def test_unknown_int_id_raises_not_found(self, session: Session):
        with pytest.raises(NotFoundError):
            remove_category(session, 999_999_906)

    def test_unknown_name_raises_not_found(self, session: Session):
        with pytest.raises(NotFoundError):
            remove_category(session, "NoSuchCategory-Mgmt")

    def test_unknown_numeric_string_raises_not_found(self, session: Session):
        with pytest.raises(NotFoundError):
            remove_category(session, "999999907")


# ---------------------------------------------------------------------------
# rename_category / set_category_description / set_category_order
# ---------------------------------------------------------------------------


class TestRenameCategory:
    def test_happy_path_returns_expected_dict(self, session: Session):
        result_add = add_category(session, "OldName-Mgmt")
        result = rename_category(session, result_add["id"], "NewName-Mgmt")
        assert result["name"] == "NewName-Mgmt"
        assert "id" in result

    def test_conflict_on_duplicate_name(self, session: Session):
        existing = Category(name="ExistingName-Mgmt")
        session.add(existing)
        session.flush()
        result_other = add_category(session, "OtherName-Mgmt")
        session.flush()
        with pytest.raises(ConflictError):
            rename_category(session, result_other["id"], "ExistingName-Mgmt")

    def test_unknown_id_raises_not_found(self, session: Session):
        with pytest.raises(NotFoundError):
            rename_category(session, 999_999_907, "NewName")


class TestSetCategoryDescription:
    def test_sets_description(self, session: Session):
        result_add = add_category(session, "DescCat-Mgmt")
        result = set_category_description(session, result_add["id"], "New desc")
        assert result["description"] == "New desc"

    def test_clears_description_with_none(self, session: Session):
        result_add = add_category(session, "ClearDescCat-Mgmt", description="existing")
        result = set_category_description(session, result_add["id"], None)
        assert result["description"] is None

    def test_unknown_id_raises_not_found(self, session: Session):
        with pytest.raises(NotFoundError):
            set_category_description(session, 999_999_908, "desc")


class TestSetCategoryOrder:
    def test_updates_sort_order(self, session: Session):
        result_add = add_category(session, "OrderCat-Mgmt")
        result = set_category_order(session, result_add["id"], 42)
        assert result["sort_order"] == 42

    def test_unknown_id_raises_not_found(self, session: Session):
        with pytest.raises(NotFoundError):
            set_category_order(session, 999_999_909, 1)


# ---------------------------------------------------------------------------
# enable_category / disable_category
# ---------------------------------------------------------------------------


class TestEnableCategory:
    def test_sets_enabled_true(self, session: Session):
        cat = Category(name="EnableCat-Mgmt", enabled=False)
        session.add(cat)
        session.flush()
        result = enable_category(session, cat.id)
        assert result["enabled"] is True

    def test_unknown_id_raises_not_found(self, session: Session):
        with pytest.raises(NotFoundError):
            enable_category(session, 999_999_910)


class TestDisableCategory:
    def test_sets_enabled_false(self, session: Session):
        cat = Category(name="DisableCat-Mgmt")
        session.add(cat)
        session.flush()
        result = disable_category(session, cat.id)
        assert result["enabled"] is False

    def test_unknown_id_raises_not_found(self, session: Session):
        with pytest.raises(NotFoundError):
            disable_category(session, 999_999_911)
