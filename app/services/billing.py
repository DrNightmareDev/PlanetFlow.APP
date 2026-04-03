"""
Billing service — wallet ingestion, transaction matching, subscription management,
grant handling, and bonus code redemption.

All ISK values are stored and computed as Decimal with zero decimal places (integers).
Never use float for ISK amounts.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
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
    BillingSubscriptionJoinCode,
    BillingSubscriptionJoinRedemption,
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
    source_code_id: int | None = None,
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
        source_code_id=source_code_id,
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


def _contains_reason_keyword(description: str | None, keyword: str) -> bool:
    text = (description or "").upper()
    return keyword.upper() in text


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

    existing_match = db.query(BillingTransactionMatch).filter(
        BillingTransactionMatch.transaction_id == transaction_id
    ).first()
    # Allow retry for previously unmatched transactions.
    if existing_match:
        if existing_match.match_status == "unmatched":
            db.delete(existing_match)
            db.flush()
        else:
            return False, "Transaction already matched."

    receiver = db.get(BillingWalletReceiver, tx.receiver_id)
    if not receiver or not receiver.is_active:
        return False, "Receiver wallet is inactive or not found."

    amount = Decimal(tx.amount_isk)
    if amount <= 0:
        return False, "Transaction amount is zero or negative."

    # ── Individual: player donation ───────────────────────────────────────────
    if tx.ref_type == "player_donation":
        receiver = db.get(BillingWalletReceiver, tx.receiver_id)

        def _char_from_name(name: str | None) -> Character | None:
            if not name:
                return None
            return db.query(Character).filter(
                Character.character_name.ilike(str(name).strip())
            ).order_by(Character.last_login.desc().nullslast()).first()

        def _extract_name_from_description(description: str | None) -> str | None:
            text = (description or "").strip()
            if not text:
                return None
            patterns = [
                r"^(.+?)\s+deposited cash into\s+.+?account",
                r"^(.+?)\s+deposited cash into\s+your account",
            ]
            for pattern in patterns:
                m = re.search(pattern, text, flags=re.IGNORECASE)
                if m:
                    return (m.group(1) or "").strip() or None
            return None

        char = None
        if tx.sender_character_id:
            char = db.query(Character).filter(
                Character.eve_character_id == tx.sender_character_id
            ).first()
            if receiver and char and int(char.eve_character_id) == int(receiver.eve_character_id):
                # Receiver side was parsed as sender — fall back to name-based matching.
                char = None

        if char is None:
            char = _char_from_name(tx.sender_character_name)
        if char is None:
            parsed_name = _extract_name_from_description(tx.description)
            char = _char_from_name(parsed_name)

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
                        notes=f"Player donation from char {char.eve_character_id}",
                    ))
                    _invalidate_entitlement_cache(db, account_id=account.id)
                    return True, f"Matched to account {account.id} ({days} days)."

    # ── Corporation: corporation_account_withdrawal ───────────────────────────
    if tx.ref_type == "corporation_account_withdrawal" and tx.sender_corporation_id:
        corp_id = tx.sender_corporation_id
        wants_corp = _contains_reason_keyword(tx.description, "CORP")
        wants_alliance = _contains_reason_keyword(tx.description, "ALLIANCE")

        if not wants_corp and not wants_alliance:
            db.add(BillingTransactionMatch(
                transaction_id=tx.id,
                subject_type="unknown",
                subject_id=0,
                plan_id=None,
                days_granted=Decimal(0),
                match_status="unmatched",
                notes='Reason missing required keyword: use "CORP" or "ALLIANCE".',
            ))
            return False, 'Reason must contain "CORP" or "ALLIANCE".'

        if wants_alliance:
            sample_char = db.query(Character).filter(
                Character.corporation_id == corp_id,
                Character.alliance_id.isnot(None),
            ).first()
            if sample_char and sample_char.alliance_id:
                alliance_id = int(sample_char.alliance_id)
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
                            notes=f'Alliance payment via corp {corp_id} (reason contains "ALLIANCE")',
                        ))
                        _invalidate_entitlement_cache_for_alliance(db, alliance_id=alliance_id)
                        return True, f"Matched to alliance {alliance_id} ({days} days)."

        if wants_corp:
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
                        notes=f'Corp withdrawal from corp {corp_id} (reason contains "CORP")',
                    ))
                    _invalidate_entitlement_cache_for_corp(db, corporation_id=corp_id)
                    return True, f"Matched to corporation {corp_id} ({days} days)."

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


def invalidate_subject_entitlements(db: Session, *, subject_type: str, subject_id: int) -> None:
    if subject_type == "account":
        _invalidate_entitlement_cache(db, account_id=int(subject_id))
    elif subject_type == "corporation":
        _invalidate_entitlement_cache_for_corp(db, corporation_id=int(subject_id))
    elif subject_type == "alliance":
        _invalidate_entitlement_cache_for_alliance(db, alliance_id=int(subject_id))


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
    source_code_id: int | None = None,
) -> BillingGrant:
    if starts_at is None:
        starts_at = datetime.now(UTC)
    grant = BillingGrant(
        account_id=account_id,
        scope_type=scope_type,
        scope_key=scope_key,
        starts_at=starts_at,
        expires_at=expires_at,
        source_code_id=source_code_id,
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
            source_code_id=code.id,
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
            source_code_id=code.id,
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


def revoke_bonus_code(
    db: Session,
    *,
    code: BillingBonusCode,
    actor_account_id: int | None = None,
) -> dict:
    """
    Revoke a bonus code and remove all previously granted benefits from that code.
    Returns counters for admin feedback.
    """
    now = datetime.now(UTC)
    code.is_active = False
    if code.expires_at is None or code.expires_at > now:
        code.expires_at = now

    affected_accounts: set[int] = set()

    redemptions = db.query(BillingBonusCodeRedemption).filter(
        BillingBonusCodeRedemption.code_id == code.id
    ).all()
    redeemed_account_ids = {int(r.account_id) for r in redemptions}
    code_note = f"Code {code.code}"

    grants = db.query(BillingGrant).filter(
        BillingGrant.revoked_at.is_(None),
        (
            (BillingGrant.source_code_id == code.id) |
            (
                BillingGrant.source_code_id.is_(None) &
                (BillingGrant.note == code_note) &
                (BillingGrant.account_id.in_(list(redeemed_account_ids) or [-1]))
            )
        ),
    ).all()
    for grant in grants:
        grant.revoked_at = now
        affected_accounts.add(grant.account_id)

    periods = db.query(BillingSubscriptionPeriod).filter(
        BillingSubscriptionPeriod.ends_at > now,
        (
            (BillingSubscriptionPeriod.source_code_id == code.id) |
            (
                BillingSubscriptionPeriod.source_code_id.is_(None) &
                (BillingSubscriptionPeriod.source_type == "bonus_code") &
                (BillingSubscriptionPeriod.note == code_note)
            )
        ),
    ).all()
    for period in periods:
        period.ends_at = now
        if period.subject_type == "account":
            affected_accounts.add(int(period.subject_id))
        elif period.subject_type == "corporation":
            _invalidate_entitlement_cache_for_corp(db, corporation_id=int(period.subject_id))
        elif period.subject_type == "alliance":
            _invalidate_entitlement_cache_for_alliance(db, alliance_id=int(period.subject_id))

    for account_id in affected_accounts:
        _invalidate_entitlement_cache(db, account_id=account_id)

    _audit(
        db,
        event_type="bonus_code.revoked",
        actor_account_id=actor_account_id,
        detail={
            "code_id": code.id,
            "code": code.code,
            "revoked_grants": len(grants),
            "ended_periods": len(periods),
            "redemption_rows": len(redemptions),
            "affected_accounts": sorted(affected_accounts),
        },
    )
    return {
        "revoked_grants": len(grants),
        "ended_periods": len(periods),
        "affected_accounts": len(affected_accounts),
    }


def create_subscription_join_code(
    db: Session,
    *,
    subject_type: str,
    subject_id: int,
    source_period_id: int | None = None,
    source_transaction_id: int | None = None,
    issued_by_receiver_id: int | None = None,
    target_character_id: int | None = None,
    expires_at: datetime | None = None,
    max_redemptions: int | None = None,
    note: str = "",
    actor_account_id: int | None = None,
) -> BillingSubscriptionJoinCode:
    if subject_type not in ("corporation", "alliance"):
        raise ValueError("subject_type must be corporation or alliance")

    code_value = secrets.token_urlsafe(9).replace("-", "").replace("_", "").upper()
    code = BillingSubscriptionJoinCode(
        code=code_value,
        subject_type=subject_type,
        subject_id=int(subject_id),
        source_period_id=source_period_id,
        source_transaction_id=source_transaction_id,
        issued_by_receiver_id=issued_by_receiver_id,
        target_character_id=target_character_id,
        max_redemptions=max_redemptions,
        expires_at=expires_at,
        note=note or None,
    )
    db.add(code)
    db.flush()
    _audit(
        db,
        event_type="subscription.join_code.created",
        actor_account_id=actor_account_id,
        detail={
            "join_code_id": code.id,
            "subject_type": subject_type,
            "subject_id": int(subject_id),
            "source_transaction_id": source_transaction_id,
            "target_character_id": target_character_id,
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )
    return code


def redeem_subscription_join_code(
    db: Session,
    *,
    code_value: str,
    account_id: int,
) -> tuple[bool, str]:
    now = datetime.now(UTC)
    normalized = (code_value or "").strip().upper()
    if not normalized:
        return False, "Join-Code fehlt."

    code = db.query(BillingSubscriptionJoinCode).filter(
        BillingSubscriptionJoinCode.code == normalized
    ).first()
    if not code:
        return False, "Join-Code nicht gefunden."
    if code.revoked_at is not None:
        return False, "Dieser Join-Code wurde widerrufen."
    if code.expires_at and code.expires_at <= now:
        return False, "Dieser Join-Code ist abgelaufen."
    if code.max_redemptions is not None and code.redemption_count >= code.max_redemptions:
        return False, "Dieser Join-Code hat keine verbleibenden Einlösungen."

    already = db.query(BillingSubscriptionJoinRedemption).filter(
        BillingSubscriptionJoinRedemption.code_id == code.id,
        BillingSubscriptionJoinRedemption.account_id == account_id,
    ).first()
    if already:
        return False, "Du hast diesen Join-Code bereits genutzt."

    chars = db.query(Character).filter(Character.account_id == account_id).all()
    if code.subject_type == "corporation":
        eligible = any(int(c.corporation_id or 0) == int(code.subject_id) for c in chars)
        if not eligible:
            return False, "Join-Code nur für Mitglieder der passenden Corporation."
    else:
        eligible = any(int(c.alliance_id or 0) == int(code.subject_id) for c in chars)
        if not eligible:
            return False, "Join-Code nur für Mitglieder der passenden Alliance."

    source_period = None
    if code.source_period_id:
        source_period = db.get(BillingSubscriptionPeriod, code.source_period_id)
    if not source_period or source_period.ends_at <= now:
        return False, "Die zugrunde liegende Subscription ist nicht mehr aktiv."

    remaining_days = Decimal(
        str(max((source_period.ends_at - now).total_seconds(), 0) / 86400)
    ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    if remaining_days <= 0:
        return False, "Keine verbleibende Laufzeit vorhanden."

    extend_subscription(
        db,
        subject_type="account",
        subject_id=account_id,
        plan_id=source_period.plan_id,
        days=remaining_days,
        source_type="manual_grant",
        note=f"Join code {code.code} ({code.subject_type}:{code.subject_id})",
        actor_account_id=account_id,
    )
    db.add(BillingSubscriptionJoinRedemption(
        code_id=code.id,
        account_id=account_id,
    ))
    code.redemption_count += 1
    _invalidate_entitlement_cache(db, account_id=account_id)
    _audit(
        db,
        event_type="subscription.join_code.redeemed",
        actor_account_id=account_id,
        target_account_id=account_id,
        detail={
            "join_code_id": code.id,
            "subject_type": code.subject_type,
            "subject_id": int(code.subject_id),
            "remaining_days": str(remaining_days),
        },
    )
    return True, "Join-Code erfolgreich eingelöst."
