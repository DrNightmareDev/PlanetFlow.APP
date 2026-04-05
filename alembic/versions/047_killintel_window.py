"""killintel_pilots: add kills_window_days column

Revision ID: 047
Revises: 046
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa

revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "killintel_pilots",
        sa.Column("kills_window_days", sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_column("killintel_pilots", "kills_window_days")
