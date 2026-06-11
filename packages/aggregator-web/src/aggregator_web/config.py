from pydantic import Field

from aggregator_common.config import Settings as BaseSettings


class WebSettings(BaseSettings):
    web_host: str = Field("0.0.0.0", description="Host to bind the web server")
    web_port: int = Field(8000, description="Port to bind the web server")
