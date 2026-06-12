from aggregator_common.config import Settings


class BriefSettings(Settings):
    brief_schedule_cron: str = "0 7 * * *"
    brief_lookback_hours: int = 24
    brief_min_importance_score: int = 50
