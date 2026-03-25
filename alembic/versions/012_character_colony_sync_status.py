"""character colony sync status

Revision ID: 012_character_colony_sync_status
Revises: 011_static_planets
Create Date: 2026-03-25 10:10:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "012_character_colony_sync_status"
down_revision = "011_static_planets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("characters", sa.Column("last_known_colony_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("characters", sa.Column("colony_sync_issue", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("characters", sa.Column("colony_sync_issue_note", sa.String(length=255), nullable=True))
    op.add_column("characters", sa.Column("last_colony_sync_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("characters", "last_colony_sync_at")
    op.drop_column("characters", "colony_sync_issue_note")
    op.drop_column("characters", "colony_sync_issue")
    op.drop_column("characters", "last_known_colony_count")
