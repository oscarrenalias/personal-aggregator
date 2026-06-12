"""Tests for aggregator_brief.config — BriefSettings field defaults and env overrides."""

from aggregator_brief.config import BriefSettings


class TestBriefSettingsDefaults:
    def test_default_model(self):
        s = BriefSettings()
        assert s.brief_llm_model == "gpt-4.1"

    def test_default_max_output_tokens(self):
        s = BriefSettings()
        assert s.brief_llm_max_output_tokens == 4096

    def test_default_tool_max_calls(self):
        s = BriefSettings()
        assert s.brief_tool_max_calls == 12

    def test_default_period_hours(self):
        s = BriefSettings()
        assert s.brief_period_hours == 24

    def test_default_generation_hour(self):
        s = BriefSettings()
        assert s.brief_generation_hour == 6

    def test_inherited_log_level_field_present(self):
        s = BriefSettings()
        # Inherited from BaseSettings; default is "INFO"
        assert s.log_level.upper() == "INFO"

    def test_database_url_inherited(self):
        s = BriefSettings()
        # DATABASE_URL is set in conftest via os.environ
        assert s.database_url is not None
        assert len(s.database_url) > 0


class TestBriefSettingsEnvOverride:
    def test_model_override(self, monkeypatch):
        monkeypatch.setenv("BRIEF_LLM_MODEL", "gpt-4o")
        s = BriefSettings()
        assert s.brief_llm_model == "gpt-4o"

    def test_max_calls_override(self, monkeypatch):
        monkeypatch.setenv("BRIEF_TOOL_MAX_CALLS", "7")
        s = BriefSettings()
        assert s.brief_tool_max_calls == 7

    def test_period_hours_override(self, monkeypatch):
        monkeypatch.setenv("BRIEF_PERIOD_HOURS", "48")
        s = BriefSettings()
        assert s.brief_period_hours == 48
