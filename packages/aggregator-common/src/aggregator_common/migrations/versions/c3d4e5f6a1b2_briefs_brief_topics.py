"""Add briefs and brief_topics tables

Revision ID: c3d4e5f6a1b2
Revises: b1c2d3e4f5a6
Create Date: 2026-06-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a1b2"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "briefs",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("claimed_by", sa.Text, nullable=True),
        sa.Column("claimed_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("period_start", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("period_end", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("generated_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("model", sa.Text, nullable=True),
        sa.Column("headline", sa.Text, nullable=True),
        sa.Column("intro", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("origin", sa.Text, nullable=False, server_default=sa.text("'auto'")),
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

    # Partial unique index: only one auto-generated brief per period_start
    op.create_index(
        "uq_briefs_period_start_auto",
        "briefs",
        ["period_start"],
        unique=True,
        postgresql_where=sa.text("origin = 'auto'"),
    )

    op.create_table(
        "brief_topics",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column(
            "brief_id",
            sa.BigInteger,
            sa.ForeignKey("briefs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("headline", sa.Text, nullable=False),
        sa.Column("what_happened", sa.Text, nullable=False),
        sa.Column("why_it_matters", sa.Text, nullable=False),
        sa.Column("historical_context", sa.Text, nullable=True),
        sa.Column("refs", JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("brief_topics")
    op.drop_index("uq_briefs_period_start_auto", table_name="briefs")
    op.drop_table("briefs")
