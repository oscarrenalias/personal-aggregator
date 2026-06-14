from typing import List

from pydantic import Field

from aggregator_common.config import Settings as BaseSettings


class ClustererSettings(BaseSettings):
    clusterer_poll_interval_seconds: int = Field(60, description="Seconds between scheduler poll cycles")
    clusterer_candidate_window_hours_fast: int = Field(48, description="Hours of history for fast-path candidate selection")
    clusterer_candidate_window_days_slow: int = Field(7, description="Days of history for slow-path candidate selection")
    clusterer_max_candidate_threads: int = Field(10, description="Maximum candidate threads per clustering run")
    clusterer_entity_overlap_threshold: float = Field(0.2, description="Minimum entity overlap ratio to consider articles related")
    clusterer_topic_overlap_threshold: float = Field(0.2, description="Minimum topic overlap ratio to consider articles related")
    clusterer_fts_similarity_threshold: float = Field(0.1, description="Minimum FTS similarity score to consider articles related")
    clusterer_llm_model: str = Field("gpt-4.1", description="LLM model for cluster reasoning")
    clusterer_llm_max_output_tokens: int = Field(512, description="Maximum output tokens for LLM calls")
    clusterer_llm_temperature: float = Field(0.0, description="LLM sampling temperature")
    clusterer_llm_timeout_seconds: int = Field(30, description="LLM call timeout in seconds")
    clusterer_claim_lease_seconds: int = Field(600, description="Work-claim lease duration in seconds")
    clusterer_dormant_age_days: int = Field(7, description="Days before an inactive thread is considered dormant")
    clusterer_archive_age_days: int = Field(30, description="Days before a dormant thread is archived")
    clusterer_batch_size: int = Field(20, description="Maximum articles processed per clustering cycle")
    clusterer_title_jaccard_threshold: float = Field(0.7, description="Minimum token Jaccard similarity to treat two titles as near-duplicates")

    # Diversity saturation (used in scoring)
    clusterer_diversity_saturation_n: int = Field(4, description="Source count at which diversity score saturates (diminishing returns beyond this)")

    # Thread merging settings
    clusterer_merge_similarity_floor: float = Field(0.35, description="Minimum similarity score required to consider merging two threads")
    clusterer_max_merge_checks: int = Field(20, description="Maximum candidate thread pairs checked for merging per cycle")

    # Consolidation throttle
    clusterer_consolidation_min_interval_minutes: int = Field(
        10,
        description="Minimum minutes between consolidation passes; explicit recluster bypasses this floor",
    )

    # Thread lifecycle settings
    clusterer_thread_view_max_age_days: int = Field(7, description="Maximum age in days for threads shown in the default thread view")
    clusterer_thread_retention_days: int = Field(30, description="Days to retain archived threads before permanent deletion")

    # Feed section title blocklist — generic RSS section headings that should not be used as thread titles
    clusterer_section_title_blocklist: List[str] = Field(
        default_factory=lambda: ["top stories", "home", "homepage", "latest", "news", "breaking news"],
        description="RSS section/category titles that are too generic to use as thread titles and should be ignored",
    )

    # Surface gate settings
    clusterer_surface_min_grade: int = Field(66, description="Minimum top_grade (0–100) for a thread to be surfaced")
    clusterer_surface_min_sources: int = Field(2, description="Minimum distinct sources required to surface a thread")
    clusterer_surface_min_members: int = Field(3, description="Minimum article members required to surface a thread")
