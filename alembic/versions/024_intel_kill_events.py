"""add intel kill events

Revision ID: 024_intel_kill_events
Revises: 023_region_kill_cache
Create Date: 2026-03-31 17:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "024_intel_kill_events"
down_revision = "023_region_kill_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intel_kill_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("killmail_id", sa.BigInteger(), nullable=False),
        sa.Column("region_id", sa.BigInteger(), nullable=False),
        sa.Column("solar_system_id", sa.BigInteger(), nullable=False),
        sa.Column("killmail_time", sa.String(length=32), nullable=False),
        sa.Column("kill_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("ix_intel_kill_events_killmail_id", "intel_kill_events", ["killmail_id"], unique=True)
    op.create_index("ix_intel_kill_events_region_id", "intel_kill_events", ["region_id"], unique=False)
    op.create_index("ix_intel_kill_events_solar_system_id", "intel_kill_events", ["solar_system_id"], unique=False)
    op.create_index("ix_intel_kill_events_created_at", "intel_kill_events", ["created_at"], unique=False)
    op.create_index("ix_intel_kill_events_region_created", "intel_kill_events", ["region_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_intel_kill_events_region_created", table_name="intel_kill_events")
    op.drop_index("ix_intel_kill_events_created_at", table_name="intel_kill_events")
    op.drop_index("ix_intel_kill_events_solar_system_id", table_name="intel_kill_events")
    op.drop_index("ix_intel_kill_events_region_id", table_name="intel_kill_events")
    op.drop_index("ix_intel_kill_events_killmail_id", table_name="intel_kill_events")
    op.drop_table("intel_kill_events")
