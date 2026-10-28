from __future__ import annotations

import copy
import json
import logging
import threading
import time
from typing import Callable

from sqlalchemy import text

from aggregator_common.experiment import (
    Decision,
    ShadowRow,
    build_arm_kwargs,
    prompt_hash,
    write_item,
)

from .config import BriefSettings
from .generate import BriefLoopError, BriefRunResult, _build_llm_kwargs, run_brief_loop
from .prompt import build_system_prompt

logger = logging.getLogger(__name__)

_SHADOW_DEADLINE_SECONDS = 600.0


class _ArmTimeoutError(Exception):
    pass


class _ArmApiError(Exception):
    pass


class _ArmRefusalError(Exception):
    pass


def run_shadow(
    result: BriefRunResult,
    decision: Decision,
    settings: BriefSettings,
    session_factory,
    *,
    stop_event: threading.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
    deadline_seconds: float = _SHADOW_DEADLINE_SECONDS,
) -> None:
    """Run shadow arms after a successful brief generation. Never raises."""
    try:
        _run_shadow(
            result,
            decision,
            settings,
            session_factory,
            stop_event=stop_event,
            clock=clock,
            deadline_seconds=deadline_seconds,
        )
    except Exception:
        logger.exception("Shadow runner raised unexpectedly; no rows written")


def _run_shadow(
    result: BriefRunResult,
    decision: Decision,
    settings: BriefSettings,
    session_factory,
    *,
    stop_event: threading.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
    deadline_seconds: float = _SHADOW_DEADLINE_SECONDS,
) -> None:
    phash = prompt_hash(result.seed_messages)
    deadline = clock() + deadline_seconds

    parts = decision.key.split(".", 1)
    service = parts[0] if len(parts) == 2 else decision.key
    operation = parts[1] if len(parts) == 2 else ""

    control_output = {
        "payload": result.payload.model_dump(mode="json"),
        "turns": result.turns,
        "forced_submit": result.forced_submit,
        "corrective_turn": result.corrective_turn,
    }
    control_row = ShadowRow(
        experiment=decision.name,
        service=service,
        operation=operation,
        ref_id=decision.ref_id,
        arm="control",
        model=result.model or settings.brief_llm_model,
        prompt_hash=phash,
        status="ok",
        attempts=result.turns,
        params={"prompt": settings.brief_prompt_variant},
        output=control_output,
        raw_output=json.dumps(control_output["payload"]),
        error=None,
        request_ids=result.request_ids or None,
    )

    rows: list[ShadowRow] = [control_row]
    skipped_deadline = 0

    for i, arm in enumerate(decision.arms):
        if stop_event is not None and stop_event.is_set():
            logger.info("Shadow runner: stop_event set, stopping after %d arm(s) run", i)
            break

        if clock() >= deadline:
            remaining = len(decision.arms) - i
            skipped_deadline += remaining
            logger.info(
                "Shadow runner: deadline reached before arm %r; skipping %d arm(s)",
                arm.name,
                remaining,
            )
            break

        arm_row = _run_arm(
            arm=arm,
            result=result,
            decision=decision,
            settings=settings,
            session_factory=session_factory,
            phash=phash,
            service=service,
            operation=operation,
            clock=clock,
            deadline=deadline,
        )
        rows.append(arm_row)

        if clock() >= deadline:
            remaining = len(decision.arms) - i - 1
            if remaining > 0:
                skipped_deadline += remaining
                logger.info(
                    "Shadow runner: arm %r consumed deadline; skipping %d remaining arm(s)",
                    arm.name,
                    remaining,
                )
            break

    if skipped_deadline:
        logger.info("Shadow runner: %d arm(s) skipped due to deadline", skipped_deadline)

    write_item(rows, truncate_raw=False)


def _run_arm(
    *,
    arm,
    result: BriefRunResult,
    decision: Decision,
    settings: BriefSettings,
    session_factory,
    phash: str,
    service: str,
    operation: str,
    clock: Callable[[], float],
    deadline: float,
) -> ShadowRow:
    import litellm

    def _arm_call_fn(messages: list[dict], force_submit: bool):
        kwargs = _build_llm_kwargs(
            messages,
            settings,
            force_submit=force_submit,
            ref_id=decision.ref_id,
        )
        arm_kwargs = build_arm_kwargs(kwargs, decision, arm)
        try:
            resp = litellm.completion(**arm_kwargs)
        except litellm.exceptions.Timeout as exc:
            raise _ArmTimeoutError(str(exc)) from exc
        except Exception as exc:
            if "timeout" in str(exc).lower():
                raise _ArmTimeoutError(str(exc)) from exc
            raise _ArmApiError(str(exc)) from exc

        msg = resp.choices[0].message
        if getattr(msg, "refusal", None) or resp.choices[0].finish_reason == "content_filter":
            raise _ArmRefusalError(
                f"refusal={getattr(msg, 'refusal', None)!r} "
                f"finish_reason={resp.choices[0].finish_reason!r}"
            )
        return resp

    arm_session = None
    status = "ok"
    output = None
    raw_output = None
    error = None
    attempts = 0
    request_ids_out: list[str] = []
    arm_phash = phash

    try:
        arm_session = session_factory()
        arm_session.execute(text("SET TRANSACTION READ ONLY"))

        arm_messages = copy.deepcopy(result.seed_messages)

        if arm.prompt != settings.brief_prompt_variant:
            new_system_prompt = build_system_prompt(
                settings, result.interest_profile_text, variant=arm.prompt
            )
            arm_messages[0] = {"role": "system", "content": new_system_prompt}
            arm_phash = prompt_hash(arm_messages)

        arm_result = run_brief_loop(
            arm_session,
            arm_messages,
            _arm_call_fn,
            settings,
            deadline=deadline,
            clock=clock,
        )
        status = "ok"
        output = {
            "payload": arm_result.payload.model_dump(mode="json"),
            "turns": arm_result.turns,
            "forced_submit": arm_result.forced_submit,
            "corrective_turn": arm_result.corrective_turn,
        }
        raw_output = json.dumps(output["payload"])
        attempts = arm_result.turns
        request_ids_out = arm_result.request_ids

    except BriefLoopError as exc:
        status = exc.status
        attempts = exc.turns
        request_ids_out = exc.request_ids
        raw_output = exc.last_submit_args
        error = str(exc)

    except _ArmTimeoutError as exc:
        status = "timeout"
        error = str(exc)
        attempts = 1

    except _ArmRefusalError as exc:
        status = "refusal"
        error = str(exc)
        attempts = 1

    except _ArmApiError as exc:
        status = "api_error"
        error = str(exc)
        attempts = 1

    except Exception as exc:
        status = "api_error"
        error = str(exc)
        attempts = 1
        logger.warning("Shadow arm %r raised unexpected exception: %s", arm.name, exc)

    finally:
        if arm_session is not None:
            try:
                arm_session.rollback()
            except Exception:
                pass
            try:
                arm_session.close()
            except Exception:
                pass

    return ShadowRow(
        experiment=decision.name,
        service=service,
        operation=operation,
        ref_id=decision.ref_id,
        arm=arm.name,
        model=arm.model,
        prompt_hash=arm_phash,
        status=status,
        attempts=attempts,
        params={**arm.params, "prompt": arm.prompt},
        output=output,
        raw_output=raw_output,
        error=error,
        request_ids=request_ids_out or None,
    )
