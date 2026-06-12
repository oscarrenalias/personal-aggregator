from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from aggregator_common.models import Article, Brief, BriefTopic, Category, InterestProfile, Source, Thread, ThreadMembership
from aggregator_common.state import ArticleStatus

ViewName = Literal["all", "unread", "important", "saved", "uncategorized", "today"]

_DEFAULT_IMPORTANT_THRESHOLD = 70
_DEFAULT_LIMIT = 50


@dataclass
class ArticleResult:
    id: int
    title: Optional[str]
    url: Optional[str]
    source_id: int
    source_name: Optional[str]
    feed_published_at: Optional[str]
    summary: Optional[str]
    excerpt: Optional[str]
    clean_text: Optional[str]
    importance_score: Optional[int]
    importance_reason: Optional[str]
    categories: Optional[list]
    topics: Optional[dict]
    is_read: bool
    is_saved: bool
    author: Optional[str]
    word_count: Optional[int]
    language: Optional[str]


@dataclass
class SourceResult:
    id: int
    name: str
    feed_url: str


@dataclass
class CategoryResult:
    id: int
    name: str
    description: Optional[str]
    sort_order: int


@dataclass
class BriefTopicResult:
    position: int
    headline: str
    what_happened: str
    why_it_matters: str
    historical_context: Optional[str]
    refs: list


@dataclass
class ThreadResult:
    id: int
    representative_title: str
    rolling_summary: Optional[str]
    known_facts: Optional[list]
    first_seen: str
    last_updated: str
    status: str
    tier: Optional[str]
    tier_reason: Optional[str]
    relevance_score: Optional[float]
    novelty_score: Optional[float]
    importance_score: Optional[float]
    diversity_score: Optional[float]
    time_sensitivity_score: Optional[float]
    source_diversity: Optional[float]
    confidence: Optional[float]
    novelty_label: Optional[str]


@dataclass
class ThreadMemberResult:
    id: int
    thread_id: int
    article_id: int
    classification_label: Optional[str]
    new_facts: Optional[list]
    reason: Optional[str]
    confidence: Optional[float]
    suppressed: bool
    assigned_at: str
    clean_title: Optional[str]
    url: Optional[str]


@dataclass
class BriefResult:
    id: int
    headline: Optional[str]
    intro: Optional[str]
    generated_at: Optional[str]
    period_start: str
    period_end: str
    model: Optional[str]
    topics: List[BriefTopicResult]


def _ready_base():
    return and_(
        Article.status == ArticleStatus.ready,
        Article.is_hidden == False,
    )


def _article_url(article: Article) -> Optional[str]:
    if isinstance(article.raw_payload, dict):
        return article.raw_payload.get("link") or article.raw_payload.get("url")
    return None


def _resolve_source_names(articles: List[Article], session: Session) -> Dict[int, str]:
    source_ids = {a.source_id for a in articles if a.source_id is not None}
    if not source_ids:
        return {}
    rows = session.execute(select(Source).where(Source.id.in_(source_ids))).scalars().all()
    return {s.id: s.name for s in rows}


def _to_result(article: Article, source_name: Optional[str] = None) -> ArticleResult:
    return ArticleResult(
        id=article.id,
        title=article.clean_title or article.feed_title,
        url=_article_url(article),
        source_id=article.source_id,
        source_name=source_name,
        feed_published_at=article.feed_published_at.isoformat() if article.feed_published_at else None,
        summary=article.summary,
        excerpt=article.excerpt,
        clean_text=article.clean_text,
        importance_score=article.importance_score,
        importance_reason=article.importance_reason,
        categories=article.categories,
        topics=article.topics,
        is_read=article.is_read,
        is_saved=article.is_saved,
        author=article.author,
        word_count=article.word_count,
        language=article.language,
    )


def _default_order(q):
    return q.order_by(
        Article.importance_score.desc().nulls_last(),
        Article.feed_published_at.desc().nulls_last(),
        Article.id.desc(),
    )


def search_articles(
    session: Session,
    query: str,
    *,
    limit: int = _DEFAULT_LIMIT,
    category: Optional[str] = None,
    source_id: Optional[int] = None,
    since: Optional[datetime] = None,
) -> List[ArticleResult]:
    """Full-text search using websearch_to_tsquery, honoring optional filters."""
    filters = [
        _ready_base(),
        Article.search_vector.op("@@")(func.websearch_to_tsquery("english", query)),
    ]
    if category is not None:
        filters.append(Article.categories.contains([category]))
    if source_id is not None:
        filters.append(Article.source_id == source_id)
    if since is not None:
        filters.append(Article.feed_published_at >= since)

    q = _default_order(select(Article).where(*filters)).limit(limit)
    articles = list(session.execute(q).scalars().all())
    names = _resolve_source_names(articles, session)
    return [_to_result(a, names.get(a.source_id)) for a in articles]


