"""Celery tasks for background ESI refresh and scheduled maintenance."""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

from app.celery_app import celery_app

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
STALE_AFTER_MINUTES = 30        # refresh accounts whose cache is older than this
MAX_ACCOUNTS_PER_RUN = 60       # max accounts dispatched per auto_refresh run
ESI_ERROR_BACKOFF_LIMIT = 3     # pause character after this many consecutive errors
PLANET_FETCH_WORKERS = 10       # parallel ESI planet-detail threads per account


# ── ESI rate limit guard ───────────────────────────────────────────────────────
# Shared across tasks in the same worker process. Celery worker_prefetch_multiplier=1
# means at most one task runs at a time per worker, so this simple counter is safe.
_esi_error_limit_remain: int = 100


def _check_esi_headers(headers: dict) -> None:
    """Read X-ESI-Error-Limit-Remain and slow down if we're running low."""
    global _esi_error_limit_remain
    remain = headers.get("X-ESI-Error-Limit-Remain")
    if remain is not None:
        try:
            _esi_error_limit_remain = int(remain)
        except ValueError:
            pass
    if _esi_error_limit_remain < 20:
        import time
        logger.warning("ESI error budget low (%d remaining) — sleeping 10s", _esi_error_limit_remain)
        time.sleep(10)


# ── Helper: build per-character colony data ───────────────────────────────────

def _refresh_character_data(char, db) -> dict | None:
    """
    Fetch ESI colony + planet data for one character.
    Returns a list of colony dicts (same format as _build_dashboard_payload),
    or None if the token is invalid or ESI is unavailable.
    """
    from app.esi import (
        ensure_valid_token, get_character_planets,
        get_planet_detail_cached, get_planet_info,
    )
    from app.routers.dashboard import (
        _compute_colony_productions, _get_colony_expiry,
        _compute_extractor_rate_summary, _compute_extractor_balance,
        _compute_extractor_status, _compute_factories, _compute_storage,
        _compute_missing_inputs, _get_extractor_status,
    )

    token = ensure_valid_token(char, db)
    if not token:
        logger.warning("tasks: no valid token for %s — skipping", char.character_name)
        return None

    try:
        raw_colonies = get_character_planets(char.eve_character_id, token)
    except Exception as exc:
        logger.warning("tasks: colony list failed for %s: %s", char.character_name, exc)
        return None

    def _fetch_planet(args):
        char_eve_id, colony, tok = args
        planet_id = colony.get("planet_id")
        info = get_planet_info(planet_id) if planet_id else {}
        detail = {}
        if tok and planet_id:
            try:
                detail = get_planet_detail_cached(char_eve_id, planet_id, tok, db)
            except Exception as e:
                logger.warning("tasks: planet %s fetch failed: %s", planet_id, e)
        return info, detail

    fetch_args = [(char.eve_character_id, colony, token) for colony in raw_colonies]
    with ThreadPoolExecutor(max_workers=PLANET_FETCH_WORKERS) as ex:
        planet_data = list(ex.map(_fetch_planet, fetch_args))

    colonies = []
    for colony, (info, detail) in zip(raw_colonies, planet_data):
        pins = detail.get("pins", [])
        productions, prod_tiers, highest_tier = _compute_colony_productions(pins)
        expiry_time = _get_colony_expiry(pins)

        now = datetime.now(timezone.utc)
        expiry_hours = None
        if expiry_time:
            delta = (expiry_time - now).total_seconds() / 3600.0
            expiry_hours = round(delta, 2)

        planet_id = colony.get("planet_id")
        planet_type = colony.get("planet_type", "unknown").capitalize()
        planet_name = info.get("name") or f"Planet {planet_id}"
        solar_system_name = info.get("solar_system_name") or ""

        colonies.append({
            "character_name": char.character_name,
            "eve_character_id": char.eve_character_id,
            "planet_id": planet_id,
            "planet_name": planet_name,
            "planet_type": planet_type,
            "solar_system_name": solar_system_name,
            "productions": productions,
            "prod_tiers": prod_tiers,
            "expiry_hours": expiry_hours,
            "expiry_time": expiry_time.isoformat() if expiry_time else None,
            "pins": pins,
            "extractor_status": _get_extractor_status(pins),
            "extractor_balance": _compute_extractor_balance(pins),
            "extractor_rate_summary": _compute_extractor_rate_summary(pins),
            "factories": _compute_factories(pins, {}),
            "storage": _compute_storage(pins),
            "missing_inputs": _compute_missing_inputs(pins),
        })

    return colonies


