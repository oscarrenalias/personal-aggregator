from pydantic import Field, model_validator

from aggregator_common.config import Settings as BaseSettings
from aggregator_common.experiment import PROMPT_VARIANTS


class BriefSettings(BaseSettings):
    brief_llm_model: str = Field("gpt-4.1", description="LLM model for brief generation")
    brief_llm_max_output_tokens: int = Field(4096, description="Maximum output tokens for LLM calls")
    brief_llm_temperature: float = Field(0.3, description="LLM sampling temperature")
    brief_llm_timeout_seconds: int = Field(120, description="LLM call timeout in seconds")
    brief_period_hours: int = Field(24, description="Hours of article history to include in each brief")
    brief_timezone: str = Field("UTC", description="Timezone for scheduling brief generation")
    brief_generation_hour: int = Field(6, description="Hour of day (in brief_timezone) to generate the brief")
    brief_max_candidate_articles: int = Field(80, description="Maximum articles fed to the LLM as candidates")
    brief_max_topics: int = Field(6, description="Maximum topic sections in the generated brief")
    brief_continuity_count: int = Field(3, description="Number of previous briefs to include for continuity context")
    brief_tool_max_calls: int = Field(12, description="Maximum LLM tool calls per brief generation run")
    brief_poll_interval_seconds: int = Field(60, description="Seconds between scheduler poll cycles")
    brief_claim_lease_seconds: int = Field(600, description="Work-claim lease duration for brief jobs in seconds")
    brief_prompt_variant: str = Field("v1", description="Prompt variant for brief generation; must be a valid variant in PROMPT_VARIANTS['brief.generate']")

    @model_validator(mode="after")
    def _validate_prompt_variant(self) -> "BriefSettings":
        valid = PROMPT_VARIANTS["brief.generate"]
        if self.brief_prompt_variant not in valid:
            raise ValueError(
                f"BRIEF_PROMPT_VARIANT={self.brief_prompt_variant!r} is not valid; "
                f"allowed values: {sorted(valid)}"
            )
        return self
