"""Webhook alert settings per account

Revision ID: 015_webhook_alerts
Revises: 014_esi_refresh_tracking
Create Date: 2026-03-29
"""

from alembic import op
import sqlalchemy as sa

revision = "015_webhook_alerts"
down_revision = "014_esi_refresh_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_alerts",
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("webhook_url", sa.String(1024), nullable=True),
        sa.Column("alert_hours", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_alert_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("webhook_alerts")
