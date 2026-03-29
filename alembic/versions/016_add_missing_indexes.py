"""add missing indexes: character.account_id, character.corporation_id, skyhook composite

Revision ID: 016_add_missing_indexes
Revises: 015_webhook_alerts
Create Date: 2026-03-30
"""
from alembic import op

revision = "016_add_missing_indexes"
down_revision = "015_webhook_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Index for the most frequent query: filter characters by account
    op.create_index("ix_characters_account_id", "characters", ["account_id"], unique=False)
    # Index for corp view query: filter characters by corporation
    op.create_index("ix_characters_corporation_id", "characters", ["corporation_id"], unique=False)
    # Composite index for skyhook dashboard query (account_id + planet_id)
    op.create_index("ix_skyhook_entries_account_planet", "skyhook_entries", ["account_id", "planet_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_skyhook_entries_account_planet", table_name="skyhook_entries")
    op.drop_index("ix_characters_corporation_id", table_name="characters")
    op.drop_index("ix_characters_account_id", table_name="characters")
