"""inventory net rate per hour for burn-down planning

Revision ID: 040
Revises: 039
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa


revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "inventory_item_summaries",
        sa.Column("net_rate_per_hour", sa.String(length=50), nullable=True),
    )


def downgrade():
    op.drop_column("inventory_item_summaries", "net_rate_per_hour")
