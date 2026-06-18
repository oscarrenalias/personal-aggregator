from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional, Tuple

from sqlalchemy import and_, exists, func, or_, select, text
from sqlalchemy.orm import Session

from aggregator_common.models import Article, Brief, BriefTopic, Category, InterestProfile, Source, Thread, ThreadMembership
from aggregator_common.state import ArticleStatus

ViewName = Literal["all", "unread", "important", "saved", "uncategorized", "today"]

_DEFAULT_IMPORTANT_THRESHOLD = 70
_DEFAULT_LIMIT = 50


def _encode_cursor(values: tuple) -> str:
    payload = json.dumps(list(values), default=str)
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_cursor(cursor: str) -> tuple:
    payload = base64.urlsafe_b64decode(cursor.encode()).decode()
    return tuple(json.loads(payload))


def _article_keyset_filter(cursor_fp: Optional[str], cursor_id: int):
    """WHERE condition that restricts to rows after (feed_published_at, id) in DESC NULLS LAST order."""
    if cursor_fp is None:
        return and_(Article.feed_published_at.is_(None), Article.id < cursor_id)
    cursor_dt = datetime.fromisoformat(cursor_fp)
    return or_(
        Article.feed_published_at < cursor_dt,
        and_(Article.feed_published_at == cursor_dt, Article.id < cursor_id),
        Article.feed_published_at.is_(None),
    )


def _thread_keyset_filter(cursor_lu: str, cursor_id: int):
    """WHERE condition that restricts to rows after (last_updated, id) in DESC order.

    Matches the 'recent' sort (last_updated DESC, id DESC).
    """
    cursor_dt = datetime.fromisoformat(cursor_lu)
    return or_(
        Thread.last_updated < cursor_dt,
        and_(Thread.last_updated == cursor_dt, Thread.id < cursor_id),
    )


def _thread_keyset_filter_importance(cursor_tg, cursor_lu: str, cursor_id: int):
    """WHERE condition restricting to rows after (top_grade, last_updated, id) in the
    'importance' sort order (top_grade DESC NULLS LAST, last_updated DESC, id DESC).

    The keyset MUST match the ORDER BY columns or pages overlap/gap. cursor_tg may be
    None (the cursor sits within the NULLS-LAST section).
    """
    cursor_dt = datetime.fromisoformat(cursor_lu)
    lu_tiebreak = or_(
        Thread.last_updated < cursor_dt,
        and_(Thread.last_updated == cursor_dt, Thread.id < cursor_id),
    )
    if cursor_tg is None:
        # Cursor is already in the NULLS-LAST section: only further NULL-grade rows remain.
        return and_(Thread.top_grade.is_(None), lu_tiebreak)
    return or_(
        Thread.top_grade < cursor_tg,
        Thread.top_grade.is_(None),  # NULL grades sort after any non-NULL grade (nulls_last)
        and_(Thread.top_grade == cursor_tg, lu_tiebreak),
    )


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
    topics: Optional[list]
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
    """Projection of a Thread row with two computed counters.

    source_count: len(source_list) — distinct sources in the thread (derived, not stored).
    member_count: count of non-suppressed ThreadMembership rows (resolved at query time).
    dismissed: mirrors Thread.dismissed; never touched by the clusterer so dismissal persists across recomputation.
    """

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
    deltas: Optional[list]
    source_list: Optional[list]
    top_grade: Optional[int] = None
    surfaced: bool = False
    dismissed: bool = False
    source_count: int = 0
    member_count: int = 0
    image_url: Optional[str] = None
    has_updates: bool = True


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
    source_name: Optional[str] = None
    published_at: Optional[str] = None


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
    cursor: Optional[str] = None,
) -> Tuple[List[ArticleResult], Optional[str]]:
    """Full-text search using websearch_to_tsquery, honoring optional filters.

    Returns (results, next_cursor). next_cursor is None when there are no further pages.
    Pass cursor to fetch the next page; omit for the first page (behaviour is identical to before).
    """
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
    if cursor is not None:
        cursor_fp, cursor_id = _decode_cursor(cursor)
        filters.append(_article_keyset_filter(cursor_fp, int(cursor_id)))

    q = _default_order(select(Article).where(*filters)).limit(limit)
    articles = list(session.execute(q).scalars().all())
    names = _resolve_source_names(articles, session)
    results = [_to_result(a, names.get(a.source_id)) for a in articles]
    next_cursor: Optional[str] = None
    if len(articles) == limit:
        last = articles[-1]
        next_cursor = _encode_cursor((
            last.feed_published_at.isoformat() if last.feed_published_at else None,
            last.id,
        ))
    return results, next_cursor


