"""billing join codes and bonus reward linkage

Revision ID: 039
Revises: 038
Create Date: 2026-04-03
"""
from alembic import op
import sqlalchemy as sa


revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "billing_subscription_periods",
        sa.Column("source_code_id", sa.Integer(), sa.ForeignKey("billing_bonus_codes.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index(
        "ix_billing_subscription_periods_source_code",
        "billing_subscription_periods",
        ["source_code_id"],
    )

    op.add_column(
        "billing_grants",
        sa.Column("source_code_id", sa.Integer(), sa.ForeignKey("billing_bonus_codes.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_billing_grants_source_code", "billing_grants", ["source_code_id"])

    op.create_table(
        "billing_subscription_join_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=20), nullable=False),
        sa.Column("subject_id", sa.BigInteger(), nullable=False),
        sa.Column("source_period_id", sa.Integer(), sa.ForeignKey("billing_subscription_periods.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_transaction_id", sa.BigInteger(), sa.ForeignKey("billing_wallet_transactions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("issued_by_receiver_id", sa.Integer(), sa.ForeignKey("billing_wallet_receivers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_character_id", sa.BigInteger(), nullable=True),
        sa.Column("max_redemptions", sa.Integer(), nullable=True),
        sa.Column("redemption_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_billing_subscription_join_code"),
    )
    op.create_index("ix_billing_subscription_join_codes_code", "billing_subscription_join_codes", ["code"])
    op.create_index("ix_billing_subscription_join_codes_subject", "billing_subscription_join_codes", ["subject_type", "subject_id"])
    op.create_index("ix_billing_subscription_join_codes_target_char", "billing_subscription_join_codes", ["target_character_id"])

    op.create_table(
        "billing_subscription_join_redemptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code_id", sa.Integer(), sa.ForeignKey("billing_subscription_join_codes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("code_id", "account_id", name="uq_billing_join_code_account_once"),
    )
    op.create_index("ix_billing_subscription_join_redemptions_code", "billing_subscription_join_redemptions", ["code_id"])
    op.create_index("ix_billing_subscription_join_redemptions_account", "billing_subscription_join_redemptions", ["account_id"])


def downgrade():
    op.drop_index("ix_billing_subscription_join_redemptions_account", table_name="billing_subscription_join_redemptions")
    op.drop_index("ix_billing_subscription_join_redemptions_code", table_name="billing_subscription_join_redemptions")
    op.drop_table("billing_subscription_join_redemptions")

    op.drop_index("ix_billing_subscription_join_codes_target_char", table_name="billing_subscription_join_codes")
    op.drop_index("ix_billing_subscription_join_codes_subject", table_name="billing_subscription_join_codes")
    op.drop_index("ix_billing_subscription_join_codes_code", table_name="billing_subscription_join_codes")
    op.drop_table("billing_subscription_join_codes")

    op.drop_index("ix_billing_grants_source_code", table_name="billing_grants")
    op.drop_column("billing_grants", "source_code_id")

    op.drop_index("ix_billing_subscription_periods_source_code", table_name="billing_subscription_periods")
    op.drop_column("billing_subscription_periods", "source_code_id")
