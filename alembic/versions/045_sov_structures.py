"""sov_structures table for cached sovereignty IHub data

Revision ID: 045
Revises: 044
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa

revision = "045"
down_revision = "044"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sov_structures",
        sa.Column("system_id", sa.Integer(), primary_key=True),
        sa.Column("alliance_id", sa.Integer(), nullable=True, index=True),
        sa.Column("alliance_name", sa.String(255), nullable=True),
        sa.Column("system_name", sa.String(100), nullable=True),
        sa.Column("region_id", sa.Integer(), nullable=True),
        sa.Column("region_name", sa.String(100), nullable=True),
        sa.Column("adm", sa.Float(), nullable=True),
        sa.Column("vuln_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("vuln_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("sov_structures")
