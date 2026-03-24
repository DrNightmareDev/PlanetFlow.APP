"""add static planets table

Revision ID: 011_static_planets
Revises: 010_translation_entries
Create Date: 2026-03-24
"""

from alembic import op
import sqlalchemy as sa


revision = "011_static_planets"
down_revision = "010_translation_entries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "static_planets",
        sa.Column("planet_id", sa.BigInteger(), nullable=False),
        sa.Column("system_id", sa.BigInteger(), nullable=False),
        sa.Column("planet_name", sa.String(length=255), nullable=False),
        sa.Column("planet_number", sa.String(length=16), nullable=True),
        sa.Column("radius", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("planet_id"),
    )
    op.create_index(op.f("ix_static_planets_planet_id"), "static_planets", ["planet_id"], unique=False)
    op.create_index(op.f("ix_static_planets_system_id"), "static_planets", ["system_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_static_planets_system_id"), table_name="static_planets")
    op.drop_index(op.f("ix_static_planets_planet_id"), table_name="static_planets")
    op.drop_table("static_planets")
