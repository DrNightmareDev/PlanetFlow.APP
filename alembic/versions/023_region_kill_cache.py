"""add region kill cache

Revision ID: 023_region_kill_cache
Revises: 022_kill_activity_cache_details
Create Date: 2026-03-31 17:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "023_region_kill_cache"
down_revision = "022_kill_activity_cache_details"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "region_kill_cache",
        sa.Column("region_id", sa.BigInteger(), nullable=False),
        sa.Column("window", sa.String(length=10), nullable=False, server_default="60m"),
        sa.Column("kill_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("kills_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("newest_kill_time", sa.String(length=32), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("region_id", "window"),
    )


def downgrade() -> None:
    op.drop_table("region_kill_cache")
