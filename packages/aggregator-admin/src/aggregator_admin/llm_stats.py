from __future__ import annotations

import typer
from rich.table import Table

from aggregator_common.db import get_session
from aggregator_common.queries import llm_stats

from .output import console

_COLUMNS = [
    "service",
    "model",
    "requests",
    "total_cost_usd",
    "avg_cost_usd",
    "avg_prompt_tokens",
    "p95_prompt_tokens",
    "avg_completion_tokens",
    "truncated",
    "errors",
    "error_pct",
    "avg_tool_calls",
    "max_tool_calls",
]

_HEADERS = [
    "Service",
    "Model",
    "Requests",
    "Total Cost (USD)",
    "Avg Cost (USD)",
    "Avg Prompt Tokens",
    "P95 Prompt Tokens",
    "Avg Completion Tokens",
    "Truncated",
    "Errors",
    "Error%",
    "Avg Tool Calls",
    "Max Tool Calls",
]


def _fmt_cost(v: float | None) -> str:
    if v is None:
        return "-"
    return f"${v:.6f}"


def _fmt_float(v: float | None, decimals: int = 1) -> str:
    if v is None:
        return "-"
    return f"{v:.{decimals}f}"


def _fmt_int(v: int | None) -> str:
    if v is None:
        return "-"
    return str(v)


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:.2f}%"


def llm_stats_cmd(
    days: int = typer.Option(7, "--days", help="Number of days to include (default: 7)."),
) -> None:
    """Show per-service LLM usage: cost, token counts, truncation, errors, and tool-call stats."""
    with get_session() as session:
        results = llm_stats(session, days)

    if not results:
        console.print(f"No LLM calls recorded in the last {days} day(s).")
        return

    rows = [
        {
            "service": r.service,
            "model": r.model,
            "requests": str(r.request_count),
            "total_cost_usd": _fmt_cost(r.total_cost_usd),
            "avg_cost_usd": _fmt_cost(r.avg_cost_usd),
            "avg_prompt_tokens": _fmt_float(r.avg_prompt_tokens, 0),
            "p95_prompt_tokens": _fmt_float(r.p95_prompt_tokens, 0),
            "avg_completion_tokens": _fmt_float(r.avg_completion_tokens, 0),
            "truncated": _fmt_int(r.truncated_count),
            "errors": _fmt_int(r.error_count),
            "error_pct": _fmt_pct(r.error_pct),
            "avg_tool_calls": _fmt_float(r.avg_tool_calls),
            "max_tool_calls": _fmt_int(r.max_tool_calls),
        }
        for r in results
    ]

    table = Table(show_header=True, header_style="bold")
    for header in _HEADERS:
        table.add_column(header)
    for row in rows:
        table.add_row(*[row[col] for col in _COLUMNS])
    console.print(f"LLM stats — last {days} day(s)")
    console.print(table)
