from pathlib import Path
from typing import Generator, List, Optional
from types import SimpleNamespace
from urllib.parse import quote, urlencode

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from aggregator_common.db import SessionFactory, get_session
from aggregator_common.models import Article, Category, Source
from aggregator_common.version import version
from aggregator_web.config import WebSettings
from aggregator_web.feeds import (
    FeedPage,
    SmartViewName,
    category_feed,
    get_sidebar_counts,
    smart_feed,
    source_feed,
)
from aggregator_web.reader import FeedSpec, mark_all_read

_BASE_DIR = Path(__file__).parent

settings = WebSettings()

app = FastAPI(title="personal-aggregator web")

app.mount("/static", StaticFiles(directory=_BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=_BASE_DIR / "templates")


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


def _build_next_url(base: str, next_cursor: Optional[str], unread_only: bool) -> Optional[str]:
    if next_cursor is None:
        return None
    params: dict = {"cursor": next_cursor}
    if unread_only:
        params["unread"] = "1"
    return f"{base}?{urlencode(params)}"


def _render_feed(
    request: Request,
    page: FeedPage,
    session: Session,
    base_url: str,
    unread_only: bool,
    hx_request: Optional[str],
    cursor: Optional[str],
) -> Response:
    _enrich_articles(page.articles, session)
    next_url = _build_next_url(base_url, page.next_cursor, unread_only)

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
        {"articles": page.articles, "next_url": next_url},
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
        SimpleNamespace(id=s.id, name=s.name, unread_count=counts.sources.get(s.id, 0))
        for s in enabled_sources
    ]
    sidebar_categories = [
        SimpleNamespace(name=c.name, unread_count=counts.categories.get(c.name, 0))
        for c in enabled_categories
    ]

    return templates.TemplateResponse(
        request,
        "_sidebar.html",
        {
            "counts": counts.smart,
            "categories": sidebar_categories,
            "sources": sidebar_sources,
        },
    )


@app.get("/feed/smart/{view}")
def feed_smart(
    request: Request,
    view: SmartViewName,
    unread: int = 0,
    cursor: Optional[str] = None,
    hx_request: Optional[str] = Header(None, alias="HX-Request"),
    db: Session = Depends(get_db),
) -> Response:
    unread_only = bool(unread)
    page = smart_feed(
        view=view,
        session=db,
        page_size=settings.web_page_size,
        important_threshold=settings.web_important_threshold,
        cursor=cursor,
        unread_only=unread_only,
    )
    return _render_feed(request, page, db, f"/feed/smart/{view}", unread_only, hx_request, cursor)


@app.get("/feed/category/{name}")
def feed_category(
    request: Request,
    name: str,
    unread: int = 0,
    cursor: Optional[str] = None,
    hx_request: Optional[str] = Header(None, alias="HX-Request"),
    db: Session = Depends(get_db),
) -> Response:
    unread_only = bool(unread)
    page = category_feed(
        name=name,
        session=db,
        page_size=settings.web_page_size,
        cursor=cursor,
        unread_only=unread_only,
    )
    return _render_feed(request, page, db, f"/feed/category/{quote(name)}", unread_only, hx_request, cursor)


@app.get("/feed/source/{source_id}")
def feed_source(
    request: Request,
    source_id: int,
    unread: int = 0,
    cursor: Optional[str] = None,
    hx_request: Optional[str] = Header(None, alias="HX-Request"),
    db: Session = Depends(get_db),
) -> Response:
    unread_only = bool(unread)
    page = source_feed(
        source_id=source_id,
        session=db,
        page_size=settings.web_page_size,
        cursor=cursor,
        unread_only=unread_only,
    )
    return _render_feed(request, page, db, f"/feed/source/{source_id}", unread_only, hx_request, cursor)


@app.post("/feed/smart/{view}/read-all", status_code=200)
def smart_read_all(
    view: SmartViewName,
    db: Session = Depends(get_db),
) -> Response:
    mark_all_read(db, FeedSpec(type="smart", value=view), settings.web_important_threshold)
    return Response(status_code=200)


@app.post("/feed/category/{name}/read-all", status_code=200)
def category_read_all(
    name: str,
    db: Session = Depends(get_db),
) -> Response:
    mark_all_read(db, FeedSpec(type="category", value=name), settings.web_important_threshold)
    return Response(status_code=200)


@app.post("/feed/source/{source_id}/read-all", status_code=200)
def source_read_all(
    source_id: int,
    db: Session = Depends(get_db),
) -> Response:
    mark_all_read(db, FeedSpec(type="source", value=source_id), settings.web_important_threshold)
    return Response(status_code=200)
