"""Create threads and thread_memberships tables

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-06-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM, JSONB, TIMESTAMP

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False: the types are created once explicitly in upgrade() via
# .create(checkfirst=True); reusing these same objects in the columns prevents
# create_table from emitting a second CREATE TYPE (generic sa.Enum ignores
# create_type, which caused a DuplicateObject on a fresh DB).
thread_status = ENUM("active", "dormant", "archived", name="thread_status", create_type=False)
thread_tier = ENUM(
    "must_know", "worth_tracking", "deep_read", "low_noise", name="thread_tier", create_type=False
)
classification_label = ENUM(
    "new_thread",
    "same_thread_new_fact",
    "same_thread_new_angle",
    "same_thread_duplicate",
    "same_thread_background_only",
    "correction_or_clarification",
    "related_new_thread",
    "irrelevant_or_low_value",
    name="classification_label",
    create_type=False,
)


def upgrade() -> None:
    thread_status.create(op.get_bind(), checkfirst=True)
    thread_tier.create(op.get_bind(), checkfirst=True)
    classification_label.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "threads",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("representative_title", sa.Text(), nullable=False),
        sa.Column("rolling_summary", sa.Text(), nullable=True),
        sa.Column("known_facts", JSONB(), nullable=True),
        sa.Column("first_seen", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_updated", TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "status",
            thread_status,
            nullable=False,
            server_default="active",
        ),
        sa.Column("source_list", JSONB(), nullable=True),
        sa.Column("source_diversity", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("novelty_label", sa.Text(), nullable=True),
        sa.Column(
            "tier",
            thread_tier,
            nullable=True,
        ),
        sa.Column("tier_reason", sa.Text(), nullable=True),
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column("novelty_score", sa.Float(), nullable=True),
        sa.Column("importance_score", sa.Float(), nullable=True),
        sa.Column("diversity_score", sa.Float(), nullable=True),
        sa.Column("time_sensitivity_score", sa.Float(), nullable=True),
        sa.Column("deltas", JSONB(), nullable=True),
    )

    op.create_index("ix_threads_status", "threads", ["status"])
    op.create_index("ix_threads_tier", "threads", ["tier"])
    op.create_index("ix_threads_last_updated", "threads", [sa.text("last_updated DESC")])

    op.create_table(
        "thread_memberships",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "thread_id",
            sa.BigInteger(),
            sa.ForeignKey("threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "article_id",
            sa.BigInteger(),
            sa.ForeignKey("articles.id"),
            nullable=False,
        ),
        sa.Column(
            "classification_label",
            classification_label,
            nullable=True,
        ),
        sa.Column("new_facts", JSONB(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("suppressed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("assigned_at", TIMESTAMP(timezone=True), nullable=False),
        sa.UniqueConstraint("article_id", name="uq_thread_memberships_article_id"),
    )

    op.create_index("ix_thread_memberships_thread_id", "thread_memberships", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_thread_memberships_thread_id", table_name="thread_memberships")
    op.drop_table("thread_memberships")
    op.drop_index("ix_threads_last_updated", table_name="threads")
    op.drop_index("ix_threads_tier", table_name="threads")
    op.drop_index("ix_threads_status", table_name="threads")
    op.drop_table("threads")
    classification_label.drop(op.get_bind(), checkfirst=True)
    thread_tier.drop(op.get_bind(), checkfirst=True)
    thread_status.drop(op.get_bind(), checkfirst=True)