def list_articles(
    session: Session,
    view: str = "all",
    *,
    category: Optional[str] = None,
    source_id: Optional[int] = None,
    unread_only: bool = False,
    limit: int = _DEFAULT_LIMIT,
    important_threshold: int = _DEFAULT_IMPORTANT_THRESHOLD,
) -> List[ArticleResult]:
    """List articles by view with optional category/source/unread filters."""
    filters = [_ready_base()]

    if view == "unread":
        filters.append(Article.is_read == False)
    elif view == "important":
        filters.append(Article.importance_score >= important_threshold)
    elif view == "saved":
        filters.append(Article.is_saved == True)
    elif view == "uncategorized":
        filters.append(
            or_(
                Article.categories.is_(None),
                func.jsonb_typeof(Article.categories) == "null",
                and_(
                    func.jsonb_typeof(Article.categories) == "array",
                    func.jsonb_array_length(Article.categories) == 0,
                ),
            )
        )
    elif view == "today":
        today_start = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        filters.append(Article.feed_published_at >= today_start)
    elif view != "all":
        raise ValueError(f"Unknown view: {view!r}")

    if category is not None:
        filters.append(Article.categories.contains([category]))
    if source_id is not None:
        filters.append(Article.source_id == source_id)
    if unread_only and view != "unread":
        filters.append(Article.is_read == False)

    q = _default_order(select(Article).where(*filters)).limit(limit)
    articles = list(session.execute(q).scalars().all())
    names = _resolve_source_names(articles, session)
    return [_to_result(a, names.get(a.source_id)) for a in articles]


def _get_article_orm(session: Session, article_id: int) -> Article:
    article = session.get(Article, article_id)
    if article is None:
        raise ValueError(f"Article {article_id} not found")
    return article


def get_article(session: Session, article_id: int) -> ArticleResult:
    """Get a single article by id. Raises ValueError for an unknown article_id."""
    article = _get_article_orm(session, article_id)
    source_name = None
    if article.source_id is not None:
        source = session.get(Source, article.source_id)
        source_name = source.name if source else None
    return _to_result(article, source_name)


def mark_read(session: Session, article_id: int) -> dict:
    """Set is_read=True. Raises ValueError for an unknown article_id."""
    article = _get_article_orm(session, article_id)
    article.is_read = True
    session.commit()
    return asdict(get_article(session, article_id))


def mark_unread(session: Session, article_id: int) -> dict:
    """Set is_read=False. Raises ValueError for an unknown article_id."""
    article = _get_article_orm(session, article_id)
    article.is_read = False
    session.commit()
    return asdict(get_article(session, article_id))


def save_article(session: Session, article_id: int) -> dict:
    """Set is_saved=True. Raises ValueError for an unknown article_id."""
    article = _get_article_orm(session, article_id)
    article.is_saved = True
    session.commit()
    return asdict(get_article(session, article_id))


def unsave_article(session: Session, article_id: int) -> dict:
    """Set is_saved=False. Raises ValueError for an unknown article_id."""
    article = _get_article_orm(session, article_id)
    article.is_saved = False
    session.commit()
    return asdict(get_article(session, article_id))


def get_interest_profile(session: Session) -> str:
    """Return the user's interest profile text, or empty string when none exists."""
    row = session.get(InterestProfile, True)
    if row is None:
        return ""
    return row.profile_text or ""


def list_categories(session: Session) -> List[CategoryResult]:
    """Return enabled categories ordered by sort_order, name."""
    rows = session.execute(
        select(Category)
        .where(Category.enabled == True)
        .order_by(Category.sort_order, Category.name)
    ).scalars().all()
    return [
        CategoryResult(
            id=c.id,
            name=c.name,
            description=c.description,
            sort_order=c.sort_order,
        )
        for c in rows
    ]


def list_sources(session: Session) -> List[SourceResult]:
    """Return enabled feed sources ordered by name."""
    rows = session.execute(
        select(Source)
        .where(Source.enabled == True)
        .order_by(Source.name)
    ).scalars().all()
    return [
        SourceResult(
            id=s.id,
            name=s.name,
            feed_url=s.feed_url,
        )
        for s in rows
    ]


