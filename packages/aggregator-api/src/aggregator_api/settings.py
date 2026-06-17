from pydantic import Field

from aggregator_common.config import Settings


class ApiSettings(Settings):
    api_cors_allow_origins: str = Field("*", description="Comma-separated list of allowed CORS origins")
