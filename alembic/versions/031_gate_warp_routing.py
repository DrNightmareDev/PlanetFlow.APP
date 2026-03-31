"""add gate warp routing tables

Revision ID: 031_gate_warp_routing
Revises: 030_hauling_preferences
Create Date: 2026-03-31
"""

from alembic import op
import sqlalchemy as sa


revision = "031_gate_warp_routing"
down_revision = "030_hauling_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hauling_preferences",
        sa.Column("route_mode", sa.String(length=20), nullable=False, server_default="jumps"),
    )

    op.create_table(
        "static_stargates",
        sa.Column("gate_id", sa.BigInteger(), nullable=False),
        sa.Column("system_id", sa.BigInteger(), nullable=False),
        sa.Column("system_name", sa.String(length=255), nullable=False),
        sa.Column("gate_name", sa.String(length=255), nullable=False),
        sa.Column("destination_system_id", sa.BigInteger(), nullable=True),
        sa.Column("destination_system_name", sa.String(length=255), nullable=True),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("z", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("gate_id"),
    )
    op.create_index("ix_static_stargates_gate_id", "static_stargates", ["gate_id"], unique=False)
    op.create_index("ix_static_stargates_destination_system_id", "static_stargates", ["destination_system_id"], unique=False)
    op.create_index("ix_static_stargates_system_dest", "static_stargates", ["system_id", "destination_system_id"], unique=False)

    op.create_table(
        "system_gate_distances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("system_id", sa.BigInteger(), nullable=False),
        sa.Column("system_name", sa.String(length=255), nullable=False),
        sa.Column("entry_gate_id", sa.BigInteger(), nullable=False),
        sa.Column("exit_gate_id", sa.BigInteger(), nullable=False),
        sa.Column("from_system_id", sa.BigInteger(), nullable=False),
        sa.Column("to_system_id", sa.BigInteger(), nullable=False),
        sa.Column("from_system_name", sa.String(length=255), nullable=False),
        sa.Column("to_system_name", sa.String(length=255), nullable=False),
        sa.Column("distance_m", sa.Float(), nullable=False),
        sa.Column("distance_au", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("system_id", "from_system_id", "to_system_id", name="uq_system_gate_distances_triplet"),
    )
    op.create_index("ix_system_gate_distances_id", "system_gate_distances", ["id"], unique=False)
    op.create_index("ix_system_gate_distances_entry_gate_id", "system_gate_distances", ["entry_gate_id"], unique=False)
    op.create_index("ix_system_gate_distances_exit_gate_id", "system_gate_distances", ["exit_gate_id"], unique=False)
    op.create_index("ix_system_gate_distances_from_system_id", "system_gate_distances", ["from_system_id"], unique=False)
    op.create_index("ix_system_gate_distances_system", "system_gate_distances", ["system_id"], unique=False)
    op.create_index("ix_system_gate_distances_system_id", "system_gate_distances", ["system_id"], unique=False)
    op.create_index("ix_system_gate_distances_to_system_id", "system_gate_distances", ["to_system_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_system_gate_distances_to_system_id", table_name="system_gate_distances")
    op.drop_index("ix_system_gate_distances_system_id", table_name="system_gate_distances")
    op.drop_index("ix_system_gate_distances_system", table_name="system_gate_distances")
    op.drop_index("ix_system_gate_distances_from_system_id", table_name="system_gate_distances")
    op.drop_index("ix_system_gate_distances_exit_gate_id", table_name="system_gate_distances")
    op.drop_index("ix_system_gate_distances_entry_gate_id", table_name="system_gate_distances")
    op.drop_index("ix_system_gate_distances_id", table_name="system_gate_distances")
    op.drop_table("system_gate_distances")

    op.drop_index("ix_static_stargates_system_dest", table_name="static_stargates")
    op.drop_index("ix_static_stargates_destination_system_id", table_name="static_stargates")
    op.drop_index("ix_static_stargates_gate_id", table_name="static_stargates")
    op.drop_table("static_stargates")

    op.drop_column("hauling_preferences", "route_mode")
