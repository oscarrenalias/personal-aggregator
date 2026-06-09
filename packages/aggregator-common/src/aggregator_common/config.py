from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(..., description="PostgreSQL connection URL (required)")
    claim_lease_seconds: int = Field(600, description="Work-claim lease duration in seconds")
    log_level: str = Field("INFO", description="Log level for all services")
