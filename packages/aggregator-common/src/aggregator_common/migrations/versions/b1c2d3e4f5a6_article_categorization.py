"""Article categorization: categories table, articles.categories jsonb, GIN index, seed data

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-06-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- categories table ---
    op.create_table(
        "categories",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default=sa.text("0")),
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
        sa.UniqueConstraint("name", name="uq_categories_name"),
    )

    op.execute(
        """
        CREATE TRIGGER trg_categories_updated_at
        BEFORE UPDATE ON categories
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )

    # --- articles.categories jsonb column ---
    op.add_column("articles", sa.Column("categories", JSONB, nullable=True))

    # --- GIN index on articles.categories ---
    op.create_index(
        "ix_articles_categories",
        "articles",
        ["categories"],
        postgresql_using="gin",
    )

    # --- seed the default categories ---
    op.execute(
        """
        INSERT INTO categories (name, description, sort_order) VALUES
        ('Technology & IT',      'General technology, software, hardware, and IT industry news.',              10),
        ('Cloud & Architecture', 'Cloud platforms, distributed systems, infrastructure, and architecture.',   20),
        ('Software Engineering', 'Software design, development practices, tooling, and programming languages.', 30),
        ('AI',                   'Artificial intelligence, machine learning, LLMs, and data science.',        40),
        ('Gaming',               'Video games, game development, hardware, and gaming culture.',              50);
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_categories_updated_at ON categories;")

    op.drop_index("ix_articles_categories", table_name="articles")
    op.drop_column("articles", "categories")
    op.drop_table("categories")
