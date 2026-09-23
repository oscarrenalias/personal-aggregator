"""Add podcast_episodes table

Revision ID: a3b4c5d6e7f8
Revises: f8a9b0c1d2e3
Create Date: 2026-09-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

# revision identifiers, used by Alembic.
revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "podcast_episodes",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("origin", sa.Text, nullable=False, server_default=sa.text("'auto'")),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("episode_theme", sa.Text, nullable=True),
        sa.Column("script_json", JSONB, nullable=True),
        sa.Column("audio_path", sa.Text, nullable=True),
        sa.Column("audio_size_bytes", sa.BigInteger, nullable=True),
        sa.Column("duration_seconds", sa.Integer, nullable=True),
        sa.Column("llm_model", sa.Text, nullable=True),
        sa.Column("tts_model", sa.Text, nullable=True),
        sa.Column("tts_voice", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("claimed_by", sa.Text, nullable=True),
        sa.Column("claimed_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("generated_at", TIMESTAMP(timezone=True), nullable=True),
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

    # Partial unique index: only one auto-generated episode per date
    op.execute(
        "CREATE UNIQUE INDEX podcast_episodes_date_auto"
        " ON podcast_episodes (date)"
        " WHERE origin = 'auto';"
    )

    op.execute(
        """
        CREATE TRIGGER trg_podcast_episodes_updated_at
        BEFORE UPDATE ON podcast_episodes
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_podcast_episodes_updated_at ON podcast_episodes;"
    )
    op.execute("DROP INDEX IF EXISTS podcast_episodes_date_auto;")
    op.drop_table("podcast_episodes")
