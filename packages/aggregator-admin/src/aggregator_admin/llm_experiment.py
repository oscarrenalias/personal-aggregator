"""Admin command: llm-experiment-report.

Turns llm_shadow_results + llm_calls into machine-readable reliability/cost metrics.
Content metrics, verdicts, disagreement rules, and --samples are added by later beads.
"""

from __future__ import annotations

import json
import os
import random
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import typer
from rich.table import Table
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from aggregator_common.db import get_session

from .output import console, json_default, _error_console

SAMPLE_SEED = 20261021

# Thresholds live in one dict so later rounds can tune them.
CRITERIA_THRESHOLDS: Dict[str, Any] = {
    # Reliability ("all" operations)
    "max_error_plus_parse_rate_excess_pp": 1.0,
    "max_length_truncation_rate": 0.01,
    # Minimum pairs before content criteria are evaluated
    "min_pairs_default": 30,
    "min_pairs_brief": 5,
    "min_ok_pairs_default": 30,
    "min_ok_pairs_brief": 5,
    # Engagement AUC: skip if fewer saved articles than this
    "min_saved_for_auc": 10,
    "summarize_rank.rank": {
        "min_spearman_rho": 0.80,
        "max_mae": 8,
        "max_flip_rate": 0.10,
        "min_is_saved_auc_delta": -0.03,
        # Disagreement rule: |Δimportance_score| >= this triggers a disagreement
        "max_score_delta_for_disagree": 15,
    },
    "clusterer.classify": {"min_outcome_agreement": 0.92},
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

# Labels that require a matching thread_id for assignment agreement.
_ATTACHING_LABELS: frozenset = frozenset({
    "same_thread_new_fact",
    "same_thread_new_angle",
    "same_thread_duplicate",
    "same_thread_background_only",
    "correction_or_clarification",
})

# Labels for which thread_title presence is tracked.
_THREAD_TITLE_LABELS: frozenset = frozenset({"new_thread", "related_new_thread"})

# Operations where the verdict is always "review" (no content pass/fail criteria).
_REVIEW_OPERATIONS: frozenset = frozenset({"clusterer.consolidate_facts", "brief.generate"})


def _classify_outcome(label: str, thread_id: Optional[str]) -> str:
    """Map a classify label to a coarser outcome category for outcome_agreement."""
    if label in _ATTACHING_LABELS:
        return f"attach:{thread_id}"
    if label == "irrelevant_or_low_value":
        return "drop"
    if label in _THREAD_TITLE_LABELS:
        return "new"
    return f"other:{label}"


# ---------------------------------------------------------------------------
# Disagreement rules
# ---------------------------------------------------------------------------


def _is_pair_disagree(
    op_key: str,
    ctrl_row: Dict,
    arm_row: Dict,
    config: Dict[str, Any],
) -> bool:
    """Return True when an arm item disagrees with the control for this operation."""
    # Status mismatch always disagrees.
    if ctrl_row.get("status") != arm_row.get("status"):
        return True

    ctrl_out = ctrl_row.get("output") or {}
    arm_out = arm_row.get("output") or {}

    if op_key == "summarize_rank.rank":
        threshold = float(config.get("WEB_IMPORTANT_THRESHOLD", 70))
        max_delta = float(
            CRITERIA_THRESHOLDS["summarize_rank.rank"]["max_score_delta_for_disagree"]
        )
        cs = ctrl_out.get("importance_score")
        as_ = arm_out.get("importance_score")
        if cs is not None and as_ is not None:
            cf, af = float(cs), float(as_)
            if (cf >= threshold) != (af >= threshold):
                return True
            if abs(af - cf) >= max_delta:
                return True
        ctrl_cats = {c.lower() for c in (ctrl_out.get("categories") or [])}
        arm_cats = {c.lower() for c in (arm_out.get("categories") or [])}
        if ctrl_cats != arm_cats:
            return True
        return False

    if op_key == "clusterer.classify":
        cl = ctrl_out.get("label", "")
        al = arm_out.get("label", "")
        if cl in _ATTACHING_LABELS or al in _ATTACHING_LABELS:
            return not (cl == al and ctrl_out.get("thread_id") == arm_out.get("thread_id"))
        return cl != al

    if op_key == "clusterer.merge":
        return bool(ctrl_out.get("same_story", False)) != bool(arm_out.get("same_story", False))

    # consolidate_facts and brief.generate: no rule.
    return False


# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------


def _compute_verdict(
    op_key: str,
    n_pairs: int,
    arm_rel: Dict[str, Any],
    ctrl_rel: Dict[str, Any],
    content: Dict[str, Any],
) -> tuple:
    """Return (verdict_str, failed_criteria_list)."""
    is_brief = op_key == "brief.generate"
    min_pairs = CRITERIA_THRESHOLDS["min_pairs_brief" if is_brief else "min_pairs_default"]
    min_ok = CRITERIA_THRESHOLDS["min_ok_pairs_brief" if is_brief else "min_ok_pairs_default"]

    # Step 1: insufficient data
    if n_pairs < min_pairs:
        return "insufficient_data", []

    failed: List[str] = []

    # Step 2: reliability criteria (all operations)
    ctrl_err = (ctrl_rel.get("error_rate") or 0.0) + (ctrl_rel.get("parse_failure_rate") or 0.0)
    arm_err = (arm_rel.get("error_rate") or 0.0) + (arm_rel.get("parse_failure_rate") or 0.0)
    excess_threshold = CRITERIA_THRESHOLDS["max_error_plus_parse_rate_excess_pp"] / 100.0
    if arm_err > ctrl_err + excess_threshold:
        failed.append("error_plus_parse_rate_exceeds_control")

    arm_trunc = arm_rel.get("finish_reason_length_rate") or 0.0
    if arm_trunc > CRITERIA_THRESHOLDS["max_length_truncation_rate"]:
        failed.append("length_truncation_rate_exceeds_threshold")

    if failed:
        return "fail", failed

    # Step 4 (early): review operations skip content criteria entirely.
    if op_key in _REVIEW_OPERATIONS:
        return "review", []

    # Step 3: content criteria
    n_ok_pairs = content.get("n_ok_pairs", 0) if content else 0
    if n_ok_pairs < min_ok:
        failed.append("insufficient_ok_pairs")
        return "fail", failed

    if op_key == "summarize_rank.rank":
        t = CRITERIA_THRESHOLDS["summarize_rank.rank"]
        rho = content.get("spearman_rho")
        if rho is not None and rho < t["min_spearman_rho"]:
            failed.append("spearman_rho_below_threshold")
        mae = content.get("mae")
        if mae is not None and mae > t["max_mae"]:
            failed.append("mae_exceeds_threshold")
        flip = content.get("flip_rate")
        if flip is not None and flip > t["max_flip_rate"]:
            failed.append("flip_rate_exceeds_threshold")
        eng = content.get("engagement") or {}
        n_saved = eng.get("n_saved", 0)
        if n_saved >= CRITERIA_THRESHOLDS["min_saved_for_auc"]:
            auc_ctrl = eng.get("auc_saved_control")
            auc_arm = eng.get("auc_saved_arm")
            if auc_ctrl is not None and auc_arm is not None:
                if auc_arm < auc_ctrl + t["min_is_saved_auc_delta"]:
                    failed.append("is_saved_auc_below_threshold")

    elif op_key == "clusterer.classify":
        t = CRITERIA_THRESHOLDS["clusterer.classify"]
        agr = content.get("assignment_agreement")
        if agr is not None and agr < t["min_assignment_agreement"]:
            failed.append("assignment_agreement_below_threshold")

    elif op_key == "clusterer.merge":
        t = CRITERIA_THRESHOLDS["clusterer.merge"]
        agr = content.get("agreement")
        if agr is not None and agr < t["min_agreement"]:
            failed.append("agreement_below_threshold")
        arm_only = content.get("arm_only_merges")
        if arm_only is not None and arm_only > t["max_arm_only_merges"]:
            failed.append("arm_only_merges_exceeds_threshold")

    if failed:
        return "fail", failed

    return "pass", []


# ---------------------------------------------------------------------------
# JSONL sample writing
# ---------------------------------------------------------------------------


def _write_op_samples(
    out_path: str,
    op_key: str,
    op_data: Dict[str, Any],
    config: Dict[str, Any],
) -> None:
    """Write one JSONL sample file for an operation."""
    all_arms = op_data["all_arms"]
    arm_pairs = op_data["arm_pairs"]
    is_brief = op_key == "brief.generate"
    is_consolidate = op_key == "clusterer.consolidate_facts"

    # Build combined per-ref_id view across all arms.
    ref_ctrl: Dict[str, Dict] = {}
    ref_arms: Dict[str, Dict[str, Dict]] = defaultdict(dict)
    for arm in all_arms:
        for ctrl_row, arm_row in arm_pairs.get(arm, []):
            rid = ctrl_row["ref_id"]
            ref_ctrl[rid] = ctrl_row
            ref_arms[rid][arm] = arm_row

    def _make_item(rid: str) -> Dict:
        ctrl = ref_ctrl[rid]
        arm_rows = ref_arms[rid]
        disagreements = []
        if not is_brief and not is_consolidate:
            for arm, arm_row in arm_rows.items():
                if _is_pair_disagree(op_key, ctrl, arm_row, config):
                    disagreements.append(arm)
        return {
            "ref_id": rid,
            "prompt_hash": ctrl.get("prompt_hash"),
            "control": {
                "status": ctrl.get("status"),
                "output": ctrl.get("output"),
                "raw_output": ctrl.get("raw_output"),
            },
            "arms": {
                arm: {
                    "status": arm_row.get("status"),
                    "output": arm_row.get("output"),
                    "raw_output": arm_row.get("raw_output"),
                }
                for arm, arm_row in arm_rows.items()
            },
            "disagreements": disagreements,
        }

    os.makedirs(os.path.dirname(out_path), exist_ok=True) if os.path.dirname(out_path) else None

    if is_brief:
        with open(out_path, "w") as fh:
            for rid in ref_ctrl:
                fh.write(json.dumps(_make_item(rid), default=json_default) + "\n")
        return

    if is_consolidate:
        pool = sorted(ref_ctrl.keys())
        rng = random.Random(SAMPLE_SEED)
        sample_rids = rng.sample(pool, min(100, len(pool)))
        with open(out_path, "w") as fh:
            for rid in sample_rids:
                fh.write(json.dumps(_make_item(rid), default=json_default) + "\n")
        return

    # Disagreeing vs agreeing split.
    disagreeing_rids = []
    agreeing_rids = []
    for rid, arm_rows in ref_arms.items():
        ctrl = ref_ctrl[rid]
        is_dis = any(
            _is_pair_disagree(op_key, ctrl, arm_row, config)
            for arm_row in arm_rows.values()
        )
        if is_dis:
            disagreeing_rids.append(rid)
        else:
            agreeing_rids.append(rid)

    # Disagreeing: up to 200, control created_at desc.
    disagreeing_rids.sort(
        key=lambda r: _row_created_at(ref_ctrl[r]) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    dis_sample = disagreeing_rids[:200]

    # Agreeing: up to 50, seeded random from ref_id-sorted pool.
    agreeing_sorted = sorted(agreeing_rids)
    rng = random.Random(SAMPLE_SEED)
    agr_sample = rng.sample(agreeing_sorted, min(50, len(agreeing_sorted)))

    with open(out_path, "w") as fh:
        for rid in dis_sample:
            fh.write(json.dumps(_make_item(rid), default=json_default) + "\n")
        for rid in agr_sample:
            fh.write(json.dumps(_make_item(rid), default=json_default) + "\n")


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


# ---------------------------------------------------------------------------
# Statistical helpers (no scipy/sklearn)
# ---------------------------------------------------------------------------


def _rank_avg_ties(vals: List[float]) -> List[float]:
    """Return 1-indexed averaged-tie ranks for vals."""
    n = len(vals)
    sorted_idx = sorted(range(n), key=lambda i: vals[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i + 1
        while j < n and vals[sorted_idx[j]] == vals[sorted_idx[i]]:
            j += 1
        avg_rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[sorted_idx[k]] = avg_rank
        i = j
    return ranks


def _pearson(x: List[float], y: List[float]) -> Optional[float]:
    n = len(x)
    if n < 2:
        return None
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    dx = sum((xi - mx) ** 2 for xi in x)
    dy = sum((yi - my) ** 2 for yi in y)
    if dx == 0.0 or dy == 0.0:
        return None
    return num / (dx * dy) ** 0.5


def _spearman(x: List[float], y: List[float]) -> Optional[float]:
    """Spearman ρ with averaged-tie ranks (general formula via Pearson of ranks)."""
    if len(x) != len(y) or len(x) < 2:
        return None
    return _pearson(_rank_avg_ties(x), _rank_avg_ties(y))


def _roc_auc(scores: List[float], labels: List[int]) -> Optional[float]:
    """ROC AUC via Mann-Whitney U (handles ties, O(n²) — fine for report sizes)."""
    n = len(scores)
    if n < 2 or len(labels) != n:
        return None
    pos_count = sum(labels)
    neg_count = n - pos_count
    if pos_count == 0 or neg_count == 0:
        return None
    pos_scores = [scores[i] for i in range(n) if labels[i] == 1]
    neg_scores = [scores[i] for i in range(n) if labels[i] == 0]
    total = 0.0
    for ps in pos_scores:
        for ns in neg_scores:
            if ps > ns:
                total += 1.0
            elif ps == ns:
                total += 0.5
    return total / (pos_count * neg_count)


def _jaccard(a: List[str], b: List[str]) -> float:
    """Case-insensitive Jaccard similarity; both-empty returns 1.0."""
    sa = {s.lower() for s in a}
    sb = {s.lower() for s in b}
    union = sa | sb
    if not union:
        return 1.0
    return len(sa & sb) / len(union)


def _row_created_at(row: Dict) -> Optional[datetime]:
    """Return a timezone-aware datetime from a shadow row dict."""
    val = row.get("created_at")
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo is not None else val.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(val))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Per-operation content metrics
# ---------------------------------------------------------------------------


def _content_summarize_rank(
    ok_pairs: List[Tuple[Dict, Dict]],
    config: Dict[str, Any],
    session: Session,
    until_dt: datetime,
) -> Dict[str, Any]:
    if not ok_pairs:
        return {}

    threshold = float(config.get("WEB_IMPORTANT_THRESHOLD", 70))

    ctrl_scores: List[float] = []
    arm_scores: List[float] = []
    signed_diffs: List[float] = []
    flip_count = 0
    topics_jaccard_vals: List[float] = []
    cats_exact = 0
    cats_jaccard_vals: List[float] = []
    summary_length_ratios: List[float] = []

    for ctrl_row, arm_row in ok_pairs:
        ctrl_out = ctrl_row.get("output") or {}
        arm_out = arm_row.get("output") or {}

        cs = ctrl_out.get("importance_score")
        as_ = arm_out.get("importance_score")
        if cs is not None and as_ is not None:
            cf, af = float(cs), float(as_)
            ctrl_scores.append(cf)
            arm_scores.append(af)
            signed_diffs.append(af - cf)
            if (cf >= threshold) != (af >= threshold):
                flip_count += 1

        topics_jaccard_vals.append(
            _jaccard(ctrl_out.get("topics") or [], arm_out.get("topics") or [])
        )

        ctrl_cats = ctrl_out.get("categories") or []
        arm_cats = arm_out.get("categories") or []
        if {c.lower() for c in ctrl_cats} == {c.lower() for c in arm_cats}:
            cats_exact += 1
        cats_jaccard_vals.append(_jaccard(ctrl_cats, arm_cats))

        ctrl_summary = ctrl_out.get("summary") or ""
        arm_summary = arm_out.get("summary") or ""
        if ctrl_summary:
            summary_length_ratios.append(len(arm_summary) / len(ctrl_summary))

    n = len(ok_pairs)
    n_score = len(ctrl_scores)

    result: Dict[str, Any] = {
        "n_ok_pairs": n,
        "spearman_rho": _spearman(ctrl_scores, arm_scores),
        "mae": statistics.mean([abs(d) for d in signed_diffs]) if signed_diffs else None,
        "mean_signed_diff": statistics.mean(signed_diffs) if signed_diffs else None,
        "flip_rate": round(flip_count / n_score, 4) if n_score else None,
        "topics_mean_jaccard": statistics.mean(topics_jaccard_vals) if topics_jaccard_vals else None,
        "categories_exact_match_rate": round(cats_exact / n, 4) if n else None,
        "categories_mean_jaccard": statistics.mean(cats_jaccard_vals) if cats_jaccard_vals else None,
        "summary_length_ratio_median": _percentile(summary_length_ratios, 50),
    }

    # Engagement block: pairs old enough to have article-level engagement signals.
    cutoff_dt = until_dt - timedelta(hours=48)
    old_pairs = []
    for c, a in ok_pairs:
        ts = _row_created_at(c)
        if ts is not None and ts <= cutoff_dt:
            old_pairs.append((c, a))

    if old_pairs:
        ref_ids = [c["ref_id"] for c, _ in old_pairs]
        try:
            int_ids = [int(r) for r in ref_ids]
        except (ValueError, TypeError):
            int_ids = []

        article_map: Dict[str, Dict] = {}
        if int_ids:
            art_sql = text(
                "SELECT id, is_saved, is_read FROM articles WHERE id IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            for art_row in session.execute(art_sql, {"ids": int_ids}).fetchall():
                article_map[str(art_row[0])] = {
                    "is_saved": bool(art_row[1]),
                    "is_read": bool(art_row[2]),
                }

        n_article_missing = sum(1 for r in ref_ids if r not in article_map)

        ctrl_scores_eng: List[float] = []
        arm_scores_eng: List[float] = []
        saved_labels: List[int] = []
        read_labels: List[int] = []

        for ctrl_row, arm_row in old_pairs:
            art = article_map.get(ctrl_row["ref_id"])
            if art is None:
                continue
            ctrl_out = ctrl_row.get("output") or {}
            arm_out = arm_row.get("output") or {}
            cs2 = ctrl_out.get("importance_score")
            as2 = arm_out.get("importance_score")
            if cs2 is not None and as2 is not None:
                ctrl_scores_eng.append(float(cs2))
                arm_scores_eng.append(float(as2))
                saved_labels.append(1 if art["is_saved"] else 0)
                read_labels.append(1 if art["is_read"] else 0)

        engagement: Dict[str, Any] = {
            "n": len(ctrl_scores_eng),
            "n_saved": sum(saved_labels),
            "n_read": sum(read_labels),
            "n_article_missing": n_article_missing,
            "auc_saved_control": _roc_auc(ctrl_scores_eng, saved_labels),
            "auc_saved_arm": _roc_auc(arm_scores_eng, saved_labels),
            "auc_read_control": _roc_auc(ctrl_scores_eng, read_labels),
            "auc_read_arm": _roc_auc(arm_scores_eng, read_labels),
            "exposure_bias": "ranking shown to the user came from control scores",
        }
        try:
            auto_read_val = int(config.get("WEB_AUTO_READ_SECONDS", 5))
        except (TypeError, ValueError):
            auto_read_val = 0
        if auto_read_val > 0:
            engagement["auto_read_inflation"] = (
                "is_read is inflated by auto-read; prefer is_saved"
            )
        result["engagement"] = engagement

    return result


def _content_clusterer_classify(ok_pairs: List[Tuple[Dict, Dict]]) -> Dict[str, Any]:
    if not ok_pairs:
        return {}

    n = len(ok_pairs)
    label_agree = 0
    assignment_agree = 0
    conf_agree_ctrl: List[float] = []
    conf_agree_arm: List[float] = []
    conf_disagree_ctrl: List[float] = []
    conf_disagree_arm: List[float] = []
    facts_len_diffs: List[float] = []
    thread_title_present = 0
    thread_title_total = 0
    label_confusion: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for ctrl_row, arm_row in ok_pairs:
        ctrl_out = ctrl_row.get("output") or {}
        arm_out = arm_row.get("output") or {}

        cl = ctrl_out.get("label", "")
        al = arm_out.get("label", "")
        ct = ctrl_out.get("thread_id")
        at = arm_out.get("thread_id")

        label_confusion[cl][al] += 1
        labels_match = cl == al
        if labels_match:
            label_agree += 1

        # Attaching labels require same label AND same thread_id.
        if cl in _ATTACHING_LABELS or al in _ATTACHING_LABELS:
            assigns_match = cl == al and ct == at
        else:
            assigns_match = labels_match
        if assigns_match:
            assignment_agree += 1

        cc = ctrl_out.get("confidence")
        ac = arm_out.get("confidence")
        if assigns_match:
            if cc is not None:
                conf_agree_ctrl.append(float(cc))
            if ac is not None:
                conf_agree_arm.append(float(ac))
        else:
            if cc is not None:
                conf_disagree_ctrl.append(float(cc))
            if ac is not None:
                conf_disagree_arm.append(float(ac))

        ctrl_facts = ctrl_out.get("new_facts") or []
        arm_facts = arm_out.get("new_facts") or []
        facts_len_diffs.append(float(len(arm_facts) - len(ctrl_facts)))

        for out in (ctrl_out, arm_out):
            if out.get("label", "") in _THREAD_TITLE_LABELS:
                thread_title_total += 1
                if out.get("thread_title"):
                    thread_title_present += 1

    return {
        "n_ok_pairs": n,
        "label_agreement": round(label_agree / n, 4) if n else None,
        "assignment_agreement": round(assignment_agree / n, 4) if n else None,
        "label_confusion": {k: dict(v) for k, v in label_confusion.items()},
        "mean_confidence_agreeing_control": (
            statistics.mean(conf_agree_ctrl) if conf_agree_ctrl else None
        ),
        "mean_confidence_agreeing_arm": (
            statistics.mean(conf_agree_arm) if conf_agree_arm else None
        ),
        "mean_confidence_disagreeing_control": (
            statistics.mean(conf_disagree_ctrl) if conf_disagree_ctrl else None
        ),
        "mean_confidence_disagreeing_arm": (
            statistics.mean(conf_disagree_arm) if conf_disagree_arm else None
        ),
        "mean_new_facts_len_diff": (
            statistics.mean(facts_len_diffs) if facts_len_diffs else None
        ),
        "thread_title_present_rate": (
            round(thread_title_present / thread_title_total, 4) if thread_title_total else None
        ),
    }


def _content_clusterer_merge(ok_pairs: List[Tuple[Dict, Dict]]) -> Dict[str, Any]:
    if not ok_pairs:
        return {}

    n = len(ok_pairs)
    agree_count = 0
    ctrl_merge_count = 0
    arm_merge_count = 0
    arm_only_merge_count = 0
    confusion: Dict[bool, Dict[bool, int]] = {
        True: {True: 0, False: 0},
        False: {True: 0, False: 0},
    }

    for ctrl_row, arm_row in ok_pairs:
        ctrl_out = ctrl_row.get("output") or {}
        arm_out = arm_row.get("output") or {}
        cs = bool(ctrl_out.get("same_story", False))
        as_ = bool(arm_out.get("same_story", False))
        confusion[cs][as_] += 1
        if cs == as_:
            agree_count += 1
        if cs:
            ctrl_merge_count += 1
        if as_:
            arm_merge_count += 1
        if as_ and not cs:
            arm_only_merge_count += 1

    return {
        "n_ok_pairs": n,
        "agreement": round(agree_count / n, 4) if n else None,
        "confusion": {
            "ctrl_true_arm_true": confusion[True][True],
            "ctrl_true_arm_false": confusion[True][False],
            "ctrl_false_arm_true": confusion[False][True],
            "ctrl_false_arm_false": confusion[False][False],
        },
        "ctrl_merge_rate": round(ctrl_merge_count / n, 4) if n else None,
        "arm_merge_rate": round(arm_merge_count / n, 4) if n else None,
        "arm_only_merges": round(arm_only_merge_count / n, 4) if n else None,
    }


def _content_clusterer_consolidate_facts(
    ok_pairs: List[Tuple[Dict, Dict]],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    if not ok_pairs:
        return {}

    max_facts = int(config.get("CLUSTERER_MAX_KNOWN_FACTS", 40))
    keep_recent = int(config.get("CLUSTERER_KNOWN_FACTS_KEEP_RECENT", 20))
    target_ratio = float(config.get("CLUSTERER_FACTS_CONDENSED_TARGET_RATIO", 0.6))
    target_total = max(1, int(max_facts * target_ratio))
    target = max(1, target_total - keep_recent)

    n = len(ok_pairs)
    bullet_counts: List[float] = []
    over_target_count = 0
    bullet_lengths: List[float] = []

    for _, arm_row in ok_pairs:
        arm_out = arm_row.get("output")
        bullets = [str(b) for b in arm_out] if isinstance(arm_out, list) else []
        count = len(bullets)
        bullet_counts.append(float(count))
        if count > target:
            over_target_count += 1
        for b in bullets:
            bullet_lengths.append(float(len(b)))

    return {
        "n_ok_pairs": n,
        "target": target,
        "mean_output_bullet_count": (
            statistics.mean(bullet_counts) if bullet_counts else None
        ),
        "over_target_rate": round(over_target_count / n, 4) if n else None,
        "mean_bullet_length": (
            statistics.mean(bullet_lengths) if bullet_lengths else None
        ),
    }


def _content_brief_generate(
    all_pairs: List[Tuple[Dict, Dict]],
    session: Session,
) -> Dict[str, Any]:
    if not all_pairs:
        return {}

    n = len(all_pairs)
    arm_ok_count = sum(1 for _, a in all_pairs if a.get("status") == "ok")
    ok_pairs = [(c, a) for c, a in all_pairs if a.get("status") == "ok"]
    n_ok = len(ok_pairs)

    turns_list: List[float] = []
    forced_submit_count = 0
    corrective_turn_count = 0
    topic_counts: List[float] = []
    linked_ref_article_ids: List[int] = []
    linked_refs_total = 0
    unlinked_refs_total = 0

    for _, arm_row in ok_pairs:
        arm_out = arm_row.get("output") or {}
        turns = arm_out.get("turns")
        if turns is not None:
            turns_list.append(float(turns))
        if arm_out.get("forced_submit"):
            forced_submit_count += 1
        if arm_out.get("corrective_turn"):
            corrective_turn_count += 1
        payload = arm_out.get("payload") or {}
        topics = payload.get("topics") or []
        topic_counts.append(float(len(topics)))
        for topic in topics:
            for ref in topic.get("references") or []:
                art_id = ref.get("article_id")
                if art_id is not None:
                    linked_refs_total += 1
                    try:
                        linked_ref_article_ids.append(int(art_id))
                    except (TypeError, ValueError):
                        pass
                else:
                    unlinked_refs_total += 1

    existing_ids: set = set()
    if linked_ref_article_ids:
        unique_ids = list(set(linked_ref_article_ids))
        art_sql = text(
            "SELECT id FROM articles WHERE id IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        for art_row in session.execute(art_sql, {"ids": unique_ids}).fetchall():
            existing_ids.add(art_row[0])
    linked_ref_valid_count = sum(1 for aid in linked_ref_article_ids if aid in existing_ids)
    all_refs_total = linked_refs_total + unlinked_refs_total

    return {
        "n_ok_pairs": n_ok,
        "ok_rate": round(arm_ok_count / n, 4) if n else None,
        "mean_turns": statistics.mean(turns_list) if turns_list else None,
        "forced_submit_rate": round(forced_submit_count / n_ok, 4) if n_ok else None,
        "corrective_turn_rate": round(corrective_turn_count / n_ok, 4) if n_ok else None,
        "mean_topic_count": statistics.mean(topic_counts) if topic_counts else None,
        "linked_ref_valid_share": (
            round(linked_ref_valid_count / linked_refs_total, 4) if linked_refs_total else None
        ),
        "unlinked_ref_share": (
            round(unlinked_refs_total / all_refs_total, 4) if all_refs_total else None
        ),
    }


def _compute_content_metrics(
    op_key: str,
    all_pairs: List[Tuple[Dict, Dict]],
    config: Dict[str, Any],
    session: Session,
    until_dt: datetime,
) -> Dict[str, Any]:
    ok_pairs = [
        (c, a) for c, a in all_pairs
        if c.get("status") == "ok" and a.get("status") == "ok"
    ]
    if op_key == "summarize_rank.rank":
        return _content_summarize_rank(ok_pairs, config, session, until_dt)
    if op_key == "clusterer.classify":
        return _content_clusterer_classify(ok_pairs)
    if op_key == "clusterer.merge":
        return _content_clusterer_merge(ok_pairs)
    if op_key == "clusterer.consolidate_facts":
        return _content_clusterer_consolidate_facts(ok_pairs, config)
    if op_key == "brief.generate":
        return _content_brief_generate(all_pairs, session)
    return {}


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
    samples_dir: Optional[str] = None,
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

            content = _compute_content_metrics(
                op_key, pairs, config, session, until_dt
            )
            verdict, failed_criteria = _compute_verdict(
                op_key, len(pairs), arm_rel, ctrl_rel, content
            )
            arms_output[arm] = {
                "pairs": len(pairs),
                "unpaired": arm_unpaired.get(arm, 0),
                "no_telemetry": arm_no_tel,
                "reliability": arm_rel,
                "cost": arm_cost,
                "content": content,
                "verdict": verdict,
                "failed_criteria": failed_criteria,
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

        if samples_dir is not None:
            out_path = os.path.join(samples_dir, f"{op_key}.jsonl")
            _write_op_samples(out_path, op_key, data, config)

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

        _VERDICT_STYLE = {
            "pass": "green",
            "fail": "red",
            "review": "yellow",
            "insufficient_data": "dim",
        }

        for arm, arm_data in op_data["arms"].items():
            arm_rel = arm_data["reliability"]
            arm_cost = arm_data["cost"]
            verdict = arm_data.get("verdict", "—")
            style = _VERDICT_STYLE.get(verdict, "")
            verdict_str = f"[{style}]{verdict}[/{style}]" if style else verdict
            table.add_row(
                arm,
                str(arm_data["pairs"]),
                _pct(arm_rel.get("error_rate")),
                _pct(arm_rel.get("parse_failure_rate")),
                _ms(arm_rel.get("latency_per_item_p50_ms")),
                _usd(arm_cost.get("mean_cost_per_item_usd")),
                _usd2(arm_cost.get("projected_monthly_usd")),
                verdict_str,
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
        report = _build_report(session, experiment, since_dt, until_dt, config, samples_dir)

    if as_json:
        typer.echo(json.dumps(report, default=json_default))
    else:
        _render_table(report)

    if samples_dir is not None:
        msg = f"[green]Samples written to {samples_dir}[/green]"
        if as_json:
            _error_console.print(msg)
        else:
            console.print(msg)
