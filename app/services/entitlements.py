"""
Entitlement resolution for planetflow.app.

Priority chain (highest wins):
  1. account.is_owner  → unrestricted access
  2. account.is_admin  → unrestricted access
  3. Active BillingGrant (global / page-scoped / feature-scoped)
  4. Active BillingSubscriptionPeriod for the account directly
  5. Active BillingSubscriptionPeriod for the account's corporation
  6. Active BillingSubscriptionPeriod for the account's alliance
  7. Deny

The entitlement cache (billing_entitlement_cache) is the single source of truth
used by the middleware and templates. It is recomputed by the Celery task
`recompute_entitlements`. The middleware never runs live entitlement resolution.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import (
    Account,
    BillingEntitlementCache,
    BillingGrant,
    BillingSubscriptionPeriod,
    Character,
    PageAccessSetting,
)
from app.page_access import PAGE_DEFINITIONS

logger = logging.getLogger(__name__)

# The access_level value that triggers entitlement check
PAID_ACCESS_LEVEL = "paid"


# ── Low-level active-record checks ───────────────────────────────────────────

def _has_active_period(
    db: Session,
    *,
    subject_type: str,
    subject_id: int,
) -> bool:
    now = datetime.now(UTC)
    return db.query(
        db.query(BillingSubscriptionPeriod)
        .filter(
            BillingSubscriptionPeriod.subject_type == subject_type,
            BillingSubscriptionPeriod.subject_id == subject_id,
            BillingSubscriptionPeriod.starts_at <= now,
            BillingSubscriptionPeriod.ends_at >= now,
        )
        .exists()
    ).scalar() or False


def _has_active_grant(
    db: Session,
    *,
    account_id: int,
    scope_type: str,
    scope_key: str | None = None,
) -> bool:
    """Check for an active, non-revoked grant matching the scope."""
    now = datetime.now(UTC)
    q = db.query(BillingGrant).filter(
        BillingGrant.account_id == account_id,
        BillingGrant.revoked_at.is_(None),
        BillingGrant.starts_at <= now,
        (BillingGrant.expires_at.is_(None)) | (BillingGrant.expires_at >= now),
    )
    if scope_type == "global":
        # Global grant covers everything
        q = q.filter(BillingGrant.scope_type == "global")
    else:
        # Match global OR specific scope
        q = q.filter(
            (BillingGrant.scope_type == "global") |
            (
                (BillingGrant.scope_type == scope_type) &
                (BillingGrant.scope_key == scope_key)
            )
        )
    return db.query(q.exists()).scalar() or False


# ── Per-page entitlement resolution ──────────────────────────────────────────

def _resolve_page_entitlement(
    db: Session,
    *,
    account: Account,
    page_key: str,
    access_level: str,
) -> bool:
    """
    Return True if account has access to this page given its access_level.
    Only called when access_level == "paid" — other levels are handled by page_access.py.
    """
    if account.is_owner or account.is_admin:
        return True

    # Global grant covers all pages
    if _has_active_grant(db, account_id=account.id, scope_type="global"):
        return True

    # Page-specific grant
    if _has_active_grant(db, account_id=account.id, scope_type="page", scope_key=page_key):
        return True

    # Individual account subscription
    if _has_active_period(db, subject_type="account", subject_id=account.id):
        return True

    # Corporation subscription — any character's corp
    corp_ids = set(
        cid for (cid,) in
        db.query(Character.corporation_id)
        .filter(Character.account_id == account.id, Character.corporation_id.isnot(None))
        .distinct()
        .all()
    )
    for corp_id in corp_ids:
        if _has_active_period(db, subject_type="corporation", subject_id=corp_id):
            return True

    # Alliance subscription — any character's alliance
    alliance_ids = set(
        aid for (aid,) in
        db.query(Character.alliance_id)
        .filter(Character.account_id == account.id, Character.alliance_id.isnot(None))
        .distinct()
        .all()
    )
    for alliance_id in alliance_ids:
        if _has_active_period(db, subject_type="alliance", subject_id=alliance_id):
            return True

    return False


# ── Cache computation ─────────────────────────────────────────────────────────

def compute_entitlements_for_account(db: Session, *, account: Account) -> dict:
    """
    Compute the full entitlement map for one account.
    Returns {"pages": {page_key: bool}, "features": {feature_key: bool}}.
    Owners and admins get blanket True for all pages.
    """
    settings_map: dict[str, str] = {
        row.page_key: row.access_level
        for row in db.query(PageAccessSetting).all()
    }

    pages: dict[str, bool] = {}
    for page in PAGE_DEFINITIONS:
        level = settings_map.get(page.key, page.default_access)
        if level == "none":
            pages[page.key] = False
        elif account.is_owner or account.is_admin:
            pages[page.key] = True
        elif level == PAID_ACCESS_LEVEL:
            pages[page.key] = _resolve_page_entitlement(
                db, account=account, page_key=page.key, access_level=level
            )
        else:
            # member / manager / admin handled by existing page_access logic — not our concern
            pages[page.key] = True  # page_access middleware handles role gating

    # Features: currently no dynamic feature settings, placeholder for future paid features
    features: dict[str, bool] = {}

    return {"pages": pages, "features": features}


def recompute_and_cache(db: Session, *, account: Account) -> BillingEntitlementCache:
    """Recompute entitlements and write to cache. Returns the cache row."""
    result = compute_entitlements_for_account(db, account=account)
    cache = db.query(BillingEntitlementCache).filter(
        BillingEntitlementCache.account_id == account.id
    ).first()
    now = datetime.now(UTC)
    if cache:
        cache.pages_json = json.dumps(result["pages"])
        cache.features_json = json.dumps(result["features"])
        cache.computed_at = now
    else:
        cache = BillingEntitlementCache(
            account_id=account.id,
            pages_json=json.dumps(result["pages"]),
            features_json=json.dumps(result["features"]),
            computed_at=now,
        )
        db.add(cache)
    return cache


def get_cached_page_entitlements(db: Session, *, account_id: int) -> dict[str, bool] | None:
    """
    Read the cached page entitlement map.
    Returns None if no cache entry exists (caller should fall back to live resolution or deny).
    """
    row = db.query(BillingEntitlementCache).filter(
        BillingEntitlementCache.account_id == account_id
    ).first()
    if not row:
        return None
    try:
        return json.loads(row.pages_json)
    except Exception:
        return None


def get_cached_feature_entitlements(db: Session, *, account_id: int) -> dict[str, bool]:
    """Read the cached feature entitlement map. Returns empty dict on miss."""
    row = db.query(BillingEntitlementCache).filter(
        BillingEntitlementCache.account_id == account_id
    ).first()
    if not row:
        return {}
    try:
        return json.loads(row.features_json)
    except Exception:
        return {}


def has_feature_access(db: Session, *, account_id: int, feature_key: str) -> bool:
    """
    Check if an account has access to a specific feature key.
    Used inside page handlers/templates for paid sub-page features.
    Falls back to live grant check if cache is missing.
    """
    cached = get_cached_feature_entitlements(db, account_id=account_id)
    if feature_key in cached:
        return cached[feature_key]
    # Cache miss: live check (grants only, no subscription check for features yet)
    return _has_active_grant(db, account_id=account_id, scope_type="feature", scope_key=feature_key)
