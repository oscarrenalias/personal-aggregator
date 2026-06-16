"""Tests for aggregator_common.llm_telemetry (LlmTelemetryLogger and setup helpers)."""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from aggregator_common.llm_telemetry import LlmTelemetryLogger, _classify_error, _extract_first_user_text, setup_llm_telemetry
from aggregator_common.models import LlmCall

_NOW = datetime.now(timezone.utc)
_START = _NOW - timedelta(milliseconds=500)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_usage(prompt=10, completion=5, total=15, cached=0):
    details = SimpleNamespace(cached_tokens=cached) if cached else None
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        prompt_tokens_details=details,
    )


def _make_choice(finish_reason="stop", tool_calls=None):
    return SimpleNamespace(
        finish_reason=finish_reason,
        message=SimpleNamespace(tool_calls=tool_calls),
    )


def _make_response(model="gpt-4.1-mini", choices=None, usage=None, resp_id=None):
    return SimpleNamespace(
        id=resp_id or str(uuid.uuid4()),
        model=model,
        usage=usage or _make_usage(),
        choices=choices or [_make_choice()],
    )


def _make_kwargs(service="test-svc", operation="test-op", ref_id="42", model="gpt-4.1-mini"):
    return {
        "model": model,
        "litellm_params": {
            "metadata": {
                "service": service,
                "operation": operation,
                "ref_id": ref_id,
            }
        },
        "litellm_call_id": "call-123",
    }


@pytest.fixture
def session(db_session_factory) -> Generator[Session, None, None]:
    s = db_session_factory()
    s.execute(text("TRUNCATE TABLE llm_calls"))
    s.commit()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


# ---------------------------------------------------------------------------
# _classify_error
# ---------------------------------------------------------------------------

class TestClassifyError:
    def test_timeout(self):
        assert _classify_error(TimeoutError()) == "timeout"

    def test_rate_limit(self):
        class RateLimitError(Exception):
            pass
        assert _classify_error(RateLimitError()) == "rate_limit"

    def test_auth_error(self):
        class AuthError(Exception):
            pass
        assert _classify_error(AuthError()) == "auth_error"

    def test_api_error_fallback(self):
        assert _classify_error(RuntimeError("boom")) == "api_error"


# ---------------------------------------------------------------------------
# Success event
# ---------------------------------------------------------------------------

class TestSuccessEvent:
    def test_writes_all_columns(self, db_session_factory, session):
        logger = LlmTelemetryLogger(db_session_factory)
        kwargs = _make_kwargs(service="clusterer", operation="classify", ref_id="99")
        response = _make_response(
            model="gpt-4.1-mini",
            usage=_make_usage(prompt=100, completion=50, total=150, cached=10),
            choices=[_make_choice(finish_reason="stop")],
            resp_id="resp-xyz",
        )
        with patch("aggregator_common.llm_telemetry.litellm.completion_cost", return_value=0.001):
            asyncio.run(logger.async_log_success_event(kwargs, response, _START, _NOW))

        rows = session.execute(select(LlmCall)).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.service == "clusterer"
        assert row.operation == "classify"
        assert row.model == "gpt-4.1-mini"
        assert row.prompt_tokens == 100
        assert row.completion_tokens == 50
        assert row.total_tokens == 150
        assert row.cached_tokens == 10
        assert float(row.cost_usd) == pytest.approx(0.001, rel=1e-4)
        assert row.latency_ms >= 0
        assert row.status == "success"
        assert row.finish_reason == "stop"
        assert row.ref_id == "99"
        assert row.num_tool_calls == 0
        assert row.tool_names is None
        assert row.request_id == "resp-xyz"

    def test_tool_calls_captured(self, db_session_factory, session):
        tc1 = SimpleNamespace(function=SimpleNamespace(name="search"))
        tc2 = SimpleNamespace(function=SimpleNamespace(name="list_articles"))
        choice = _make_choice(finish_reason="tool_calls", tool_calls=[tc1, tc2])
        response = _make_response(choices=[choice])
        logger = LlmTelemetryLogger(db_session_factory)
        with patch("aggregator_common.llm_telemetry.litellm.completion_cost", return_value=0.0):
            asyncio.run(logger.async_log_success_event(_make_kwargs(), response, _START, _NOW))

        row = session.execute(select(LlmCall)).scalars().first()
        assert row is not None
        assert row.num_tool_calls == 2
        assert set(row.tool_names) == {"search", "list_articles"}

    def test_cost_fallback_on_pricing_error(self, db_session_factory, session):
        logger = LlmTelemetryLogger(db_session_factory)
        response = _make_response()
        with patch(
            "aggregator_common.llm_telemetry.litellm.completion_cost",
            side_effect=Exception("no pricing data"),
        ):
            asyncio.run(logger.async_log_success_event(_make_kwargs(), response, _START, _NOW))

        row = session.execute(select(LlmCall)).scalars().first()
        assert row is not None
        assert float(row.cost_usd) == 0.0


# ---------------------------------------------------------------------------
# Failure event
# ---------------------------------------------------------------------------

