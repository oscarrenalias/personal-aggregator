"""Tests for llm-experiment-report and shadow-aware llm-stats.

Coverage:
  AC1 – content metrics (Spearman, MAE, flip rate, AUC, projected cost) with
         hand-computed values using seeded DB fixtures.
  AC2 – verdict logic: pass / fail / review / insufficient_data.
  AC3 – disagreement rules per operation + status-mismatch override.
  AC4 – --samples JSONL output, caps (200 disagree / 50 agree), determinism.
  AC5 – unpaired rows excluded from content metrics, counted under 'unpaired'.
  AC6 – llm_stats excludes shadow by default; --include-shadow adds breakdown;
         --json emits valid JSON in both modes.
  AC7 – llm-experiment-report uses SET TRANSACTION READ ONLY without raising.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from aggregator_admin.llm_experiment import (
    CRITERIA_THRESHOLDS,
    SAMPLE_SEED,
    _compute_verdict,
    _is_pair_disagree,
    _pair_shadow_rows,
    _percentile,
    _spearman,
    _roc_auc,
    _jaccard,
    _compute_metrics,
    _build_report,
    _read_config,
)
from aggregator_admin.main import app
from aggregator_common.models import LlmCall, LlmShadowResult
from aggregator_common.queries import llm_stats, llm_shadow_stats

_NOW = datetime.now(tz=timezone.utc)
_RECENT = _NOW - timedelta(days=1)
_EXP = "test-exp"


# ---------------------------------------------------------------------------
# Helpers for seeding DB rows
# ---------------------------------------------------------------------------


def _insert_llm_call(
    session: Session,
    *,
    service: str = "summarize_rank",
    operation: str = "rank",
    model: str = "gpt-4.1-mini",
    arm: str | None = None,
    experiment: str | None = None,
    ref_id: str | None = None,
    cost_usd: float = 0.001,
    latency_ms: int = 200,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    finish_reason: str | None = None,
    status: str = "success",
    reasoning_tokens: int | None = None,
    created_at: datetime = _RECENT,
) -> LlmCall:
    row = LlmCall(
        id=uuid.uuid4(),
        service=service,
        operation=operation,
        model=model,
        arm=arm,
        experiment=experiment,
        ref_id=ref_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        cached_tokens=0,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        finish_reason=finish_reason,
        status=status,
        num_tool_calls=0,
        reasoning_tokens=reasoning_tokens,
    )
    session.add(row)
    session.flush()
    session.execute(
        text("UPDATE llm_calls SET created_at = :ts WHERE id = :id"),
        {"ts": created_at, "id": row.id},
    )
    session.commit()
    return row


def _insert_shadow(
    session: Session,
    *,
    experiment: str = _EXP,
    service: str = "summarize_rank",
    operation: str = "rank",
    ref_id: str = "1",
    arm: str = "control",
    model: str = "gpt-4.1-mini",
    status: str = "ok",
    output: dict | list | None = None,
    created_at: datetime = _RECENT,
) -> LlmShadowResult:
    row = LlmShadowResult(
        experiment=experiment,
        service=service,
        operation=operation,
        ref_id=ref_id,
        arm=arm,
        model=model,
        prompt_hash="abc",
        status=status,
        attempts=1,
        params={},
        output=output,
    )
    session.add(row)
    session.flush()
    session.execute(
        text("UPDATE llm_shadow_results SET created_at = :ts WHERE id = :id"),
        {"ts": created_at, "id": row.id},
    )
    session.commit()
    return row


def _truncate_experiment_tables(session: Session) -> None:
    session.execute(text("TRUNCATE TABLE llm_calls RESTART IDENTITY CASCADE"))
    session.execute(text("TRUNCATE TABLE llm_shadow_results RESTART IDENTITY CASCADE"))
    session.commit()


# ---------------------------------------------------------------------------
# AC3 – Disagreement rules (pure-function, no DB)
# ---------------------------------------------------------------------------


class TestDisagreementRules:
    """AC3: per-operation disagreement fixtures."""

    def _make_rank_out(self, score: int, cats: list[str] | None = None) -> dict:
        return {"importance_score": score, "categories": cats or ["Tech"]}

    def test_rank_status_mismatch_disagrees(self) -> None:
        ctrl = {"status": "ok", "output": self._make_rank_out(50)}
        arm = {"status": "api_error", "output": self._make_rank_out(50)}
        assert _is_pair_disagree("summarize_rank.rank", ctrl, arm, {}) is True

    def test_rank_importance_flip_disagrees(self) -> None:
        ctrl = {"status": "ok", "output": self._make_rank_out(65)}
        arm = {"status": "ok", "output": self._make_rank_out(75)}
        assert _is_pair_disagree("summarize_rank.rank", ctrl, arm, {"WEB_IMPORTANT_THRESHOLD": 70}) is True

    def test_rank_large_delta_disagrees(self) -> None:
        ctrl = {"status": "ok", "output": self._make_rank_out(50)}
        arm = {"status": "ok", "output": self._make_rank_out(70)}
        assert _is_pair_disagree("summarize_rank.rank", ctrl, arm, {}) is True

    def test_rank_small_delta_agrees(self) -> None:
        ctrl = {"status": "ok", "output": self._make_rank_out(50, ["Tech"])}
        arm = {"status": "ok", "output": self._make_rank_out(55, ["Tech"])}
        assert _is_pair_disagree("summarize_rank.rank", ctrl, arm, {}) is False

    def test_rank_category_mismatch_disagrees(self) -> None:
        ctrl = {"status": "ok", "output": self._make_rank_out(50, ["Tech"])}
        arm = {"status": "ok", "output": self._make_rank_out(50, ["Science"])}
        assert _is_pair_disagree("summarize_rank.rank", ctrl, arm, {}) is True

    def test_classify_different_label_disagrees(self) -> None:
        ctrl = {"status": "ok", "output": {"label": "new_thread"}}
        arm = {"status": "ok", "output": {"label": "unrelated"}}
        assert _is_pair_disagree("clusterer.classify", ctrl, arm, {}) is True

    def test_classify_attaching_same_label_diff_thread_disagrees(self) -> None:
        ctrl = {"status": "ok", "output": {"label": "same_thread_new_fact", "thread_id": "1"}}
        arm = {"status": "ok", "output": {"label": "same_thread_new_fact", "thread_id": "2"}}
        assert _is_pair_disagree("clusterer.classify", ctrl, arm, {}) is True

    def test_classify_attaching_same_label_same_thread_agrees(self) -> None:
        ctrl = {"status": "ok", "output": {"label": "same_thread_new_fact", "thread_id": "1"}}
        arm = {"status": "ok", "output": {"label": "same_thread_new_fact", "thread_id": "1"}}
        assert _is_pair_disagree("clusterer.classify", ctrl, arm, {}) is False

    def test_classify_non_attaching_same_label_agrees(self) -> None:
        ctrl = {"status": "ok", "output": {"label": "unrelated"}}
        arm = {"status": "ok", "output": {"label": "unrelated"}}
        assert _is_pair_disagree("clusterer.classify", ctrl, arm, {}) is False

    def test_merge_same_story_flip_disagrees(self) -> None:
        ctrl = {"status": "ok", "output": {"same_story": True}}
        arm = {"status": "ok", "output": {"same_story": False}}
        assert _is_pair_disagree("clusterer.merge", ctrl, arm, {}) is True

    def test_merge_both_true_agrees(self) -> None:
        ctrl = {"status": "ok", "output": {"same_story": True}}
        arm = {"status": "ok", "output": {"same_story": True}}
        assert _is_pair_disagree("clusterer.merge", ctrl, arm, {}) is False

    def test_consolidate_facts_never_disagrees(self) -> None:
        ctrl = {"status": "ok", "output": ["fact1"]}
        arm = {"status": "ok", "output": ["fact2", "fact3"]}
        assert _is_pair_disagree("clusterer.consolidate_facts", ctrl, arm, {}) is False

    def test_brief_generate_never_disagrees_by_rule(self) -> None:
        ctrl = {"status": "ok", "output": {"turns": 3}}
        arm = {"status": "ok", "output": {"turns": 1}}
        assert _is_pair_disagree("brief.generate", ctrl, arm, {}) is False


# ---------------------------------------------------------------------------
# AC2 – Verdict logic (pure-function, no DB)
# ---------------------------------------------------------------------------


class TestVerdictLogic:
    def _ok_content(self, op_key: str, n: int = 50) -> dict:
        if op_key == "summarize_rank.rank":
            return {
                "n_ok_pairs": n,
                "spearman_rho": 0.95,
                "mae": 5,
                "flip_rate": 0.05,
                "engagement": {"n_saved": 0},
            }
        if op_key == "clusterer.classify":
            return {"n_ok_pairs": n, "outcome_agreement": 0.95, "assignment_agreement": 0.90}
        if op_key == "clusterer.merge":
            return {"n_ok_pairs": n, "agreement": 0.95, "arm_only_merges": 0.02}
        return {"n_ok_pairs": n}

    def _rel(self, error_rate: float = 0.0, parse_rate: float = 0.0, trunc: float = 0.0) -> dict:
        return {
            "error_rate": error_rate,
            "parse_failure_rate": parse_rate,
            "finish_reason_length_rate": trunc,
        }

    def test_insufficient_data(self) -> None:
        v, fc = _compute_verdict("summarize_rank.rank", 10, self._rel(), self._rel(), {})
        assert v == "insufficient_data"
        assert fc == []

    def test_pass_summarize_rank(self) -> None:
        v, fc = _compute_verdict(
            "summarize_rank.rank", 30, self._rel(), self._rel(),
            self._ok_content("summarize_rank.rank")
        )
        assert v == "pass"
        assert fc == []

    def test_fail_reliability_error_rate(self) -> None:
        arm_rel = self._rel(error_rate=0.05)
        v, fc = _compute_verdict(
            "summarize_rank.rank", 30, arm_rel, self._rel(),
            self._ok_content("summarize_rank.rank")
        )
        assert v == "fail"
        assert "error_plus_parse_rate_exceeds_control" in fc

    def test_fail_truncation_rate(self) -> None:
        arm_rel = self._rel(trunc=0.05)
        v, fc = _compute_verdict(
            "summarize_rank.rank", 30, arm_rel, self._rel(),
            self._ok_content("summarize_rank.rank")
        )
        assert v == "fail"
        assert "length_truncation_rate_exceeds_threshold" in fc

    def test_fail_spearman_below_threshold(self) -> None:
        content = self._ok_content("summarize_rank.rank")
        content["spearman_rho"] = 0.70
        v, fc = _compute_verdict("summarize_rank.rank", 30, self._rel(), self._rel(), content)
        assert v == "fail"
        assert "spearman_rho_below_threshold" in fc

    def test_fail_mae_exceeds_threshold(self) -> None:
        content = self._ok_content("summarize_rank.rank")
        content["mae"] = 10
        v, fc = _compute_verdict("summarize_rank.rank", 30, self._rel(), self._rel(), content)
        assert v == "fail"
        assert "mae_exceeds_threshold" in fc

    def test_fail_flip_rate_exceeds_threshold(self) -> None:
        content = self._ok_content("summarize_rank.rank")
        content["flip_rate"] = 0.20
        v, fc = _compute_verdict("summarize_rank.rank", 30, self._rel(), self._rel(), content)
        assert v == "fail"
        assert "flip_rate_exceeds_threshold" in fc

    def test_review_consolidate_facts(self) -> None:
        v, fc = _compute_verdict(
            "clusterer.consolidate_facts", 30, self._rel(), self._rel(),
            {"n_ok_pairs": 30}
        )
        assert v == "review"
        assert fc == []

    def test_review_brief_generate(self) -> None:
        v, fc = _compute_verdict(
            "brief.generate", 5, self._rel(), self._rel(), {"n_ok_pairs": 5}
        )
        assert v == "review"

    def test_pass_classify(self) -> None:
        v, fc = _compute_verdict(
            "clusterer.classify", 30, self._rel(), self._rel(),
            self._ok_content("clusterer.classify")
        )
        assert v == "pass"

    def test_fail_classify_agreement_below_threshold(self) -> None:
        content = self._ok_content("clusterer.classify")
        content["outcome_agreement"] = 0.70  # below min_outcome_agreement: 0.92
        v, fc = _compute_verdict("clusterer.classify", 30, self._rel(), self._rel(), content)
        assert v == "fail"
        assert "outcome_agreement_below_threshold" in fc

    def test_pass_merge(self) -> None:
        v, fc = _compute_verdict(
            "clusterer.merge", 30, self._rel(), self._rel(),
            self._ok_content("clusterer.merge")
        )
        assert v == "pass"

    def test_fail_merge_arm_only_exceeds(self) -> None:
        content = self._ok_content("clusterer.merge")
        content["arm_only_merges"] = 0.10
        v, fc = _compute_verdict("clusterer.merge", 30, self._rel(), self._rel(), content)
        assert v == "fail"
        assert "arm_only_merges_exceeds_threshold" in fc


# ---------------------------------------------------------------------------
# AC1 – Content metrics with hand-computed expected values (pure-function)
# ---------------------------------------------------------------------------


class TestContentMetricsPureFunction:
    """Validate statistical helpers against hand-computed values."""

    def test_spearman_perfect_correlation(self) -> None:
        rho = _spearman([1, 2, 3, 4], [1, 2, 3, 4])
        assert rho == pytest.approx(1.0)

    def test_spearman_reverse_correlation(self) -> None:
        rho = _spearman([1, 2, 3, 4], [4, 3, 2, 1])
        assert rho == pytest.approx(-1.0)

    def test_spearman_known_value(self) -> None:
        # Hand-computed: x=[1,2,3], y=[1,3,2] → ranks match [1,3,2] / [1,2,3]
        # Pearson of ranks ([1,2,3],[1,3,2]): rho=0.5
        rho = _spearman([1.0, 2.0, 3.0], [1.0, 3.0, 2.0])
        assert rho == pytest.approx(0.5, abs=1e-6)

    def test_roc_auc_perfect(self) -> None:
        scores = [10.0, 9.0, 1.0, 0.0]
        labels = [1, 1, 0, 0]
        assert _roc_auc(scores, labels) == pytest.approx(1.0)

    def test_roc_auc_random(self) -> None:
        scores = [1.0, 1.0, 1.0, 1.0]
        labels = [1, 0, 1, 0]
        assert _roc_auc(scores, labels) == pytest.approx(0.5)

    def test_roc_auc_none_when_no_positives(self) -> None:
        assert _roc_auc([1.0, 2.0], [0, 0]) is None

    def test_jaccard_identical(self) -> None:
        assert _jaccard(["A", "B"], ["a", "b"]) == pytest.approx(1.0)

    def test_jaccard_disjoint(self) -> None:
        assert _jaccard(["A"], ["B"]) == pytest.approx(0.0)

    def test_jaccard_partial(self) -> None:
        assert _jaccard(["A", "B"], ["B", "C"]) == pytest.approx(1 / 3, abs=1e-6)

    def test_jaccard_both_empty(self) -> None:
        assert _jaccard([], []) == pytest.approx(1.0)

    def test_percentile_single(self) -> None:
        assert _percentile([5.0], 50) == pytest.approx(5.0)

    def test_percentile_empty(self) -> None:
        assert _percentile([], 50) is None

    def test_percentile_known_p95(self) -> None:
        # [1..20]: p95 = 19.05 (linear interp, 0-indexed)
        vals = list(range(1, 21))
        result = _percentile([float(v) for v in vals], 95)
        assert result == pytest.approx(19.05, abs=0.01)


# ---------------------------------------------------------------------------
# AC5 – _pair_shadow_rows unpaired counting (pure-function)
# ---------------------------------------------------------------------------


class TestPairShadowRows:
    def _make_row(self, ref_id: str, arm: str, service: str = "summarize_rank", op: str = "rank") -> dict:
        return {
            "service": service,
            "operation": op,
            "ref_id": ref_id,
            "arm": arm,
            "status": "ok",
            "output": {},
            "raw_output": None,
            "prompt_hash": "x",
            "created_at": _RECENT,
        }

    def test_fully_paired(self) -> None:
        rows = [
            self._make_row("r1", "control"),
            self._make_row("r1", "arm-a"),
        ]
        result = _pair_shadow_rows(rows)
        op = result["summarize_rank.rank"]
        assert op["arm_pairs"]["arm-a"] == [(rows[0], rows[1])]
        assert op["arm_unpaired"]["arm-a"] == 0
        assert op["control_only_count"] == 0

    def test_arm_without_control_is_unpaired(self) -> None:
        rows = [self._make_row("r1", "arm-a")]
        result = _pair_shadow_rows(rows)
        op = result["summarize_rank.rank"]
        assert op["arm_unpaired"]["arm-a"] == 1
        assert op["arm_pairs"]["arm-a"] == []

    def test_control_without_arm_is_control_only(self) -> None:
        rows = [self._make_row("r1", "control")]
        result = _pair_shadow_rows(rows)
        op = result["summarize_rank.rank"]
        assert op["control_only_count"] == 1

    def test_partial_missing_arm_counted(self) -> None:
        rows = [
            self._make_row("r1", "control"),
            self._make_row("r1", "arm-a"),
            self._make_row("r2", "control"),
            # r2 has no arm-a
        ]
        result = _pair_shadow_rows(rows)
        op = result["summarize_rank.rank"]
        assert len(op["arm_pairs"]["arm-a"]) == 1
        assert op["arm_unpaired"]["arm-a"] == 1


# ---------------------------------------------------------------------------
# AC6 – llm_stats shadow exclusion + --include-shadow + --json (DB tests)
# ---------------------------------------------------------------------------


class TestLlmStatsShadow:
    def test_shadow_excluded_by_default(self, db_session, db_engine):
        with db_engine.connect() as conn:
            conn.execute(text("TRUNCATE TABLE llm_calls RESTART IDENTITY CASCADE"))
            conn.commit()

        # Control-tagged production row (should be included)
        _insert_llm_call(db_session, arm=None, cost_usd=0.002)
        # Shadow arm row (should be excluded)
        _insert_llm_call(db_session, arm="arm-a", experiment=_EXP, cost_usd=0.010)

        rows = llm_stats(db_session, days=7, include_shadow=False)
        # Only the non-shadow row should appear
        total_cost = sum(r.total_cost_usd or 0 for r in rows)
        assert total_cost == pytest.approx(0.002, abs=1e-6)

    def test_shadow_included_when_flag_set(self, db_session, db_engine):
        with db_engine.connect() as conn:
            conn.execute(text("TRUNCATE TABLE llm_calls RESTART IDENTITY CASCADE"))
            conn.commit()

        _insert_llm_call(db_session, arm=None, cost_usd=0.002)
        _insert_llm_call(db_session, arm="arm-a", experiment=_EXP, cost_usd=0.010)

        rows = llm_stats(db_session, days=7, include_shadow=True)
        total_cost = sum(r.total_cost_usd or 0 for r in rows)
        assert total_cost == pytest.approx(0.012, abs=1e-6)

    def test_shadow_stats_groups_by_arm(self, db_session, db_engine):
        with db_engine.connect() as conn:
            conn.execute(text("TRUNCATE TABLE llm_calls RESTART IDENTITY CASCADE"))
            conn.commit()

        _insert_llm_call(db_session, arm="control", experiment=_EXP, cost_usd=0.001)
        _insert_llm_call(db_session, arm="arm-a", experiment=_EXP, cost_usd=0.005)

        rows = llm_shadow_stats(db_session, days=7)
        arms = {r.arm for r in rows}
        assert "control" in arms
        assert "arm-a" in arms

    def test_llm_stats_json_output_no_shadow(self, runner, db_session, db_engine):
        with db_engine.connect() as conn:
            conn.execute(text("TRUNCATE TABLE llm_calls RESTART IDENTITY CASCADE"))
            conn.commit()

        _insert_llm_call(db_session, arm=None, cost_usd=0.002)
        result = runner.invoke(app, ["llm-stats", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "stats" in data
        assert "days" in data
        assert "shadow_breakdown" not in data

    def test_llm_stats_json_output_with_shadow(self, runner, db_session, db_engine):
        with db_engine.connect() as conn:
            conn.execute(text("TRUNCATE TABLE llm_calls RESTART IDENTITY CASCADE"))
            conn.commit()

        _insert_llm_call(db_session, arm=None, cost_usd=0.002)
        _insert_llm_call(db_session, arm="arm-a", experiment=_EXP, cost_usd=0.005)
        result = runner.invoke(app, ["llm-stats", "--include-shadow", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "shadow_breakdown" in data
        assert isinstance(data["shadow_breakdown"], list)


# ---------------------------------------------------------------------------
# AC7 – READ ONLY transaction (DB test)
# ---------------------------------------------------------------------------


class TestReadOnlySession:
    def test_experiment_report_runs_without_raising(self, runner, db_session, db_engine):
        """AC7: llm-experiment-report uses SET TRANSACTION READ ONLY without raising."""
        with db_engine.connect() as conn:
            conn.execute(text("TRUNCATE TABLE llm_shadow_results RESTART IDENTITY CASCADE"))
            conn.commit()

        result = runner.invoke(
            app,
            [
                "llm-experiment-report",
                "--experiment", _EXP,
                "--json",
                "--since", "2020-01-01",
                "--until", "2020-01-02",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["experiment"] == _EXP
        assert data["operations"] == {}

    def test_read_only_session_cannot_write(self, db_engine):
        """AC7: verify the read-only session constraint is real (not a mock)."""
        from sqlalchemy.orm import sessionmaker as _sessionmaker
        from sqlalchemy.exc import DBAPIError

        factory = _sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
        s = factory()
        try:
            s.execute(text("SET TRANSACTION READ ONLY"))
            with pytest.raises(DBAPIError):
                s.execute(text("INSERT INTO llm_shadow_results"
                               "(experiment,service,operation,ref_id,arm,model,prompt_hash,status,attempts,params)"
                               " VALUES('x','s','o','r','a','m','h','ok',1,'{}')"))
                s.flush()
        finally:
            s.rollback()
            s.close()


# ---------------------------------------------------------------------------
# AC1 + AC5 – _compute_metrics no_telemetry counting (pure-function)
# ---------------------------------------------------------------------------


class TestComputeMetrics:
    def _shadow_row(self, status: str = "ok", ref_id: str = "1") -> dict:
        return {
            "service": "summarize_rank",
            "operation": "rank",
            "ref_id": ref_id,
            "arm": "arm-a",
            "status": status,
            "output": {},
        }

    def test_no_telemetry_counted_when_no_calls(self) -> None:
        rows = [self._shadow_row("ok", "r1")]
        rel, cost, no_tel = _compute_metrics(rows, "arm-a", {})
        assert no_tel == 1
        assert cost["mean_cost_per_item_usd"] is None

    def test_error_rate_computed(self) -> None:
        rows = [
            self._shadow_row("ok", "r1"),
            self._shadow_row("api_error", "r2"),
        ]
        rel, cost, no_tel = _compute_metrics(rows, "arm-a", {})
        assert rel["error_rate"] == pytest.approx(0.5)

    def test_parse_failure_rate_computed(self) -> None:
        rows = [
            self._shadow_row("parse_error", "r1"),
            self._shadow_row("ok", "r2"),
        ]
        rel, _, _ = _compute_metrics(rows, "arm-a", {})
        assert rel["parse_failure_rate"] == pytest.approx(0.5)

    def test_finish_reason_length_tracked(self) -> None:
        # One call with finish_reason='length', one without
        call_index = {
            ("arm-a", "summarize_rank", "rank", "r1"): [
                {"latency_ms": 100, "prompt_tokens": 10, "completion_tokens": 5,
                 "cost_usd": 0.001, "finish_reason": "length", "reasoning_tokens": None}
            ],
            ("arm-a", "summarize_rank", "rank", "r2"): [
                {"latency_ms": 100, "prompt_tokens": 10, "completion_tokens": 5,
                 "cost_usd": 0.001, "finish_reason": "stop", "reasoning_tokens": None}
            ],
        }
        rows = [self._shadow_row("ok", "r1"), self._shadow_row("ok", "r2")]
        rel, _, _ = _compute_metrics(rows, "arm-a", call_index)
        assert rel["finish_reason_length_rate"] == pytest.approx(0.5)

    def test_reasoning_tokens_summed(self) -> None:
        call_index = {
            ("arm-a", "summarize_rank", "rank", "r1"): [
                {"latency_ms": 100, "prompt_tokens": 10, "completion_tokens": 5,
                 "cost_usd": 0.001, "finish_reason": "stop", "reasoning_tokens": 50}
            ],
        }
        rows = [self._shadow_row("ok", "r1")]
        rel, _, _ = _compute_metrics(rows, "arm-a", call_index)
        assert rel["mean_reasoning_tokens_per_item"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# AC4 – --samples JSONL output, caps, and determinism (DB tests)
# ---------------------------------------------------------------------------


class TestSamplesOutput:
    def _seed_rank_shadow(self, session: Session, n_disagree: int, n_agree: int) -> None:
        """Seed (n_disagree + n_agree) control+arm pairs for summarize_rank.rank."""
        threshold = 70
        for i in range(n_disagree):
            ref = str(i + 1000)
            # control below threshold, arm above → flip → disagree
            ctrl_out = {"importance_score": 60, "categories": ["Tech"]}
            arm_out = {"importance_score": 80, "categories": ["Tech"]}
            _insert_shadow(session, ref_id=ref, arm="control", output=ctrl_out)
            _insert_shadow(session, ref_id=ref, arm="arm-a", output=arm_out)
        for i in range(n_agree):
            ref = str(i + 2000)
            ctrl_out = {"importance_score": 50, "categories": ["Tech"]}
            arm_out = {"importance_score": 52, "categories": ["Tech"]}
            _insert_shadow(session, ref_id=ref, arm="control", output=ctrl_out)
            _insert_shadow(session, ref_id=ref, arm="arm-a", output=arm_out)

    def test_samples_written_per_operation(self, runner, db_session, db_engine, tmp_path):
        _truncate_experiment_tables(db_session)
        self._seed_rank_shadow(db_session, n_disagree=5, n_agree=5)

        samples_dir = str(tmp_path / "samples")
        result = runner.invoke(
            app,
            [
                "llm-experiment-report",
                "--experiment", _EXP,
                "--json",
                "--samples", samples_dir,
            ],
        )
        assert result.exit_code == 0, result.output
        sample_file = tmp_path / "samples" / "summarize_rank.rank.jsonl"
        assert sample_file.exists(), f"Expected {sample_file}"
        lines = sample_file.read_text().strip().splitlines()
        assert len(lines) > 0
        for line in lines:
            obj = json.loads(line)
            assert "ref_id" in obj
            assert "control" in obj
            assert "arms" in obj
            assert "disagreements" in obj

    def test_samples_cap_200_disagree(self, runner, db_session, db_engine, tmp_path):
        _truncate_experiment_tables(db_session)
        # 210 disagreeing pairs → should be capped at 200 in output
        self._seed_rank_shadow(db_session, n_disagree=210, n_agree=0)

        samples_dir = str(tmp_path / "samples_cap")
        result = runner.invoke(
            app,
            [
                "llm-experiment-report",
                "--experiment", _EXP,
                "--json",
                "--samples", samples_dir,
            ],
        )
        assert result.exit_code == 0, result.output
        sample_file = tmp_path / "samples_cap" / "summarize_rank.rank.jsonl"
        lines = sample_file.read_text().strip().splitlines()
        # All are disagreeing, cap is 200
        assert len(lines) <= 200

    def test_samples_cap_50_agree(self, runner, db_session, db_engine, tmp_path):
        _truncate_experiment_tables(db_session)
        self._seed_rank_shadow(db_session, n_disagree=0, n_agree=60)

        samples_dir = str(tmp_path / "samples_agree_cap")
        result = runner.invoke(
            app,
            [
                "llm-experiment-report",
                "--experiment", _EXP,
                "--json",
                "--samples", samples_dir,
            ],
        )
        assert result.exit_code == 0, result.output
        sample_file = tmp_path / "samples_agree_cap" / "summarize_rank.rank.jsonl"
        lines = sample_file.read_text().strip().splitlines()
        # All agreeing, cap is 50
        assert len(lines) <= 50

    def test_agreeing_sample_deterministic(self, runner, db_session, db_engine, tmp_path):
        """AC4: calling --samples twice on same data produces byte-for-byte identical files."""
        _truncate_experiment_tables(db_session)
        self._seed_rank_shadow(db_session, n_disagree=0, n_agree=30)

        out1 = str(tmp_path / "det1")
        out2 = str(tmp_path / "det2")

        for samples_dir in (out1, out2):
            result = runner.invoke(
                app,
                [
                    "llm-experiment-report",
                    "--experiment", _EXP,
                    "--json",
                    "--samples", samples_dir,
                ],
            )
            assert result.exit_code == 0, result.output

        f1 = (Path(out1) / "summarize_rank.rank.jsonl").read_bytes()
        f2 = (Path(out2) / "summarize_rank.rank.jsonl").read_bytes()
        assert f1 == f2, "Agreeing sample must be deterministic across two calls"

    def test_consolidate_facts_random_sample(self, runner, db_session, db_engine, tmp_path):
        """consolidate_facts uses a random 100-item sample (seeded)."""
        _truncate_experiment_tables(db_session)
        # Seed 5 control+arm pairs for consolidate_facts
        for i in range(5):
            ref = str(i + 3000)
            _insert_shadow(session=db_session, service="clusterer", operation="consolidate_facts",
                           ref_id=ref, arm="control", output=["fact"])
            _insert_shadow(session=db_session, service="clusterer", operation="consolidate_facts",
                           ref_id=ref, arm="arm-a", output=["fact-new"])

        samples_dir = str(tmp_path / "cf_samples")
        result = runner.invoke(
            app,
            [
                "llm-experiment-report",
                "--experiment", _EXP,
                "--json",
                "--samples", samples_dir,
            ],
        )
        assert result.exit_code == 0, result.output
        sample_file = tmp_path / "cf_samples" / "clusterer.consolidate_facts.jsonl"
        assert sample_file.exists()
        lines = sample_file.read_text().strip().splitlines()
        assert 1 <= len(lines) <= 100

    def test_brief_generate_all_pairs_in_sample(self, runner, db_session, db_engine, tmp_path):
        """brief.generate writes all pairs (no cap)."""
        _truncate_experiment_tables(db_session)
        for i in range(3):
            ref = str(i + 4000)
            _insert_shadow(session=db_session, service="brief", operation="generate",
                           ref_id=ref, arm="control",
                           output={"turns": 3, "forced_submit": False, "corrective_turn": False,
                                   "payload": {"topics": []}})
            _insert_shadow(session=db_session, service="brief", operation="generate",
                           ref_id=ref, arm="arm-a",
                           output={"turns": 2, "forced_submit": False, "corrective_turn": False,
                                   "payload": {"topics": []}})

        samples_dir = str(tmp_path / "brief_samples")
        result = runner.invoke(
            app,
            [
                "llm-experiment-report",
                "--experiment", _EXP,
                "--json",
                "--samples", samples_dir,
            ],
        )
        assert result.exit_code == 0, result.output
        sample_file = tmp_path / "brief_samples" / "brief.generate.jsonl"
        assert sample_file.exists()
        lines = sample_file.read_text().strip().splitlines()
        # All 3 pairs written
        assert len(lines) == 3

    def test_json_samples_stdout_is_pure_json(self, runner, db_session, db_engine, tmp_path):
        """--json --samples must not pollute stdout; json.loads(stdout) must succeed."""
        _truncate_experiment_tables(db_session)
        self._seed_rank_shadow(db_session, n_disagree=3, n_agree=3)

        samples_dir = str(tmp_path / "purity_samples")
        result = runner.invoke(
            app,
            [
                "llm-experiment-report",
                "--experiment", _EXP,
                "--json",
                "--samples", samples_dir,
            ],
        )
        assert result.exit_code == 0, result.output
        # result.stdout is stdout-only; must be valid JSON with no trailing message
        data = json.loads(result.stdout)
        assert data["experiment"] == _EXP
        # samples file must still be created
        assert (tmp_path / "purity_samples" / "summarize_rank.rank.jsonl").exists()


# ---------------------------------------------------------------------------
# AC1 – _build_report projected cost with telemetry rows (DB test)
# ---------------------------------------------------------------------------


class TestBuildReportCost:
    def test_projected_cost_computed(self, db_session, db_engine):
        """Projected monthly cost = mean_cost * items_per_day * 30."""
        _truncate_experiment_tables(db_session)
        with db_engine.connect() as conn:
            conn.execute(text("TRUNCATE TABLE llm_calls RESTART IDENTITY CASCADE"))
            conn.commit()

        ref_id = "5001"
        # Shadow rows
        _insert_shadow(db_session, ref_id=ref_id, arm="control",
                       output={"importance_score": 60, "categories": ["Tech"]})
        _insert_shadow(db_session, ref_id=ref_id, arm="arm-a",
                       output={"importance_score": 65, "categories": ["Tech"]})
        # Matching llm_calls rows
        _insert_llm_call(db_session, arm="control", experiment=_EXP,
                         service="summarize_rank", operation="rank",
                         ref_id=ref_id, cost_usd=0.001)
        _insert_llm_call(db_session, arm="arm-a", experiment=_EXP,
                         service="summarize_rank", operation="rank",
                         ref_id=ref_id, cost_usd=0.002)

        now = datetime.now(tz=timezone.utc)
        since = now - timedelta(days=2)
        config = _read_config()

        report = _build_report(db_session, _EXP, since, now, config)
        op = report["operations"].get("summarize_rank.rank")
        assert op is not None, "Expected summarize_rank.rank in report"
        arm_data = op["arms"].get("arm-a", {})
        # projected_monthly_usd should be a number when telemetry exists
        proj = arm_data.get("cost", {}).get("projected_monthly_usd")
        assert proj is not None
        assert proj > 0
