"""add director_corp_id and director_corp_name to accounts

Revision ID: 035_director_corp_id
Revises: 034_add_is_director
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa

revision = "035_director_corp_id"
down_revision = "034_add_is_director"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("accounts", sa.Column("director_corp_id", sa.BigInteger(), nullable=True))
    op.add_column("accounts", sa.Column("director_corp_name", sa.String(255), nullable=True))


def downgrade():
    op.drop_column("accounts", "director_corp_name")
    op.drop_column("accounts", "director_corp_id")
