"""Admin command: llm-experiment-report.

Turns llm_shadow_results + llm_calls into machine-readable reliability/cost metrics.
Content metrics, verdicts, disagreement rules, and --samples are added by later beads.
"""

from __future__ import annotations

import json
import os
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import typer
from rich.table import Table
from sqlalchemy import text
from sqlalchemy.orm import Session

from aggregator_common.db import get_session

from .output import console, json_default

# Thresholds live in one dict so later rounds can tune them.
CRITERIA_THRESHOLDS: Dict[str, Any] = {
    "max_error_plus_parse_rate_excess_pp": 1.0,
    "max_length_truncation_rate": 0.01,
    "summarize_rank.rank": {
        "min_spearman_rho": 0.80,
        "max_mae": 8,
        "max_flip_rate": 0.10,
        "min_is_saved_auc_delta": -0.03,
    },
    "clusterer.classify": {"min_assignment_agreement": 0.85},
    "clusterer.merge": {"min_agreement": 0.90, "max_arm_only_merges": 0.05},
}

_CONFIG_DEFAULTS: Dict[str, Any] = {
    "WEB_IMPORTANT_THRESHOLD": 70,
    "WEB_AUTO_READ_SECONDS": 5,
    "BRIEF_TOOL_MAX_CALLS": 12,
    "CLUSTERER_MAX_KNOWN_FACTS": 40,
    "CLUSTERER_KNOWN_FACTS_KEEP_RECENT": 20,
    "CLUSTERER_FACTS_CONDENSED_TARGET_RATIO": 0.6,
}


def _read_config() -> Dict[str, Any]:
    config: Dict[str, Any] = {}
    for key, default in _CONFIG_DEFAULTS.items():
        raw = os.environ.get(key)
        if raw is not None:
            try:
                config[key] = type(default)(raw)
            except (ValueError, TypeError):
                config[key] = default
        else:
            config[key] = default
    return config


def _percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    sv = sorted(values)
    n = len(sv)
    idx = (p / 100.0) * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sv[lo] * (1 - frac) + sv[hi] * frac


def _parse_date_arg(s: Optional[str], default: datetime) -> datetime:
    if s is None:
        return default
    s = s.strip()
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        raise typer.BadParameter(f"Cannot parse date: {s!r}")


def _load_shadow_rows(
    session: Session, experiment: str, since: datetime, until: datetime
) -> List[Dict]:
    sql = text("""
        SELECT id, created_at, service, operation, ref_id, arm,
               model, prompt_hash, status, attempts, params, output, raw_output, error, request_ids
        FROM llm_shadow_results
        WHERE experiment = :experiment
          AND created_at >= :since
          AND created_at < :until
        ORDER BY service, operation, ref_id, arm
    """)
    rows = session.execute(sql, {"experiment": experiment, "since": since, "until": until})
    return [dict(r._mapping) for r in rows]


def _load_call_rows(
    session: Session, experiment: str, since: datetime, until: datetime
) -> List[Dict]:
    sql = text("""
        SELECT service, operation, ref_id, arm,
               prompt_tokens, completion_tokens, total_tokens, cached_tokens,
               cost_usd, latency_ms, status, finish_reason, reasoning_tokens
        FROM llm_calls
        WHERE experiment = :experiment
          AND created_at >= :since
          AND created_at < :until
    """)
    rows = session.execute(sql, {"experiment": experiment, "since": since, "until": until})
    return [dict(r._mapping) for r in rows]


def _build_call_index(calls: List[Dict]) -> Dict[Tuple, List[Dict]]:
    """Index llm_calls by (arm, service, operation, ref_id) per spec A's join key."""
    index: Dict[Tuple, List[Dict]] = defaultdict(list)
    for c in calls:
        key = (c["arm"], c["service"], c["operation"], c["ref_id"])
        index[key].append(c)
    return index


