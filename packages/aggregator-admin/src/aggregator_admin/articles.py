from __future__ import annotations

from typing import Any, Optional

import typer
from sqlalchemy import func

from aggregator_common.db import get_session
from aggregator_common.models import Article
from aggregator_common.state import ArticleStatus, can_transition

from .output import json_or_table

articles_app = typer.Typer(help="Inspect and operate on articles.")

_LIST_COLUMNS = ["id", "source_id", "status", "feed_title", "feed_published_at", "retrieved_at"]

_SHOW_COLUMNS = [
    "id",
    "source_id",
    "status",
    "claimed_by",
    "claimed_at",
    "retry_count",
    "next_retry_at",
    "last_error",
    "feed_title",
    "feed_url",
    "feed_published_at",
    "retrieved_at",
    "clean_title",
    "excerpt",
    "author",
    "published_at",
    "word_count",
    "language",
    "processed_at",
    "summary",
    "topics",
    "importance_score",
    "importance_reason",
    "summarized_at",
    "is_read",
    "is_saved",
    "is_hidden",
    "created_at",
    "updated_at",
]


def _to_row(article: Article, columns: list[str]) -> dict[str, Any]:
    return {col: getattr(article, col) for col in columns}


@articles_app.command("list")
def list_articles(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by article status."),
    source: Optional[int] = typer.Option(None, "--source", help="Filter by source ID."),
    limit: int = typer.Option(50, "--limit", help="Maximum number of results."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List articles, newest first."""
    with get_session() as session:
        q = session.query(Article).order_by(Article.created_at.desc())
        if status:
            q = q.filter(Article.status == status)
        if source is not None:
            q = q.filter(Article.source_id == source)
        rows = [_to_row(a, _LIST_COLUMNS) for a in q.limit(limit).all()]
    json_or_table(rows, _LIST_COLUMNS, as_json=as_json)


_RETRY_TARGET: dict[ArticleStatus, ArticleStatus] = {
    ArticleStatus.failed_processing: ArticleStatus.pending_processing,
    ArticleStatus.failed_ranking: ArticleStatus.pending_ranking,
}


@articles_app.command("show")
def show_article(
    article_id: int = typer.Argument(..., help="Article ID."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Show full details for a single article."""
    with get_session() as session:
        article = session.get(Article, article_id)
        row = _to_row(article, _SHOW_COLUMNS) if article is not None else None
    if row is None:
        typer.echo(f"Error: article {article_id} not found.", err=True)
        raise typer.Exit(code=1)
    json_or_table([row], _SHOW_COLUMNS, as_json=as_json)


@articles_app.command("search")
def search_articles(
    query: str = typer.Argument(..., help="Full-text search query. Matches only processed articles."),
    limit: int = typer.Option(50, "--limit", help="Maximum number of results."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Search articles by full-text query.

    Only matches articles that have been processed and have a search_vector.
    """
    tsq = func.plainto_tsquery("english", query)
    with get_session() as session:
        rows = [
            _to_row(a, _LIST_COLUMNS)
            for a in (
                session.query(Article)
                .filter(Article.search_vector.isnot(None))
                .filter(Article.search_vector.op("@@")(tsq))
                .order_by(func.ts_rank(Article.search_vector, tsq).desc())
                .limit(limit)
                .all()
            )
        ]
    json_or_table(rows, _LIST_COLUMNS, as_json=as_json)
