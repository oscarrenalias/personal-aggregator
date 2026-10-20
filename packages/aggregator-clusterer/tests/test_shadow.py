"""Shadow evaluation tests for the clusterer (AC-2, AC-3, AC-9, AC-11).

Covers:
- AC-9: shadow runs for classify, merge, and facts never write to threads,
  thread_memberships, or thread_merge_verdicts
- AC-2 (clusterer ops): with experiment configured, control + arm rows written with same prompt_hash
- AC-3 (clusterer statuses): api_error, timeout, parse_error, ok for classify/merge/facts
- AC-11: merge and facts calls carry ref_id in llm_calls (via metadata)
"""
from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://placeholder:placeholder@localhost/placeholder")

import aggregator_common.experiment as _exp_module  # noqa: E402
from aggregator_common.models import (  # noqa: E402
    ClassificationLabel,
    LlmShadowResult,
    Thread,
    ThreadMembership,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UNTIL_FUTURE = "2099-01-01T00:00:00Z"


def _reset_experiment() -> None:
    with _exp_module._lock:
        _exp_module._config = None
        _exp_module._initialized = False
        _exp_module._executor = None
    _exp_module._counters = _exp_module._Counters()
    _exp_module._budget = _exp_module._BudgetCache()


@pytest.fixture(autouse=True)
def reset_exp(db_engine):
    _reset_experiment()
    yield
    with _exp_module._lock:
        ex = _exp_module._executor
        _exp_module._executor = None
        _exp_module._config = None
        _exp_module._initialized = False
    if ex is not None:
        ex.shutdown()


@pytest.fixture(autouse=True)
def truncate_shadow(db_engine):
    """Truncate llm_shadow_results before each test for isolation."""
    from sqlalchemy import text
    with db_engine.connect() as conn:
        conn.execute(text("TRUNCATE TABLE llm_shadow_results"))
        conn.commit()
    yield


def _init_experiment(ops: dict | None = None):
    if ops is None:
        ops = {
            "clusterer.classify": {
                "sample_rate": 1.0,
                "arms": [{"name": "luna-none", "model": "gpt-6-luna",
                          "params": {"reasoning_effort": "none", "max_tokens": None, "max_completion_tokens": 512}}],
            },
            "clusterer.merge": {
                "sample_rate": 1.0,
                "arms": [{"name": "luna-none", "model": "gpt-6-luna",
                          "params": {"reasoning_effort": "none", "max_tokens": None, "max_completion_tokens": 128}}],
            },
            "clusterer.consolidate_facts": {
                "sample_rate": 1.0,
                "arms": [{"name": "luna-low", "model": "gpt-6-luna",
                          "params": {"reasoning_effort": "low", "temperature": None,
                                     "max_tokens": None, "max_completion_tokens": 2000}}],
            },
        }
    cfg = json.dumps({
        "enabled": True,
        "name": "test-exp",
        "until": _UNTIL_FUTURE,
        "max_daily_cost_usd": 9999.0,
        "operations": ops,
    })
    _exp_module.init(SimpleNamespace(llm_experiment=cfg))


def _make_classify_response(label: str = "new_thread", thread_id: int | None = None) -> SimpleNamespace:
    content = json.dumps({
        "label": label,
        "thread_id": thread_id,
        "confidence": 0.9,
        "new_facts": ["fact one"],
        "reason": "test reason",
        "thread_title": "Test Thread Title",
    })
    msg = SimpleNamespace(content=content, refusal=None)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=50, completion_tokens=30, total_tokens=80)
    return SimpleNamespace(choices=[choice], usage=usage, model="gpt-4.1", id="resp-c")


def _make_merge_response(same_story: bool = False) -> SimpleNamespace:
    content = json.dumps({"same_story": same_story, "reason": "test"})
    msg = SimpleNamespace(content=content, refusal=None)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=30, completion_tokens=10, total_tokens=40)
    return SimpleNamespace(choices=[choice], usage=usage, model="gpt-4.1", id="resp-m")


def _make_facts_response(facts: list[str] | None = None) -> SimpleNamespace:
    content = json.dumps(facts or ["condensed fact"])
    msg = SimpleNamespace(content=content, refusal=None)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=40, completion_tokens=20, total_tokens=60)
    return SimpleNamespace(choices=[choice], usage=usage, model="gpt-4.1", id="resp-f")


