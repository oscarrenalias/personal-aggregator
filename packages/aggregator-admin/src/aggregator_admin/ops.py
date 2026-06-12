from __future__ import annotations

import json
from typing import Optional

import typer

import aggregator_common.ops as ops
from aggregator_common.config import Settings
from aggregator_common.db import get_session

from .output import json_or_table, render_table

ops_app = typer.Typer(help="Pipeline diagnostics and maintenance.")

_CLI_STAGE_TO_OPS: dict[str, str] = {
    "processing": "processor",
    "ranking": "summarize_rank",
}


@ops_app.command("status")
def status_cmd(
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """Show article counts by status, in-flight count, and source counts."""
    with get_session() as session:
        data = ops.pipeline_status(session)

    if as_json:
        typer.echo(json.dumps(data))
    else:
        article_rows = [{"status": k, "count": str(v)} for k, v in data["article_counts"].items()]
        article_rows.append({"status": "in_flight (claimed)", "count": str(data["in_flight"])})
        render_table(article_rows, ["status", "count"])
        render_table(
            [
                {"state": "enabled", "count": str(data["sources"]["enabled"])},
                {"state": "disabled", "count": str(data["sources"]["disabled"])},
            ],
            ["state", "count"],
        )


@ops_app.command("stuck")
def stuck_cmd(
    lease_seconds: Optional[int] = typer.Option(
        None,
        "--lease-seconds",
        help="Staleness threshold in seconds (default: CLAIM_LEASE_SECONDS from config).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List articles with a stale claim older than the lease threshold."""
    threshold = lease_seconds if lease_seconds is not None else Settings().claim_lease_seconds
    with get_session() as session:
        rows = ops.list_stuck(session, threshold)

    for row in rows:
        row["source"] = row.pop("source_name")

    json_or_table(rows, ["id", "status", "claimed_by", "claimed_at", "source"], as_json=as_json)


@ops_app.command("failures")
def failures_cmd(
    stage: Optional[str] = typer.Option(
        None,
        "--stage",
        help="Filter by stage: processing or ranking. Omit to show all failures.",
    ),
    limit: int = typer.Option(50, "--limit", help="Maximum number of rows to return."),
    as_json: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """List failed articles with last_error, retry_count, and source name."""
    if stage is not None and stage not in _CLI_STAGE_TO_OPS:
        typer.echo(f"Error: --stage must be 'processing' or 'ranking', got {stage!r}", err=True)
        raise typer.Exit(code=1)

    helper_stage = _CLI_STAGE_TO_OPS.get(stage) if stage is not None else None

    with get_session() as session:
        rows = ops.list_failures(session, stage=helper_stage, limit=limit)

    for row in rows:
        row["source"] = row.pop("source_name")

    json_or_table(rows, ["id", "status", "retry_count", "last_error", "source"], as_json=as_json)


@ops_app.command("reap")
def reap_cmd(
    lease_seconds: Optional[int] = typer.Option(
        None,
        "--lease-seconds",
        help="Lease timeout in seconds (default: CLAIM_LEASE_SECONDS from config).",
    ),
) -> None:
    """Release stale work claims older than the lease timeout."""
    threshold = lease_seconds if lease_seconds is not None else Settings().claim_lease_seconds
    with get_session() as session:
        counts = ops.reap_stale_claims(session, threshold)
    typer.echo(
        f"Released {counts['articles_released']} article(s) and {counts['briefs_released']} brief(s)."
    )
