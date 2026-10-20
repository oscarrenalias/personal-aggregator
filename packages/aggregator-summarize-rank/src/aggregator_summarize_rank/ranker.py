from __future__ import annotations

import json
import logging
from typing import Any, Sequence

import litellm
from pydantic import ValidationError

from aggregator_common import experiment
from aggregator_common.experiment import CallOutcome

from .config import SummarizeRankSettings
from .prompt import _CategoryEntry, build_messages, get_prompt_version
from .schema import RankResult

logger = logging.getLogger(__name__)


class RankError(Exception):
    pass


def _parse_rank_result(response: Any) -> RankResult:
    message = response.choices[0].message
    # litellm may expose a parsed attribute for structured outputs
    if hasattr(message, "parsed") and message.parsed is not None:
        parsed = message.parsed
        if isinstance(parsed, RankResult):
            return parsed
        return RankResult.model_validate(parsed)
    content = message.content
    if not content:
        raise ValueError("Empty response content from LLM")
    return RankResult.model_validate_json(content)


def _filter_categories(raw: list[str], enabled: Sequence[_CategoryEntry]) -> list[str]:
    """Canonicalize LLM-returned category names against the enabled set
    (case-insensitive), drop unknowns, dedupe preserving order."""
    canonical = {c.name.lower(): c.name for c in enabled}
    seen: set[str] = set()
    out: list[str] = []
    for name in raw or []:
        canon = canonical.get(name.strip().lower())
        if canon and canon not in seen:
            seen.add(canon)
            out.append(canon)
    return out


def attempt(call_kwargs: dict) -> CallOutcome:
    """Run litellm.completion with 1 retry on parse error.

    Returns CallOutcome(status='ok') on success.
    Re-raises the original exception unchanged for api_error / timeout.
    Raises RankError for parse_error after retry.
    """
    last_parse_error: Exception | None = None
    request_ids: list = []

    for i in range(2):
        try:
            response = litellm.completion(**call_kwargs)
        except Exception:
            raise  # api_error or timeout — re-raise unchanged

        rid = getattr(response, "id", None)
        if rid:
            request_ids = [rid]

        raw: str | None = None
        if response.choices:
            raw = response.choices[0].message.content

        try:
            result = _parse_rank_result(response)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_parse_error = exc
            if i == 0:
                logger.warning("Malformed LLM response (attempt 1), retrying: %s", exc)
                continue
            raise RankError(f"Invalid LLM response after retry: {exc}") from exc

        return CallOutcome(
            status="ok",
            value=result,
            output=result.model_dump(),
            raw_output=raw,
            attempts=i + 1,
            request_ids=request_ids,
            error=None,
            response=response,
        )

    raise RankError(f"Invalid LLM response after retry: {last_parse_error}") from last_parse_error


def rank(
    article_input: dict[str, Any],
    interest_profile_text: str,
    settings: SummarizeRankSettings,
    enabled_categories: Sequence[_CategoryEntry] | None = None,
    article_id: int | None = None,
) -> tuple[RankResult, dict]:
    messages = build_messages(article_input, interest_profile_text, settings, enabled_categories)
    prompt_version = get_prompt_version()

    ref_id = str(article_id) if article_id is not None else None
    decision = experiment.decide("summarize_rank.rank", ref_id)

    base_metadata: dict[str, Any] = {
        "service": "summarize_rank",
        "operation": "rank",
        "ref_id": ref_id,
    }

    control_kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": messages,
        "response_format": RankResult,
        "max_tokens": settings.llm_max_output_tokens,
        "temperature": settings.llm_temperature,
        "timeout": settings.llm_timeout_seconds,
        "metadata": decision.control_metadata(base_metadata) if decision is not None else base_metadata,
    }

    control_outcome = attempt(control_kwargs)

    result: RankResult = control_outcome.value
    result.categories = _filter_categories(result.categories, enabled_categories or [])

    response = control_outcome.response
    usage = response.usage if response.usage else None
    prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage, "completion_tokens", 0) or 0

    cost: float | None = None
    try:
        cost = litellm.completion_cost(completion_response=response)
    except Exception:
        pass

    usage_dict: dict[str, Any] = {
        "model": response.model or settings.llm_model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "prompt_version": prompt_version,
    }
    if cost is not None:
        usage_dict["cost"] = cost

    experiment.submit_shadow(decision, control_kwargs, control_outcome, attempt)

    return result, usage_dict
