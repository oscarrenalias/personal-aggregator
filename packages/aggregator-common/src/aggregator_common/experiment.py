"""LLM shadow-evaluation framework.

Public API::

    init(settings)                    – parse + validate LLM_EXPERIMENT once; idempotent
    shutdown()                        – drop queued items, log final counters; idempotent
    decide(key, ref_id)               – 6-step decision gate; returns Decision | None
    build_arm_kwargs(kwargs, d, arm)  – build per-arm kwargs with model/param/metadata overrides
    prompt_hash(messages)             – sha256 hex of json.dumps(messages, sort_keys=True)
    write_item(rows, ...)             – persist rows in one transaction; never raises
    submit_shadow(decision, ...)      – enqueue onto executor; no-op if decision is None
"""
from __future__ import annotations

import hashlib
import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

_KNOWN_TOP = frozenset({"enabled", "name", "until", "max_daily_cost_usd", "operations"})
_KNOWN_OP = frozenset({"sample_rate", "arms"})
_KNOWN_ARM = frozenset({"name", "model", "params", "prompt"})
_KNOWN_OPS = frozenset({
    "summarize_rank.rank",
    "clusterer.classify",
    "clusterer.merge",
    "clusterer.consolidate_facts",
    "brief.generate",
})
_FORBIDDEN_PARAMS = frozenset({"messages", "tools", "tool_choice", "response_format", "timeout", "metadata", "prompt"})

PROMPT_VARIANTS: dict[str, frozenset] = {
    "summarize_rank.rank":         frozenset({"v1", "v2"}),
    "clusterer.classify":          frozenset({"v1", "v2"}),
    "clusterer.merge":             frozenset({"v1", "v2"}),
    "clusterer.consolidate_facts": frozenset({"v1"}),
    "brief.generate":              frozenset({"v1", "v2"}),
}

_EXECUTOR_WORKERS = 2
_QUEUE_MAXSIZE = 50
_BUDGET_TTL = 60.0        # seconds between budget cache refreshes
_COUNTER_LOG_TTL = 600.0  # 10 minutes between periodic counter logs
_RAW_TRUNC = 8192         # 8 KB raw_output truncation
_ERR_TRUNC = 2048         # 2 KB error truncation


# ──────────────────────────────────────────────
# Public data structures
# ──────────────────────────────────────────────

@dataclass
class Arm:
    name: str
    model: str
    params: dict
    prompt: str = "v1"


@dataclass
class Decision:
    name: str     # experiment name
    key: str      # operation key, e.g. "summarize_rank.rank"
    ref_id: str
    arms: list    # list[Arm]

    def control_metadata(self, base: dict) -> dict:
        """Return a copy of base metadata tagged with experiment name and arm='control'."""
        md = dict(base)
        md["experiment"] = self.name
        md["arm"] = "control"
        return md

    def prompt_variants(self, control_variant: str = "v1") -> set:
        """Return the set of distinct non-control prompt variants across all arms."""
        return {arm.prompt for arm in self.arms} - {control_variant}


@dataclass
class CallOutcome:
    status: str              # ok | parse_error | api_error | timeout | refusal
    value: Any               # parsed result or None
    output: Any              # dict | list | None — JSON-serialisable form of value
    raw_output: Optional[str]
    attempts: int
    request_ids: list
    error: Optional[str]
    exception: Optional[BaseException] = None  # never stored; lets callers re-raise unchanged
    response: Any = None                       # never stored; lets rank() build its usage dict


@dataclass
class ShadowRow:
    experiment: str
    service: str
    operation: str
    ref_id: str
    arm: str
    model: str
    prompt_hash: str
    status: str
    attempts: int
    params: dict
    output: Any = None
    raw_output: Optional[str] = None
    error: Optional[str] = None
    request_ids: Optional[list] = None


# ──────────────────────────────────────────────
# Internal config types
# ──────────────────────────────────────────────

@dataclass
class _OpConfig:
    sample_rate: float
    arms: list  # list[Arm]


@dataclass
class _Config:
    name: str
    until: datetime           # UTC-aware
    max_daily_cost_usd: float
    operations: dict          # str -> _OpConfig


# ──────────────────────────────────────────────
# Module state (guarded by _lock for mutations)
# ──────────────────────────────────────────────

