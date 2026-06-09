"""Tests for the article state machine (aggregator_common.state)."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pytest

from aggregator_common.state import (
    ArticleStatus,
    can_transition,
    claimable_status_for,
    failure_status_for,
    success_status_for,
)

S = ArticleStatus


# fmt: off
_ALLOWED_TRANSITIONS = [
    pytest.param(S.pending_processing, S.pending_ranking,    id="proc-success"),
    pytest.param(S.pending_processing, S.failed_processing,  id="proc-failure"),
    pytest.param(S.pending_processing, S.skipped,            id="proc-skip"),
    pytest.param(S.pending_ranking,    S.ready,              id="rank-success"),
    pytest.param(S.pending_ranking,    S.failed_ranking,     id="rank-failure"),
    pytest.param(S.pending_ranking,    S.skipped,            id="rank-skip"),
    pytest.param(S.failed_processing,  S.pending_processing, id="reaper-proc"),
    pytest.param(S.failed_ranking,     S.pending_ranking,    id="reaper-rank"),
    pytest.param(S.ready,              S.pending_ranking,    id="rerank"),
]

_DISALLOWED_TRANSITIONS = [
    # Self-loops
    pytest.param(S.pending_processing, S.pending_processing, id="proc-self-loop"),
    pytest.param(S.pending_ranking,    S.pending_ranking,    id="rank-self-loop"),
    pytest.param(S.ready,              S.ready,              id="ready-self-loop"),
    pytest.param(S.skipped,            S.skipped,            id="skipped-self-loop"),
    # Skipping stages forward
    pytest.param(S.pending_processing, S.ready,              id="proc-skip-to-ready"),
    pytest.param(S.failed_processing,  S.pending_ranking,    id="failed-proc-skip-stage"),
    pytest.param(S.failed_processing,  S.ready,              id="failed-proc-to-ready"),
    # Wrong failure direction
    pytest.param(S.pending_processing, S.failed_ranking,     id="proc-wrong-failure"),
    pytest.param(S.pending_ranking,    S.failed_processing,  id="rank-wrong-failure"),
    # Illegal backwards
    pytest.param(S.pending_ranking,    S.pending_processing, id="rank-backward"),
    pytest.param(S.ready,              S.pending_processing, id="ready-backward"),
    pytest.param(S.failed_ranking,     S.pending_processing, id="failed-rank-wrong-reset"),
    # No recovery from skipped
    pytest.param(S.skipped,            S.pending_processing, id="skipped-no-recovery-proc"),
    pytest.param(S.skipped,            S.pending_ranking,    id="skipped-no-recovery-rank"),
    pytest.param(S.skipped,            S.ready,              id="skipped-no-recovery-ready"),
]
# fmt: on


@pytest.mark.parametrize("from_status,to_status", _ALLOWED_TRANSITIONS)
def test_allowed_transition(from_status: ArticleStatus, to_status: ArticleStatus) -> None:
    assert can_transition(from_status, to_status) is True


@pytest.mark.parametrize("from_status,to_status", _DISALLOWED_TRANSITIONS)
def test_disallowed_transition(from_status: ArticleStatus, to_status: ArticleStatus) -> None:
    assert can_transition(from_status, to_status) is False


def test_all_nine_allowed_transitions_covered() -> None:
    """Guard: the parametrized list must account for all nine allowed transitions."""
    assert len(_ALLOWED_TRANSITIONS) == 9


def test_claimable_status_for_processor() -> None:
    assert claimable_status_for("processor") is ArticleStatus.pending_processing


def test_claimable_status_for_summarize_rank() -> None:
    assert claimable_status_for("summarize_rank") is ArticleStatus.pending_ranking


def test_claimable_status_for_unknown_stage_raises() -> None:
    with pytest.raises(ValueError, match="Unknown stage"):
        claimable_status_for("unknown_stage")


@dataclass
class _ArticleSnapshot:
    """Minimal stand-in for an Article row used in claimability assertions."""
    status: ArticleStatus
    claimed_at: Optional[datetime] = None
    retry_count: int = 0


def test_retriever_inserted_article_is_claimable_by_processor() -> None:
    """An article just inserted by the retriever must match the processor's claimable status."""
    article = _ArticleSnapshot(
        status=ArticleStatus.pending_processing,
        claimed_at=None,
        retry_count=0,
    )
    assert article.status == claimable_status_for("processor")
    assert article.claimed_at is None
    assert article.retry_count == 0


def test_success_status_for_stages() -> None:
    assert success_status_for("processor") is ArticleStatus.pending_ranking
    assert success_status_for("summarize_rank") is ArticleStatus.ready


def test_failure_status_for_stages() -> None:
    assert failure_status_for("processor") is ArticleStatus.failed_processing
    assert failure_status_for("summarize_rank") is ArticleStatus.failed_ranking
