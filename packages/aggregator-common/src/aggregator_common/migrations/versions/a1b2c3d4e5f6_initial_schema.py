"""Initial database schema

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-06-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, TSVECTOR

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- enum type ---
    article_status = sa.Enum(
        "pending_processing",
        "pending_ranking",
        "ready",
        "failed_processing",
        "failed_ranking",
        "skipped",
        name="article_status",
    )
    article_status.create(op.get_bind(), checkfirst=False)

    # --- sources ---
    op.create_table(
        "sources",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("feed_url", sa.Text, nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "refresh_interval_seconds",
            sa.Integer,
            nullable=False,
            server_default=sa.text("3600"),
        ),
        sa.Column("priority", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("last_checked_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("next_check_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("etag", sa.Text, nullable=True),
        sa.Column("last_modified", sa.Text, nullable=True),
        sa.Column(
            "consecutive_failures",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("default_image_url", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # --- articles ---
    op.create_table(
        "articles",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column(
            "source_id",
            sa.BigInteger,
            sa.ForeignKey("sources.id"),
            nullable=False,
        ),
        sa.Column("dedup_key", sa.Text, nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending_processing",
                "pending_ranking",
                "ready",
                "failed_processing",
                "failed_ranking",
                "skipped",
                name="article_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("claimed_by", sa.Text, nullable=True),
        sa.Column("claimed_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("next_retry_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("raw_payload", JSONB, nullable=False),
        sa.Column("feed_title", sa.Text, nullable=True),
        sa.Column("feed_url", sa.Text, nullable=True),
        sa.Column("feed_summary", sa.Text, nullable=True),
        sa.Column("feed_published_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("retrieved_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("clean_title", sa.Text, nullable=True),
        sa.Column("clean_text", sa.Text, nullable=True),
        sa.Column("excerpt", sa.Text, nullable=True),
        sa.Column("author", sa.Text, nullable=True),
        sa.Column("published_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("header_image_url", sa.Text, nullable=True),
        sa.Column("word_count", sa.Integer, nullable=True),
        sa.Column("language", sa.Text, nullable=True),
        sa.Column("processed_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("search_vector", TSVECTOR, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("topics", JSONB, nullable=True),
        sa.Column("entities", JSONB, nullable=True),
        sa.Column("importance_score", sa.Integer, nullable=True),
        sa.Column("importance_reason", sa.Text, nullable=True),
        sa.Column("llm_meta", JSONB, nullable=True),
        sa.Column("summarized_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("is_read", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("read_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("is_saved", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("is_hidden", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("source_id", "dedup_key", name="uq_articles_source_dedup"),
    )

    # --- interest_profile (singleton row enforced by boolean PK + check) ---
    op.create_table(
        "interest_profile",
        sa.Column(
            "id",
            sa.Boolean,
            primary_key=True,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "profile_text",
            sa.Text,
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "updated_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("id", name="ck_interest_profile_singleton"),
    )

    # --- indexes ---

    # 1. Partial index for work-claiming queries (worker picks rows WHERE claimed_at IS NULL)
    op.create_index(
        "ix_articles_status_next_retry",
        "articles",
        ["status", "next_retry_at"],
        postgresql_where=sa.text("claimed_at IS NULL"),
    )

    # 2. Index for reaper queries that scan by claimed_at to find stale claims
    op.create_index("ix_articles_claimed_at", "articles", ["claimed_at"])

    # 3. GIN index for full-text search on search_vector
    op.create_index(
        "ix_articles_search_vector",
        "articles",
        ["search_vector"],
        postgresql_using="gin",
    )

    # 4. Descending index for ranking display (highest importance first)
    op.execute(
        "CREATE INDEX ix_articles_importance_score"
        " ON articles (importance_score DESC NULLS LAST)"
    )

    # 5. Descending index for chronological feed display (newest first)
    op.execute(
        "CREATE INDEX ix_articles_feed_published_at"
        " ON articles (feed_published_at DESC NULLS LAST)"
    )

    # 6. Index on sources.next_check_at so the retriever can efficiently find feeds due for polling
    op.create_index("ix_sources_next_check_at", "sources", ["next_check_at"])

    # --- BEFORE UPDATE trigger that keeps updated_at current on all three tables ---
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_sources_updated_at
        BEFORE UPDATE ON sources
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_articles_updated_at
        BEFORE UPDATE ON articles
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_interest_profile_updated_at
        BEFORE UPDATE ON interest_profile
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )


def downgrade() -> None:
    # Drop triggers before the tables they depend on
    op.execute(
        "DROP TRIGGER IF EXISTS trg_interest_profile_updated_at ON interest_profile;"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_articles_updated_at ON articles;")
    op.execute("DROP TRIGGER IF EXISTS trg_sources_updated_at ON sources;")

    op.execute("DROP FUNCTION IF EXISTS set_updated_at();")

    # Drop named indexes (DESC indexes were created with raw SQL, drop them likewise)
    op.execute("DROP INDEX IF EXISTS ix_articles_feed_published_at;")
    op.execute("DROP INDEX IF EXISTS ix_articles_importance_score;")

    op.drop_index("ix_sources_next_check_at", table_name="sources")
    op.drop_index("ix_articles_search_vector", table_name="articles")
    op.drop_index("ix_articles_claimed_at", table_name="articles")
    op.drop_index("ix_articles_status_next_retry", table_name="articles")

    # Drop tables in reverse FK order
    op.drop_table("interest_profile")
    op.drop_table("articles")
    op.drop_table("sources")

    # Drop the enum type last (no table references it any more)
    sa.Enum(name="article_status").drop(op.get_bind(), checkfirst=False)