def _wait_for_shadow_rows(db_session, min_count: int = 2, timeout: float = 5.0) -> list:
    from sqlalchemy import select
    deadline = time.monotonic() + timeout
    rows = []
    while time.monotonic() < deadline:
        db_session.rollback()
        rows = db_session.execute(select(LlmShadowResult)).scalars().all()
        if len(rows) >= min_count:
            break
        time.sleep(0.1)
    return rows


def _freeze_budget():
    """Force budget cache to 0 so decide() always passes budget check."""
    _exp_module._budget._cost = 0.0
    _exp_module._budget._refreshed_at = time.monotonic() + 3600


# ---------------------------------------------------------------------------
# AC-9: Shadow writes never touch production tables
# ---------------------------------------------------------------------------

class TestShadowNoProductionTableWrites:
    def test_classify_shadow_leaves_threads_unchanged(self, db_session):
        """AC-9: classify shadow run must not write threads, thread_memberships, or thread_merge_verdicts."""
        from aggregator_common.models import Thread, ThreadMembership
        from aggregator_clusterer.classification import classify_article
        from aggregator_clusterer.config import ClustererSettings
        from sqlalchemy import select, func

        _init_experiment()
        _freeze_budget()

        src = make_source(db_session)
        article = make_article(db_session, source_id=src.id, dedup_key="shadow-test-1")

        thread_count_before = db_session.execute(select(func.count()).select_from(Thread)).scalar()
        membership_count_before = db_session.execute(select(func.count()).select_from(ThreadMembership)).scalar()

        settings = ClustererSettings()
        with patch("litellm.completion", return_value=_make_classify_response()):
            result = classify_article(article, [], db_session, settings)
            # Wait inside mock context for background thread
            time.sleep(0.5)
            db_session.rollback()
            thread_count_after = db_session.execute(select(func.count()).select_from(Thread)).scalar()
            membership_count_after = db_session.execute(select(func.count()).select_from(ThreadMembership)).scalar()

        assert thread_count_after == thread_count_before, "classify shadow must not write to threads table"
        assert membership_count_after == membership_count_before, "classify shadow must not write thread_memberships"

    def test_merge_shadow_leaves_threads_unchanged(self, db_session):
        """AC-9: merge shadow run must not write to threads or related tables."""
        from aggregator_clusterer.worker import _make_llm_merge_fn
        from aggregator_clusterer.config import ClustererSettings
        from aggregator_common.models import Thread
        from sqlalchemy import select, func

        _init_experiment()
        _freeze_budget()

        thread_a = make_thread(db_session, title="Thread A")
        thread_b = make_thread(db_session, title="Thread B")

        thread_count_before = db_session.execute(select(func.count()).select_from(Thread)).scalar()

        settings = ClustererSettings()
        merge_fn = _make_llm_merge_fn(settings)
        with patch("litellm.completion", return_value=_make_merge_response(same_story=False)):
            result = merge_fn(thread_a, thread_b)
            time.sleep(0.5)
            db_session.rollback()
            thread_count_after = db_session.execute(select(func.count()).select_from(Thread)).scalar()

        assert thread_count_after == thread_count_before, "merge shadow must not write to threads"
        assert result is False  # same_story=False → don't merge

    def test_facts_shadow_leaves_threads_unchanged(self, db_session):
        """AC-9: facts shadow run must not modify threads or write other production rows."""
        from aggregator_clusterer.worker import _make_llm_facts_fn
        from aggregator_clusterer.config import ClustererSettings
        from aggregator_common.models import Thread
        from sqlalchemy import select, func

        _init_experiment()
        _freeze_budget()

        thread = make_thread(db_session, title="Facts Thread", known_facts=["old fact 1", "old fact 2"])
        thread_count_before = db_session.execute(select(func.count()).select_from(Thread)).scalar()

        settings = ClustererSettings()
        facts_fn = _make_llm_facts_fn(settings)
        with patch("litellm.completion", return_value=_make_facts_response(["condensed"])):
            result = facts_fn(thread.id, ["old fact 1", "old fact 2"])
            time.sleep(0.5)
            db_session.rollback()
            thread_count_after = db_session.execute(select(func.count()).select_from(Thread)).scalar()

        assert thread_count_after == thread_count_before, "facts shadow must not write to threads"
        assert result == ["condensed"]


