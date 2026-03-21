from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey,
    Integer, String, Text, func
)
from sqlalchemy.orm import relationship
from app.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    is_admin = Column(Boolean, default=False, nullable=False)
    main_character_id = Column(Integer, ForeignKey("characters.id", use_alter=True, name="fk_account_main_char"), nullable=True)

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
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
