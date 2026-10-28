from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import litellm
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from aggregator_common.models import Article, Brief, BriefTopic, InterestProfile

from .config import BriefSettings
from .prompt import build_seed_messages, build_system_prompt
from .schema import BriefSubmitSchema
from .tools import TOOL_SCHEMAS, dispatch_tool

logger = logging.getLogger(__name__)


class GenerationError(Exception):
    pass


@dataclass
class BriefRunResult:
    seed_messages: list[dict]
    payload: BriefSubmitSchema
    turns: int
    forced_submit: bool
    corrective_turn: bool
    request_ids: list[str]
    model: str = ""
    recovered_from_text_stop: bool = False
    interest_profile_text: str = ""


class BriefLoopError(GenerationError):
    def __init__(
        self,
        message: str,
        *,
        status: str,
        turns: int = 0,
        request_ids: list[str] | None = None,
        last_submit_args: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.turns = turns
        self.request_ids = request_ids if request_ids is not None else []
        self.last_submit_args = last_submit_args


def _to_assistant_dict(message: Any) -> dict:
    d: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
    if message.tool_calls:
        d["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in message.tool_calls
        ]
    return d


def _reconcile_references(session: Session, raw_refs: list[dict]) -> list[dict]:
    """Resolve article_id references against the DB.

    - Matching id in DB → internal=True
    - Unknown id + url present → article_id=None, internal=False (external ref)
    - Unknown id + no url → dropped entirely
    - No article_id (already external) → kept if url present, else dropped
    """
    if not raw_refs:
        return []

    candidate_ids = {r["article_id"] for r in raw_refs if r.get("article_id") is not None}
    existing_ids: set[int] = set()
    if candidate_ids:
        existing_ids = set(
            session.execute(select(Article.id).where(Article.id.in_(candidate_ids))).scalars().all()
        )

    out: list[dict] = []
    for ref in raw_refs:
        article_id = ref.get("article_id")
        url = ref.get("url")
        title = ref.get("title", "")

        if article_id is None:
            if url:
                out.append({"article_id": None, "title": title, "url": url, "internal": False})
        elif article_id in existing_ids:
            out.append({"article_id": article_id, "title": title, "url": url, "internal": True})
        else:
            # Unknown id
            if url:
                out.append({"article_id": None, "title": title, "url": url, "internal": False})
            # else: drop — dangling internal link with no fallback url

    return out


def _build_llm_kwargs(
    messages: list[dict],
    settings: BriefSettings,
    *,
    force_submit: bool = False,
    ref_id: str | None = None,
    extra_metadata: dict | None = None,
) -> dict:
    tool_choice: Any = (
        {"type": "function", "function": {"name": "submit_brief"}}
        if force_submit
        else "auto"
    )
    metadata: dict = {"service": "brief", "operation": "generate", "ref_id": ref_id}
    if extra_metadata:
        metadata.update(extra_metadata)
    return {
        "model": settings.brief_llm_model,
        "messages": messages,
        "tools": TOOL_SCHEMAS,
        "tool_choice": tool_choice,
        "max_tokens": settings.brief_llm_max_output_tokens,
        "temperature": settings.brief_llm_temperature,
        "timeout": settings.brief_llm_timeout_seconds,
        "metadata": metadata,
    }


def _call_llm(
    messages: list[dict],
    settings: BriefSettings,
    *,
    force_submit: bool = False,
    ref_id: str | None = None,
    extra_metadata: dict | None = None,
) -> Any:
    # On the final iteration we force submit_brief so the loop always yields a
    # brief instead of failing when the model keeps exploring with search/get
    # tools. The seed message already carries the top candidate articles, so the
    # model has enough context to submit even if it never called a tool.
    return litellm.completion(
        **_build_llm_kwargs(
            messages,
            settings,
            force_submit=force_submit,
            ref_id=ref_id,
            extra_metadata=extra_metadata,
        )
    )


def _extract_submit(tool_calls: list[Any]) -> dict | None:
    for tc in tool_calls:
        if tc.function.name == "submit_brief":
            try:
                return json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                return None
    return None


def run_brief_loop(
    session: Session,
    messages: list[dict],
    call_fn: Callable[[list[dict], bool], Any],
    settings: BriefSettings,
    *,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> BriefRunResult:
    """Run the brief tool-use loop parameterised by call_fn.

    call_fn(messages, force_submit) must return a litellm-compatible response.
    Raises BriefLoopError on all failure paths (same message texts as the
    GenerationError paths in generate_brief so existing match= tests keep passing).
    """
    seed_messages = list(messages)
    messages = list(messages)

    submit_payload: dict | None = None
    turns = 0
    forced_submit_turn = False
    corrective_turn = False
    text_stop_recovery_attempted = False
    request_ids: list[str] = []
    last_submit_args: str | None = None
    last_model: str = settings.brief_llm_model

    for _iteration in range(settings.brief_tool_max_calls):
        if deadline is not None and clock() >= deadline:
            raise BriefLoopError(
                "deadline",
                status="timeout",
                turns=turns,
                request_ids=request_ids,
                last_submit_args=last_submit_args,
            )

        force_submit = _iteration == settings.brief_tool_max_calls - 1
        response = call_fn(messages, force_submit)
        turns += 1
        last_model = getattr(response, "model", None) or last_model

        req_id = getattr(response, "id", None)
        if req_id:
            request_ids.append(req_id)

        assistant_message = response.choices[0].message
        messages.append(_to_assistant_dict(assistant_message))

        tool_calls = assistant_message.tool_calls or []
        if not tool_calls:
            # One-shot text-stop recovery: if the model wrote plain text instead of
            # calling a tool, nudge it with a forced submit_brief call.  Only attempt
            # once, only when submit_brief has not yet been received, and only when
            # we are not already on a forced-submit iteration (which means the model
            # ignored force_submit=True — a different failure mode).
            if submit_payload is None and not force_submit and not text_stop_recovery_attempted:
                text_stop_recovery_attempted = True
                messages.append({
                    "role": "user",
                    "content": (
                        "You stopped without calling any tools. "
                        "Please call submit_brief now with the brief you just wrote."
                    ),
                })
                if deadline is not None and clock() >= deadline:
                    raise BriefLoopError(
                        "deadline",
                        status="timeout",
                        turns=turns,
                        request_ids=request_ids,
                        last_submit_args=last_submit_args,
                    )
                recovery_response = call_fn(messages, True)
                turns += 1
                rec_req_id = getattr(recovery_response, "id", None)
                if rec_req_id:
                    request_ids.append(rec_req_id)
                last_model = getattr(recovery_response, "model", None) or last_model
                recovery_message = recovery_response.choices[0].message
                messages.append(_to_assistant_dict(recovery_message))
                for tc in recovery_message.tool_calls or []:
                    if tc.function.name == "submit_brief":
                        last_submit_args = tc.function.arguments
                        try:
                            submit_payload = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            submit_payload = None
                        else:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": json.dumps({"status": "received"}),
                            })
                            forced_submit_turn = True
                        break
                if submit_payload is not None:
                    break
                raise BriefLoopError(
                    f"model wrote plain text without submit_brief"
                    f" (after {turns} turn(s), recovery attempted)",
                    status="parse_error",
                    turns=turns,
                    request_ids=request_ids,
                    last_submit_args=last_submit_args,
                )
            break

        found_submit = False
        for tc in tool_calls:
            tool_name = tc.function.name
            try:
                tool_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError as exc:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"error": f"Invalid JSON arguments: {exc}"}),
                })
                continue

            if tool_name == "submit_brief":
                last_submit_args = tc.function.arguments
                submit_payload = tool_args
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"status": "received"}),
                })
                found_submit = True
                forced_submit_turn = force_submit
            else:
                try:
                    result = dispatch_tool(session, tool_name, tool_args)
                except Exception as exc:
                    result = {"error": str(exc)}
                    logger.warning("Tool %r raised: %s", tool_name, exc)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })

        if found_submit:
            break

    if submit_payload is None:
        raise BriefLoopError(
            f"Tool loop exhausted {settings.brief_tool_max_calls} iteration(s) without submit_brief",
            status="parse_error",
            turns=turns,
            request_ids=request_ids,
            last_submit_args=last_submit_args,
        )

    try:
        validated = BriefSubmitSchema.model_validate(submit_payload)
    except ValidationError as first_exc:
        logger.warning("submit_brief validation failed, sending corrective turn: %s", first_exc)
        corrective_turn = True
        messages.append({
            "role": "user",
            "content": (
                "Your submit_brief payload was invalid. Correct the errors below and call "
                "submit_brief again with a valid payload.\n\n"
                f"Validation errors:\n{first_exc}"
            ),
        })

        if deadline is not None and clock() >= deadline:
            raise BriefLoopError(
                "deadline",
                status="timeout",
                turns=turns,
                request_ids=request_ids,
                last_submit_args=last_submit_args,
            )

        corrective_response = call_fn(messages, False)
        turns += 1
        last_model = getattr(corrective_response, "model", None) or last_model

        req_id = getattr(corrective_response, "id", None)
        if req_id:
            request_ids.append(req_id)

        corrective_message = corrective_response.choices[0].message
        corrective_tool_calls = corrective_message.tool_calls or []
        for tc in corrective_tool_calls:
            if tc.function.name == "submit_brief":
                last_submit_args = tc.function.arguments
                break

        corrective_payload = _extract_submit(corrective_tool_calls)

        if corrective_payload is None:
            raise BriefLoopError(
                f"Model did not call submit_brief in corrective turn: {first_exc}",
                status="parse_error",
                turns=turns,
                request_ids=request_ids,
                last_submit_args=last_submit_args,
            )

        try:
            validated = BriefSubmitSchema.model_validate(corrective_payload)
        except ValidationError as second_exc:
            raise BriefLoopError(
                f"submit_brief still invalid after corrective turn: {second_exc}",
                status="parse_error",
                turns=turns,
                request_ids=request_ids,
                last_submit_args=last_submit_args,
            ) from second_exc

    return BriefRunResult(
        seed_messages=seed_messages,
        payload=validated,
        turns=turns,
        forced_submit=forced_submit_turn,
        corrective_turn=corrective_turn,
        request_ids=request_ids,
        model=last_model,
        recovered_from_text_stop=text_stop_recovery_attempted,
    )