_lock = threading.Lock()
_config: Optional[_Config] = None
_initialized: bool = False
_executor: Optional["_Executor"] = None


# ──────────────────────────────────────────────
# Per-process counters
# ──────────────────────────────────────────────

class _Counters:
    __slots__ = (
        "_lock",
        "not_configured", "no_ref_id", "skipped_until", "not_sampled", "skipped_budget",
        "enqueued", "dropped_queue_full", "written", "write_failed",
        "_last_logged",
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.not_configured = 0
        self.no_ref_id = 0
        self.skipped_until = 0
        self.not_sampled = 0
        self.skipped_budget = 0
        self.enqueued = 0
        self.dropped_queue_full = 0
        self.written = 0
        self.write_failed = 0
        self._last_logged = 0.0

    def inc(self, name: str) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + 1)
        self._maybe_log()

    def _maybe_log(self) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._last_logged < _COUNTER_LOG_TTL:
                return
            self._last_logged = now
        self.log("periodic")

    def log(self, label: str) -> None:
        with self._lock:
            logger.info(
                "experiment[%s] counters: not_configured=%d no_ref_id=%d "
                "skipped_until=%d not_sampled=%d skipped_budget=%d "
                "enqueued=%d dropped_queue_full=%d written=%d write_failed=%d",
                label,
                self.not_configured, self.no_ref_id, self.skipped_until,
                self.not_sampled, self.skipped_budget,
                self.enqueued, self.dropped_queue_full, self.written, self.write_failed,
            )


_counters = _Counters()


# ──────────────────────────────────────────────
# Per-process budget cache
# ──────────────────────────────────────────────

class _BudgetCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cost = 0.0
        self._refreshed_at = 0.0

    def get(self, experiment_name: str) -> float:
        """Return today's shadow cost; refresh from DB at most every 60 s."""
        with self._lock:
            if time.monotonic() - self._refreshed_at < _BUDGET_TTL:
                return self._cost
        cost = self._query(experiment_name)
        with self._lock:
            self._cost = cost
            self._refreshed_at = time.monotonic()
        return cost

    def _query(self, experiment_name: str) -> float:
        try:
            from aggregator_common.db import SessionFactory
            from aggregator_common.models import LlmCall
            from sqlalchemy import func as sqf, and_

            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            sess = SessionFactory()
            try:
                val = sess.query(
                    sqf.coalesce(sqf.sum(LlmCall.cost_usd), 0.0)
                ).filter(
                    and_(
                        LlmCall.experiment == experiment_name,
                        LlmCall.arm != "control",
                        LlmCall.created_at >= today,
                    )
                ).scalar()
                return float(val or 0.0)
            finally:
                sess.close()
        except Exception as exc:
            logger.debug("experiment: budget query failed: %s", exc)
            return 0.0


_budget = _BudgetCache()


# ──────────────────────────────────────────────
# Background executor
# ──────────────────────────────────────────────

class _Executor:
    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._stop = threading.Event()
        self._threads = [
            threading.Thread(target=self._worker, daemon=True, name=f"exp-shadow-{i}")
            for i in range(_EXECUTOR_WORKERS)
        ]
        for t in self._threads:
            t.start()

    def submit(self, item: dict) -> bool:
        """Enqueue item. Returns True if accepted, False if queue full (item dropped)."""
        try:
            self._q.put_nowait(item)
            return True
        except queue.Full:
            return False

    def shutdown(self) -> None:
        """Signal workers to stop; drain remaining items without processing them."""
        self._stop.set()
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        for _ in self._threads:
            try:
                self._q.put_nowait(None)
            except queue.Full:
                pass
        for t in self._threads:
            t.join(timeout=2.0)

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                break
            try:
                _process_item(item)
            except Exception as exc:
                logger.debug("experiment: worker error: %s", exc)
            finally:
                self._q.task_done()


