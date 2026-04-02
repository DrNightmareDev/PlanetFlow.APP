"""Add billing system: wallet receivers, subscriptions, grants, bonus codes, entitlement cache, audit log

Revision ID: 033_billing_system
Revises: 032_static_planet_positions
Create Date: 2026-04-02
"""

from alembic import op
import sqlalchemy as sa

revision = "033_billing_system"
down_revision = "032_static_planet_positions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Wallet receivers ──────────────────────────────────────────────────────
    op.create_table(
        "billing_wallet_receivers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("eve_character_id", sa.BigInteger(), nullable=False),
        sa.Column("character_name", sa.String(255), nullable=False),
        sa.Column("character_fk", sa.Integer(), sa.ForeignKey("characters.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("notes", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("eve_character_id", name="uq_billing_wallet_receivers_char_id"),
    )
    op.create_index("ix_billing_wallet_receivers_char_id", "billing_wallet_receivers", ["eve_character_id"])

    # ── Subscription plans ────────────────────────────────────────────────────
    op.create_table(
        "billing_subscription_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(50), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("daily_price_isk", sa.Numeric(20, 0), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("key", name="uq_billing_subscription_plans_key"),
    )
    op.create_index("ix_billing_subscription_plans_key", "billing_subscription_plans", ["key"])

    # ── Pricing tiers ─────────────────────────────────────────────────────────
    op.create_table(
        "billing_pricing_tiers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("min_members", sa.Integer(), nullable=False),
        sa.Column("max_members", sa.Integer(), nullable=True),
        sa.Column("daily_price_isk", sa.Numeric(20, 0), nullable=False),
        sa.UniqueConstraint("scope", "min_members", name="uq_billing_pricing_tier_scope_min"),
        sa.CheckConstraint("max_members IS NULL OR max_members >= min_members", name="ck_billing_pricing_tier_range"),
    )
    op.create_index("ix_billing_pricing_tiers_scope", "billing_pricing_tiers", ["scope"])

    # ── Wallet transactions ───────────────────────────────────────────────────
    op.create_table(
        "billing_wallet_transactions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("receiver_id", sa.Integer(), sa.ForeignKey("billing_wallet_receivers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ref_type", sa.String(100), nullable=False),
        sa.Column("sender_character_id", sa.BigInteger(), nullable=True),
        sa.Column("sender_character_name", sa.String(255), nullable=True),
        sa.Column("sender_corporation_id", sa.BigInteger(), nullable=True),
        sa.Column("sender_corporation_name", sa.String(255), nullable=True),
        sa.Column("amount_isk", sa.Numeric(20, 0), nullable=False),
        sa.Column("description", sa.String(1024), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_billing_wallet_transactions_receiver", "billing_wallet_transactions", ["receiver_id"])
    op.create_index("ix_billing_wallet_transactions_sender_char", "billing_wallet_transactions", ["sender_character_id"])
    op.create_index("ix_billing_wallet_transactions_sender_corp", "billing_wallet_transactions", ["sender_corporation_id"])
    op.create_index("ix_billing_wallet_transactions_occurred_at", "billing_wallet_transactions", ["occurred_at"])

    # ── Transaction matches ───────────────────────────────────────────────────
    op.create_table(
        "billing_transaction_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transaction_id", sa.BigInteger(), sa.ForeignKey("billing_wallet_transactions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject_type", sa.String(20), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("billing_subscription_plans.id", ondelete="SET NULL"), nullable=True),
        sa.Column("days_granted", sa.Numeric(12, 4), nullable=False),
        sa.Column("match_status", sa.String(20), nullable=False, server_default="matched"),
        sa.Column("notes", sa.String(255), nullable=True),
        sa.Column("matched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("transaction_id", name="uq_billing_transaction_matches_tx"),
    )
    op.create_index("ix_billing_transaction_matches_subject", "billing_transaction_matches", ["subject_type", "subject_id"])

    # ── Subscription periods ──────────────────────────────────────────────────
    op.create_table(
        "billing_subscription_periods",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject_type", sa.String(20), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("billing_subscription_plans.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_by_account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_billing_subscription_periods_subject", "billing_subscription_periods", ["subject_type", "subject_id"])
    op.create_index("ix_billing_subscription_periods_ends_at", "billing_subscription_periods", ["ends_at"])

    # ── Grants ────────────────────────────────────────────────────────────────
    op.create_table(
        "billing_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope_type", sa.String(20), nullable=False, server_default="global"),
        sa.Column("scope_key", sa.String(100), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("granted_by_account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_billing_grants_account", "billing_grants", ["account_id"])

    # ── Bonus codes ───────────────────────────────────────────────────────────
    op.create_table(
        "billing_bonus_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("reward_type", sa.String(30), nullable=False),
        sa.Column("reward_value", sa.String(255), nullable=False),
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("billing_subscription_plans.id", ondelete="SET NULL"), nullable=True),
        sa.Column("max_redemptions", sa.Integer(), nullable=True),
        sa.Column("redemption_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by_account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_billing_bonus_codes_code"),
    )
    op.create_index("ix_billing_bonus_codes_code", "billing_bonus_codes", ["code"])

    # ── Bonus code redemptions ────────────────────────────────────────────────
    op.create_table(
        "billing_bonus_code_redemptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code_id", sa.Integer(), sa.ForeignKey("billing_bonus_codes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("reward_snapshot", sa.Text(), nullable=True),
    )
    op.create_index("ix_billing_bonus_code_redemptions_code", "billing_bonus_code_redemptions", ["code_id"])
    op.create_index("ix_billing_bonus_code_redemptions_account", "billing_bonus_code_redemptions", ["account_id"])

    # ── Entitlement cache ─────────────────────────────────────────────────────
    op.create_table(
        "billing_entitlement_cache",
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("pages_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("features_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── Audit log ─────────────────────────────────────────────────────────────
    op.create_table(
        "billing_audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("actor_account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("detail_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_billing_audit_log_event_type", "billing_audit_log", ["event_type"])
    op.create_index("ix_billing_audit_log_created_at", "billing_audit_log", ["created_at"])
    op.create_index("ix_billing_audit_log_actor", "billing_audit_log", ["actor_account_id", "event_type"])

    # ── Extend PageAccessSetting to support "paid" level ─────────────────────
    # No schema change needed: access_level is a free String column.
    # The new value "paid" is handled in application logic (page_access.py + entitlements.py).


def downgrade() -> None:
    op.drop_table("billing_audit_log")
    op.drop_table("billing_entitlement_cache")
    op.drop_table("billing_bonus_code_redemptions")
    op.drop_table("billing_bonus_codes")
    op.drop_table("billing_grants")
    op.drop_table("billing_subscription_periods")
    op.drop_table("billing_transaction_matches")
    op.drop_table("billing_wallet_transactions")
    op.drop_table("billing_pricing_tiers")
    op.drop_table("billing_subscription_plans")
    op.drop_table("billing_wallet_receivers")