class TestFailureEvent:
    def test_writes_error_row(self, db_session_factory, session):
        logger = LlmTelemetryLogger(db_session_factory)
        exc = TimeoutError("timed out")
        kwargs = {
            **_make_kwargs(service="summarize-rank", operation="rank"),
            "exception": exc,
        }
        asyncio.run(logger.async_log_failure_event(kwargs, None, _START, _NOW))

        rows = session.execute(select(LlmCall)).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.status == "error"
        assert row.error_type == "timeout"
        assert row.service == "summarize-rank"
        assert row.operation == "rank"
        assert row.prompt_tokens == 0
        assert row.completion_tokens == 0
        assert row.total_tokens == 0
        assert row.cost_usd is None
        assert row.latency_ms >= 0

    def test_error_type_fallback_no_exception(self, db_session_factory, session):
        logger = LlmTelemetryLogger(db_session_factory)
        kwargs = _make_kwargs()
        asyncio.run(logger.async_log_failure_event(kwargs, None, _START, _NOW))

        row = session.execute(select(LlmCall)).scalars().first()
        assert row is not None
        assert row.error_type == "api_error"


# ---------------------------------------------------------------------------
# Exception swallowing
# ---------------------------------------------------------------------------

class TestExceptionSwallowing:
    def test_db_add_error_does_not_propagate(self):
        def bad_factory():
            s = MagicMock()
            s.add.side_effect = RuntimeError("DB exploded")
            return s

        logger = LlmTelemetryLogger(bad_factory)
        response = _make_response()
        with patch("aggregator_common.llm_telemetry.litellm.completion_cost", return_value=0.0):
            # must not raise
            asyncio.run(logger.async_log_success_event(_make_kwargs(), response, _START, _NOW))

    def test_failure_db_error_does_not_propagate(self):
        def bad_factory():
            s = MagicMock()
            s.add.side_effect = RuntimeError("DB exploded")
            return s

        logger = LlmTelemetryLogger(bad_factory)
        asyncio.run(logger.async_log_failure_event({}, None, _START, _NOW))

    def test_malformed_kwargs_does_not_propagate(self):
        mock_factory = MagicMock()
        logger = LlmTelemetryLogger(mock_factory)
        # Pass completely empty kwargs — missing litellm_params, model, etc.
        asyncio.run(logger.async_log_success_event({}, SimpleNamespace(), _START, _NOW))


# ---------------------------------------------------------------------------
# setup_llm_telemetry
# ---------------------------------------------------------------------------

class TestSetupLlmTelemetry:
    def test_idempotent_registration(self):
        import litellm
        from aggregator_common.config import Settings

        original = list(litellm.callbacks)
        try:
            with patch.dict(os.environ, {"DATABASE_URL": "postgresql://x:x@localhost/test"}):
                settings = Settings(llm_telemetry_enabled=True)
            # Remove any existing LlmTelemetryLogger first
            litellm.callbacks = [cb for cb in litellm.callbacks if not isinstance(cb, LlmTelemetryLogger)]
            with patch("aggregator_common.db.SessionFactory", MagicMock()):
                setup_llm_telemetry(settings)
                setup_llm_telemetry(settings)
            count = sum(1 for cb in litellm.callbacks if isinstance(cb, LlmTelemetryLogger))
            assert count == 1
        finally:
            litellm.callbacks = original

    def test_disabled_does_not_register(self):
        import litellm
        from aggregator_common.config import Settings

        original = list(litellm.callbacks)
        try:
            with patch.dict(os.environ, {"DATABASE_URL": "postgresql://x:x@localhost/test"}):
                settings = Settings(llm_telemetry_enabled=False)
            before = sum(1 for cb in litellm.callbacks if isinstance(cb, LlmTelemetryLogger))
            setup_llm_telemetry(settings)
            after = sum(1 for cb in litellm.callbacks if isinstance(cb, LlmTelemetryLogger))
            assert after == before
        finally:
            litellm.callbacks = original

    def test_langfuse_not_added_without_keys(self):
        from aggregator_common.config import Settings

        # When Langfuse keys are None, setup_llm_telemetry must not raise.
        # The Langfuse gate is never entered, so litellm.success_callbacks is never touched.
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://x:x@localhost/test"}):
            settings = Settings(
                llm_telemetry_enabled=False,
                langfuse_public_key=None,
                langfuse_secret_key=None,
                langfuse_host=None,
            )
        setup_llm_telemetry(settings)  # must not raise

    def test_capture_prompts_flag_passed_to_logger(self):
        import litellm
        from aggregator_common.config import Settings

        original = list(litellm.callbacks)
        try:
            with patch.dict(os.environ, {
                "DATABASE_URL": "postgresql://x:x@localhost/test",
                "LLM_TELEMETRY_CAPTURE_PROMPTS": "true",
            }):
                settings = Settings(llm_telemetry_enabled=True)
            litellm.callbacks = [cb for cb in litellm.callbacks if not isinstance(cb, LlmTelemetryLogger)]
            with patch("aggregator_common.db.SessionFactory", MagicMock()):
                setup_llm_telemetry(settings)
            registered = [cb for cb in litellm.callbacks if isinstance(cb, LlmTelemetryLogger)]
            assert len(registered) == 1
            assert registered[0]._capture_prompts is True
        finally:
            litellm.callbacks = original


