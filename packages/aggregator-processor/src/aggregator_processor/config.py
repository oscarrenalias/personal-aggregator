from pydantic import Field

from aggregator_common.config import Settings as BaseSettings


class ProcessorSettings(BaseSettings):
    processor_poll_interval_seconds: int = Field(5, description="Seconds between poll cycles")
    processor_max_workers: int = Field(4, description="Maximum concurrent worker threads")
    processor_batch_size: int = Field(20, description="Articles claimed per poll cycle")
    processor_http_timeout_seconds: int = Field(30, description="HTTP request timeout in seconds")
    processor_max_page_bytes: int = Field(5_242_880, description="Maximum page size to download (5 MiB)")
    processor_user_agent: str = Field(
        "personal-aggregator/0.1 (processor)",
        description="User-Agent header for HTTP requests",
    )
    processor_feed_content_min_chars: int = Field(
        1500,
        description="Minimum chars from feed entry body to skip full-page fetch",
    )
    processor_min_content_chars: int = Field(
        200,
        description="Minimum extracted content length before marking article as skipped",
    )
    processor_max_retries: int = Field(3, description="Maximum retry attempts before failing an article")
    processor_backoff_base_seconds: int = Field(
        30,
        description="Base seconds for exponential retry backoff",
    )
