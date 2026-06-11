from pydantic import Field

from aggregator_common.config import Settings as BaseSettings


class WebSettings(BaseSettings):
    # 127.0.0.1: never bind on 0.0.0.0; expose via Tailscale Serve instead
    web_host: str = Field("127.0.0.1", description="Host to bind the web server (WEB_HOST)")
    web_port: int = Field(8000, description="Port to bind the web server (WEB_PORT)")
    web_page_size: int = Field(50, description="Number of articles per page (WEB_PAGE_SIZE)")
    # 70: midpoint calibrated against the LLM's 0-100 importance_score range
    web_important_threshold: int = Field(70, description="Minimum importance score to highlight articles (WEB_IMPORTANT_THRESHOLD)")
