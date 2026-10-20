"""LLM shadow evaluation storage: llm_calls columns and llm_shadow_results table

Revision ID: d6e7f8a9b0c1
Revises: c6d7e8f9a0b1
Create Date: 2026-10-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d6e7f8a9b0c1"
down_revision: Union[str, None] = "c6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new nullable columns to llm_calls
    op.add_column("llm_calls", sa.Column("experiment", sa.Text(), nullable=True))
    op.add_column("llm_calls", sa.Column("arm", sa.Text(), nullable=True))
    op.add_column("llm_calls", sa.Column("reasoning_effort", sa.Text(), nullable=True))
    op.add_column("llm_calls", sa.Column("reasoning_tokens", sa.Integer(), nullable=True))

    # Index on llm_calls for spec C joins
    op.create_index(
        "ix_llm_calls_experiment_arm",
        "llm_calls",
        ["experiment", "arm", "service", "operation", "ref_id"],
    )

    # New llm_shadow_results table
    op.create_table(
        "llm_shadow_results",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("experiment", sa.Text(), nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.Text(), nullable=False),
        sa.Column("arm", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("output", postgresql.JSONB(), nullable=True),
        sa.Column("raw_output", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("request_ids", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "experiment", "service", "operation", "ref_id", "arm",
            name="uq_llm_shadow_results_item_arm",
        ),
    )
    op.create_index(
        "ix_llm_shadow_results_experiment_service_operation",
        "llm_shadow_results",
        ["experiment", "service", "operation"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_shadow_results_experiment_service_operation", table_name="llm_shadow_results")
    op.drop_table("llm_shadow_results")

    op.drop_index("ix_llm_calls_experiment_arm", table_name="llm_calls")

    op.drop_column("llm_calls", "reasoning_tokens")
    op.drop_column("llm_calls", "reasoning_effort")
    op.drop_column("llm_calls", "arm")
    op.drop_column("llm_calls", "experiment")
