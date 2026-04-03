from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey,
    Float, Index, Integer, Numeric, String, Text, UniqueConstraint, func
)
from sqlalchemy.orm import relationship
from app.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    is_admin = Column(Boolean, default=False, nullable=False)
    is_director = Column(Boolean, default=False, nullable=False, server_default="false")
    is_corp_manager = Column(Boolean, default=False, nullable=False, server_default="false")
    is_fc = Column(Boolean, default=False, nullable=False, server_default="false")

    @property
    def is_owner(self) -> bool:
        """True wenn einer der Charaktere dieses Accounts die EVE_OWNER_CHARACTER_ID hat."""
        from app.config import get_settings
        owner_id = get_settings().eve_owner_character_id
        if not owner_id:
            return False
        return any(c.eve_character_id == owner_id for c in self.characters)
    director_corp_id = Column(BigInteger, nullable=True)
    director_corp_name = Column(String(255), nullable=True)
    main_character_id = Column(Integer, ForeignKey("characters.id", use_alter=True, name="fk_account_main_char"), nullable=True)
    price_mode = Column(String(10), nullable=False, default="sell")

    characters = relationship(
        "Character",
        back_populates="account",
        foreign_keys="Character.account_id",
        cascade="all, delete-orphan"
    )
    main_character = relationship(
        "Character",
        foreign_keys=[main_character_id],
        post_update=True,
    )


class Character(Base):
    __tablename__ = "characters"

    id = Column(Integer, primary_key=True, index=True)
    eve_character_id = Column(BigInteger, unique=True, nullable=False, index=True)
    character_name = Column(String(255), nullable=False)
    corporation_id = Column(BigInteger, nullable=True, index=True)
    corporation_name = Column(String(255), nullable=True)
    alliance_id = Column(BigInteger, nullable=True)
    alliance_name = Column(String(255), nullable=True)

    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)
    scopes = Column(Text, nullable=True)

    portrait_64 = Column(String(512), nullable=True)
    portrait_128 = Column(String(512), nullable=True)
    portrait_256 = Column(String(512), nullable=True)
    last_known_colony_count = Column(Integer, nullable=False, default=0, server_default="0")
    colony_sync_issue = Column(Boolean, nullable=False, default=False, server_default="false")
    colony_sync_issue_note = Column(String(255), nullable=True)
    last_colony_sync_at = Column(DateTime(timezone=True), nullable=True)
    last_esi_refresh_at = Column(DateTime(timezone=True), nullable=True)
    esi_consecutive_errors = Column(Integer, nullable=False, default=0, server_default="0")
    vacation_mode = Column(Boolean, nullable=False, default=False, server_default="false")
    corp_roles = Column(Text, nullable=True)  # JSON list of ESI corp role strings, cached from last refresh

    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    last_login = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    account = relationship("Account", back_populates="characters", foreign_keys=[account_id])

    @property
    def corp_role_list(self) -> list[str]:
        """Returns cached corp roles as a list. Empty list if none stored."""
        if not self.corp_roles:
            return []
        try:
            import json
            return json.loads(self.corp_roles)
        except Exception:
            return []

    @property
    def portrait_url(self) -> str:
        if self.portrait_128:
            return self.portrait_128
        return f"https://images.evetech.net/characters/{self.eve_character_id}/portrait?size=128"

    @property
    def is_main(self) -> bool:
        if self.account and self.account.main_character_id == self.id:
            return True
        return False


