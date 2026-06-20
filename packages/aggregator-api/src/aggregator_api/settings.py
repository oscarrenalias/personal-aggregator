from pydantic import Field

from aggregator_common.config import Settings


class ApiSettings(Settings):
    api_cors_allow_origins: str = Field("*", description="Comma-separated list of allowed CORS origins")
    web_important_threshold: int = Field(70, description="Minimum importance_score for priority activity signals (mirrors WEB_IMPORTANT_THRESHOLD)")