def get_latest_brief(session: Session) -> Optional[BriefResult]:
    """Return the newest ready brief with topics ordered by position, or None."""
    brief = session.execute(
        select(Brief)
        .where(Brief.status == "ready")
        .order_by(Brief.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if brief is None:
        return None
    topics = session.execute(
        select(BriefTopic)
        .where(BriefTopic.brief_id == brief.id)
        .order_by(BriefTopic.position)
    ).scalars().all()
    return BriefResult(
        id=brief.id,
        headline=brief.headline,
        intro=brief.intro,
        generated_at=brief.generated_at.isoformat() if brief.generated_at else None,
        period_start=brief.period_start.isoformat(),
        period_end=brief.period_end.isoformat(),
        model=brief.model,
        topics=[
            BriefTopicResult(
                position=t.position,
                headline=t.headline,
                what_happened=t.what_happened,
                why_it_matters=t.why_it_matters,
                historical_context=t.historical_context,
                refs=t.topic_refs,
            )
            for t in topics
        ],
    )


def _to_thread_result(thread: Thread) -> ThreadResult:
    return ThreadResult(
        id=thread.id,
        representative_title=thread.representative_title,
        rolling_summary=thread.rolling_summary,
        known_facts=thread.known_facts,
        first_seen=thread.first_seen.isoformat(),
        last_updated=thread.last_updated.isoformat(),
        status=thread.status,
        tier=thread.tier,
        tier_reason=thread.tier_reason,
        relevance_score=thread.relevance_score,
        novelty_score=thread.novelty_score,
        importance_score=thread.importance_score,
        diversity_score=thread.diversity_score,
        time_sensitivity_score=thread.time_sensitivity_score,
        source_diversity=thread.source_diversity,
        confidence=thread.confidence,
        novelty_label=thread.novelty_label,
    )


def list_threads(
    session: Session,
    *,
    tier: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
) -> List[ThreadResult]:
    """List threads with optional tier/status filters, ordered by last_updated desc."""
    filters = []
    if tier is not None:
        filters.append(Thread.tier == tier)
    if status is not None:
        filters.append(Thread.status == status)
    q = select(Thread)
    if filters:
        q = q.where(*filters)
    q = q.order_by(Thread.last_updated.desc()).limit(limit).offset(offset)
    return [_to_thread_result(t) for t in session.execute(q).scalars().all()]


def get_thread(session: Session, thread_id: int) -> Optional[ThreadResult]:
    """Get a single thread by id, or None if not found."""
    thread = session.get(Thread, thread_id)
    if thread is None:
        return None
    return _to_thread_result(thread)


def get_thread_members(session: Session, thread_id: int) -> List[ThreadMemberResult]:
    """Return all members (suppressed and non-suppressed) for a thread, with article details."""
    rows = session.execute(
        select(ThreadMembership, Article)
        .join(Article, ThreadMembership.article_id == Article.id)
        .where(ThreadMembership.thread_id == thread_id)
        .order_by(ThreadMembership.assigned_at.desc())
    ).all()
    return [
        ThreadMemberResult(
            id=tm.id,
            thread_id=tm.thread_id,
            article_id=tm.article_id,
            classification_label=tm.classification_label,
            new_facts=tm.new_facts,
            reason=tm.reason,
            confidence=tm.confidence,
            suppressed=tm.suppressed,
            assigned_at=tm.assigned_at.isoformat(),
            clean_title=a.clean_title or a.feed_title,
            url=_article_url(a),
        )
        for tm, a in rows
    ]


def list_unassigned_ready_articles(
    session: Session,
    since: datetime,
    limit: int = _DEFAULT_LIMIT,
) -> List[Article]:
    """Return ready articles with no thread_memberships row, published on or after since."""
    has_membership = exists().where(ThreadMembership.article_id == Article.id)
    q = (
        select(Article)
        .where(
            Article.status == ArticleStatus.ready,
            Article.feed_published_at >= since,
            ~has_membership,
        )
        .order_by(Article.feed_published_at.desc().nulls_last())
        .limit(limit)
    )
    return list(session.execute(q).scalars().all())


def count_suppressed_today(session: Session) -> int:
    """Count suppressed thread memberships whose assigned_at is on or after midnight UTC today."""
    today_midnight = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return session.execute(
        select(func.count()).select_from(ThreadMembership).where(
            ThreadMembership.suppressed == True,
            ThreadMembership.assigned_at >= today_midnight,
        )
    ).scalar_one()


def enqueue_brief(session: Session) -> dict:
    """Enqueue a manual brief. Returns {"status": "queued"} or {"status": "already_pending"}."""
    existing = session.execute(
        select(Brief).where(Brief.status.in_(["pending", "generating"])).limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return {"status": "already_pending"}
    today_start = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    new_brief = Brief(
        status="pending",
        origin="manual",
        period_start=today_start,
        period_end=today_start + timedelta(days=1),
    )
    session.add(new_brief)
    session.commit()
    return {"status": "queued"}
