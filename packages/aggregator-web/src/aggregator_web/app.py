import mimetypes
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator, List, Optional
from types import SimpleNamespace
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from markupsafe import Markup, escape

mimetypes.add_type("application/manifest+json", ".webmanifest")

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from aggregator_common.db import SessionFactory, get_session
from aggregator_common.management import enqueue_recluster, mark_thread_viewed, set_thread_dismissed
from aggregator_common.models import Article, Brief, BriefTopic, Category, Source
from aggregator_common.queries import (
    get_thread,
    get_thread_members,
    list_threads,
)
from aggregator_common.version import version
from aggregator_web.config import WebSettings
from aggregator_web.feeds import (
    CategoryEntry,
    FeedPage,
    SmartViewName,
    SourceEntry,
    category_feed,
    category_feed_count,
    category_feed_max_id,
    get_sidebar_counts,
    smart_feed,
    smart_feed_count,
    smart_feed_max_id,
    source_feed,
    source_feed_count,
    source_feed_max_id,
)
from aggregator_web.reader import (
    ArticleNotFoundError,
    FeedSpec,
    mark_all_read,
    mark_read,
    mark_unread,
    save,
    unsave,
)

_BASE_DIR = Path(__file__).parent

settings = WebSettings()
_brief_tz = ZoneInfo(settings.brief_timezone)

app = FastAPI(title="personal-aggregator web")

