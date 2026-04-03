"""
Billing service — wallet ingestion, transaction matching, subscription management,
grant handling, and bonus code redemption.

All ISK values are stored and computed as Decimal with zero decimal places (integers).
Never use float for ISK amounts.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models import (
    Account,
    BillingAuditLog,
    BillingBonusCode,
    BillingBonusCodeRedemption,
    BillingEntitlementCache,
    BillingGrant,
    BillingPricingTier,
    BillingSubscriptionPeriod,
    BillingSubscriptionPlan,
    BillingTransactionMatch,
    BillingWalletReceiver,
    BillingWalletTransaction,
    Character,
    PageAccessSetting,
)

logger = logging.getLogger(__name__)


# ── Audit helpers ─────────────────────────────────────────────────────────────

def _audit(
    db: Session,
    *,
    event_type: str,
    actor_account_id: int | None = None,
    target_account_id: int | None = None,
    detail: dict,
) -> None:
    db.add(BillingAuditLog(
        event_type=event_type,
        actor_account_id=actor_account_id,
        target_account_id=target_account_id,
        detail_json=json.dumps(detail),
    ))


# ── Subscription period helpers ───────────────────────────────────────────────

def _active_period_end(
    db: Session,
    *,
    subject_type: str,
    subject_id: int,
) -> datetime | None:
    """Return the latest ends_at of any currently active period, or None."""
    now = datetime.now(UTC)
    row = (
        db.query(BillingSubscriptionPeriod)
        .filter(
            BillingSubscriptionPeriod.subject_type == subject_type,
            BillingSubscriptionPeriod.subject_id == subject_id,
            BillingSubscriptionPeriod.ends_at >= now,
        )
        .order_by(BillingSubscriptionPeriod.ends_at.desc())
        .first()
    )
    return row.ends_at if row else None


def extend_subscription(
    db: Session,
    *,
    subject_type: str,
    subject_id: int,
    plan_id: int | None,
    days: Decimal,
    source_type: str,
    note: str,
    actor_account_id: int | None = None,
) -> BillingSubscriptionPeriod:
    """
    Extend (or create) a subscription period.
    If an active period already exists, the new period starts where the old one ends.
    """
    now = datetime.now(UTC)
    starts_at = _active_period_end(db, subject_type=subject_type, subject_id=subject_id) or now
    if starts_at < now:
        starts_at = now
    ends_at = starts_at + timedelta(seconds=float(days * Decimal(86400)))
    period = BillingSubscriptionPeriod(
        subject_type=subject_type,
        subject_id=subject_id,
        plan_id=plan_id,
        source_type=source_type,
        starts_at=starts_at,
        ends_at=ends_at,
        granted_by_account_id=actor_account_id,
        note=note,
    )
    db.add(period)
    _audit(
        db,
        event_type="subscription.extended",
        actor_account_id=actor_account_id,
        target_account_id=subject_id if subject_type == "account" else None,
        detail={
            "subject_type": subject_type,
            "subject_id": subject_id,
            "plan_id": plan_id,
            "days": str(days),
            "source_type": source_type,
            "note": note,
        },
    )
    return period


# ── Tier pricing ──────────────────────────────────────────────────────────────

def _get_tier_daily_price(
    db: Session,
    *,
    scope: str,
    member_count: int,
) -> Decimal | None:
    """Return the daily ISK price for the matching tier, or None if no tier configured."""
    tier = (
        db.query(BillingPricingTier)
        .filter(
            BillingPricingTier.scope == scope,
            BillingPricingTier.min_members <= member_count,
            (BillingPricingTier.max_members.is_(None)) | (BillingPricingTier.max_members >= member_count),
        )
        .order_by(BillingPricingTier.min_members.desc())
        .first()
    )
    return Decimal(tier.daily_price_isk) if tier else None


def _count_corp_members(db: Session, corporation_id: int) -> int:
    """Count characters in DB with this corporation_id — no ESI call."""
    return db.query(Character).filter(Character.corporation_id == corporation_id).count()


def _count_alliance_members(db: Session, alliance_id: int) -> int:
    """Count characters in DB with this alliance_id — no ESI call."""
    return db.query(Character).filter(Character.alliance_id == alliance_id).count()


# ── Wallet transaction matching ───────────────────────────────────────────────

def match_wallet_transaction(
    db: Session,
    *,
    transaction_id: int,
    actor_account_id: int | None = None,
) -> tuple[bool, str]:
    """
    Attempt to match a wallet transaction to an account/corp/alliance subscription.

    Matching priority:
    1. player_donation → match sender_character_id → Account
    2. corporation_account_withdrawal → match sender_corporation_id → corp subscription
       (also checks if corp holds an alliance subscription)

    Returns (success, message).
    """
    tx = db.get(BillingWalletTransaction, transaction_id)
    if not tx:
        return False, "Transaction not found."

    # Dedup: already matched?
    if db.query(BillingTransactionMatch).filter(
        BillingTransactionMatch.transaction_id == transaction_id
    ).first():
        return False, "Transaction already matched."

    receiver = db.get(BillingWalletReceiver, tx.receiver_id)
    if not receiver or not receiver.is_active:
        return False, "Receiver wallet is inactive or not found."

    amount = Decimal(tx.amount_isk)
    if amount <= 0:
        return False, "Transaction amount is zero or negative."

    # ── Individual: player donation ───────────────────────────────────────────
    if tx.ref_type == "player_donation" and tx.sender_character_id:
        char = db.query(Character).filter(
            Character.eve_character_id == tx.sender_character_id
        ).first()
        if char:
            account = db.query(Account).filter(Account.id == char.account_id).first()
            if account:
                plan = db.query(BillingSubscriptionPlan).filter(
                    BillingSubscriptionPlan.scope == "individual",
                    BillingSubscriptionPlan.is_active == True,
                ).order_by(BillingSubscriptionPlan.id.asc()).first()
                if plan and Decimal(plan.daily_price_isk) > 0:
                    days = (amount / Decimal(plan.daily_price_isk)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                    extend_subscription(
                        db,
                        subject_type="account",
                        subject_id=account.id,
                        plan_id=plan.id,
                        days=days,
                        source_type="payment",
                        note=f"Wallet tx {tx.id}",
                        actor_account_id=actor_account_id,
                    )
                    db.add(BillingTransactionMatch(
                        transaction_id=tx.id,
                        subject_type="account",
                        subject_id=account.id,
                        plan_id=plan.id,
                        days_granted=days,
                        match_status="matched",
                        notes=f"Player donation from char {tx.sender_character_id}",
                    ))
                    _invalidate_entitlement_cache(db, account_id=account.id)
                    return True, f"Matched to account {account.id} ({days} days)."

    # ── Corporation: corporation_account_withdrawal ───────────────────────────
    if tx.ref_type == "corporation_account_withdrawal" and tx.sender_corporation_id:
        corp_id = tx.sender_corporation_id

        # Try corp subscription first
        plan = db.query(BillingSubscriptionPlan).filter(
            BillingSubscriptionPlan.scope == "corporation",
            BillingSubscriptionPlan.is_active == True,
        ).order_by(BillingSubscriptionPlan.id.asc()).first()
        if plan:
            member_count = _count_corp_members(db, corp_id)
            daily_price = _get_tier_daily_price(db, scope="corporation", member_count=member_count)
            if daily_price is None:
                daily_price = Decimal(plan.daily_price_isk)
            if daily_price > 0:
                days = (amount / daily_price).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                extend_subscription(
                    db,
                    subject_type="corporation",
                    subject_id=corp_id,
                    plan_id=plan.id,
                    days=days,
                    source_type="payment",
                    note=f"Wallet tx {tx.id}",
                    actor_account_id=actor_account_id,
                )
                db.add(BillingTransactionMatch(
                    transaction_id=tx.id,
                    subject_type="corporation",
                    subject_id=corp_id,
                    plan_id=plan.id,
                    days_granted=days,
                    match_status="matched",
                    notes=f"Corp withdrawal from corp {corp_id}",
                ))
                # Invalidate cache for all accounts in this corp
                _invalidate_entitlement_cache_for_corp(db, corporation_id=corp_id)
                return True, f"Matched to corporation {corp_id} ({days} days)."

        # Try alliance subscription (corp is holding corp of an alliance payment)
        # Check if any character from this corp has an alliance_id
        sample_char = db.query(Character).filter(
            Character.corporation_id == corp_id,
            Character.alliance_id.isnot(None),
        ).first()
        if sample_char and sample_char.alliance_id:
            alliance_id = sample_char.alliance_id
            plan = db.query(BillingSubscriptionPlan).filter(
                BillingSubscriptionPlan.scope == "alliance",
                BillingSubscriptionPlan.is_active == True,
            ).order_by(BillingSubscriptionPlan.id.asc()).first()
            if plan:
                member_count = _count_alliance_members(db, alliance_id)
                daily_price = _get_tier_daily_price(db, scope="alliance", member_count=member_count)
                if daily_price is None:
                    daily_price = Decimal(plan.daily_price_isk)
                if daily_price > 0:
                    days = (amount / daily_price).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                    extend_subscription(
                        db,
                        subject_type="alliance",
                        subject_id=alliance_id,
                        plan_id=plan.id,
                        days=days,
                        source_type="payment",
                        note=f"Wallet tx {tx.id}",
                        actor_account_id=actor_account_id,
                    )
                    db.add(BillingTransactionMatch(
                        transaction_id=tx.id,
                        subject_type="alliance",
                        subject_id=alliance_id,
                        plan_id=plan.id,
                        days_granted=days,
                        match_status="matched",
                        notes=f"Alliance payment via corp {corp_id}",
                    ))
                    _invalidate_entitlement_cache_for_alliance(db, alliance_id=alliance_id)
                    return True, f"Matched to alliance {alliance_id} ({days} days)."

    # Store as unmatched so admin can manually resolve
    db.add(BillingTransactionMatch(
        transaction_id=tx.id,
        subject_type="unknown",
        subject_id=0,
        plan_id=None,
        days_granted=Decimal(0),
        match_status="unmatched",
        notes="No matching account/corp/alliance found",
    ))
    logger.warning("billing: unmatched transaction %s (ref_type=%s sender_char=%s sender_corp=%s)",
                   tx.id, tx.ref_type, tx.sender_character_id, tx.sender_corporation_id)
    return False, "No matching account or corporation found."


# ── Entitlement cache invalidation ───────────────────────────────────────────

def _invalidate_entitlement_cache(db: Session, *, account_id: int) -> None:
    """Delete cached entitlement for one account so it gets recomputed."""
    db.query(BillingEntitlementCache).filter(
        BillingEntitlementCache.account_id == account_id
    ).delete()


def _invalidate_entitlement_cache_for_corp(db: Session, *, corporation_id: int) -> None:
    affected = (
        db.query(Character.account_id)
        .filter(Character.corporation_id == corporation_id)
        .distinct()
        .all()
    )
    ids = [row[0] for row in affected]
    if ids:
        db.query(BillingEntitlementCache).filter(
            BillingEntitlementCache.account_id.in_(ids)
        ).delete(synchronize_session=False)


def _invalidate_entitlement_cache_for_alliance(db: Session, *, alliance_id: int) -> None:
    affected = (
        db.query(Character.account_id)
        .filter(Character.alliance_id == alliance_id)
        .distinct()
        .all()
    )
    ids = [row[0] for row in affected]
    if ids:
        db.query(BillingEntitlementCache).filter(
            BillingEntitlementCache.account_id.in_(ids)
        ).delete(synchronize_session=False)


# ── Grants ────────────────────────────────────────────────────────────────────

def create_grant(
    db: Session,
    *,
    account_id: int,
    scope_type: str = "global",
    scope_key: str | None = None,
    starts_at: datetime | None = None,
    expires_at: datetime | None = None,
    granted_by_account_id: int | None = None,
    note: str = "",
) -> BillingGrant:
    if starts_at is None:
        starts_at = datetime.now(UTC)
    grant = BillingGrant(
        account_id=account_id,
        scope_type=scope_type,
        scope_key=scope_key,
        starts_at=starts_at,
        expires_at=expires_at,
        granted_by_account_id=granted_by_account_id,
        note=note,
    )
    db.add(grant)
    _audit(
        db,
        event_type="grant.created",
        actor_account_id=granted_by_account_id,
        target_account_id=account_id,
        detail={"scope_type": scope_type, "scope_key": scope_key, "expires_at": expires_at.isoformat() if expires_at else None, "note": note},
    )
    _invalidate_entitlement_cache(db, account_id=account_id)
    return grant


def revoke_grant(
    db: Session,
    *,
    grant: BillingGrant,
    actor_account_id: int | None = None,
) -> None:
    grant.revoked_at = datetime.now(UTC)
    _audit(
        db,
        event_type="grant.revoked",
        actor_account_id=actor_account_id,
        target_account_id=grant.account_id,
        detail={"grant_id": grant.id, "scope_type": grant.scope_type, "scope_key": grant.scope_key},
    )
    _invalidate_entitlement_cache(db, account_id=grant.account_id)


# ── Bonus codes ───────────────────────────────────────────────────────────────

def redeem_bonus_code(
    db: Session,
    *,
    code_value: str,
    account_id: int,
) -> tuple[bool, str]:
    """
    Redeem a bonus code for an account.
    Returns (success, message).
    """
    now = datetime.now(UTC)
    code = db.query(BillingBonusCode).filter(
        BillingBonusCode.code == code_value.upper().strip()
    ).first()
    if not code:
        return False, "Unbekannter Bonus-Code."
    if not code.is_active:
        return False, "Dieser Bonus-Code ist nicht mehr aktiv."
    if code.expires_at and code.expires_at < now:
        return False, "Dieser Bonus-Code ist abgelaufen."
    if code.max_redemptions is not None and code.redemption_count >= code.max_redemptions:
        return False, "Dieser Bonus-Code hat keine verbleibenden Einlösungen."

    # Dedup: same account + same code already redeemed?
    already = db.query(BillingBonusCodeRedemption).filter(
        BillingBonusCodeRedemption.code_id == code.id,
        BillingBonusCodeRedemption.account_id == account_id,
    ).first()
    if already:
        return False, "Du hast diesen Code bereits eingelöst."

    reward_snapshot: dict = {
        "reward_type": code.reward_type,
        "reward_value": code.reward_value,
        "code": code.code,
    }

    if code.reward_type == "subscription_days":
        try:
            days = Decimal(str(code.reward_value or "").strip())
        except Exception:
            return False, "Ungültiger Code (fehlerhafter reward_value)."
        extend_subscription(
            db,
            subject_type="account",
            subject_id=account_id,
            plan_id=code.plan_id,
            days=days,
            source_type="bonus_code",
            note=f"Code {code.code}",
            actor_account_id=account_id,
        )

    elif code.reward_type in ("page_access", "feature_access", "global_access"):
        scope_type = {
            "page_access": "page",
            "feature_access": "feature",
            "global_access": "global",
        }[code.reward_type]
        scope_key = code.reward_value if scope_type != "global" else None
        days = Decimal(code.reward_value.split(":")[1]) if ":" in code.reward_value else Decimal(30)
        # For page/feature codes reward_value format: "page_key:days" or just "page_key" (30d default)
        if ":" in code.reward_value:
            scope_key, days_str = code.reward_value.split(":", 1)
            days = Decimal(days_str)
        else:
            scope_key = code.reward_value if scope_type != "global" else None
            days = Decimal(30)
        create_grant(
            db,
            account_id=account_id,
            scope_type=scope_type,
            scope_key=scope_key,
            starts_at=now,
            expires_at=now + timedelta(seconds=float(days * Decimal(86400))),
            granted_by_account_id=None,
            note=f"Code {code.code}",
        )
    else:
        return False, f"Unbekannter Reward-Typ: {code.reward_type}"

    db.add(BillingBonusCodeRedemption(
        code_id=code.id,
        account_id=account_id,
        reward_snapshot=json.dumps(reward_snapshot),
    ))
    code.redemption_count += 1
    _audit(
        db,
        event_type="bonus_code.redeemed",
        actor_account_id=account_id,
        target_account_id=account_id,
        detail={"code": code.code, "reward_type": code.reward_type, "reward_value": code.reward_value},
    )
    _invalidate_entitlement_cache(db, account_id=account_id)
    return True, "Code erfolgreich eingelöst."
