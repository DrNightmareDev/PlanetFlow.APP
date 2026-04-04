"""Seed default billing subscription plans (100 ISK/day each)

Revision ID: 043
Revises: 042
Create Date: 2026-04-04
"""
from alembic import op
from sqlalchemy.sql import table, column
import sqlalchemy as sa

revision = '043'
down_revision = '042'
branch_labels = None
depends_on = None

_plans = table(
    "billing_subscription_plans",
    column("key", sa.String),
    column("scope", sa.String),
    column("display_name", sa.String),
    column("daily_price_isk", sa.Numeric),
    column("is_active", sa.Boolean),
)

DEFAULT_PLANS = [
    {"key": "individual", "scope": "individual", "display_name": "Individual",   "daily_price_isk": 100, "is_active": True},
    {"key": "corporation", "scope": "corporation", "display_name": "Corporation", "daily_price_isk": 100, "is_active": True},
    {"key": "alliance",   "scope": "alliance",   "display_name": "Alliance",     "daily_price_isk": 100, "is_active": True},
]


def upgrade():
    conn = op.get_bind()
    for plan in DEFAULT_PLANS:
        exists = conn.execute(
            sa.text("SELECT 1 FROM billing_subscription_plans WHERE key = :key"),
            {"key": plan["key"]},
        ).fetchone()
        if not exists:
            op.bulk_insert(_plans, [plan])


def downgrade():
    op.execute(
        "DELETE FROM billing_subscription_plans WHERE key IN ('individual','corporation','alliance')"
    )
