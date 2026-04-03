"""add is_director to accounts

Revision ID: 034_add_is_director
Revises: 033_billing_system
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa

revision = "034_add_is_director"
down_revision = "033_billing_system"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "accounts",
        sa.Column("is_director", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade():
    op.drop_column("accounts", "is_director")