class CorpBridgeConnection(Base):
    __tablename__ = "corp_bridge_connections"
    __table_args__ = (
        UniqueConstraint("corporation_id", "from_system_id", "to_system_id", name="uq_corp_bridge_connections_pair"),
        Index("ix_corp_bridge_connections_corp_pair", "corporation_id", "from_system_id", "to_system_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    corporation_id = Column(BigInteger, nullable=False, index=True)
    corporation_name = Column(String(255), nullable=False)
    from_system_id = Column(BigInteger, nullable=False, index=True)
    from_system_name = Column(String(255), nullable=False)
    to_system_id = Column(BigInteger, nullable=False, index=True)
    to_system_name = Column(String(255), nullable=False)
    notes = Column(String(255), nullable=True)
    created_by_account_id = Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SSOState(Base):
    __tablename__ = "sso_states"

    id = Column(Integer, primary_key=True, index=True)
    state = Column(String(255), unique=True, nullable=False, index=True)
    flow = Column(String(50), nullable=False)  # 'login' or 'add_character'
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MarketCache(Base):
    __tablename__ = "market_cache"

    id = Column(Integer, primary_key=True, index=True)
    type_id = Column(Integer, unique=True, nullable=False, index=True)
    type_name = Column(String(255), nullable=True)
    best_buy = Column(String(50), nullable=True)   # stored as string to avoid float precision issues
    best_sell = Column(String(50), nullable=True)
    avg_volume = Column(String(50), nullable=True)
    avg_volume_7d = Column(String(50), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class KillActivityCache(Base):
    __tablename__ = "kill_activity_cache"

    system_id = Column(BigInteger, primary_key=True)
    kill_count = Column(Integer, nullable=False, default=0, server_default="0")
    latest_kills_json = Column(Text, nullable=False, default="[]", server_default="[]")
    window = Column(String(10), nullable=False, default="60m", server_default="60m")
    fetched_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RegionKillCache(Base):
    __tablename__ = "region_kill_cache"

    region_id = Column(BigInteger, primary_key=True)
    window = Column(String(10), primary_key=True, nullable=False, default="60m", server_default="60m")
    kill_count = Column(Integer, nullable=False, default=0, server_default="0")
    kills_json = Column(Text, nullable=False, default="[]", server_default="[]")
    newest_kill_time = Column(String(32), nullable=True)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now())


class IntelKillEvent(Base):
    __tablename__ = "intel_kill_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    killmail_id = Column(BigInteger, nullable=False, unique=True, index=True)
    region_id = Column(BigInteger, nullable=False, index=True)
    solar_system_id = Column(BigInteger, nullable=False, index=True)
    killmail_time = Column(String(32), nullable=False)
    kill_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class CombatIntelPreference(Base):
    __tablename__ = "combat_intel_preferences"

    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    region_id = Column(BigInteger, nullable=True)
    window = Column(String(10), nullable=False, default="60m", server_default="60m")
    kill_type = Column(String(20), nullable=False, default="all", server_default="all")
    layout = Column(String(10), nullable=False, default="geo", server_default="geo")
    tracked_character_id = Column(Integer, ForeignKey("characters.id", ondelete="SET NULL"), nullable=True)
    follow_character = Column(Boolean, nullable=False, default=False, server_default="false")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class HaulingPreference(Base):
    __tablename__ = "hauling_preferences"

    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    return_to_start = Column(Boolean, nullable=False, default=False, server_default="false")
    route_mode = Column(String(20), nullable=False, default="jumps", server_default="jumps")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IntelStreamState(Base):
    __tablename__ = "intel_stream_state"

    stream_key = Column(String(50), primary_key=True)
    last_sequence_id = Column(BigInteger, nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(String(255), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DashboardCache(Base):
    """Persistenter Colony-Cache pro Account — überlebt Server-Neustarts."""
    __tablename__ = "dashboard_cache_db"

    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    colonies_json = Column(Text, nullable=False, default="[]")
    meta_json = Column(Text, nullable=False, default="{}")
    fetched_at = Column(DateTime(timezone=True), server_default=func.now())


class IskSnapshot(Base):
    __tablename__ = "isk_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now())
    isk_day = Column(String(50), nullable=False)
    colony_count = Column(Integer, nullable=False, default=0)


class PiFavorite(Base):
    __tablename__ = "pi_favorites"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    product_name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class InventoryItemSummary(Base):
    __tablename__ = "inventory_item_summaries"
    __table_args__ = (
        UniqueConstraint("account_id", "type_id", name="uq_inventory_item_account_type"),
        Index("ix_inventory_item_account_tier", "account_id", "tier"),
    )

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    type_id = Column(Integer, nullable=False, index=True)
    item_name = Column(String(255), nullable=False)
    tier = Column(String(10), nullable=False, index=True)
    quantity_on_hand = Column(BigInteger, nullable=False, default=0, server_default="0")
    weighted_average_cost = Column(String(50), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class InventoryLot(Base):
    __tablename__ = "inventory_lots"
    __table_args__ = (
        Index("ix_inventory_lot_account_type_created", "account_id", "type_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    type_id = Column(Integer, nullable=False, index=True)
    item_name = Column(String(255), nullable=False)
    tier = Column(String(10), nullable=False, index=True)
    quantity_added = Column(BigInteger, nullable=False)
    quantity_remaining = Column(BigInteger, nullable=False)
    unit_cost = Column(String(50), nullable=True)
    total_cost = Column(String(50), nullable=True)
    source_kind = Column(String(20), nullable=False, default="manual", server_default="manual")
    note = Column(String(255), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class InventoryAdjustment(Base):
    __tablename__ = "inventory_adjustments"
    __table_args__ = (
        Index("ix_inventory_adjustment_account_type_created", "account_id", "type_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    type_id = Column(Integer, nullable=False, index=True)
    item_name = Column(String(255), nullable=False)
    tier = Column(String(10), nullable=False, index=True)
    delta_quantity = Column(BigInteger, nullable=False)
    reason = Column(String(50), nullable=False, default="manual", server_default="manual")
    note = Column(String(255), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class SkyhookEntry(Base):
    __tablename__ = "skyhook_entries"
    __table_args__ = (
        Index("ix_skyhook_entries_account_planet", "account_id", "planet_id"),
    )

    id             = Column(Integer, primary_key=True, index=True)
    account_id     = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    planet_id      = Column(Integer, nullable=False, index=True)
    character_name = Column(String(255), nullable=True)
    recorded_at    = Column(DateTime(timezone=True), server_default=func.now())

    items = relationship("SkyhookItem", back_populates="entry", cascade="all, delete-orphan")


class SkyhookItem(Base):
    __tablename__ = "skyhook_items"

    id           = Column(Integer, primary_key=True, index=True)
    entry_id     = Column(Integer, ForeignKey("skyhook_entries.id", ondelete="CASCADE"), nullable=False, index=True)
    product_name = Column(String(255), nullable=False)
    quantity     = Column(Integer, nullable=False, default=0)

    entry = relationship("SkyhookEntry", back_populates="items")


class SkyhookValueCache(Base):
    __tablename__ = "skyhook_value_cache"

    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    planet_id = Column(Integer, primary_key=True)
    price_mode = Column(String(10), primary_key=True)
    total_value = Column(String(50), nullable=False, default="0")
    details_json = Column(Text, nullable=False, default="[]")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AccessPolicy(Base):
    __tablename__ = "access_policy"

    id = Column(Integer, primary_key=True)  # singleton, always id=1
    mode = Column(String(20), nullable=False, default="open")  # open | allowlist | blocklist
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    entries = relationship("AccessPolicyEntry", back_populates="policy", cascade="all, delete-orphan")


class AccessPolicyEntry(Base):
    __tablename__ = "access_policy_entries"

    id = Column(Integer, primary_key=True, index=True)
    policy_id = Column(Integer, ForeignKey("access_policy.id", ondelete="CASCADE"), nullable=False)
    entity_type = Column(String(20), nullable=False)   # "corporation" | "alliance"
    entity_id = Column(BigInteger, nullable=False)
    entity_name = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    policy = relationship("AccessPolicy", back_populates="entries")


class PageAccessSetting(Base):
    __tablename__ = "page_access_settings"

    page_key = Column(String(100), primary_key=True)
    access_level = Column(String(20), nullable=False, default="member")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TranslationEntry(Base):
    __tablename__ = "translation_entries"
    __table_args__ = (
        UniqueConstraint("locale", "key", name="uq_translation_entries_locale_key"),
    )

    id = Column(Integer, primary_key=True, index=True)
    locale = Column(String(20), nullable=False, index=True)
    key = Column(String(255), nullable=False, index=True)
    text = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StaticPlanet(Base):
    __tablename__ = "static_planets"

    planet_id = Column(BigInteger, primary_key=True, index=True)
    system_id = Column(BigInteger, nullable=False, index=True)
    planet_name = Column(String(255), nullable=False)
    planet_number = Column(String(16), nullable=True)
    radius = Column(BigInteger, nullable=True)
    x = Column(Float, nullable=True)
    y = Column(Float, nullable=True)
    z = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class StaticStargate(Base):
    __tablename__ = "static_stargates"
    __table_args__ = (
        Index("ix_static_stargates_system_dest", "system_id", "destination_system_id"),
    )

    gate_id = Column(BigInteger, primary_key=True, index=True)
    system_id = Column(BigInteger, nullable=False, index=True)
    system_name = Column(String(255), nullable=False)
    gate_name = Column(String(255), nullable=False)
    destination_system_id = Column(BigInteger, nullable=True, index=True)
    destination_system_name = Column(String(255), nullable=True)
    x = Column(Float, nullable=False)
    y = Column(Float, nullable=False)
    z = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SystemGateDistance(Base):
    __tablename__ = "system_gate_distances"
    __table_args__ = (
        UniqueConstraint("system_id", "from_system_id", "to_system_id", name="uq_system_gate_distances_triplet"),
        Index("ix_system_gate_distances_system", "system_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    system_id = Column(BigInteger, nullable=False, index=True)
    system_name = Column(String(255), nullable=False)
    entry_gate_id = Column(BigInteger, nullable=False, index=True)
    exit_gate_id = Column(BigInteger, nullable=False, index=True)
    from_system_id = Column(BigInteger, nullable=False, index=True)
    to_system_id = Column(BigInteger, nullable=False, index=True)
    from_system_name = Column(String(255), nullable=False)
    to_system_name = Column(String(255), nullable=False)
    distance_m = Column(Float, nullable=False)
    distance_au = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PlanetEsiCache(Base):
    """Per-planet ESI response cache with ETag support to avoid redundant fetches."""
    __tablename__ = "planet_esi_cache"
    __table_args__ = (
        UniqueConstraint("eve_character_id", "planet_id", name="uq_planet_esi_cache"),
    )

    id = Column(Integer, primary_key=True, index=True)
    eve_character_id = Column(BigInteger, nullable=False, index=True)
    planet_id = Column(Integer, nullable=False, index=True)
    etag = Column(String(255), nullable=True)
    response_json = Column(Text, nullable=False, server_default="{}")
    fetched_at = Column(DateTime(timezone=True), server_default=func.now())


class PlanetTemplate(Base):
    """User-saved or community PI surface templates (building layout on a planet)."""
    __tablename__ = "planet_templates"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True, index=True)
    # NULL account_id = community/seeded template visible to all
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    planet_type = Column(String(64), nullable=True)   # e.g. "Barren", "Gas", "Oceanic" …
    layout_json = Column(Text, nullable=False)         # raw JSON from EVE_PI_Templates format
    is_community = Column(Boolean, nullable=False, default=False, server_default="false")
    source_url = Column(String(512), nullable=True)    # original repo URL if seeded
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WebhookAlert(Base):
    """Per-account Discord/webhook configuration for colony expiry alerts."""
    __tablename__ = "webhook_alerts"

    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    webhook_url = Column(String(1024), nullable=True)
    alert_hours = Column(Integer, nullable=False, default=2, server_default="2")
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    last_alert_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Billing System ────────────────────────────────────────────────────────────

class BillingWalletReceiver(Base):
    """EVE characters configured to receive ISK payments for subscriptions."""
    __tablename__ = "billing_wallet_receivers"

    id = Column(Integer, primary_key=True, index=True)
    eve_character_id = Column(BigInteger, nullable=False, unique=True, index=True)
    character_name = Column(String(255), nullable=False)
    # character_id FK into characters table (nullable: receiver may not be a logged-in user)
    character_fk = Column(Integer, ForeignKey("characters.id", ondelete="SET NULL"), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    notes = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BillingSubscriptionPlan(Base):
    """Named subscription plan with base daily ISK price. One per scope (individual/corp/alliance)."""
    __tablename__ = "billing_subscription_plans"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(50), nullable=False, unique=True, index=True)   # e.g. "individual", "corporation", "alliance"
    scope = Column(String(20), nullable=False)                           # "individual" | "corporation" | "alliance"
    display_name = Column(String(255), nullable=False)
    daily_price_isk = Column(Numeric(20, 0), nullable=False)            # ISK as integer (no floats)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BillingPricingTier(Base):
    """Character-count tiers for corporation and alliance subscription pricing."""
    __tablename__ = "billing_pricing_tiers"
    __table_args__ = (
        UniqueConstraint("scope", "min_members", name="uq_billing_pricing_tier_scope_min"),
        CheckConstraint("max_members IS NULL OR max_members >= min_members", name="ck_billing_pricing_tier_range"),
    )

    id = Column(Integer, primary_key=True, index=True)
    scope = Column(String(20), nullable=False, index=True)              # "corporation" | "alliance"
    min_members = Column(Integer, nullable=False)
    max_members = Column(Integer, nullable=True)                        # NULL = unbounded
    daily_price_isk = Column(Numeric(20, 0), nullable=False)


class BillingWalletTransaction(Base):
    """Raw wallet journal entries ingested from ESI for configured receiver characters."""
    __tablename__ = "billing_wallet_transactions"

    id = Column(BigInteger, primary_key=True)                           # ESI journal_id — natural dedup key
    receiver_id = Column(Integer, ForeignKey("billing_wallet_receivers.id", ondelete="CASCADE"), nullable=False, index=True)
    ref_type = Column(String(100), nullable=False)                      # "player_donation" | "corporation_account_withdrawal" etc.
    sender_character_id = Column(BigInteger, nullable=True, index=True)
    sender_character_name = Column(String(255), nullable=True)
    sender_corporation_id = Column(BigInteger, nullable=True, index=True)
    sender_corporation_name = Column(String(255), nullable=True)
    amount_isk = Column(Numeric(20, 0), nullable=False)                 # rounded to integer on import
    description = Column(String(1024), nullable=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, index=True)
    imported_at = Column(DateTime(timezone=True), server_default=func.now())


class BillingTransactionMatch(Base):
    """Links a wallet transaction to the subscription period it created."""
    __tablename__ = "billing_transaction_matches"

    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(BigInteger, ForeignKey("billing_wallet_transactions.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    subject_type = Column(String(20), nullable=False)                   # "account" | "corporation" | "alliance"
    subject_id = Column(Integer, nullable=False, index=True)            # account.id | corporation_id | alliance_id
    plan_id = Column(Integer, ForeignKey("billing_subscription_plans.id", ondelete="SET NULL"), nullable=True)
    days_granted = Column(Numeric(12, 4), nullable=False)
    match_status = Column(String(20), nullable=False, default="matched", server_default="matched")  # "matched" | "unmatched" | "manual"
    notes = Column(String(255), nullable=True)
    matched_at = Column(DateTime(timezone=True), server_default=func.now())


class BillingSubscriptionPeriod(Base):
    """Active or historical subscription period for an account, corporation, or alliance."""
    __tablename__ = "billing_subscription_periods"

    id = Column(Integer, primary_key=True, index=True)
    subject_type = Column(String(20), nullable=False)                   # "account" | "corporation" | "alliance"
    subject_id = Column(Integer, nullable=False, index=True)            # account.id OR eve corporation_id OR eve alliance_id
    plan_id = Column(Integer, ForeignKey("billing_subscription_plans.id", ondelete="SET NULL"), nullable=True, index=True)
    source_type = Column(String(30), nullable=False)                    # "payment" | "bonus_code" | "manual_grant"
    starts_at = Column(DateTime(timezone=True), nullable=False)
    ends_at = Column(DateTime(timezone=True), nullable=False, index=True)
    granted_by_account_id = Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    note = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BillingGrant(Base):
    """Manual free-access grants — global, page-scoped, or feature-scoped."""
    __tablename__ = "billing_grants"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    scope_type = Column(String(20), nullable=False, default="global", server_default="global")  # "global" | "page" | "feature"
    scope_key = Column(String(100), nullable=True)                      # page_key or feature_key; NULL = global
    starts_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)         # NULL = permanent
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    granted_by_account_id = Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    note = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BillingBonusCode(Base):
    """Redeemable bonus codes that grant subscription time, page access, or feature access."""
    __tablename__ = "billing_bonus_codes"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(64), nullable=False, unique=True, index=True)
    reward_type = Column(String(30), nullable=False)                    # "subscription_days" | "page_access" | "feature_access" | "global_access"
    reward_value = Column(String(255), nullable=False)                  # "30" for days, "skyhook" for page_key, etc.
    plan_id = Column(Integer, ForeignKey("billing_subscription_plans.id", ondelete="SET NULL"), nullable=True)
    max_redemptions = Column(Integer, nullable=True)                    # NULL = unlimited
    redemption_count = Column(Integer, nullable=False, default=0, server_default="0")
    expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_by_account_id = Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    note = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BillingBonusCodeRedemption(Base):
    """Audit trail for bonus code redemptions."""
    __tablename__ = "billing_bonus_code_redemptions"

    id = Column(Integer, primary_key=True, index=True)
    code_id = Column(Integer, ForeignKey("billing_bonus_codes.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    redeemed_at = Column(DateTime(timezone=True), server_default=func.now())
    reward_snapshot = Column(Text, nullable=True)                       # JSON snapshot of reward at redemption time


class BillingEntitlementCache(Base):
    """Pre-computed entitlement state per account. Recomputed by Celery task."""
    __tablename__ = "billing_entitlement_cache"

    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    # JSON: {"dashboard": true, "skyhook": true, ...} — one key per page_key
    pages_json = Column(Text, nullable=False, default="{}", server_default="{}")
    # JSON: {"feature_key": true, ...} — for future paid features within pages
    features_json = Column(Text, nullable=False, default="{}", server_default="{}")
    computed_at = Column(DateTime(timezone=True), server_default=func.now())


class BillingAuditLog(Base):
    """Immutable append-only billing event log."""
    __tablename__ = "billing_audit_log"
    __table_args__ = (
        Index("ix_billing_audit_log_account_event", "actor_account_id", "event_type"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    event_type = Column(String(100), nullable=False, index=True)
    actor_account_id = Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    target_account_id = Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    detail_json = Column(Text, nullable=False, default="{}", server_default="{}")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
