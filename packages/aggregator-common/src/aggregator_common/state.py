from enum import Enum


class ArticleStatus(str, Enum):
    pending_processing = "pending_processing"
    pending_ranking = "pending_ranking"
    ready = "ready"
    failed_processing = "failed_processing"
    failed_ranking = "failed_ranking"
    skipped = "skipped"


# (from_status, to_status) pairs that are explicitly allowed.
# Nine transitions:
#   Processor stage (3): success, failure, skip
#   Summarize-rank stage (3): success, failure, skip
#   Reaper retry (2): reset failed articles to their pending state
#   Re-rank (1): allow the web layer to queue a ready article for re-ranking
_ALLOWED_TRANSITIONS: frozenset[tuple[ArticleStatus, ArticleStatus]] = frozenset(
    {
        (ArticleStatus.pending_processing, ArticleStatus.pending_ranking),
        (ArticleStatus.pending_processing, ArticleStatus.failed_processing),
        (ArticleStatus.pending_processing, ArticleStatus.skipped),
        (ArticleStatus.pending_ranking, ArticleStatus.ready),
        (ArticleStatus.pending_ranking, ArticleStatus.failed_ranking),
        (ArticleStatus.pending_ranking, ArticleStatus.skipped),
        (ArticleStatus.failed_processing, ArticleStatus.pending_processing),
        (ArticleStatus.failed_ranking, ArticleStatus.pending_ranking),
        (ArticleStatus.ready, ArticleStatus.pending_ranking),
    }
)

# Maps stage name → the status a worker claims from.
_CLAIMABLE_STATUS: dict[str, ArticleStatus] = {
    "processor": ArticleStatus.pending_processing,
    "summarize_rank": ArticleStatus.pending_ranking,
}

# Maps stage name → success/failure target statuses.
_SUCCESS_STATUS: dict[str, ArticleStatus] = {
    "processor": ArticleStatus.pending_ranking,
    "summarize_rank": ArticleStatus.ready,
}

_FAILURE_STATUS: dict[str, ArticleStatus] = {
    "processor": ArticleStatus.failed_processing,
    "summarize_rank": ArticleStatus.failed_ranking,
}


def can_transition(from_status: ArticleStatus, to_status: ArticleStatus) -> bool:
    return (from_status, to_status) in _ALLOWED_TRANSITIONS


def claimable_status_for(stage: str) -> ArticleStatus:
    try:
        return _CLAIMABLE_STATUS[stage]
    except KeyError:
        raise ValueError(f"Unknown stage: {stage!r}")


def success_status_for(stage: str) -> ArticleStatus:
    try:
        return _SUCCESS_STATUS[stage]
    except KeyError:
        raise ValueError(f"Unknown stage: {stage!r}")


def failure_status_for(stage: str) -> ArticleStatus:
    try:
        return _FAILURE_STATUS[stage]
    except KeyError:
        raise ValueError(f"Unknown stage: {stage!r}")
