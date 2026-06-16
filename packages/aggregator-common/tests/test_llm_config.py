"""Tests for LLM-telemetry config fields on aggregator_common.config.Settings."""
from __future__ import annotations

import os
from unittest.mock import patch


def test_defaults_without_db_url():
    from aggregator_common.config import Settings

    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://x:x@localhost/test"}, clear=False):
        s = Settings()

    assert s.llm_telemetry_enabled is True
    assert s.llm_telemetry_capture_prompts is False
    assert s.langfuse_public_key is None
    assert s.langfuse_secret_key is None
    assert s.langfuse_host is None
    assert s.janitor_llm_telemetry_retention_days == 30


def test_env_overrides():
    from aggregator_common.config import Settings

    overrides = {
        "DATABASE_URL": "postgresql://x:x@localhost/test",
        "LLM_TELEMETRY_ENABLED": "false",
        "LLM_TELEMETRY_CAPTURE_PROMPTS": "true",
        "LANGFUSE_PUBLIC_KEY": "pub-key",
        "LANGFUSE_SECRET_KEY": "sec-key",
        "LANGFUSE_HOST": "https://langfuse.example.com",
        "JANITOR_LLM_TELEMETRY_RETENTION_DAYS": "60",
    }
    with patch.dict(os.environ, overrides, clear=False):
        s = Settings()

    assert s.llm_telemetry_enabled is False
    assert s.llm_telemetry_capture_prompts is True
    assert s.langfuse_public_key == "pub-key"
    assert s.langfuse_secret_key == "sec-key"
    assert s.langfuse_host == "https://langfuse.example.com"
    assert s.janitor_llm_telemetry_retention_days == 60
