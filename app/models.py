from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey,
    Integer, String, Text, UniqueConstraint, func
)
from sqlalchemy.orm import relationship
from app.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    is_admin = Column(Boolean, default=False, nullable=False)
    is_owner = Column(Boolean, default=False, nullable=False)
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
    corporation_id = Column(BigInteger, nullable=True)
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

    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    last_login = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    account = relationship("Account", back_populates="characters", foreign_keys=[account_id])

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


class SkyhookEntry(Base):
    __tablename__ = "skyhook_entries"

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