app.mount("/static", StaticFiles(directory=_BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=_BASE_DIR / "templates")


def _paragraphs_filter(text: str) -> Markup:
    """Convert plain text to HTML paragraphs.

    Splits on newlines, discards blank lines, HTML-escapes each line via
    markupsafe.escape, then wraps in <p> tags.  Returns a Markup object so
    Jinja does not double-escape the output.  Used as the ``paragraphs``
    template filter on article.summary and article.clean_text.
    """
    lines = [escape(line) for line in text.splitlines() if line.strip()]
    return Markup("".join(f"<p>{line}</p>" for line in lines))


def _timeago_filter(dt_str: str) -> str:
    """Convert an ISO datetime string to a human-relative string (e.g. '3h ago')."""
    try:
        dt = datetime.fromisoformat(str(dt_str).replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
        s = int(delta.total_seconds())
        if s < 60:
            return "just now"
        if s < 3600:
            m = s // 60
            return f"{m}m ago"
        if s < 86400:
            h = s // 3600
            return f"{h}h ago"
        d = s // 86400
        if d == 1:
            return "yesterday"
        if d < 7:
            return f"{d}d ago"
        return dt.strftime("%-d %b %Y")
    except (ValueError, AttributeError):
        return str(dt_str)[:10] if dt_str else ""


def _format_brief_date(dt: datetime, fmt: str = "%-d %b %Y") -> str:
    """Format a datetime in the configured brief timezone for display in templates."""
    if dt is None:
        return ""
    return dt.astimezone(_brief_tz).strftime(fmt)


templates.env.filters["paragraphs"] = _paragraphs_filter
templates.env.filters["timeago"] = _timeago_filter
templates.env.globals["format_brief_date"] = _format_brief_date


def get_db() -> Generator[Session, None, None]:
    db = SessionFactory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _enrich_articles(articles: List[Article], session: Session) -> None:
    """Attach source_name attribute to each article for template rendering."""
    source_ids = {a.source_id for a in articles if a.source_id is not None}
    if source_ids:
        sources = session.execute(
            select(Source).where(Source.id.in_(source_ids))
        ).scalars().all()
        name_map = {s.id: s.name for s in sources}
    else:
        name_map = {}
    for a in articles:
        a.source_name = name_map.get(a.source_id, "")  # type: ignore[attr-defined]


def _enrich_article(article: Article, session: Session) -> None:
    """Attach source_name attribute to a single article for template rendering."""
    if article.source_id is not None:
        source = session.get(Source, article.source_id)
        article.source_name = source.name if source else ""  # type: ignore[attr-defined]
    else:
        article.source_name = ""  # type: ignore[attr-defined]


def _pick_topic_image(refs: list, image_map: dict) -> Optional[str]:
    """Pick best header image for a topic's refs (highest importance, most-recent tie-break)."""
    candidates = []
    for ref in refs:
        if ref.get("internal") and ref.get("article_id") is not None:
            row = image_map.get(int(ref["article_id"]))
            if row and row["header_image_url"]:
                candidates.append(row)
    if not candidates:
        return None
    candidates.sort(
        key=lambda r: (
            r["importance_score"] if r["importance_score"] is not None else -1,
            r["feed_published_at"].timestamp() if r["feed_published_at"] is not None else 0.0,
        ),
        reverse=True,
    )
    return candidates[0]["header_image_url"]


def _attach_brief_images(briefs: List[Brief], session: Session) -> None:
    """Single DB query for all brief topic images regardless of brief/topic count; avoids N+1 per topic."""
    all_article_ids: set = set()
    for brief in briefs:
        for topic in brief.topics:
            for ref in topic.topic_refs or []:
                if ref.get("internal") and ref.get("article_id") is not None:
                    all_article_ids.add(int(ref["article_id"]))

    image_map: dict = {}
    if all_article_ids:
        rows = session.execute(
            select(
                Article.id,
                Article.header_image_url,
                Article.importance_score,
                Article.feed_published_at,
            ).where(Article.id.in_(all_article_ids))
        ).all()
        for row in rows:
            image_map[row.id] = {
                "header_image_url": row.header_image_url,
                "importance_score": row.importance_score,
                "feed_published_at": row.feed_published_at,
            }

    for brief in briefs:
        for topic in brief.topics:
            img = _pick_topic_image(topic.topic_refs or [], image_map)
            topic.image_url = img  # type: ignore[attr-defined]


def _render_interaction_response(
    request: Request,
    article: Article,
    hx_target: Optional[str],
) -> Response:
    """Return primary fragment + OOB counterpart so both list card and detail pane stay in sync.

    When the reader pane is the target (HX-Target == 'article-detail'), the detail
    fragment is primary and the matching card is appended with hx-swap-oob="true".
    When a card is the target, the card is primary and the detail pane is appended
    OOB.  HTMX only applies the OOB swap when the target element exists in the DOM,
    so it silently no-ops when the other representation is not currently rendered.

    Every response also sets ``HX-Trigger: refreshSidebar`` to keep sidebar counts
    in sync.
    """
    if hx_target == "article-detail":
        primary = templates.get_template("_article_detail.html").render(article=article)
        oob = templates.get_template("_article_card.html").render(
            article=article, is_last=False, next_url=None, oob=True
        )
    else:
        primary = templates.get_template("_article_card.html").render(
            article=article, is_last=False, next_url=None
        )
        oob = templates.get_template("_article_detail.html").render(
            article=article, oob=True
        )
    response: Response = HTMLResponse(primary + oob)
    response.headers["HX-Trigger"] = "refreshSidebar"
    return response


def _build_next_url(
    base: str,
    next_cursor: Optional[str],
    unread_only: bool,
    sort: str = "relevance",
) -> Optional[str]:
    if next_cursor is None:
        return None
    params: dict = {"cursor": next_cursor}
    if unread_only:
        params["unread"] = "1"
    if sort != "relevance":
        params["sort"] = sort
    return f"{base}?{urlencode(params)}"


def _render_feed(
    request: Request,
    page: FeedPage,
    session: Session,
    base_url: str,
    unread_only: bool,
    hx_request: Optional[str],
    cursor: Optional[str],
    newest_id: int = 0,
    sort: str = "relevance",
) -> Response:
    _enrich_articles(page.articles, session)
    next_url = _build_next_url(base_url, page.next_cursor, unread_only, sort)

    # Pagination (infinite-scroll) request: return card fragments only so HTMX
    # can append them after the last card (hx-swap="afterend").
    if hx_request and cursor:
        rendered = "".join(
            templates.get_template("_article_card.html").render(
                article=article,
                is_last=(i == len(page.articles) - 1),
                next_url=next_url,
            )
            for i, article in enumerate(page.articles)
        )
        return HTMLResponse(rendered)

    return templates.TemplateResponse(
        request,
        "_article_list.html",
        {
            "articles": page.articles,
            "next_url": next_url,
            "newest_id": newest_id,
            "base_url": base_url,
            "unread_only": unread_only,
            "sort": sort,
        },
    )


@app.get("/healthz")
def healthz() -> JSONResponse:
    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"

    status_code = 200 if db_status == "ok" else 500
    return JSONResponse(
        status_code=status_code,
        content={"version": version(), "db": db_status},
    )


@app.get("/")
def index(request: Request) -> Response:
    return templates.TemplateResponse(request, "shell.html", {})


@app.get("/sidebar")
def sidebar(request: Request, db: Session = Depends(get_db)) -> Response:
    counts = get_sidebar_counts(db, settings.web_important_threshold)

    enabled_sources = db.execute(
        select(Source).where(Source.enabled == True).order_by(Source.name)  # noqa: E712
    ).scalars().all()
    enabled_categories = db.execute(
        select(Category)
        .where(Category.enabled == True)  # noqa: E712
        .order_by(Category.sort_order, Category.name)
    ).scalars().all()

    sidebar_sources = [
        SimpleNamespace(
            id=s.id,
            name=s.name,
            unread_count=counts.sources.get(s.id, SourceEntry()).count,
            has_new=counts.sources.get(s.id, SourceEntry()).has_new,
            has_priority=counts.sources.get(s.id, SourceEntry()).has_priority,
        )
        for s in enabled_sources
    ]
    sidebar_categories = [
        SimpleNamespace(
            name=c.name,
            unread_count=counts.categories.get(c.name, CategoryEntry()).count,
            has_new=counts.categories.get(c.name, CategoryEntry()).has_new,
            has_priority=counts.categories.get(c.name, CategoryEntry()).has_priority,
            last_activity=counts.categories.get(c.name, CategoryEntry()).last_activity,
        )
        for c in enabled_categories
    ]

    _now = datetime.utcnow()
    return templates.TemplateResponse(
        request,
        "_sidebar.html",
        {
            "counts": {k: v.count for k, v in counts.smart.items()},
            "smart_entries": counts.smart,
            "categories": sidebar_categories,
            "sources": sidebar_sources,
            "show_unread_counts": settings.web_show_unread_counts,
            "utcnow_date": _now.date(),
            "yesterday_date": (_now - timedelta(days=1)).date(),
        },
    )


_VALID_SORT_VALUES = {"relevance", "newest"}


def _normalize_sort(sort: str) -> str:
    return sort if sort in _VALID_SORT_VALUES else "relevance"


@app.get("/feed/smart/{view}")
def feed_smart(
    request: Request,
    view: SmartViewName,
    unread: int = 0,
    cursor: Optional[str] = None,
    sort: str = "relevance",
    hx_request: Optional[str] = Header(None, alias="HX-Request"),
    db: Session = Depends(get_db),
) -> Response:
    unread_only = bool(unread)
    sort = _normalize_sort(sort)
    page = smart_feed(
        view=view,
        session=db,
        page_size=settings.web_page_size,
        important_threshold=settings.web_important_threshold,
        cursor=cursor,
        unread_only=unread_only,
        sort=sort,
    )
    newest_id = smart_feed_max_id(
        view=view,
        session=db,
        important_threshold=settings.web_important_threshold,
        unread_only=unread_only,
    )
    return _render_feed(request, page, db, f"/feed/smart/{view}", unread_only, hx_request, cursor, newest_id=newest_id, sort=sort)


@app.get("/feed/category/{name}")
def feed_category(
    request: Request,
    name: str,
    unread: int = 0,
    cursor: Optional[str] = None,
    sort: str = "relevance",
    hx_request: Optional[str] = Header(None, alias="HX-Request"),
    db: Session = Depends(get_db),
) -> Response:
    unread_only = bool(unread)
    sort = _normalize_sort(sort)
    page = category_feed(
        name=name,
        session=db,
        page_size=settings.web_page_size,
        cursor=cursor,
        unread_only=unread_only,
        sort=sort,
    )
    newest_id = category_feed_max_id(name=name, session=db, unread_only=unread_only)
    return _render_feed(request, page, db, f"/feed/category/{quote(name)}", unread_only, hx_request, cursor, newest_id=newest_id, sort=sort)


@app.get("/feed/source/{source_id}")
def feed_source(
    request: Request,
    source_id: int,
    unread: int = 0,
    cursor: Optional[str] = None,
    sort: str = "relevance",
    hx_request: Optional[str] = Header(None, alias="HX-Request"),
    db: Session = Depends(get_db),
) -> Response:
    unread_only = bool(unread)
    sort = _normalize_sort(sort)
    page = source_feed(
        source_id=source_id,
        session=db,
        page_size=settings.web_page_size,
        cursor=cursor,
        unread_only=unread_only,
        sort=sort,
    )
    newest_id = source_feed_max_id(source_id=source_id, session=db, unread_only=unread_only)
    return _render_feed(request, page, db, f"/feed/source/{source_id}", unread_only, hx_request, cursor, newest_id=newest_id, sort=sort)


@app.post("/feed/smart/{view}/read-all", status_code=200)
def smart_read_all(
    view: SmartViewName,
    db: Session = Depends(get_db),
) -> Response:
    mark_all_read(db, FeedSpec(type="smart", value=view), settings.web_important_threshold)
    return Response(status_code=200, headers={"HX-Trigger": "refreshSidebar"})


@app.post("/feed/category/{name}/read-all", status_code=200)
def category_read_all(
    name: str,
    db: Session = Depends(get_db),
) -> Response:
    mark_all_read(db, FeedSpec(type="category", value=name), settings.web_important_threshold)
    return Response(status_code=200, headers={"HX-Trigger": "refreshSidebar"})


@app.post("/feed/source/{source_id}/read-all", status_code=200)
def source_read_all(
    source_id: int,
    db: Session = Depends(get_db),
) -> Response:
    mark_all_read(db, FeedSpec(type="source", value=source_id), settings.web_important_threshold)
    return Response(status_code=200, headers={"HX-Trigger": "refreshSidebar"})


@app.get("/feed/smart/{view}/updates")
def smart_feed_updates(
    request: Request,
    view: SmartViewName,
    since: int,
    unread: int = 0,
    sort: str = "relevance",
    db: Session = Depends(get_db),
) -> Response:
    unread_only = bool(unread)
    sort = _normalize_sort(sort)
    count = smart_feed_count(
        view=view,
        session=db,
        since=since,
        important_threshold=settings.web_important_threshold,
        unread_only=unread_only,
    )
    return templates.TemplateResponse(
        request,
        "_new_articles_pill.html",
        {"count": count, "base_url": f"/feed/smart/{view}", "unread_only": unread_only, "sort": sort},
    )


@app.get("/feed/category/{name}/updates")
def category_feed_updates(
    request: Request,
    name: str,
    since: int,
    unread: int = 0,
    sort: str = "relevance",
    db: Session = Depends(get_db),
) -> Response:
    unread_only = bool(unread)
    sort = _normalize_sort(sort)
    count = category_feed_count(
        name=name,
        session=db,
        since=since,
        unread_only=unread_only,
    )
    return templates.TemplateResponse(
        request,
        "_new_articles_pill.html",
        {"count": count, "base_url": f"/feed/category/{name}", "unread_only": unread_only, "sort": sort},
    )


@app.get("/feed/source/{source_id}/updates")
def source_feed_updates(
    request: Request,
    source_id: int,
    since: int,
    unread: int = 0,
    sort: str = "relevance",
    db: Session = Depends(get_db),
) -> Response:
    unread_only = bool(unread)
    sort = _normalize_sort(sort)
    count = source_feed_count(
        source_id=source_id,
        session=db,
        since=since,
        unread_only=unread_only,
    )
    return templates.TemplateResponse(
        request,
        "_new_articles_pill.html",
        {"count": count, "base_url": f"/feed/source/{source_id}", "unread_only": unread_only, "sort": sort},
    )


@app.get("/article/{article_id}")
def article_detail(
    request: Request,
    article_id: int,
    hx_request: Optional[str] = Header(None, alias="HX-Request"),
    db: Session = Depends(get_db),
) -> Response:
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    _enrich_article(article, db)
    if hx_request:
        return templates.TemplateResponse(
            request,
            "_article_detail.html",
            {"article": article},
        )
    # Direct browser navigation (deep link / open-in-new-tab / refresh):
    # return the full shell with the article pre-loaded into #reader-content.
    initial_content = Markup(
        templates.get_template("_article_detail.html").render(article=article)
    )
    return templates.TemplateResponse(
        request,
        "shell.html",
        {"initial_reader_content": initial_content, "initial_reader_open": True},
    )


@app.post("/article/{article_id}/read")
def article_read(
    request: Request,
    article_id: int,
    hx_target: Optional[str] = Header(None, alias="HX-Target"),
    db: Session = Depends(get_db),
) -> Response:
    try:
        article = mark_read(db, article_id)
    except ArticleNotFoundError:
        raise HTTPException(status_code=404, detail="Article not found")
    _enrich_article(article, db)
    return _render_interaction_response(request, article, hx_target)


@app.post("/article/{article_id}/unread")
def article_unread(
    request: Request,
    article_id: int,
    hx_target: Optional[str] = Header(None, alias="HX-Target"),
    db: Session = Depends(get_db),
) -> Response:
    try:
        article = mark_unread(db, article_id)
    except ArticleNotFoundError:
        raise HTTPException(status_code=404, detail="Article not found")
    _enrich_article(article, db)
    return _render_interaction_response(request, article, hx_target)


@app.post("/article/{article_id}/save")
def article_save(
    request: Request,
    article_id: int,
    hx_target: Optional[str] = Header(None, alias="HX-Target"),
    db: Session = Depends(get_db),
) -> Response:
    try:
        article = save(db, article_id)
    except ArticleNotFoundError:
        raise HTTPException(status_code=404, detail="Article not found")
    _enrich_article(article, db)
    return _render_interaction_response(request, article, hx_target)


@app.post("/article/{article_id}/unsave")
def article_unsave(
    request: Request,
    article_id: int,
    hx_target: Optional[str] = Header(None, alias="HX-Target"),
    db: Session = Depends(get_db),
) -> Response:
    try:
        article = unsave(db, article_id)
    except ArticleNotFoundError:
        raise HTTPException(status_code=404, detail="Article not found")
    _enrich_article(article, db)
    return _render_interaction_response(request, article, hx_target)


@app.get("/today")
def today(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    briefs = list(
        db.execute(
            select(Brief)
            .where(Brief.status == "ready")
            .order_by(Brief.created_at.desc())
            .limit(30)
            .options(selectinload(Brief.topics))
        ).scalars().all()
    )
    generating = (
        db.execute(
            select(Brief).where(Brief.status.in_(["pending", "generating"])).limit(1)
        ).scalar_one_or_none()
        is not None
    )
    _attach_brief_images(briefs, db)
    selected_id = briefs[0].id if briefs else None
    return templates.TemplateResponse(
        request,
        "_today.html",
        {"briefs": briefs, "generating": generating, "selected_id": selected_id},
    )


@app.post("/today/refresh")
def today_refresh(
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    now_local = datetime.now(timezone.utc).astimezone(_brief_tz)
    today_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = today_start_local.astimezone(timezone.utc)
    today_end = (today_start_local + timedelta(days=1)).astimezone(timezone.utc)

    existing = db.execute(
        select(Brief)
        .where(Brief.status.in_(["pending", "generating"]))
        .limit(1)
    ).scalar_one_or_none()

    if existing is None:
        new_brief = Brief(
            status="pending",
            origin="manual",
            period_start=today_start,
            period_end=today_end,
        )
        db.add(new_brief)
        db.flush()

    briefs = list(
        db.execute(
            select(Brief)
            .where(Brief.status == "ready")
            .order_by(Brief.created_at.desc())
            .limit(30)
            .options(selectinload(Brief.topics))
        ).scalars().all()
    )
    _attach_brief_images(briefs, db)
    selected_id = briefs[0].id if briefs else None
    return templates.TemplateResponse(
        request,
        "_today.html",
        {"briefs": briefs, "generating": True, "selected_id": selected_id},
    )


@app.get("/brief/{brief_id}")
def brief_detail_view(
    brief_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    brief = db.execute(
        select(Brief)
        .where(Brief.id == brief_id)
        .options(selectinload(Brief.topics))
    ).scalar_one_or_none()
    if brief is None:
        raise HTTPException(status_code=404, detail="Brief not found")
    _attach_brief_images([brief], db)
    return templates.TemplateResponse(
        request,
        "_brief_detail.html",
        {"brief": brief},
    )


_VALID_THREAD_SORT_VALUES = {"importance", "recent"}


def _normalize_thread_sort(sort: str) -> str:
    return sort if sort in _VALID_THREAD_SORT_VALUES else "importance"


@app.get("/threads")
def threads_index(
    request: Request,
    sort: str = "importance",
    show_dismissed: bool = False,
    hx_request: Optional[str] = Header(None, alias="HX-Request"),
    db: Session = Depends(get_db),
) -> Response:
    sort = _normalize_thread_sort(sort)
    threads = list_threads(db, sort=sort, include_dismissed=show_dismissed)
    ctx = {"threads": threads, "sort": sort, "base_url": "/threads", "show_dismissed": show_dismissed}
    if hx_request:
        return templates.TemplateResponse(request, "_thread_list.html", ctx)
    return templates.TemplateResponse(request, "threads/index.html", ctx)


@app.get("/threads/{thread_id}")
def thread_detail(
    request: Request,
    thread_id: int,
    hx_request: Optional[str] = Header(None, alias="HX-Request"),
    db: Session = Depends(get_db),
) -> Response:
    thread = get_thread(db, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    mark_thread_viewed(db, thread_id)
    members = get_thread_members(db, thread_id)
    ctx = {"thread": thread, "members": members}
    if hx_request:
        return templates.TemplateResponse(
            request, "_thread_detail.html", ctx
        )
    initial_content = Markup(
        templates.get_template("_thread_detail.html").render(**ctx)
    )
    return templates.TemplateResponse(
        request,
        "shell.html",
        {"initial_reader_content": initial_content, "initial_reader_open": True},
    )


@app.post("/threads/{thread_id}/dismiss", status_code=200)
def thread_dismiss(thread_id: int, db: Session = Depends(get_db)) -> Response:
    thread = set_thread_dismissed(db, thread_id, True)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return Response(status_code=200, headers={"HX-Trigger": "refreshThreadList"})


@app.post("/threads/{thread_id}/restore", status_code=200)
def thread_restore(thread_id: int, db: Session = Depends(get_db)) -> Response:
    thread = set_thread_dismissed(db, thread_id, False)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return Response(status_code=200, headers={"HX-Trigger": "refreshThreadList"})


@app.post("/threads/recluster", status_code=202)
def threads_recluster(db: Session = Depends(get_db)) -> Response:
    enqueue_recluster(db)
    return Response(status_code=202, headers={"HX-Trigger": "reclustered"})


@app.get("/search")
def search(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
) -> Response:
    articles: List[Article] = []
    if q.strip():
        rows = db.execute(
            select(Article)
            .where(Article.status == "ready")
            .where(Article.search_vector.op("@@")(func.websearch_to_tsquery("english", q)))
            .order_by(
                Article.importance_score.desc().nulls_last(),
                Article.feed_published_at.desc().nulls_last(),
                Article.id.desc(),
            )
            .limit(settings.web_page_size)
        ).scalars().all()
        articles = list(rows)
        _enrich_articles(articles, db)

    return templates.TemplateResponse(
        request,
        "_search.html",
        {"query": q, "articles": articles, "next_url": None},
    )
