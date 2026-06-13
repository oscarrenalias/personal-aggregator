"""Add consolidation throttle fields to cluster_state and relevance gate cache to threads

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-06-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP

# revision identifiers, used by Alembic.
revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # cluster_state: dirty flag + last_consolidated_at for throttle
    op.add_column(
        "cluster_state",
        sa.Column("dirty", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "cluster_state",
        sa.Column("last_consolidated_at", TIMESTAMP(timezone=True), nullable=True),
    )

    # threads: relevance gate cache (hash + pass result)
    op.add_column(
        "threads",
        sa.Column("relevance_gate_hash", sa.Text(), nullable=True),
    )
    op.add_column(
        "threads",
        sa.Column("relevance_gate_pass", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("threads", "relevance_gate_pass")
    op.drop_column("threads", "relevance_gate_hash")
    op.drop_column("cluster_state", "last_consolidated_at")
    op.drop_column("cluster_state", "dirty")