def _process_item(item: dict) -> None:
    """Run all arms then write control + arm rows in one transaction."""
    decision: Decision = item["decision"]
    control_kwargs: dict = item["control_kwargs"]
    control_outcome: CallOutcome = item["control_outcome"]
    attempt_fn: Callable = item["attempt_fn"]
    p_hash: str = item["phash"]

    cfg = _config
    if cfg is None:
        return
    op_cfg = cfg.operations.get(decision.key)
    if op_cfg is None:
        return

    parts = decision.key.split(".", 1)
    service = parts[0] if len(parts) == 2 else decision.key
    operation = parts[1] if len(parts) == 2 else ""

    rows: list[ShadowRow] = [
        ShadowRow(
            experiment=decision.name,
            service=service,
            operation=operation,
            ref_id=decision.ref_id,
            arm="control",
            model=control_kwargs.get("model", "unknown"),
            prompt_hash=p_hash,
            status=control_outcome.status,
            attempts=control_outcome.attempts,
            params={},
            output=control_outcome.output,
            raw_output=control_outcome.raw_output,
            error=control_outcome.error,
            request_ids=control_outcome.request_ids or None,
        )
    ]

    for arm in decision.arms:
        arm_kwargs = build_arm_kwargs(control_kwargs, decision, arm)
        try:
            arm_outcome = attempt_fn(arm_kwargs)
        except Exception as exc:
            arm_outcome = CallOutcome(
                status="api_error",
                value=None,
                output=None,
                raw_output=None,
                attempts=1,
                request_ids=[],
                error=str(exc),
                exception=exc,
            )
        rows.append(ShadowRow(
            experiment=decision.name,
            service=service,
            operation=operation,
            ref_id=decision.ref_id,
            arm=arm.name,
            model=arm.model,
            prompt_hash=p_hash,
            status=arm_outcome.status,
            attempts=arm_outcome.attempts,
            params=arm.params,
            output=arm_outcome.output,
            raw_output=arm_outcome.raw_output,
            error=arm_outcome.error,
            request_ids=arm_outcome.request_ids or None,
        ))

    write_item(rows)


# ──────────────────────────────────────────────
# Config parsing/validation
# ──────────────────────────────────────────────

