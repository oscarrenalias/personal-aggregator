from pydantic import Field

from aggregator_common.config import Settings as BaseSettings


class SummarizeRankSettings(BaseSettings):
    llm_model: str = Field("claude-sonnet-4-6", description="LLM model identifier")
    openai_api_key: str = Field("", description="OpenAI API key")
    anthropic_api_key: str = Field("", description="Anthropic API key")
    llm_max_input_chars: int = Field(32_000, description="Maximum characters of article content sent to LLM")
    llm_max_output_tokens: int = Field(1024, description="Maximum tokens in LLM response")
    llm_temperature: float = Field(0.3, description="LLM sampling temperature")
    llm_timeout_seconds: int = Field(60, description="LLM API call timeout in seconds")
    summarize_rank_poll_interval_seconds: int = Field(5, description="Seconds between poll cycles")
    summarize_rank_max_workers: int = Field(4, description="Maximum concurrent worker threads")
    summarize_rank_batch_size: int = Field(10, description="Articles claimed per poll cycle")
    summarize_rank_max_retries: int = Field(3, description="Maximum retry attempts before failing an article")
    summarize_rank_backoff_base_seconds: int = Field(30, description="Base seconds for exponential retry backoff")
    summarize_rank_min_content_chars: int = Field(
        200,
        description="Minimum content length before skipping LLM summarization",
    )
