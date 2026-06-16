from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(..., description="PostgreSQL connection URL (required)")
    claim_lease_seconds: int = Field(600, description="Work-claim lease duration in seconds")
    log_level: str = Field("INFO", description="Log level for all services")

    # LLM telemetry
    llm_telemetry_enabled: bool = Field(True, description="Enable LLM call telemetry logging")
    llm_telemetry_capture_prompts: bool = Field(False, description="Persist prompt preview/hash (privacy-sensitive; off by default)")
    langfuse_public_key: Optional[str] = Field(None, description="Langfuse public key (enables Langfuse callback when all three keys are set)")
    langfuse_secret_key: Optional[str] = Field(None, description="Langfuse secret key")
    langfuse_host: Optional[str] = Field(None, description="Langfuse host URL (defaults to Langfuse cloud when omitted)")
