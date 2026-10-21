from __future__ import annotations

import json
import sys
from dataclasses import asdict

import typer
from rich.table import Table

from aggregator_common.db import get_session
from aggregator_common.queries import llm_shadow_stats, llm_stats

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

_SHADOW_COLUMNS = [
    "experiment",
    "arm",
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

_SHADOW_HEADERS = [
    "Experiment",
    "Arm",
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


def _stat_row(r) -> dict:
    return {
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


def llm_stats_cmd(
    days: int = typer.Option(7, "--days", help="Number of days to include (default: 7)."),
    include_shadow: bool = typer.Option(
        False,
        "--include-shadow",
        help="Include shadow arm rows in the main stats and render an additional per-experiment/arm breakdown table.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit full stats as JSON to stdout (includes shadow_breakdown when --include-shadow is set).",
    ),
) -> None:
    """Show per-service LLM usage: cost, token counts, truncation, errors, and tool-call stats."""
    with get_session() as session:
        results = llm_stats(session, days, include_shadow=include_shadow)
        shadow_results = llm_shadow_stats(session, days) if include_shadow else []

    if json_output:
        output: dict = {
            "days": days,
            "stats": [asdict(r) for r in results],
        }
        if include_shadow:
            output["shadow_breakdown"] = [asdict(r) for r in shadow_results]
        sys.stdout.write(json.dumps(output, default=str) + "\n")
        return

    if not results:
        console.print(f"No LLM calls recorded in the last {days} day(s).")
        return

    table = Table(show_header=True, header_style="bold")
    for header in _HEADERS:
        table.add_column(header)
    for r in results:
        row = _stat_row(r)
        table.add_row(*[row[col] for col in _COLUMNS])
    console.print(f"LLM stats — last {days} day(s)")
    console.print(table)

    if include_shadow:
        if not shadow_results:
            console.print("\nNo shadow arm rows found in the last {days} day(s).")
            return
        shadow_table = Table(show_header=True, header_style="bold")
        for header in _SHADOW_HEADERS:
            shadow_table.add_column(header)
        for r in shadow_results:
            row = {
                "experiment": r.experiment,
                "arm": r.arm,
                **_stat_row(r),
            }
            shadow_table.add_row(*[row[col] for col in _SHADOW_COLUMNS])
        console.print(f"\nShadow arm breakdown — last {days} day(s)")
        console.print(shadow_table)
