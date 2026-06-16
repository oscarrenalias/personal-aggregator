from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

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


def _call_llm(
    messages: list[dict],
    settings: BriefSettings,
    *,
    force_submit: bool = False,
    ref_id: str | None = None,
) -> Any:
    # On the final iteration we force submit_brief so the loop always yields a
    # brief instead of failing when the model keeps exploring with search/get
    # tools. The seed message already carries the top candidate articles, so the
    # model has enough context to submit even if it never called a tool.
    tool_choice: Any = (
        {"type": "function", "function": {"name": "submit_brief"}}
        if force_submit
        else "auto"
    )
    return litellm.completion(
        model=settings.brief_llm_model,
        messages=messages,
        tools=TOOL_SCHEMAS,
        tool_choice=tool_choice,
        max_tokens=settings.brief_llm_max_output_tokens,
        temperature=settings.brief_llm_temperature,
        timeout=settings.brief_llm_timeout_seconds,
        metadata={"service": "brief", "operation": "generate", "ref_id": ref_id},
    )


def _extract_submit(tool_calls: list[Any]) -> dict | None:
    for tc in tool_calls:
        if tc.function.name == "submit_brief":
            try:
                return json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                return None
    return None


def generate_brief(session: Session, brief: Brief, settings: BriefSettings) -> None:
    """Run the agentic tool-use loop and persist the resulting brief.

    Raises GenerationError on unrecoverable failures (tool limit exceeded,
    schema validation failure after corrective turn, etc.).
    Callers (loop.py) are responsible for marking the brief complete or failed.
    """
    profile_row = session.scalar(select(InterestProfile))
    interest_profile_text = (
        profile_row.profile_text.strip() if profile_row and profile_row.profile_text else ""
    )

    system_prompt = build_system_prompt(settings, interest_profile_text)
    seed_messages = build_seed_messages(session, settings, brief.period_start, brief.period_end)

    messages: list[dict] = [{"role": "system", "content": system_prompt}] + seed_messages

    submit_payload: dict | None = None
    last_response: Any = None

    brief_ref_id = str(brief.id)
    for _iteration in range(settings.brief_tool_max_calls):
        force_submit = _iteration == settings.brief_tool_max_calls - 1
        response = _call_llm(messages, settings, force_submit=force_submit, ref_id=brief_ref_id)
        last_response = response
        assistant_message = response.choices[0].message
        messages.append(_to_assistant_dict(assistant_message))

        tool_calls = assistant_message.tool_calls or []
        if not tool_calls:
            # Model stopped calling tools without submit_brief
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
                submit_payload = tool_args
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"status": "received"}),
                })
                found_submit = True
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
        raise GenerationError(
            f"Tool loop exhausted {settings.brief_tool_max_calls} iteration(s) without submit_brief"
        )

    # Validate — one corrective turn on failure
    try:
        validated = BriefSubmitSchema.model_validate(submit_payload)
    except ValidationError as first_exc:
        logger.warning("submit_brief validation failed, sending corrective turn: %s", first_exc)
        messages.append({
            "role": "user",
            "content": (
                "Your submit_brief payload was invalid. Correct the errors below and call "
                "submit_brief again with a valid payload.\n\n"
                f"Validation errors:\n{first_exc}"
            ),
        })
        corrective_response = _call_llm(messages, settings, ref_id=brief_ref_id)
        last_response = corrective_response
        corrective_message = corrective_response.choices[0].message
        corrective_payload = _extract_submit(corrective_message.tool_calls or [])

        if corrective_payload is None:
            raise GenerationError(
                f"Model did not call submit_brief in corrective turn: {first_exc}"
            ) from first_exc

        try:
            validated = BriefSubmitSchema.model_validate(corrective_payload)
        except ValidationError as second_exc:
            raise GenerationError(
                f"submit_brief still invalid after corrective turn: {second_exc}"
            ) from second_exc

    # Reconcile and persist
    model_name: str = getattr(last_response, "model", None) or settings.brief_llm_model

    # Clear any existing topics (idempotent re-generation)
    for existing in list(brief.topics):
        session.delete(existing)
    session.flush()

    brief.headline = validated.headline
    brief.intro = validated.intro
    brief.model = model_name
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
        model_name,
    )
