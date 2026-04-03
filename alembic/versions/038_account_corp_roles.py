"""add is_corp_manager and is_fc to accounts

Revision ID: 038
Revises: 037
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("accounts", sa.Column("is_corp_manager", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("accounts", sa.Column("is_fc", sa.Boolean(), nullable=False, server_default="false"))


def downgrade():
    op.drop_column("accounts", "is_fc")
    op.drop_column("accounts", "is_corp_manager")
