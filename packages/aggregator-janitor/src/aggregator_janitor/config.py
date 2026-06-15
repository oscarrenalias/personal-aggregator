from pydantic import Field

from aggregator_common.config import Settings as BaseSettings


class JanitorSettings(BaseSettings):
    janitor_article_retention_days: int = Field(14, description="Days to retain articles before purging (not saved, not in a live thread)")
    janitor_thread_retention_days: int = Field(30, description="Days to retain archived threads before permanent deletion")
    janitor_brief_retention_days: int = Field(30, description="Days to retain completed briefs before pruning")
    janitor_run_hour: int = Field(4, description="Hour of day (in janitor_timezone) to run the retention sweep")
    janitor_timezone: str = Field("UTC", description="Timezone for scheduling the retention sweep")
    janitor_poll_interval_seconds: int = Field(3600, description="Seconds between scheduler poll cycles")
    janitor_advisory_lock_key: int = Field(2047839251, description="Postgres advisory lock key (distinct from the clusterer's 1129855059)")
