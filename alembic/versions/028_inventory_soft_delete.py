"""add soft delete to inventory summaries

Revision ID: 028_inventory_soft_delete
Revises: 027_inventory_stock
Create Date: 2026-03-31
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "028_inventory_soft_delete"
down_revision = "027_inventory_stock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("inventory_item_summaries", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_inventory_item_summaries_deleted_at", "inventory_item_summaries", ["deleted_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_inventory_item_summaries_deleted_at", table_name="inventory_item_summaries")
    op.drop_column("inventory_item_summaries", "deleted_at")
