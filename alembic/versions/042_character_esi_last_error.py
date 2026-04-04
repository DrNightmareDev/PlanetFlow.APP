"""Add esi_last_error to characters

Revision ID: 042
Revises: 041
Create Date: 2026-04-04
"""
from alembic import op
import sqlalchemy as sa

revision = '042'
down_revision = '041'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('characters', sa.Column('esi_last_error', sa.String(512), nullable=True))


def downgrade():
    op.drop_column('characters', 'esi_last_error')
