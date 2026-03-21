"""Initial schema

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Accounts Tabelle
    op.create_table('accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('is_admin', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('main_character_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_accounts_id'), 'accounts', ['id'], unique=False)

    # Characters Tabelle
    op.create_table('characters',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('eve_character_id', sa.BigInteger(), nullable=False),
        sa.Column('character_name', sa.String(length=255), nullable=False),
        sa.Column('corporation_id', sa.BigInteger(), nullable=True),
        sa.Column('corporation_name', sa.String(length=255), nullable=True),
        sa.Column('alliance_id', sa.BigInteger(), nullable=True),
        sa.Column('alliance_name', sa.String(length=255), nullable=True),
        sa.Column('access_token', sa.Text(), nullable=True),
        sa.Column('refresh_token', sa.Text(), nullable=True),
        sa.Column('token_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('scopes', sa.Text(), nullable=True),
        sa.Column('portrait_64', sa.String(length=512), nullable=True),
        sa.Column('portrait_128', sa.String(length=512), nullable=True),
        sa.Column('portrait_256', sa.String(length=512), nullable=True),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('last_login', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_characters_id'), 'characters', ['id'], unique=False)
    op.create_index(op.f('ix_characters_eve_character_id'), 'characters', ['eve_character_id'], unique=True)

    # FK main_character_id nach Character-Tabelle erstellen
    op.create_foreign_key(
        'fk_account_main_char',
        'accounts', 'characters',
        ['main_character_id'], ['id'],
        use_alter=True,
        deferrable=True,
        initially='DEFERRED'
    )

    # SSO States Tabelle
    op.create_table('sso_states',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(length=255), nullable=False),
        sa.Column('flow', sa.String(length=50), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sso_states_id'), 'sso_states', ['id'], unique=False)
    op.create_index(op.f('ix_sso_states_state'), 'sso_states', ['state'], unique=True)

    # Market Cache Tabelle
    op.create_table('market_cache',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('type_id', sa.Integer(), nullable=False),
        sa.Column('type_name', sa.String(length=255), nullable=True),
        sa.Column('best_buy', sa.String(length=50), nullable=True),
        sa.Column('best_sell', sa.String(length=50), nullable=True),
        sa.Column('avg_volume', sa.String(length=50), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_market_cache_id'), 'market_cache', ['id'], unique=False)
    op.create_index(op.f('ix_market_cache_type_id'), 'market_cache', ['type_id'], unique=True)


def downgrade() -> None:
    op.drop_table('market_cache')
    op.drop_table('sso_states')
    op.drop_constraint('fk_account_main_char', 'accounts', type_='foreignkey')
    op.drop_table('characters')
    op.drop_table('accounts')