# ---------------------------------------------------------------------------
# AC-2: classify, merge, facts produce control + arm rows with same prompt_hash
# ---------------------------------------------------------------------------

class TestClustererShadowRows:
    def test_classify_writes_control_and_arm_rows(self, db_session):
        from aggregator_clusterer.classification import classify_article
        from aggregator_clusterer.config import ClustererSettings

        _init_experiment()
        _freeze_budget()

        src = make_source(db_session, url="https://src2.example.com/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="shadow-classify-rows")

        settings = ClustererSettings()
        # Keep mock alive while waiting for background thread inside context
        with patch("litellm.completion", return_value=_make_classify_response()):
            classify_article(article, [], db_session, settings)
            rows = _wait_for_shadow_rows(db_session, min_count=2)

        arms = {r.arm for r in rows}
        assert "control" in arms, f"Expected control arm, got arms={arms}"
        assert "luna-none" in arms, f"Expected luna-none arm, got arms={arms}"

        hashes = {r.prompt_hash for r in rows}
        assert len(hashes) == 1, f"All rows must share same prompt_hash, got {hashes}"

    def test_merge_writes_control_and_arm_rows(self, db_session):
        from aggregator_clusterer.worker import _make_llm_merge_fn
        from aggregator_clusterer.config import ClustererSettings

        _init_experiment()
        _freeze_budget()

        t1 = make_thread(db_session, title="Merge Thread 1")
        t2 = make_thread(db_session, title="Merge Thread 2")

        settings = ClustererSettings()
        merge_fn = _make_llm_merge_fn(settings)
        with patch("litellm.completion", return_value=_make_merge_response()):
            merge_fn(t1, t2)
            rows = _wait_for_shadow_rows(db_session, min_count=2)

        arms = {r.arm for r in rows}
        assert "control" in arms, f"Expected control arm, got arms={arms}"
        assert "luna-none" in arms, f"Expected luna-none arm, got arms={arms}"

        hashes = {r.prompt_hash for r in rows}
        assert len(hashes) == 1, f"All rows must share same prompt_hash, got {hashes}"

    def test_facts_writes_control_and_arm_rows(self, db_session):
        from aggregator_clusterer.worker import _make_llm_facts_fn
        from aggregator_clusterer.config import ClustererSettings

        _init_experiment()
        _freeze_budget()

        thread = make_thread(db_session, title="Facts Thread 2", known_facts=["f1", "f2"])

        settings = ClustererSettings()
        facts_fn = _make_llm_facts_fn(settings)
        with patch("litellm.completion", return_value=_make_facts_response()):
            facts_fn(thread.id, ["f1", "f2"])
            rows = _wait_for_shadow_rows(db_session, min_count=2)

        arms = {r.arm for r in rows}
        assert "control" in arms, f"Expected control arm, got arms={arms}"
        assert "luna-low" in arms, f"Expected luna-low arm, got arms={arms}"


# ---------------------------------------------------------------------------
# AC-11: merge and facts ref_id is set in metadata
# ---------------------------------------------------------------------------

class TestRefIdInMetadata:
    def test_merge_ref_id_in_kwargs_metadata(self, db_session):
        """AC-11: merge control call uses ref_id=f'{min(a,b)}:{max(a,b)}'."""
        from aggregator_clusterer.worker import _make_llm_merge_fn
        from aggregator_clusterer.config import ClustererSettings

        _init_experiment()
        _freeze_budget()
        t1 = make_thread(db_session, title="Ref Test A")
        t2 = make_thread(db_session, title="Ref Test B")
        captured_kwargs: list[dict] = []

        def mock_completion(**kwargs):
            captured_kwargs.append(dict(kwargs))
            return _make_merge_response()

        settings = ClustererSettings()
        merge_fn = _make_llm_merge_fn(settings)
        with patch("litellm.completion", side_effect=mock_completion):
            merge_fn(t1, t2)

        assert captured_kwargs, "litellm.completion should have been called"
        ctrl_metadata = captured_kwargs[0].get("metadata", {})
        expected_ref = f"{min(t1.id, t2.id)}:{max(t1.id, t2.id)}"
        assert ctrl_metadata.get("ref_id") == expected_ref

    def test_facts_ref_id_in_kwargs_metadata(self, db_session):
        """AC-11: facts control call uses ref_id=str(thread_id)."""
        from aggregator_clusterer.worker import _make_llm_facts_fn
        from aggregator_clusterer.config import ClustererSettings

        _init_experiment()
        _freeze_budget()
        thread = make_thread(db_session, title="Facts RefId Thread", known_facts=["x"])
        captured_kwargs: list[dict] = []

        def mock_completion(**kwargs):
            captured_kwargs.append(dict(kwargs))
            return _make_facts_response()

        settings = ClustererSettings()
        facts_fn = _make_llm_facts_fn(settings)
        with patch("litellm.completion", side_effect=mock_completion):
            facts_fn(thread.id, ["x"])

        assert captured_kwargs
        ctrl_metadata = captured_kwargs[0].get("metadata", {})
        assert ctrl_metadata.get("ref_id") == str(thread.id)


# ---------------------------------------------------------------------------
# AC-3: Status handling for clusterer operations
# ---------------------------------------------------------------------------

class TestClustererStatusHandling:
    def test_classify_api_error_returns_error_result(self, db_session):
        """AC-3: api_error from litellm → classify returns _error_result() (not raises)."""
        from aggregator_clusterer.classification import classify_article
        from aggregator_clusterer.config import ClustererSettings

        src = make_source(db_session, url="https://err.example.com/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="status-api-error")
        settings = ClustererSettings()

        with patch("litellm.completion", side_effect=RuntimeError("API down")):
            result = classify_article(article, [], db_session, settings)

        assert result.is_error is True

    def test_classify_parse_error_returns_error_result(self, db_session):
        """AC-3: parse_error (invalid label) → classify returns _error_result()."""
        from aggregator_clusterer.classification import classify_article
        from aggregator_clusterer.config import ClustererSettings

        src = make_source(db_session, url="https://parse.example.com/feed.xml")
        article = make_article(db_session, source_id=src.id, dedup_key="status-parse-error")
        settings = ClustererSettings()

        bad_response = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(
                    content='{"label": "INVALID_LABEL", "thread_id": null, "confidence": 0.5, "new_facts": [], "reason": "x"}',
                    refusal=None,
                ),
                finish_reason="stop",
            )],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            model="gpt-4.1",
            id="resp-bad",
        )

        with patch("litellm.completion", return_value=bad_response):
            result = classify_article(article, [], db_session, settings)

        assert result.is_error is True

    def test_merge_fail_open_on_api_error(self, db_session):
        """AC-3: merge api_error → fail-open returns False."""
        from aggregator_clusterer.worker import _make_llm_merge_fn
        from aggregator_clusterer.config import ClustererSettings

        t1 = make_thread(db_session, title="Merge Err A")
        t2 = make_thread(db_session, title="Merge Err B")
        settings = ClustererSettings()
        merge_fn = _make_llm_merge_fn(settings)

        with patch("litellm.completion", side_effect=RuntimeError("merge api error")):
            result = merge_fn(t1, t2)

        assert result is False

    def test_facts_fail_open_on_api_error(self, db_session):
        """AC-3: facts api_error → fail-open returns None."""
        from aggregator_clusterer.worker import _make_llm_facts_fn
        from aggregator_clusterer.config import ClustererSettings

        thread = make_thread(db_session, title="Facts Err Thread", known_facts=["f1"])
        settings = ClustererSettings()
        facts_fn = _make_llm_facts_fn(settings)

        with patch("litellm.completion", side_effect=RuntimeError("facts api error")):
            result = facts_fn(thread.id, ["f1"])

        assert result is None  # fail-open


# ---------------------------------------------------------------------------
# Import conftest helpers for this module
# ---------------------------------------------------------------------------

from .conftest import make_source, make_article, make_thread  # noqa: E402
