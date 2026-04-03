"""Remove is_owner column from accounts (now derived from EVE_OWNER_CHARACTER_ID env var)

Revision ID: 036
Revises: 035
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa

revision = "036"
down_revision = "035_director_corp_id"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("accounts", "is_owner")


def downgrade():
    op.add_column(
        "accounts",
        sa.Column("is_owner", sa.Boolean(), nullable=False, server_default="false"),
    )