# ---------------------------------------------------------------------------
# _extract_first_user_text
# ---------------------------------------------------------------------------

class TestExtractFirstUserText:
    def test_string_content(self):
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]
        assert _extract_first_user_text(messages) == "hello"

    def test_multipart_content(self):
        messages = [{"role": "user", "content": [{"type": "text", "text": "hi there"}, {"type": "image_url", "image_url": "..."}]}]
        assert _extract_first_user_text(messages) == "hi there"

    def test_no_user_message(self):
        messages = [{"role": "system", "content": "sys"}]
        assert _extract_first_user_text(messages) == ""

    def test_empty_messages(self):
        assert _extract_first_user_text([]) == ""


# ---------------------------------------------------------------------------
# Prompt capture
# ---------------------------------------------------------------------------

class TestPromptCapture:
    def test_capture_disabled_by_default(self, db_session_factory, session):
        logger = LlmTelemetryLogger(db_session_factory)
        kwargs = {
            **_make_kwargs(),
            "messages": [{"role": "user", "content": "sensitive question"}],
        }
        response = _make_response()
        with patch("aggregator_common.llm_telemetry.litellm.completion_cost", return_value=0.0):
            asyncio.run(logger.async_log_success_event(kwargs, response, _START, _NOW))

        row = session.execute(select(LlmCall)).scalars().first()
        assert row is not None
        assert row.prompt_preview is None
        assert row.prompt_hash is None

    def test_capture_enabled_writes_preview_and_hash(self, db_session_factory, session):
        import hashlib
        logger = LlmTelemetryLogger(db_session_factory, capture_prompts=True)
        user_text = "What is the capital of France?"
        kwargs = {
            **_make_kwargs(),
            "messages": [{"role": "user", "content": user_text}],
        }
        response = _make_response()
        with patch("aggregator_common.llm_telemetry.litellm.completion_cost", return_value=0.0):
            asyncio.run(logger.async_log_success_event(kwargs, response, _START, _NOW))

        row = session.execute(select(LlmCall)).scalars().first()
        assert row is not None
        assert row.prompt_preview == user_text
        assert row.prompt_hash == hashlib.sha256(user_text.encode()).hexdigest()

    def test_capture_truncates_long_content(self, db_session_factory, session):
        logger = LlmTelemetryLogger(db_session_factory, capture_prompts=True)
        long_text = "x" * 1000
        kwargs = {
            **_make_kwargs(),
            "messages": [{"role": "user", "content": long_text}],
        }
        response = _make_response()
        with patch("aggregator_common.llm_telemetry.litellm.completion_cost", return_value=0.0):
            asyncio.run(logger.async_log_success_event(kwargs, response, _START, _NOW))

        row = session.execute(select(LlmCall)).scalars().first()
        assert row is not None
        assert row.prompt_preview == "x" * 500
        # hash is of the full text, not truncated
        import hashlib
        assert row.prompt_hash == hashlib.sha256(long_text.encode()).hexdigest()

    def test_capture_no_user_message_leaves_null(self, db_session_factory, session):
        logger = LlmTelemetryLogger(db_session_factory, capture_prompts=True)
        kwargs = {
            **_make_kwargs(),
            "messages": [{"role": "system", "content": "You are a helpful assistant."}],
        }
        response = _make_response()
        with patch("aggregator_common.llm_telemetry.litellm.completion_cost", return_value=0.0):
            asyncio.run(logger.async_log_success_event(kwargs, response, _START, _NOW))

        row = session.execute(select(LlmCall)).scalars().first()
        assert row is not None
        assert row.prompt_preview is None
        assert row.prompt_hash is None


# ---------------------------------------------------------------------------
# End-to-end: telemetry must fire through a real (mocked) sync litellm.completion.
# Regression for the bug where only async handlers existed, so the SYNC handler
# LiteLLM actually calls for litellm.completion() never fired and no row was written.
# ---------------------------------------------------------------------------

class TestFiresThroughLiteLLMCompletion:
    def test_sync_completion_persists_row_with_metadata(self, db_session_factory, session):
        import time
        import litellm

        logger = LlmTelemetryLogger(db_session_factory)
        original = list(litellm.callbacks)
        litellm.callbacks = [logger]
        try:
            litellm.completion(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": "hi"}],
                mock_response="ok",
                metadata={"service": "clusterer", "operation": "classify", "ref_id": "7"},
            )
            # The sync success callback runs as part of litellm.completion(); poll briefly.
            row = None
            for _ in range(20):
                session.rollback()  # fresh READ COMMITTED snapshot each poll
                row = session.execute(
                    select(LlmCall).where(LlmCall.service == "clusterer")
                ).scalars().first()
                if row is not None:
                    break
                time.sleep(0.1)
        finally:
            litellm.callbacks = original

        assert row is not None, "litellm.completion() did not persist a telemetry row (sync handler missing?)"
        assert row.operation == "classify"
        assert row.ref_id == "7"
        assert row.status == "success"
