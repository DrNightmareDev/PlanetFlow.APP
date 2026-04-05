"""site_settings table with billing_enabled flag

Revision ID: 044
Revises: 043
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "site_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("billing_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Insert the singleton row
    op.execute("INSERT INTO site_settings (id, billing_enabled) VALUES (1, false)")


def downgrade():
    op.drop_table("site_settings")