def generate_brief(
    session: Session,
    brief: Brief,
    settings: BriefSettings,
    *,
    extra_metadata: dict | None = None,
) -> BriefRunResult:
    """Run the agentic tool-use loop and persist the resulting brief.

    Raises GenerationError on unrecoverable failures (tool limit exceeded,
    schema validation failure after corrective turn, etc.).
    Callers (loop.py) are responsible for marking the brief complete or failed.
    """
    profile_row = session.scalar(select(InterestProfile))
    interest_profile_text = (
        profile_row.profile_text.strip() if profile_row and profile_row.profile_text else ""
    )

    system_prompt = build_system_prompt(settings, interest_profile_text, variant=settings.brief_prompt_variant)
    seed_messages = build_seed_messages(session, settings, brief.period_start, brief.period_end)

    messages: list[dict] = [{"role": "system", "content": system_prompt}] + seed_messages

    brief_ref_id = str(brief.id)
    _extra: dict = {"extra_metadata": extra_metadata} if extra_metadata is not None else {}
    call_fn = lambda m, f: _call_llm(  # noqa: E731
        m, settings, force_submit=f, ref_id=brief_ref_id, **_extra
    )

    try:
        result = run_brief_loop(session, messages, call_fn, settings)
    except BriefLoopError as exc:
        raise GenerationError(str(exc)) from exc
    result.interest_profile_text = interest_profile_text

    validated = result.payload

    # Reconcile and persist
    for existing in list(brief.topics):
        session.delete(existing)
    session.flush()

    brief.headline = validated.headline
    brief.intro = validated.intro
    brief.model = result.model
    brief.generated_at = datetime.now(tz=timezone.utc)

    for position, topic_schema in enumerate(validated.topics):
        raw_refs = [ref.model_dump() for ref in topic_schema.references]
        reconciled_refs = _reconcile_references(session, raw_refs)

        topic = BriefTopic(
            brief_id=brief.id,
            position=position,
            headline=topic_schema.headline,
            what_happened=topic_schema.what_happened,
            why_it_matters=topic_schema.why_it_matters,
            historical_context=topic_schema.historical_context,
            topic_refs=reconciled_refs,
        )
        session.add(topic)

    session.flush()
    logger.info(
        "Brief %d generated: %d topic(s) via %s",
        brief.id,
        len(validated.topics),
        result.model,
    )

    return result
