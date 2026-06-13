"""Add cluster_state singleton table for recluster signalling

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-06-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP

# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cluster_state",
        sa.Column("id", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("recluster_requested", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("requested_at", TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("id", name="ck_cluster_state_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("cluster_state")