def _parse_config(raw: str) -> Optional[_Config]:
    """Parse and validate LLM_EXPERIMENT JSON. Returns None and logs one ERROR on any problem."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("LLM_EXPERIMENT: invalid JSON — experiment disabled: %s", exc)
        return None

    if not isinstance(data, dict):
        logger.error("LLM_EXPERIMENT: top-level value must be a JSON object — experiment disabled")
        return None

    unknown = set(data.keys()) - _KNOWN_TOP
    if unknown:
        logger.error(
            "LLM_EXPERIMENT: unknown top-level field(s) %s — experiment disabled", sorted(unknown)
        )
        return None

    if not data.get("enabled", True):
        return None

    name = data.get("name")
    if not isinstance(name, str) or not name:
        logger.error("LLM_EXPERIMENT: 'name' must be a non-empty string — experiment disabled")
        return None

    until_raw = data.get("until")
    try:
        until_str = until_raw.replace("Z", "+00:00") if isinstance(until_raw, str) else ""
        until = datetime.fromisoformat(until_str)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
    except (AttributeError, ValueError) as exc:
        logger.error(
            "LLM_EXPERIMENT: 'until' is not a valid ISO-8601 timestamp — experiment disabled: %s", exc
        )
        return None

    max_cost = data.get("max_daily_cost_usd", 0.0)
    if not isinstance(max_cost, (int, float)):
        logger.error("LLM_EXPERIMENT: 'max_daily_cost_usd' must be a number — experiment disabled")
        return None

    ops_raw = data.get("operations")
    if not isinstance(ops_raw, dict):
        logger.error("LLM_EXPERIMENT: 'operations' must be an object — experiment disabled")
        return None

    operations: dict[str, _OpConfig] = {}
    for op_key, op_val in ops_raw.items():
        if op_key not in _KNOWN_OPS:
            logger.error(
                "LLM_EXPERIMENT: unknown operation key %r — experiment disabled", op_key
            )
            return None
        if not isinstance(op_val, dict):
            logger.error(
                "LLM_EXPERIMENT: operation %r must be an object — experiment disabled", op_key
            )
            return None
        unknown_op = set(op_val.keys()) - _KNOWN_OP
        if unknown_op:
            logger.error(
                "LLM_EXPERIMENT: operation %r has unknown field(s) %s — experiment disabled",
                op_key, sorted(unknown_op),
            )
            return None
        sample_rate = op_val.get("sample_rate", 1.0)
        if not isinstance(sample_rate, (int, float)) or not (0.0 <= float(sample_rate) <= 1.0):
            logger.error(
                "LLM_EXPERIMENT: operation %r 'sample_rate' must be 0–1 — experiment disabled", op_key
            )
            return None
        arms_raw = op_val.get("arms", [])
        if not isinstance(arms_raw, list):
            logger.error(
                "LLM_EXPERIMENT: operation %r 'arms' must be a list — experiment disabled", op_key
            )
            return None
        arms: list[Arm] = []
        for i, arm_raw in enumerate(arms_raw):
            if not isinstance(arm_raw, dict):
                logger.error(
                    "LLM_EXPERIMENT: operation %r arm[%d] must be an object — experiment disabled",
                    op_key, i,
                )
                return None
            unknown_arm = set(arm_raw.keys()) - _KNOWN_ARM
            if unknown_arm:
                logger.error(
                    "LLM_EXPERIMENT: operation %r arm[%d] has unknown field(s) %s — experiment disabled",
                    op_key, i, sorted(unknown_arm),
                )
                return None
            arm_name = arm_raw.get("name")
            arm_model = arm_raw.get("model")
            arm_params = arm_raw.get("params", {})
            if not isinstance(arm_name, str) or not arm_name:
                logger.error(
                    "LLM_EXPERIMENT: operation %r arm[%d] 'name' must be a non-empty string — experiment disabled",
                    op_key, i,
                )
                return None
            if not isinstance(arm_model, str) or not arm_model:
                logger.error(
                    "LLM_EXPERIMENT: operation %r arm[%d] 'model' must be a non-empty string — experiment disabled",
                    op_key, i,
                )
                return None
            if not isinstance(arm_params, dict):
                logger.error(
                    "LLM_EXPERIMENT: operation %r arm[%d] 'params' must be an object — experiment disabled",
                    op_key, i,
                )
                return None
            forbidden = set(arm_params.keys()) & _FORBIDDEN_PARAMS
            if forbidden:
                logger.error(
                    "LLM_EXPERIMENT: operation %r arm[%d] 'params' contains forbidden key(s) %s — experiment disabled",
                    op_key, i, sorted(forbidden),
                )
                return None
            arm_prompt = arm_raw.get("prompt", "v1")
            if not isinstance(arm_prompt, str) or not arm_prompt:
                logger.error(
                    "LLM_EXPERIMENT: operation %r arm[%d] 'prompt' must be a non-empty string — experiment disabled",
                    op_key, i,
                )
                return None
            allowed_prompts = PROMPT_VARIANTS.get(op_key, frozenset())
            if arm_prompt not in allowed_prompts:
                logger.error(
                    "LLM_EXPERIMENT: operation %r arm[%d] 'prompt' %r is not a registered variant — experiment disabled",
                    op_key, i, arm_prompt,
                )
                return None
            arms.append(Arm(name=arm_name, model=arm_model, params=arm_params, prompt=arm_prompt))
        operations[op_key] = _OpConfig(sample_rate=float(sample_rate), arms=arms)

    return _Config(
        name=name,
        until=until,
        max_daily_cost_usd=float(max_cost),
        operations=operations,
    )


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def init(settings: Any) -> None:
    """Parse and validate LLM_EXPERIMENT once; idempotent."""
    global _config, _initialized, _executor
    with _lock:
        if _initialized:
            return
        _initialized = True
        raw = getattr(settings, "llm_experiment", None)
        if not raw:
            return
        cfg = _parse_config(raw)
        if cfg is not None:
            _config = cfg
            _executor = _Executor()
            logger.info(
                "experiment: initialized %r until=%s ops=%s",
                cfg.name, cfg.until.isoformat(), list(cfg.operations.keys()),
            )


def shutdown() -> None:
    """Drop queued items, log final counters; idempotent and safe without prior init."""
    global _config, _executor
    with _lock:
        if not _initialized:
            return
        ex = _executor
        _executor = None
        _config = None
    if ex is not None:
        ex.shutdown()
    _counters.log("shutdown")


def decide(key: str, ref_id: Optional[str]) -> Optional[Decision]:
    """6-step decision gate. Returns Decision if sampled, None otherwise.

    Steps:
    1. Experiment enabled and key configured → else not_configured
    2. ref_id not None → else no_ref_id
    3. Now ≤ until (UTC) → else skipped_until
    4. Deterministic hash sample → else not_sampled
    5. Daily budget under cap → else skipped_budget
    6. Return Decision (control metadata tagging is the caller's responsibility)
    """
    cfg = _config
    if cfg is None or key not in cfg.operations:
        _counters.inc("not_configured")
        return None

    if ref_id is None:
        _counters.inc("no_ref_id")
        return None

    if datetime.now(timezone.utc) > cfg.until:
        _counters.inc("skipped_until")
        return None

    op_cfg = cfg.operations[key]
    sample_key = f"{cfg.name}:{key}:{ref_id}"
    h = int(hashlib.sha256(sample_key.encode()).hexdigest()[:8], 16)
    if h / (2**32) >= op_cfg.sample_rate:
        _counters.inc("not_sampled")
        return None

    if _budget.get(cfg.name) >= cfg.max_daily_cost_usd:
        _counters.inc("skipped_budget")
        return None

    return Decision(name=cfg.name, key=key, ref_id=ref_id, arms=op_cfg.arms)


def build_arm_kwargs(control_kwargs: dict, decision: Decision, arm: Arm) -> dict:
    """Build kwargs for one arm: replace model, apply params (null removes key), set arm metadata.

    Automatically adds allowed_openai_params=["reasoning_effort"] when reasoning_effort
    is present in the effective kwargs (Phase 0 finding 1).
    """
    kw = dict(control_kwargs)
    kw["model"] = arm.model

    for param_key, param_val in arm.params.items():
        if param_val is None:
            kw.pop(param_key, None)
        else:
            kw[param_key] = param_val

    base_meta = kw.get("metadata") or {}
    arm_meta = dict(base_meta)
    arm_meta["experiment"] = decision.name
    arm_meta["arm"] = arm.name
    kw["metadata"] = arm_meta

    if "reasoning_effort" in kw:
        existing = list(kw.get("allowed_openai_params") or [])
        if "reasoning_effort" not in existing:
            existing.append("reasoning_effort")
        kw["allowed_openai_params"] = existing

    return kw


def prompt_hash(messages: list) -> str:
    """Return sha256 hex of json.dumps(messages, sort_keys=True)."""
    return hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()


def write_item(rows: list, *, truncate_raw: bool = True) -> None:
    """Persist ShadowRows in one transaction with ON CONFLICT DO NOTHING. Never raises."""
    try:
        from aggregator_common.db import SessionFactory
        from aggregator_common.models import LlmShadowResult
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        values = []
        for row in rows:
            raw_out = row.raw_output
            err = row.error
            if truncate_raw:
                if raw_out is not None and len(raw_out) > _RAW_TRUNC:
                    raw_out = raw_out[:_RAW_TRUNC]
                if err is not None and len(err) > _ERR_TRUNC:
                    err = err[:_ERR_TRUNC]
            values.append({
                "experiment": row.experiment,
                "service": row.service,
                "operation": row.operation,
                "ref_id": row.ref_id,
                "arm": row.arm,
                "model": row.model,
                "prompt_hash": row.prompt_hash,
                "status": row.status,
                "attempts": row.attempts,
                "params": row.params,
                "output": row.output,
                "raw_output": raw_out,
                "error": err,
                "request_ids": row.request_ids,
            })

        if not values:
            return

        stmt = pg_insert(LlmShadowResult).values(values).on_conflict_do_nothing(
            constraint="uq_llm_shadow_results_item_arm"
        )

        sess = SessionFactory()
        try:
            sess.execute(stmt)
            sess.commit()
        except Exception:
            sess.rollback()
            raise
        finally:
            sess.close()

        _counters.inc("written")

    except Exception as exc:
        logger.error("experiment: write_item failed: %s", exc)
        _counters.inc("write_failed")


def submit_shadow(
    decision: Optional[Decision],
    control_kwargs: dict,
    control_outcome: CallOutcome,
    attempt: Callable,
) -> None:
    """Enqueue shadow work onto the background executor. No-op if decision is None."""
    if decision is None:
        return
    ex = _executor
    if ex is None:
        return

    phash = prompt_hash(control_kwargs.get("messages") or [])
    item = {
        "decision": decision,
        "control_kwargs": dict(control_kwargs),  # shallow copy so caller mutations don't race
        "control_outcome": control_outcome,
        "attempt_fn": attempt,
        "phash": phash,
    }
    if ex.submit(item):
        _counters.inc("enqueued")
    else:
        _counters.inc("dropped_queue_full")