# ── Task: refresh one account ──────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    name="app.tasks.refresh_account_task",
)
def refresh_account_task(self, account_id: int) -> dict:
    """Refresh ESI colony data for all characters of one account and save to DB cache."""
    from app.database import SessionLocal
    from app.models import Account, Character, DashboardCache
    from app.routers.dashboard import get_prices_by_mode

    logger.info("tasks: refreshing account %d", account_id)

    with SessionLocal() as db:
        account = db.get(Account, account_id)
        if not account:
            logger.warning("tasks: account %d not found", account_id)
            return {"ok": False, "reason": "not_found"}

        characters = (
            db.query(Character)
            .filter_by(account_id=account_id)
            .all()
        )

        all_colonies: list[dict] = []
        char_errors = 0

        for char in characters:
            if not char.refresh_token:
                continue
            if char.esi_consecutive_errors >= ESI_ERROR_BACKOFF_LIMIT:
                logger.info(
                    "tasks: skipping %s — %d consecutive errors",
                    char.character_name, char.esi_consecutive_errors,
                )
                continue
            try:
                cols = _refresh_character_data(char, db)
                if cols is None:
                    char.esi_consecutive_errors += 1
                    char_errors += 1
                else:
                    all_colonies.extend(cols)
                    char.esi_consecutive_errors = 0
                    char.last_esi_refresh_at = datetime.now(timezone.utc)
            except Exception as exc:
                char.esi_consecutive_errors += 1
                char_errors += 1
                logger.warning("tasks: char %s failed: %s", char.character_name, exc)

        # Enrich with market prices
        all_product_names = {
            name for col in all_colonies for name in col.get("productions", {})
        }
        prices = get_prices_by_mode(list(all_product_names), account.price_mode, db) if all_product_names else {}

        for col in all_colonies:
            col["isk_day"] = sum(
                qty * prices.get(name, 0.0)
                for name, qty in col.get("productions", {}).items()
            )

        total_isk_day = sum(col.get("isk_day", 0.0) for col in all_colonies)

        payload_colonies = json.dumps(all_colonies)
        payload_meta = json.dumps({
            "total_isk_day": total_isk_day,
            "colony_count": len(all_colonies),
            "char_count": len(characters),
        })

        existing = db.query(DashboardCache).filter_by(account_id=account_id).first()

        if len(all_colonies) == 0 and existing:
            # ESI returned 0 colonies — keep existing colony data to avoid wiping
            # the cache on transient token failures or ESI outages. Only bump
            # fetched_at so auto-refresh doesn't immediately re-trigger.
            logger.warning(
                "tasks: account %d — ESI returned 0 colonies, keeping cached data",
                account_id,
            )
            existing.fetched_at = datetime.now(timezone.utc)
        else:
            # Save to DB cache
            if existing:
                existing.colonies_json = payload_colonies
                existing.meta_json = payload_meta
                existing.fetched_at = datetime.now(timezone.utc)
            else:
                db.add(DashboardCache(
                    account_id=account_id,
                    colonies_json=payload_colonies,
                    meta_json=payload_meta,
                    fetched_at=datetime.now(timezone.utc),
                ))

        db.commit()

    logger.info(
        "tasks: account %d refreshed — %d colonies, %d char errors",
        account_id, len(all_colonies), char_errors,
    )
    return {"ok": True, "colonies": len(all_colonies), "char_errors": char_errors}


