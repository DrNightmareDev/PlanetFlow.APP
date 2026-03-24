"""add translation entries table

Revision ID: 010_translation_entries
Revises: 009_market_volume_7d
Create Date: 2026-03-24
"""

from alembic import op
import sqlalchemy as sa


revision = "010_translation_entries"
down_revision = "009_market_volume_7d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "translation_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("locale", sa.String(length=20), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("locale", "key", name="uq_translation_entries_locale_key"),
    )
    op.create_index(op.f("ix_translation_entries_id"), "translation_entries", ["id"], unique=False)
    op.create_index(op.f("ix_translation_entries_key"), "translation_entries", ["key"], unique=False)
    op.create_index(op.f("ix_translation_entries_locale"), "translation_entries", ["locale"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_translation_entries_locale"), table_name="translation_entries")
    op.drop_index(op.f("ix_translation_entries_key"), table_name="translation_entries")
    op.drop_index(op.f("ix_translation_entries_id"), table_name="translation_entries")
    op.drop_table("translation_entries")
