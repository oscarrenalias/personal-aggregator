"""Add surfaced and top_grade columns to threads table

Revision ID: a2b3c4d5e6f7
Revises: a8b9c0d1e2f3
Create Date: 2026-06-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "threads",
        sa.Column("surfaced", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "threads",
        sa.Column("top_grade", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("threads", "top_grade")
    op.drop_column("threads", "surfaced")