def _pair_shadow_rows(
    shadow_rows: List[Dict],
) -> Dict[str, Dict]:
    """
    Group shadow rows by (service, operation), then pair control and arm rows by ref_id.

    Returns a dict keyed by "service.operation" containing:
      - service, operation
      - all_arms: sorted list of arm names (excluding "control")
      - arm_pairs[arm]: list of (ctrl_row, arm_row) for fully paired items
      - arm_unpaired[arm]: count of items lacking a control or arm row for this arm
      - control_rows: {ref_id: ctrl_row} for all control rows in any pair
      - control_only_count: control rows with no matching arm row for any arm
    """
    by_op: Dict[Tuple[str, str], Dict[str, Dict[str, Dict]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in shadow_rows:
        key = (row["service"], row["operation"])
        by_op[key][row["ref_id"]][row["arm"]] = row

    result: Dict[str, Dict] = {}
    for (svc, op), ref_map in by_op.items():
        op_key = f"{svc}.{op}"

        all_arms: set = set()
        for arm_map in ref_map.values():
            for arm in arm_map:
                if arm != "control":
                    all_arms.add(arm)

        arm_pairs: Dict[str, List[Tuple[Dict, Dict]]] = {arm: [] for arm in all_arms}
        arm_unpaired: Dict[str, int] = {arm: 0 for arm in all_arms}
        control_rows: Dict[str, Dict] = {}
        control_only_count = 0

        for ref_id, arm_map in ref_map.items():
            ctrl_row = arm_map.get("control")
            non_ctrl = {arm: arm_map[arm] for arm in arm_map if arm != "control"}

            if ctrl_row is None:
                for arm in non_ctrl:
                    arm_unpaired[arm] = arm_unpaired.get(arm, 0) + 1
            else:
                paired_with_any = False
                for arm in all_arms:
                    arm_row = non_ctrl.get(arm)
                    if arm_row is None:
                        arm_unpaired[arm] = arm_unpaired.get(arm, 0) + 1
                    else:
                        arm_pairs[arm].append((ctrl_row, arm_row))
                        control_rows[ref_id] = ctrl_row
                        paired_with_any = True
                if not paired_with_any:
                    control_only_count += 1

        result[op_key] = {
            "service": svc,
            "operation": op,
            "all_arms": sorted(all_arms),
            "arm_pairs": arm_pairs,
            "arm_unpaired": arm_unpaired,
            "control_rows": control_rows,
            "control_only_count": control_only_count,
        }
    return result


def _compute_metrics(
    rows: List[Dict],
    arm_name: str,
    call_index: Dict[Tuple, List[Dict]],
) -> Tuple[Dict[str, Any], Dict[str, Any], int]:
    """
    Compute reliability and cost metrics for a list of shadow rows (all same arm).

    Returns: (reliability_dict, cost_dict, no_telemetry_count)
    - reliability_dict: n, status counts, error/parse/refusal rates, latency p50/p95, token means
    - cost_dict: mean_cost_per_item_usd (projected_monthly_usd added by caller)
    - no_telemetry_count: items with no matching llm_calls rows (excluded from cost/latency/token means)
    """
    status_counts: Dict[str, int] = defaultdict(int)
    latency_per_item: List[float] = []
    latency_per_call: List[float] = []
    prompt_tokens: List[float] = []
    completion_tokens: List[float] = []
    reasoning_tokens: List[float] = []
    cost_per_item: List[float] = []
    finish_length_count = 0
    no_telemetry = 0

    for row in rows:
        status_counts[row["status"]] += 1
        svc, op, ref_id = row["service"], row["operation"], row["ref_id"]
        calls = call_index.get((arm_name, svc, op, ref_id), [])

        if not calls:
            no_telemetry += 1
        else:
            item_latency = sum(c["latency_ms"] for c in calls)
            latency_per_item.append(float(item_latency))
            for c in calls:
                latency_per_call.append(float(c["latency_ms"]))

            prompt_tokens.append(float(sum(c["prompt_tokens"] for c in calls)))
            completion_tokens.append(float(sum(c["completion_tokens"] for c in calls)))
            reasoning_tokens.append(float(sum((c["reasoning_tokens"] or 0) for c in calls)))
            cost_per_item.append(float(sum((c["cost_usd"] or 0.0) for c in calls)))

            if any(c["finish_reason"] == "length" for c in calls):
                finish_length_count += 1

    n = len(rows)
    error_n = status_counts.get("api_error", 0) + status_counts.get("timeout", 0)
    parse_n = status_counts.get("parse_error", 0)
    refusal_n = status_counts.get("refusal", 0)
    mean_cost = statistics.mean(cost_per_item) if cost_per_item else None

    reliability: Dict[str, Any] = {
        "n": n,
        "status": dict(status_counts),
        "error_rate": round(error_n / n, 4) if n else None,
        "parse_failure_rate": round(parse_n / n, 4) if n else None,
        "refusal_rate": round(refusal_n / n, 4) if n else None,
        "finish_reason_length_rate": round(finish_length_count / n, 4) if n else None,
        "latency_per_item_p50_ms": _percentile(latency_per_item, 50),
        "latency_per_item_p95_ms": _percentile(latency_per_item, 95),
        "latency_per_call_p50_ms": _percentile(latency_per_call, 50),
        "latency_per_call_p95_ms": _percentile(latency_per_call, 95),
        "mean_prompt_tokens_per_item": statistics.mean(prompt_tokens) if prompt_tokens else None,
        "mean_completion_tokens_per_item": statistics.mean(completion_tokens) if completion_tokens else None,
        "mean_reasoning_tokens_per_item": statistics.mean(reasoning_tokens) if reasoning_tokens else None,
    }
    cost: Dict[str, Any] = {"mean_cost_per_item_usd": mean_cost}
    return reliability, cost, no_telemetry


def _items_per_day(
    session: Session,
    service: str,
    operation: str,
    since: datetime,
    until: datetime,
) -> float:
    """
    Count distinct (UTC date, ref_id) from ALL llm_calls for service/operation in window,
    divided by the number of UTC days in [since, until).

    For brief.generate, only ref_ids of origin='auto' briefs are counted (≈1/day).
    Uses all calls regardless of experiment tagging so the rate reflects true throughput.
    """
    delta_days = (until - since).total_seconds() / 86400.0
    days = max(1.0, delta_days)

    if service == "brief" and operation == "generate":
        sql = text("""
            SELECT COUNT(DISTINCT (DATE(lc.created_at AT TIME ZONE 'UTC'), lc.ref_id))
            FROM llm_calls lc
            JOIN briefs b ON b.id::text = lc.ref_id
            WHERE lc.service = :service
              AND lc.operation = :operation
              AND lc.created_at >= :since
              AND lc.created_at < :until
              AND b.origin = 'auto'
        """)
    else:
        sql = text("""
            SELECT COUNT(DISTINCT (DATE(created_at AT TIME ZONE 'UTC'), ref_id))
            FROM llm_calls
            WHERE service = :service
              AND operation = :operation
              AND created_at >= :since
              AND created_at < :until
        """)

    scalar = session.execute(
        sql, {"service": service, "operation": operation, "since": since, "until": until}
    ).scalar()
    distinct = int(scalar) if scalar else 0
    return distinct / days


def _build_report(
    session: Session,
    experiment: str,
    since_dt: datetime,
    until_dt: datetime,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    shadow_rows = _load_shadow_rows(session, experiment, since_dt, until_dt)
    call_rows = _load_call_rows(session, experiment, since_dt, until_dt)
    call_index = _build_call_index(call_rows)
    op_data = _pair_shadow_rows(shadow_rows)

    operations_output: Dict[str, Any] = {}

    for op_key, data in op_data.items():
        svc = data["service"]
        op = data["operation"]
        all_arms = data["all_arms"]
        arm_pairs = data["arm_pairs"]
        arm_unpaired = data["arm_unpaired"]
        control_rows = data["control_rows"]
        control_only_count = data["control_only_count"]

        ipd = _items_per_day(session, svc, op, since_dt, until_dt)

        ctrl_row_list = list(control_rows.values())
        ctrl_rel, ctrl_cost, ctrl_no_tel = _compute_metrics(ctrl_row_list, "control", call_index)
        ctrl_mean_cost = ctrl_cost["mean_cost_per_item_usd"]
        ctrl_projected = (ctrl_mean_cost * ipd * 30) if ctrl_mean_cost is not None else None
        ctrl_cost["projected_monthly_usd"] = ctrl_projected

        arms_output: Dict[str, Any] = {}
        for arm in all_arms:
            pairs = arm_pairs.get(arm, [])
            arm_rows = [arm_row for _, arm_row in pairs]
            arm_rel, arm_cost, arm_no_tel = _compute_metrics(arm_rows, arm, call_index)
            arm_mean_cost = arm_cost["mean_cost_per_item_usd"]
            arm_projected = (arm_mean_cost * ipd * 30) if arm_mean_cost is not None else None
            arm_cost["projected_monthly_usd"] = arm_projected

            arms_output[arm] = {
                "pairs": len(pairs),
                "unpaired": arm_unpaired.get(arm, 0),
                "no_telemetry": arm_no_tel,
                "reliability": arm_rel,
                "cost": arm_cost,
                "content": {},
                "verdict": "insufficient_data",
                "failed_criteria": [],
            }

        operations_output[op_key] = {
            "control": {
                "n": ctrl_rel.pop("n"),
                "no_telemetry": ctrl_no_tel,
                "unpaired": control_only_count,
                "reliability": ctrl_rel,
                "cost": ctrl_cost,
            },
            "arms": arms_output,
        }

    return {
        "experiment": experiment,
        "window": {"since": since_dt.isoformat(), "until": until_dt.isoformat()},
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "config": config,
        "operations": operations_output,
    }


def _render_table(report: Dict[str, Any]) -> None:
    console.print(f"[bold]Experiment:[/bold] {report['experiment']}")
    console.print(f"[bold]Window:[/bold] {report['window']['since']} – {report['window']['until']}")

    if not report["operations"]:
        console.print("[yellow]No shadow data found for this experiment and window.[/yellow]")
        return

    for op_key, op_data in report["operations"].items():
        table = Table(title=op_key, show_header=True, header_style="bold")
        table.add_column("Side")
        table.add_column("N / Pairs")
        table.add_column("Error rate")
        table.add_column("Parse fail")
        table.add_column("Lat p50/item")
        table.add_column("Mean cost/item")
        table.add_column("Proj. monthly")
        table.add_column("Verdict")

        ctrl = op_data["control"]
        ctrl_rel = ctrl["reliability"]
        ctrl_cost = ctrl["cost"]

        def _pct(v: Optional[float]) -> str:
            return f"{v * 100:.1f}%" if v is not None else "-"

        def _ms(v: Optional[float]) -> str:
            return f"{v:.0f} ms" if v is not None else "-"

        def _usd(v: Optional[float]) -> str:
            return f"${v:.6f}" if v is not None else "-"

        def _usd2(v: Optional[float]) -> str:
            return f"${v:.2f}" if v is not None else "-"

        table.add_row(
            "control",
            str(ctrl.get("n", "?")),
            _pct(ctrl_rel.get("error_rate")),
            _pct(ctrl_rel.get("parse_failure_rate")),
            _ms(ctrl_rel.get("latency_per_item_p50_ms")),
            _usd(ctrl_cost.get("mean_cost_per_item_usd")),
            _usd2(ctrl_cost.get("projected_monthly_usd")),
            "—",
        )

        for arm, arm_data in op_data["arms"].items():
            arm_rel = arm_data["reliability"]
            arm_cost = arm_data["cost"]
            table.add_row(
                arm,
                str(arm_data["pairs"]),
                _pct(arm_rel.get("error_rate")),
                _pct(arm_rel.get("parse_failure_rate")),
                _ms(arm_rel.get("latency_per_item_p50_ms")),
                _usd(arm_cost.get("mean_cost_per_item_usd")),
                _usd2(arm_cost.get("projected_monthly_usd")),
                arm_data.get("verdict", "—"),
            )

        console.print(table)


def llm_experiment_report_cmd(
    experiment: str = typer.Option(..., "--experiment", help="Experiment name to report on."),
    since: Optional[str] = typer.Option(
        None, "--since", help="Window start (ISO date, inclusive). Default: 30 days ago."
    ),
    until: Optional[str] = typer.Option(
        None, "--until", help="Window end (ISO date, exclusive). Default: now."
    ),
    as_json: bool = typer.Option(False, "--json", is_flag=True, help="Emit full metrics as JSON."),
    samples_dir: Optional[str] = typer.Option(
        None, "--samples", help="Directory to write per-operation JSONL sample files."
    ),
) -> None:
    """Report reliability and cost metrics for an LLM shadow experiment."""
    now = datetime.now(tz=timezone.utc)
    since_dt = _parse_date_arg(since, now - timedelta(days=30))
    until_dt = _parse_date_arg(until, now)
    config = _read_config()

    with get_session() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        report = _build_report(session, experiment, since_dt, until_dt, config)

    if as_json:
        typer.echo(json.dumps(report, default=json_default))
    else:
        _render_table(report)

    if samples_dir is not None:
        console.print(
            "[yellow]--samples is not yet implemented (content metrics bead).[/yellow]"
        )
