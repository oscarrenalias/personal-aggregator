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
    clusterer_tier_must_know_threshold: float = Field(0.75, description="Minimum importance score for must-know tier")
    clusterer_tier_worth_tracking_threshold: float = Field(0.5, description="Minimum importance score for worth-tracking tier")
    clusterer_tier_deep_read_threshold: float = Field(0.25, description="Minimum importance score for deep-read tier")
    clusterer_batch_size: int = Field(20, description="Maximum articles processed per clustering cycle")
    clusterer_title_jaccard_threshold: float = Field(0.7, description="Minimum token Jaccard similarity to treat two titles as near-duplicates")
    clusterer_weight_relevance: float = Field(0.25, description="Composite score weight for relevance dimension")
    clusterer_weight_novelty: float = Field(0.15, description="Composite score weight for novelty dimension")
    clusterer_weight_importance: float = Field(0.30, description="Composite score weight for importance dimension")
    clusterer_weight_diversity: float = Field(0.05, description="Composite score weight for source diversity dimension")
    clusterer_weight_confidence: float = Field(0.10, description="Composite score weight for clustering confidence dimension")
    clusterer_weight_time_sensitivity: float = Field(0.15, description="Composite score weight for time sensitivity dimension")