def list_articles(
    session: Session,
    view: str = "all",
    *,
    category: Optional[str] = None,
    source_id: Optional[int] = None,
    unread_only: bool = False,
    limit: int = _DEFAULT_LIMIT,
    important_threshold: int = _DEFAULT_IMPORTANT_THRESHOLD,
    cursor: Optional[str] = None,
) -> Tuple[List[ArticleResult], Optional[str]]:
    """List articles by view with optional category/source/unread filters.

    Returns (results, next_cursor). next_cursor is None when there are no further pages.
    Pass cursor to fetch the next page; omit for the first page (behaviour is identical to before).
    """
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
    if cursor is not None:
        cursor_fp, cursor_id = _decode_cursor(cursor)
        filters.append(_article_keyset_filter(cursor_fp, int(cursor_id)))

    q = _default_order(select(Article).where(*filters)).limit(limit)
    articles = list(session.execute(q).scalars().all())
    names = _resolve_source_names(articles, session)
    results = [_to_result(a, names.get(a.source_id)) for a in articles]
    next_cursor: Optional[str] = None
    if len(articles) == limit:
        last = articles[-1]
        next_cursor = _encode_cursor((
            last.feed_published_at.isoformat() if last.feed_published_at else None,
            last.id,
        ))
    return results, next_cursor


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


def _to_thread_result(thread: Thread, member_count: int = 0, image_url: Optional[str] = None) -> ThreadResult:
    has_updates = thread.last_viewed_at is None or thread.last_updated > thread.last_viewed_at
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
        deltas=thread.deltas,
        source_list=thread.source_list,
        top_grade=thread.top_grade,
        surfaced=thread.surfaced,
        dismissed=thread.dismissed,
        source_count=len(thread.source_list) if thread.source_list else 0,
        member_count=member_count,
        image_url=image_url,
        has_updates=has_updates,
    )


ThreadSortMode = Literal["importance", "recent"]


