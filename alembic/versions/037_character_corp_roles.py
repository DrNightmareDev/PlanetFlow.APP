"""add corp_roles to characters

Revision ID: 037
Revises: 036
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("characters", sa.Column("corp_roles", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("characters", "corp_roles")
