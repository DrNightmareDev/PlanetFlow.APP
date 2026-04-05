"""killintel_pilots, killintel_killmails, killintel_items tables

Revision ID: 046
Revises: 045
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "killintel_pilots",
        sa.Column("character_id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("corporation_id", sa.BigInteger(), nullable=True),
        sa.Column("corporation_name", sa.String(255), nullable=True),
        sa.Column("alliance_id", sa.BigInteger(), nullable=True),
        sa.Column("alliance_name", sa.String(255), nullable=True),
        sa.Column("danger_ratio", sa.Integer(), nullable=True),
        sa.Column("ships_destroyed", sa.Integer(), nullable=True),
        sa.Column("ships_lost", sa.Integer(), nullable=True),
        sa.Column("isk_destroyed", sa.BigInteger(), nullable=True),
        sa.Column("isk_lost", sa.BigInteger(), nullable=True),
        sa.Column("last_activity", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "killintel_killmails",
        sa.Column("killmail_id", sa.BigInteger(), primary_key=True),
        sa.Column("character_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("ship_type_id", sa.Integer(), nullable=True),
        sa.Column("ship_name", sa.String(255), nullable=True),
        sa.Column("is_loss", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("killmail_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_value", sa.BigInteger(), nullable=True),
        sa.Column("hydrated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_killintel_km_char_time", "killintel_killmails", ["character_id", "killmail_time"])

    op.create_table(
        "killintel_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("killmail_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("type_id", sa.Integer(), nullable=False),
        sa.Column("type_name", sa.String(255), nullable=True),
        sa.Column("slot", sa.String(16), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade():
    op.drop_table("killintel_items")
    op.drop_index("ix_killintel_km_char_time", table_name="killintel_killmails")
    op.drop_table("killintel_killmails")
    op.drop_table("killintel_pilots")
