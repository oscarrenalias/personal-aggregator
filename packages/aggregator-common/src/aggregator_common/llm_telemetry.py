"""LLM telemetry: LiteLLM CustomLogger that persists one LlmCall row per completion."""
from __future__ import annotations

import logging
from typing import Any

import litellm
from litellm.integrations.custom_logger import CustomLogger

from aggregator_common.config import Settings
from aggregator_common.models import LlmCall

logger = logging.getLogger(__name__)


def _classify_error(exc: BaseException) -> str:
    """Map an exception to a terse error_type string based on class name."""
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "timeout"
    if "ratelimit" in name or "rate_limit" in name:
        return "rate_limit"
    if "auth" in name or "authentication" in name or "apikey" in name:
        return "auth_error"
    if "context" in name or "contextlength" in name or "tokenlimit" in name:
        return "context_length"
    return "api_error"


class LlmTelemetryLogger(CustomLogger):
    """LiteLLM custom logger that persists one LlmCall row per completion."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def async_log_success_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        try:
            # Token usage — response.usage may be an object or a dict
            usage = getattr(response_obj, "usage", None) or {}
            if hasattr(usage, "prompt_tokens"):
                prompt_tokens = usage.prompt_tokens or 0
                completion_tokens = usage.completion_tokens or 0
                total_tokens = usage.total_tokens or 0
                details = getattr(usage, "prompt_tokens_details", None)
                cached_tokens = (getattr(details, "cached_tokens", 0) or 0) if details else 0
            else:
                prompt_tokens = usage.get("prompt_tokens", 0) or 0
                completion_tokens = usage.get("completion_tokens", 0) or 0
                total_tokens = usage.get("total_tokens", 0) or 0
                details = usage.get("prompt_tokens_details") or {}
                cached_tokens = (details.get("cached_tokens", 0) if isinstance(details, dict) else 0) or 0

            # Cost — falls back to 0 if litellm can't price the model
            try:
                cost = litellm.completion_cost(response_obj) or 0.0
            except Exception:
                cost = 0.0

            latency_ms = int((end_time - start_time).total_seconds() * 1000)

            # Response choices
            choices = getattr(response_obj, "choices", None) or []
            finish_reason = None
            tool_names: list[str] = []
            if choices:
                ch = choices[0]
                finish_reason = getattr(ch, "finish_reason", None)
                msg = getattr(ch, "message", None)
                if msg:
                    tcs = getattr(msg, "tool_calls", None)
                    if tcs:
                        tool_names = [
                            tc.function.name
                            for tc in tcs
                            if hasattr(tc, "function") and hasattr(tc.function, "name")
                        ]

            model = getattr(response_obj, "model", None) or kwargs.get("model", "unknown")

            # Service/operation/ref_id come from metadata passed at the call site
            litellm_params = kwargs.get("litellm_params") or {}
            metadata = litellm_params.get("metadata") or {}
            service = str(metadata.get("service", "unknown"))
            operation = str(metadata.get("operation", "unknown"))
            ref_id_val = metadata.get("ref_id")
            ref_id = str(ref_id_val) if ref_id_val is not None else None

            request_id_val = getattr(response_obj, "id", None) or kwargs.get("litellm_call_id")
            request_id = str(request_id_val) if request_id_val is not None else None

            row = LlmCall(
                service=service,
                operation=operation,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cached_tokens=cached_tokens,
                cost_usd=cost,
                latency_ms=latency_ms,
                status="success",
                finish_reason=finish_reason,
                num_tool_calls=len(tool_names),
                tool_names=tool_names or None,
                ref_id=ref_id,
                request_id=request_id,
            )

            session = self._session_factory()
            try:
                session.add(row)
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        except Exception as exc:
            logger.debug("LlmTelemetryLogger: failed to write success row: %s", exc)

    async def async_log_failure_event(
        self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        try:
            exception = kwargs.get("exception")
            error_type = _classify_error(exception) if exception is not None else "api_error"

            latency_ms = int((end_time - start_time).total_seconds() * 1000)

            model = kwargs.get("model") or "unknown"

            litellm_params = kwargs.get("litellm_params") or {}
            metadata = litellm_params.get("metadata") or {}
            service = str(metadata.get("service", "unknown"))
            operation = str(metadata.get("operation", "unknown"))
            ref_id_val = metadata.get("ref_id")
            ref_id = str(ref_id_val) if ref_id_val is not None else None

            request_id_val = kwargs.get("litellm_call_id")
            request_id = str(request_id_val) if request_id_val is not None else None

            row = LlmCall(
                service=service,
                operation=operation,
                model=model,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                cached_tokens=0,
                cost_usd=None,
                latency_ms=latency_ms,
                status="error",
                error_type=error_type,
                num_tool_calls=0,
                tool_names=None,
                ref_id=ref_id,
                request_id=request_id,
            )

            session = self._session_factory()
            try:
                session.add(row)
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        except Exception as exc:
            logger.debug("LlmTelemetryLogger: failed to write failure row: %s", exc)


def setup_llm_telemetry(settings: Settings) -> None:
    """Idempotently register LlmTelemetryLogger and optional Langfuse callback.

    Call once at each service entrypoint after load_env() and Settings().
    """
    if settings.llm_telemetry_enabled:
        already = any(isinstance(cb, LlmTelemetryLogger) for cb in litellm.callbacks)
        if not already:
            # Lazy import to defer DB engine creation until after load_env()
            from aggregator_common.db import SessionFactory
            litellm.callbacks.append(LlmTelemetryLogger(SessionFactory))

    if (
        settings.langfuse_public_key
        and settings.langfuse_secret_key
        and settings.langfuse_host
    ):
        if "langfuse" not in litellm.success_callbacks:
            litellm.success_callbacks.append("langfuse")
