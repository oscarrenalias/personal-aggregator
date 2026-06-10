from pydantic import Field
from pydantic_settings import SettingsConfigDict

from aggregator_common.config import Settings as CommonSettings


class Settings(CommonSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    retriever_poll_interval_seconds: int = Field(60, description="Seconds between feed poll cycles")
    retriever_max_workers: int = Field(8, description="Maximum concurrent feed-fetch workers")
    retriever_http_timeout_seconds: int = Field(30, description="HTTP request timeout in seconds")
    retriever_max_feed_bytes: int = Field(10_485_760, description="Maximum feed response size in bytes (10 MiB)")
    retriever_user_agent: str = Field(
        "personal-aggregator/0.1 (feed retriever)",
        description="User-Agent header sent with feed requests",
    )
    retriever_max_source_failures: int = Field(20, description="Consecutive failures before a source is disabled")
    retriever_backoff_base_seconds: int = Field(60, description="Base delay for per-source exponential backoff")
    retriever_backoff_cap_seconds: int = Field(21_600, description="Maximum per-source backoff delay in seconds (6 h)")
