"""Add known_facts_condensed_len to threads for facts-consolidation change-guard

Revision ID: f8a9b0c1d2e3
Revises: b4c5d6e7f8a9
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, None] = "b4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("threads", sa.Column("known_facts_condensed_len", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("threads", "known_facts_condensed_len")
