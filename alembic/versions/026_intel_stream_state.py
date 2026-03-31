"""add intel stream state

Revision ID: 026_intel_stream_state
Revises: 025_combat_intel_preferences
Create Date: 2026-03-31 20:50:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "026_intel_stream_state"
down_revision = "025_combat_intel_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intel_stream_state",
        sa.Column("stream_key", sa.String(length=50), primary_key=True),
        sa.Column("last_sequence_id", sa.BigInteger(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("intel_stream_state")
