class TestSettingsDefaults:
    def test_all_fields_present_with_correct_defaults(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
        from aggregator_retriever.config import Settings

        s = Settings()
        assert s.retriever_poll_interval_seconds == 60
        assert s.retriever_max_workers == 8
        assert s.retriever_http_timeout_seconds == 30
        assert s.retriever_max_feed_bytes == 10_485_760
        assert s.retriever_user_agent == "personal-aggregator/0.1 (feed retriever)"
        assert s.retriever_max_source_failures == 20
        assert s.retriever_backoff_base_seconds == 60
        assert s.retriever_backoff_cap_seconds == 21_600

    def test_settings_with_only_database_url_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
        from aggregator_retriever.config import Settings

        Settings()  # must not raise

    def test_env_override_max_workers(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
        monkeypatch.setenv("RETRIEVER_MAX_WORKERS", "4")
        from aggregator_retriever.config import Settings

        s = Settings()
        assert s.retriever_max_workers == 4

    def test_env_override_poll_interval(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
        monkeypatch.setenv("RETRIEVER_POLL_INTERVAL_SECONDS", "120")
        from aggregator_retriever.config import Settings

        s = Settings()
        assert s.retriever_poll_interval_seconds == 120

    def test_env_override_backoff_base(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
        monkeypatch.setenv("RETRIEVER_BACKOFF_BASE_SECONDS", "30")
        from aggregator_retriever.config import Settings

        s = Settings()
        assert s.retriever_backoff_base_seconds == 30
