"""Add visual subscription badge flag to page access settings

Revision ID: 041
Revises: 040
Create Date: 2026-04-03 19:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "page_access_settings",
        sa.Column(
            "show_subscription_badge",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("page_access_settings", "show_subscription_badge")
