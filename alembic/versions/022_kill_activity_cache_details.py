"""add kill activity cache details

Revision ID: 022_kill_activity_cache_details
Revises: 021_page_access_settings
Create Date: 2026-03-31
"""

from alembic import op
import sqlalchemy as sa


revision = "022_kill_activity_cache_details"
down_revision = "021_page_access_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kill_activity_cache",
        sa.Column("latest_kills_json", sa.Text(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "kill_activity_cache",
        sa.Column("window", sa.String(length=10), nullable=False, server_default="60m"),
    )


def downgrade() -> None:
    op.drop_column("kill_activity_cache", "window")
    op.drop_column("kill_activity_cache", "latest_kills_json")