# ── Task: auto-refresh all stale accounts ─────────────────────────────────────

@celery_app.task(name="app.tasks.auto_refresh_stale_accounts")
def auto_refresh_stale_accounts() -> dict:
    """
    Find all accounts with stale colony cache and dispatch refresh tasks.
    Priority: accounts with soonest-expiring colonies first.
    Runs every 5 minutes via Celery Beat.
    """
    from app.database import SessionLocal
    from app.models import Account, DashboardCache, Character

    now = datetime.now(timezone.utc)
    stale_threshold = now - timedelta(minutes=STALE_AFTER_MINUTES)

    with SessionLocal() as db:
        # Accounts that have at least one character with a refresh token
        active_account_ids = {
            row.account_id
            for row in db.query(Character.account_id)
            .filter(Character.refresh_token.isnot(None))
            .distinct()
            .all()
        }

        # Get cache state for all active accounts
        caches = {
            row.account_id: row
            for row in db.query(DashboardCache)
            .filter(DashboardCache.account_id.in_(active_account_ids))
            .all()
        }

        stale_account_ids = []
        for account_id in active_account_ids:
            cache = caches.get(account_id)
            if cache is None or cache.fetched_at is None:
                stale_account_ids.append((account_id, 0.0))  # never refreshed = highest prio
                continue
            fetched = cache.fetched_at
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            if fetched < stale_threshold:
                # Priority = soonest expiring colony (lower hours = higher priority)
                prio = _min_expiry_hours(cache)
                stale_account_ids.append((account_id, prio))

        # Sort: lowest expiry_hours first (most urgent), never-refreshed always first
        stale_account_ids.sort(key=lambda x: x[1])

        dispatched = 0
        for account_id, prio in stale_account_ids[:MAX_ACCOUNTS_PER_RUN]:
            refresh_account_task.delay(account_id)
            dispatched += 1

    logger.info(
        "tasks: auto_refresh — %d stale accounts found, %d dispatched",
        len(stale_account_ids), dispatched,
    )
    return {"stale": len(stale_account_ids), "dispatched": dispatched}


def _min_expiry_hours(cache) -> float:
    """Return the minimum expiry_hours across all colonies in a cache row."""
    try:
        colonies = json.loads(cache.colonies_json or "[]")
        hours = [
            c["expiry_hours"]
            for c in colonies
            if c.get("expiry_hours") is not None
        ]
        return min(hours) if hours else 9999.0
    except Exception:
        return 9999.0


# ── Task: market price refresh ────────────────────────────────────────────────

@celery_app.task(name="app.tasks.refresh_market_prices_task")
def refresh_market_prices_task() -> dict:
    """Refresh all PI market prices and update cached ISK/day values. Runs every 15 min."""
    from app.database import SessionLocal
    from app.market import refresh_all_pi_prices
    from app.routers.dashboard import refresh_dashboard_price_cache
    from app.routers.skyhook import refresh_skyhook_value_cache

    logger.info("tasks: starting market price refresh")
    with SessionLocal() as db:
        try:
            refresh_all_pi_prices(db)
            refresh_dashboard_price_cache(db)
            refresh_skyhook_value_cache(db)
            logger.info("tasks: market price refresh complete")
            return {"ok": True}
        except Exception as exc:
            logger.warning("tasks: market price refresh failed: %s", exc)
            return {"ok": False, "error": str(exc)}


# ── Task: SSO state cleanup ───────────────────────────────────────────────────

@celery_app.task(name="app.tasks.cleanup_sso_states_task")
def cleanup_sso_states_task() -> dict:
    """Delete expired SSO states (older than 1 hour). Runs every hour."""
    from app.database import SessionLocal
    from app.models import SSOState

    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as db:
        deleted = db.query(SSOState).filter(SSOState.created_at < cutoff).delete()
        db.commit()
    logger.info("tasks: cleaned up %d expired SSO states", deleted)
    return {"deleted": deleted}