def list_threads(
    session: Session,
    *,
    status: Optional[str] = None,
    sort: ThreadSortMode = "importance",
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
    include_dismissed: bool = False,
    cursor: Optional[str] = None,
) -> Tuple[List[ThreadResult], Optional[str]]:
    """List surfaced threads updated within the last 7 days.

    By default dismissed threads are excluded. Pass include_dismissed=True to include them
    (e.g. for a 'Show dismissed' view). get_thread and get_thread_members always return
    dismissed threads by id regardless of this parameter.

    Returns (results, next_cursor). next_cursor is None when there are no further pages.
    Pass cursor to fetch the next page; omit for the first page (behaviour is identical to before).
    cursor and offset are mutually exclusive; cursor takes precedence when both are supplied.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=7)
    filters: list = [
        Thread.surfaced == True,
        Thread.last_updated >= cutoff,
    ]
    if not include_dismissed:
        filters.append(Thread.dismissed == False)
    if status is not None:
        filters.append(Thread.status == status)
    if cursor is not None:
        decoded = _decode_cursor(cursor)
        if sort == "recent":
            cursor_lu, cursor_id = decoded
            filters.append(_thread_keyset_filter(str(cursor_lu), int(cursor_id)))
        else:
            cursor_tg, cursor_lu, cursor_id = decoded
            filters.append(
                _thread_keyset_filter_importance(cursor_tg, str(cursor_lu), int(cursor_id))
            )
    order = (
        (Thread.last_updated.desc(), Thread.id.desc())
        if sort == "recent"
        else (Thread.top_grade.desc().nulls_last(), Thread.last_updated.desc(), Thread.id.desc())
    )
    effective_offset = 0 if cursor is not None else offset
    q = (
        select(Thread)
        .where(*filters)
        .order_by(*order)
        .limit(limit)
        .offset(effective_offset)
    )
    threads = list(session.execute(q).scalars().all())

    member_counts: Dict[int, int] = {}
    image_urls: Dict[int, str] = {}
    if threads:
        thread_ids = [t.id for t in threads]
        rows = session.execute(
            select(ThreadMembership.thread_id, func.count().label("cnt"))
            .where(
                ThreadMembership.thread_id.in_(thread_ids),
                ThreadMembership.suppressed == False,
            )
            .group_by(ThreadMembership.thread_id)
        ).all()
        member_counts = {row.thread_id: row.cnt for row in rows}

        # DISTINCT ON with importance_score DESC + feed_published_at DESC picks one representative image per thread.
        img_rows = session.execute(
            select(ThreadMembership.thread_id, Article.header_image_url)
            .join(Article, ThreadMembership.article_id == Article.id)
            .where(
                ThreadMembership.thread_id.in_(thread_ids),
                ThreadMembership.suppressed == False,
                Article.header_image_url.isnot(None),
                Article.header_image_url != "",
            )
            .distinct(ThreadMembership.thread_id)
            .order_by(
                ThreadMembership.thread_id,
                Article.importance_score.desc().nulls_last(),
                Article.feed_published_at.desc().nulls_last(),
            )
        ).all()
        image_urls = {row.thread_id: row.header_image_url for row in img_rows}

    thread_results = [_to_thread_result(t, member_counts.get(t.id, 0), image_urls.get(t.id)) for t in threads]
    next_cursor: Optional[str] = None
    if len(threads) == limit:
        last = threads[-1]
        if sort == "recent":
            next_cursor = _encode_cursor((last.last_updated.isoformat(), last.id))
        else:
            next_cursor = _encode_cursor((last.top_grade, last.last_updated.isoformat(), last.id))
    return thread_results, next_cursor


def get_thread(session: Session, thread_id: int) -> Optional[ThreadResult]:
    """Get a single thread by id, or None if not found."""
    thread = session.get(Thread, thread_id)
    if thread is None:
        return None
    member_count = session.execute(
        select(func.count()).select_from(ThreadMembership).where(
            ThreadMembership.thread_id == thread_id,
            ThreadMembership.suppressed == False,
        )
    ).scalar_one()
    # Highest importance_score wins; most-recent feed_published_at breaks ties.
    image_url = session.execute(
        select(Article.header_image_url)
        .join(ThreadMembership, ThreadMembership.article_id == Article.id)
        .where(
            ThreadMembership.thread_id == thread_id,
            ThreadMembership.suppressed == False,
            Article.header_image_url.isnot(None),
            Article.header_image_url != "",
        )
        .order_by(
            Article.importance_score.desc().nulls_last(),
            Article.feed_published_at.desc().nulls_last(),
        )
        .limit(1)
    ).scalar_one_or_none()
    return _to_thread_result(thread, member_count, image_url)


def get_thread_members(session: Session, thread_id: int) -> List[ThreadMemberResult]:
    """Return all members (suppressed and non-suppressed) for a thread, with article details."""
    rows = session.execute(
        select(ThreadMembership, Article)
        .join(Article, ThreadMembership.article_id == Article.id)
        .where(ThreadMembership.thread_id == thread_id)
        .order_by(ThreadMembership.assigned_at.desc())
    ).all()
    articles = [a for _, a in rows]
    source_names = _resolve_source_names(articles, session)
    def _published_at(a: Article) -> Optional[str]:
        ts = a.feed_published_at or a.retrieved_at
        return ts.isoformat() if ts else None

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
            source_name=source_names.get(a.source_id),
            published_at=_published_at(a),
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


@dataclass
class LlmStatResult:
    service: str
    model: str
    request_count: int
    total_cost_usd: Optional[float]
    avg_cost_usd: Optional[float]
    avg_prompt_tokens: Optional[float]
    p95_prompt_tokens: Optional[float]
    avg_completion_tokens: Optional[float]
    truncated_count: int
    error_count: int
    error_pct: Optional[float]
    avg_tool_calls: Optional[float]
    max_tool_calls: Optional[int]


def llm_stats(session: Session, days: int) -> List[LlmStatResult]:
    """Aggregate LLM call stats grouped by (service, model) over the last N days."""
    sql = text("""
        SELECT
            service,
            model,
            COUNT(*) AS request_count,
            SUM(cost_usd) AS total_cost_usd,
            AVG(cost_usd) AS avg_cost_usd,
            AVG(prompt_tokens) AS avg_prompt_tokens,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY prompt_tokens) AS p95_prompt_tokens,
            AVG(completion_tokens) AS avg_completion_tokens,
            COUNT(*) FILTER (WHERE finish_reason = 'length') AS truncated_count,
            COUNT(*) FILTER (WHERE status = 'error') AS error_count,
            ROUND(
                100.0 * COUNT(*) FILTER (WHERE status = 'error') / COUNT(*),
                2
            ) AS error_pct,
            AVG(num_tool_calls) AS avg_tool_calls,
            MAX(num_tool_calls) AS max_tool_calls
        FROM llm_calls
        WHERE created_at >= NOW() - INTERVAL '1 day' * :days
        GROUP BY service, model
        ORDER BY service, model
    """)
    rows = session.execute(sql, {"days": days}).mappings().all()
    return [
        LlmStatResult(
            service=row["service"],
            model=row["model"],
            request_count=int(row["request_count"]),
            total_cost_usd=float(row["total_cost_usd"]) if row["total_cost_usd"] is not None else None,
            avg_cost_usd=float(row["avg_cost_usd"]) if row["avg_cost_usd"] is not None else None,
            avg_prompt_tokens=float(row["avg_prompt_tokens"]) if row["avg_prompt_tokens"] is not None else None,
            p95_prompt_tokens=float(row["p95_prompt_tokens"]) if row["p95_prompt_tokens"] is not None else None,
            avg_completion_tokens=float(row["avg_completion_tokens"]) if row["avg_completion_tokens"] is not None else None,
            truncated_count=int(row["truncated_count"]),
            error_count=int(row["error_count"]),
            error_pct=float(row["error_pct"]) if row["error_pct"] is not None else None,
            avg_tool_calls=float(row["avg_tool_calls"]) if row["avg_tool_calls"] is not None else None,
            max_tool_calls=int(row["max_tool_calls"]) if row["max_tool_calls"] is not None else None,
        )
        for row in rows
    ]


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
