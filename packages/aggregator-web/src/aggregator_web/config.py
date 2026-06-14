from pydantic import Field

from aggregator_common.config import Settings as BaseSettings


class WebSettings(BaseSettings):
    # 127.0.0.1: never bind on 0.0.0.0; expose via Tailscale Serve instead
    web_host: str = Field("127.0.0.1", description="Host to bind the web server (WEB_HOST)")
    web_port: int = Field(8000, description="Port to bind the web server (WEB_PORT)")
    web_page_size: int = Field(50, description="Number of articles per page (WEB_PAGE_SIZE)")
    # 70: midpoint calibrated against the LLM's 0-100 importance_score range
    web_important_threshold: int = Field(70, description="Minimum importance score to highlight articles (WEB_IMPORTANT_THRESHOLD)")
    clusterer_thread_view_max_age_days: int = Field(7, description="Maximum age in days for threads shown in the Threads view (CLUSTERER_THREAD_VIEW_MAX_AGE_DAYS)")
    brief_timezone: str = Field("UTC", description="Timezone for brief period calculation — must match BRIEF_TIMEZONE (BRIEF_TIMEZONE)")
    web_show_unread_counts: bool = Field(False, description="Show numeric unread counts instead of qualitative markers (WEB_SHOW_UNREAD_COUNTS)")
