"""Add price_mode column to accounts

Revision ID: 006
Revises: 005
Create Date: 2026-03-23 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '006'
down_revision: Union[str, None] = '005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('accounts', sa.Column('price_mode', sa.String(10), nullable=False, server_default='sell'))


def downgrade() -> None:
    op.drop_column('accounts', 'price_mode')
