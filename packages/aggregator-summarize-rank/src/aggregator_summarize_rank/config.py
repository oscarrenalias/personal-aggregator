from pydantic import Field, field_validator

from aggregator_common.config import Settings as BaseSettings
from aggregator_common.experiment import PROMPT_VARIANTS


class SummarizeRankSettings(BaseSettings):
    llm_model: str = Field("gpt-4.1-mini", description="litellm model identifier (provider implied)")
    openai_api_key: str = Field("", description="OpenAI API key")
    anthropic_api_key: str = Field("", description="Anthropic API key")
    llm_max_input_chars: int = Field(6_000, description="Maximum characters of article content sent to LLM")
    llm_max_output_tokens: int = Field(400, description="Maximum tokens in LLM response")
    llm_temperature: float = Field(0.2, description="LLM sampling temperature")
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
    summarize_rank_prompt_variant: str = Field(
        "v1",
        description="Prompt variant for summarize_rank.rank; must be one of PROMPT_VARIANTS['summarize_rank.rank']",
    )

    @field_validator("summarize_rank_prompt_variant")
    @classmethod
    def _validate_prompt_variant(cls, v: str) -> str:
        valid = PROMPT_VARIANTS.get("summarize_rank.rank", frozenset())
        if v not in valid:
            raise ValueError(
                f"SUMMARIZE_RANK_PROMPT_VARIANT={v!r} is not valid; "
                f"must be one of {sorted(valid)}"
            )
        return v
