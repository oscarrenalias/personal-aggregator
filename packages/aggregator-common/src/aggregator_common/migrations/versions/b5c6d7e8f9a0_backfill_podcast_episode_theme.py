"""Backfill episode_theme from script_json for existing podcast episodes

Revision ID: b5c6d7e8f9a0
Revises: a3b4c5d6e7f8
Create Date: 2026-09-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE podcast_episodes
        SET episode_theme = NULLIF(TRIM(script_json->>'episode_theme'), '')
        WHERE episode_theme IS NULL
          AND script_json IS NOT NULL
          AND TRIM(script_json->>'episode_theme') <> ''
        """
    )


def downgrade() -> None:
    # Backfilled data cannot be reversed without regenerating episodes.
    pass
