from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Enum, Float, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.schema import Identity


class ThreadStatus(PyEnum):
    active = "active"
    dormant = "dormant"
    archived = "archived"


class ThreadTier(PyEnum):
    must_know = "must_know"
    worth_tracking = "worth_tracking"
    deep_read = "deep_read"
    low_noise = "low_noise"


class ClassificationLabel(PyEnum):
    new_thread = "new_thread"
    same_thread_new_fact = "same_thread_new_fact"
    same_thread_new_angle = "same_thread_new_angle"
    same_thread_duplicate = "same_thread_duplicate"
    same_thread_background_only = "same_thread_background_only"
    correction_or_clarification = "correction_or_clarification"
    related_new_thread = "related_new_thread"
    irrelevant_or_low_value = "irrelevant_or_low_value"


# Postgres enum type declared here; Python-level enum and state machine live in state.py
article_status_type = Enum(
    "pending_processing",
    "pending_ranking",
    "ready",
    "failed_processing",
    "failed_ranking",
    "skipped",
    name="article_status",
)

thread_status_type = Enum("active", "dormant", "archived", name="thread_status")

thread_tier_type = Enum("must_know", "worth_tracking", "deep_read", "low_noise", name="thread_tier")

classification_label_type = Enum(
    "new_thread",
    "same_thread_new_fact",
    "same_thread_new_angle",
    "same_thread_duplicate",
    "same_thread_background_only",
    "correction_or_clarification",
    "related_new_thread",
    "irrelevant_or_low_value",
    name="classification_label",
)


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    feed_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    refresh_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3600")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    next_check_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    etag: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_modified: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    default_image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (UniqueConstraint("source_id", "dedup_key", name="uq_articles_source_dedup"),)

    # Identity / dedup
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sources.id"), nullable=False)
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(article_status_type, nullable=False)

    # Claim / failure
    claimed_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Raw (retriever)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    feed_title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    feed_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    feed_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    feed_published_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    comments_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    # Processed (processor)
    clean_title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    clean_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    header_image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    word_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    language: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    search_vector: Mapped[Optional[str]] = mapped_column(TSVECTOR, nullable=True)

    # LLM (summarize-rank)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    topics: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    entities: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    importance_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    importance_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_meta: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    summarized_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # Categorization
    categories: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    # Interaction (web)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    read_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    is_saved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    # Thread clustering
    thread_membership: Mapped[Optional["ThreadMembership"]] = relationship(
        "ThreadMembership", back_populates="article", uselist=False
    )


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("name", name="uq_categories_name"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class InterestProfile(Base):
    __tablename__ = "interest_profile"
    __table_args__ = (CheckConstraint("id", name="ck_interest_profile_singleton"),)

    id: Mapped[bool] = mapped_column(Boolean, primary_key=True, server_default="true")
    profile_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="''")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class Brief(Base):
    __tablename__ = "briefs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    claimed_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    period_start: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    generated_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    headline: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    intro: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column(Text, nullable=False, server_default="auto")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    topics: Mapped[List["BriefTopic"]] = relationship(
        "BriefTopic", back_populates="brief", cascade="all, delete-orphan", order_by="BriefTopic.position"
    )


class BriefTopic(Base):
    __tablename__ = "brief_topics"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    brief_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("briefs.id", ondelete="CASCADE"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    what_happened: Mapped[str] = mapped_column(Text, nullable=False)
    why_it_matters: Mapped[str] = mapped_column(Text, nullable=False)
    historical_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    topic_refs: Mapped[list] = mapped_column("refs", JSONB, nullable=False, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    brief: Mapped["Brief"] = relationship("Brief", back_populates="topics")


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    representative_title: Mapped[str] = mapped_column(Text, nullable=False)
    rolling_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    known_facts: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    last_updated: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(thread_status_type, nullable=False, server_default="active")
    source_list: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    source_diversity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    novelty_label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tier: Mapped[Optional[str]] = mapped_column(thread_tier_type, nullable=True)
    tier_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    relevance_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    novelty_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    importance_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    diversity_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    time_sensitivity_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    deltas: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    members: Mapped[List["ThreadMembership"]] = relationship(
        "ThreadMembership", back_populates="thread", cascade="all, delete-orphan"
    )


class ThreadMembership(Base):
    __tablename__ = "thread_memberships"
    __table_args__ = (UniqueConstraint("article_id", name="uq_thread_memberships_article_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    thread_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False)
    article_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("articles.id"), nullable=False)
    classification_label: Mapped[Optional[str]] = mapped_column(classification_label_type, nullable=True)
    new_facts: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    suppressed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    assigned_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    thread: Mapped["Thread"] = relationship("Thread", back_populates="members")
    article: Mapped["Article"] = relationship("Article", back_populates="thread_membership")


class ClusterState(Base):
    """Singleton control row for the thread clustering worker.

    Mechanism: the worker poll loop checks ``recluster_requested`` every cycle.
    To trigger a full recluster pass, callers upsert this row with
    ``recluster_requested=True``.  The worker atomically reads and clears the
    flag in one statement::

        UPDATE cluster_state
           SET recluster_requested = false
         WHERE recluster_requested = true
         RETURNING *

    Because the UPDATE is atomic, concurrent callers can safely enqueue without
    losing a signal, and the worker never processes the same request twice.
    """

    __tablename__ = "cluster_state"
    __table_args__ = (CheckConstraint("id", name="ck_cluster_state_singleton"),)

    id: Mapped[bool] = mapped_column(Boolean, primary_key=True, server_default="true")
    recluster_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    requested_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
