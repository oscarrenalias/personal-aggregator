"""Add thread_merge_verdicts table for memoized merge decisions

Revision ID: b4c5d6e7f8a9
Revises: a8b9c0d1e2f3
Create Date: 2026-06-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP

# revision identifiers, used by Alembic.
revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "thread_merge_verdicts",
        sa.Column(
            "keep_id",
            sa.BigInteger(),
            sa.ForeignKey("threads.id", ondelete="CASCADE"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "absorb_id",
            sa.BigInteger(),
            sa.ForeignKey("threads.id", ondelete="CASCADE"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column("keep_last_updated", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("absorb_last_updated", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("decided_at", TIMESTAMP(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("thread_merge_verdicts")
