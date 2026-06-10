from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import typer
from sqlalchemy import func

from sqlalchemy.orm import Session

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


@articles_app.command("retry")
def retry_article(
    article_id: Optional[int] = typer.Argument(None, help="Article ID to retry."),
    status: Optional[str] = typer.Option(None, "--status", help="Retry all articles with this failed status."),
) -> None:
    """Retry a failed article, or all articles with a given failed status."""
    if article_id is None and status is None:
        typer.echo("Error: provide either an article ID or --status.", err=True)
        raise typer.Exit(code=1)
    if article_id is not None and status is not None:
        typer.echo("Error: provide either an article ID or --status, not both.", err=True)
        raise typer.Exit(code=1)

    if article_id is not None:
        with get_session() as session:
            article = session.get(Article, article_id)
            if article is None:
                typer.echo(f"Error: article {article_id} not found.", err=True)
                raise typer.Exit(code=1)
            current = ArticleStatus(article.status)
            target = _RETRY_TARGET.get(current)
            if target is None:
                typer.echo(
                    f"Error: article {article_id} is not in a failed status (current: {article.status}).",
                    err=True,
                )
                raise typer.Exit(code=1)
            if not can_transition(current, target):
                typer.echo(
                    f"Error: cannot transition article {article_id} from {article.status} to {target.value}.",
                    err=True,
                )
                raise typer.Exit(code=1)
            article.status = target
            article.claimed_by = None
            article.claimed_at = None
            article.last_error = None
            article.retry_count = 0
            article.next_retry_at = None
        typer.echo(f"Article {article_id} queued for retry (→ {target.value}).")
    else:
        try:
            batch_status = ArticleStatus(status)
        except ValueError:
            typer.echo(f"Error: unknown status {status!r}.", err=True)
            raise typer.Exit(code=1)
        if batch_status not in _RETRY_TARGET:
            typer.echo(
                f"Error: --status must be a failed status (failed_processing or failed_ranking), got {status!r}.",
                err=True,
            )
            raise typer.Exit(code=1)
        target = _RETRY_TARGET[batch_status]
        retried = 0
        skipped = 0
        with get_session() as session:
            articles = session.query(Article).filter(Article.status == batch_status).all()
            for article in articles:
                current = ArticleStatus(article.status)
                if can_transition(current, target):
                    article.status = target
                    article.claimed_by = None
                    article.claimed_at = None
                    article.last_error = None
                    article.retry_count = 0
                    article.next_retry_at = None
                    retried += 1
                else:
                    skipped += 1
        typer.echo(f"Retried {retried} article(s), skipped {skipped}.")


@articles_app.command("rerank")
def rerank_article(
    article_id: int = typer.Argument(..., help="Article ID to rerank."),
) -> None:
    """Queue a ready article for re-ranking."""
    with get_session() as session:
        article = session.get(Article, article_id)
        if article is None:
            typer.echo(f"Error: article {article_id} not found.", err=True)
            raise typer.Exit(code=1)
        current = ArticleStatus(article.status)
        target = ArticleStatus.pending_ranking
        if not can_transition(current, target):
            typer.echo(
                f"Error: article {article_id} cannot be reranked (current status: {article.status}; must be ready).",
                err=True,
            )
            raise typer.Exit(code=1)
        article.status = target
        article.claimed_by = None
        article.claimed_at = None
    typer.echo(f"Article {article_id} queued for re-ranking (→ pending_ranking).")


def _get_article_or_exit(session: Session, article_id: int) -> Article:
    article = session.get(Article, article_id)
    if article is None:
        typer.echo(f"Error: article {article_id} not found.", err=True)
        raise typer.Exit(code=1)
    return article


@articles_app.command("mark-read")
def mark_read(
    article_id: int = typer.Argument(..., help="Article ID."),
) -> None:
    """Mark an article as read."""
    with get_session() as session:
        article = _get_article_or_exit(session, article_id)
        article.is_read = True
        article.read_at = datetime.now(tz=timezone.utc)
    typer.echo(f"Article {article_id} marked as read.")


@articles_app.command("mark-unread")
def mark_unread(
    article_id: int = typer.Argument(..., help="Article ID."),
) -> None:
    """Mark an article as unread."""
    with get_session() as session:
        article = _get_article_or_exit(session, article_id)
        article.is_read = False
        article.read_at = None
    typer.echo(f"Article {article_id} marked as unread.")


@articles_app.command("save")
def save_article(
    article_id: int = typer.Argument(..., help="Article ID."),
) -> None:
    """Save an article."""
    with get_session() as session:
        article = _get_article_or_exit(session, article_id)
        article.is_saved = True
    typer.echo(f"Article {article_id} saved.")


@articles_app.command("unsave")
def unsave_article(
    article_id: int = typer.Argument(..., help="Article ID."),
) -> None:
    """Unsave an article."""
    with get_session() as session:
        article = _get_article_or_exit(session, article_id)
        article.is_saved = False
    typer.echo(f"Article {article_id} unsaved.")


@articles_app.command("hide")
def hide_article(
    article_id: int = typer.Argument(..., help="Article ID."),
) -> None:
    """Hide an article."""
    with get_session() as session:
        article = _get_article_or_exit(session, article_id)
        article.is_hidden = True
    typer.echo(f"Article {article_id} hidden.")


@articles_app.command("unhide")
def unhide_article(
    article_id: int = typer.Argument(..., help="Article ID."),
) -> None:
    """Unhide an article."""
    with get_session() as session:
        article = _get_article_or_exit(session, article_id)
        article.is_hidden = False
    typer.echo(f"Article {article_id} unhidden.")
