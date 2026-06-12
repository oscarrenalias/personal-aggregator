from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import typer
from sqlalchemy import func, inspect as sa_inspect

from sqlalchemy.orm import Session

import aggregator_common.ops as ops
from aggregator_common.db import get_session
from aggregator_common.models import Article
from aggregator_common.state import ArticleStatus, can_transition

from .output import confirm, json_default, json_or_table, render_kv_table

articles_app = typer.Typer(help="Inspect and operate on articles.")

_LIST_COLUMNS = [
    "id",
    "source_id",
    "status",
    "feed_title",
    "feed_published_at",
    "retrieved_at",
    "header_image_url",
    "importance_score",
    "importance_reason",
    "summary",
    "topics",
    "entities",
    "llm_meta",
    "summarized_at",
    "is_read",
    "is_saved",
    "is_hidden",
]

# search_vector is a PostgreSQL TSVECTOR used internally for FTS; excluded from output.
_SHOW_EXCLUDED: frozenset[str] = frozenset({"search_vector"})
# Long fields truncated in human display; appear in full in --json output.
_SHOW_TRUNCATE_LIMITS: dict[str, int] = {"clean_text": 300, "raw_payload": 300}


def _article_show_columns() -> list[str]:
    """All Article ORM column names except search_vector."""
    return [
        prop.key
        for prop in sa_inspect(Article).column_attrs
        if prop.key not in _SHOW_EXCLUDED
    ]


def _to_row(article: Article, columns: list[str]) -> dict[str, Any]:
    return {col: getattr(article, col) for col in columns}


def _to_display_row(article: Article, columns: list[str]) -> dict[str, Any]:
    """Build a display row for human output; truncates long fields with an indicator."""
    row: dict[str, Any] = {}
    for col in columns:
        val = getattr(article, col)
        if col in _SHOW_TRUNCATE_LIMITS and val is not None:
            limit = _SHOW_TRUNCATE_LIMITS[col]
            s = json.dumps(val) if isinstance(val, (dict, list)) else str(val)
            if len(s) > limit:
                val = f"{s[:limit]}… [truncated]"
        row[col] = val
    return row


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

_FAILED_STATUS_TO_STAGE: dict[ArticleStatus, str] = {
    ArticleStatus.failed_processing: "processor",
    ArticleStatus.failed_ranking: "summarize_rank",
}


@articles_app.command("show")
def show_article(
    article_id: int = typer.Argument(..., help="Article ID."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Show full details for a single article."""
    columns = _article_show_columns()
    with get_session() as session:
        article = session.get(Article, article_id)
        if article is None:
            typer.echo(f"Error: article {article_id} not found.", err=True)
            raise typer.Exit(code=1)
        if as_json:
            row = _to_row(article, columns)
        else:
            row = _to_display_row(article, columns)
    if as_json:
        typer.echo(json.dumps(row, default=json_default))
    else:
        render_kv_table(row)


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
            ops.retry_failed(session, article_id=article_id)
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
        stage = _FAILED_STATUS_TO_STAGE[batch_status]
        with get_session() as session:
            result = ops.retry_failed(session, stage=stage)
        typer.echo(f"Retried {result['retried']} article(s), skipped 0.")


@articles_app.command("rerank")
def rerank_article(
    article_id: Optional[int] = typer.Argument(None, help="Article ID to rerank."),
    all_ready: bool = typer.Option(False, "--all", help="Requeue all ready articles for re-ranking."),
    failed: bool = typer.Option(
        False,
        "--failed",
        help="Requeue all failed_ranking articles back to pending_ranking (recover from mass ranking failures).",
    ),
    yes: bool = typer.Option(False, "--yes", help="Confirm bulk requeue without prompting."),
) -> None:
    """Queue articles for re-ranking.

    Provide an ARTICLE_ID to requeue a single ready article, --all to requeue
    all ready articles, or --failed to recover articles stuck in failed_ranking
    back to pending_ranking so summarize-rank can re-claim them.
    """
    options_set = sum([article_id is not None, all_ready, failed])
    if options_set > 1:
        typer.echo("Error: provide exactly one of ARTICLE_ID, --all, or --failed.", err=True)
        raise typer.Exit(code=1)
    if options_set == 0:
        typer.echo("Error: provide exactly one of ARTICLE_ID, --all, or --failed.", err=True)
        raise typer.Exit(code=1)

    if all_ready:
        confirm(yes=yes, prompt="This will requeue all ready articles for re-ranking.")
        with get_session() as session:
            result = ops.rerank(session, all_ready=True)
        typer.echo(f"Requeued {result['reranked']} article(s) for re-ranking (→ pending_ranking).")
    elif failed:
        confirm(yes=yes, prompt="This will requeue all failed_ranking articles for re-ranking.")
        with get_session() as session:
            result = ops.rerank(session, failed_only=True)
        typer.echo(f"Requeued {result['reranked']} article(s) for re-ranking (→ pending_ranking).")
    else:
        with get_session() as session:
            article = session.get(Article, article_id)
            if article is None:
                typer.echo(f"Error: article {article_id} not found.", err=True)
                raise typer.Exit(code=1)
            current = ArticleStatus(article.status)
            if not can_transition(current, ArticleStatus.pending_ranking):
                typer.echo(
                    f"Error: article {article_id} cannot be reranked (current status: {article.status}; must be ready).",
                    err=True,
                )
                raise typer.Exit(code=1)
            ops.rerank(session, article_id=article_id)
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


@articles_app.command("purge")
def purge_articles(
    status: Optional[str] = typer.Option(None, "--status", help="Delete articles with this status."),
    source: Optional[int] = typer.Option(None, "--source", help="Delete articles from this source ID."),
    before: Optional[str] = typer.Option(None, "--before", help="Delete articles retrieved before this ISO date (e.g. 2024-01-01)."),
    yes: bool = typer.Option(False, "--yes", help="Confirm deletion without prompting."),
) -> None:
    """Delete articles matching the given filters.

    At least one of --status, --source, or --before is required.
    """
    if status is None and source is None and before is None:
        typer.echo("Error: at least one filter (--status, --source, or --before) is required.", err=True)
        raise typer.Exit(code=1)

    before_dt: Optional[datetime] = None
    if before is not None:
        try:
            before_dt = datetime.fromisoformat(before).replace(tzinfo=timezone.utc)
        except ValueError:
            typer.echo(f"Error: --before value {before!r} is not a valid ISO date.", err=True)
            raise typer.Exit(code=1)

    confirm(yes=yes, prompt="This will permanently delete matching articles.")

    with get_session() as session:
        q = session.query(Article)
        if status is not None:
            q = q.filter(Article.status == status)
        if source is not None:
            q = q.filter(Article.source_id == source)
        if before_dt is not None:
            q = q.filter(Article.retrieved_at < before_dt)
        deleted = q.delete(synchronize_session=False)

    typer.echo(f"Deleted {deleted} article(s).")
