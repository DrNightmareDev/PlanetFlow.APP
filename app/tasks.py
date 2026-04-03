"""Celery tasks for background ESI refresh and scheduled maintenance."""
from __future__ import annotations

import json
import logging
import time
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
        get_planet_detail_cached, get_planet_info, esi_error_budget_ok,
        get_character_roles,
    )
    from app.routers.dashboard import (
        _compute_colony_productions, _get_colony_expiry,
        _compute_extractor_rate_summary, _compute_extractor_balance,
        _compute_factories, _compute_storage,
        _compute_missing_inputs, _get_extractor_status, _check_factory_stall,
    )
    from app.sde import get_system_local
    from app.pi_data import PLANET_TYPE_COLORS

    token = ensure_valid_token(char, db)
    if not token:
        logger.warning("tasks: no valid token for %s — skipping", char.character_name)
        return None

    if not esi_error_budget_ok():
        logger.warning("tasks: ESI error budget low — skipping %s", char.character_name)
        return None

    # Cache corp roles if scope is available
    import json as _json_roles
    ROLES_SCOPE = "esi-characters.read_corporation_roles.v1"
    if ROLES_SCOPE in (char.scopes or ""):
        try:
            roles_data = get_character_roles(char.eve_character_id, token)
            roles = roles_data.get("roles", []) if isinstance(roles_data, dict) else []
            char.corp_roles = _json_roles.dumps(roles)
        except Exception:
            pass

    try:
        raw_colonies = get_character_planets(char.eve_character_id, token)
    except Exception as exc:
        logger.warning("tasks: colony list failed for %s: %s", char.character_name, exc)
        return None

    # Pre-load ETag cache rows on the main thread (DB session is not thread-safe)
    from app.models import PlanetEsiCache
    import json as _json_tasks
    planet_ids = [c.get("planet_id") for c in raw_colonies if c.get("planet_id")]
    etag_rows: dict[tuple, PlanetEsiCache] = {}
    if planet_ids:
        for row in db.query(PlanetEsiCache).filter(
            PlanetEsiCache.eve_character_id == char.eve_character_id,
            PlanetEsiCache.planet_id.in_(planet_ids),
        ).all():
            etag_rows[(char.eve_character_id, row.planet_id)] = row

    def _fetch_planet(args):
        char_eve_id, colony, tok = args
        planet_id = colony.get("planet_id")
        info = get_planet_info(planet_id) if planet_id else {}
        detail = {}
        new_etag = None
        changed = False
        if tok and planet_id:
            try:
                existing = etag_rows.get((char_eve_id, planet_id))
                detail, new_etag, changed = get_planet_detail_cached(
                    char_eve_id, planet_id, tok,
                    etag=existing.etag if existing else None,
                    cached_json=existing.response_json if existing else None,
                )
            except Exception as e:
                logger.warning("tasks: planet %s fetch failed: %s", planet_id, e)
        return info, detail, planet_id, new_etag, changed

    fetch_args = [(char.eve_character_id, colony, token) for colony in raw_colonies]
    with ThreadPoolExecutor(max_workers=PLANET_FETCH_WORKERS) as ex:
        _raw_planet_data = list(ex.map(_fetch_planet, fetch_args))

    # Persist ETag updates on the main thread (thread-safe DB access)
    from datetime import datetime as _dt, timezone as _tz
    for _, _, planet_id, new_etag, changed in _raw_planet_data:
        if not planet_id or not changed or new_etag is None:
            continue
        existing = etag_rows.get((char.eve_character_id, planet_id))
        detail_data = next(
            (d for _, d, pid, _, _ in _raw_planet_data if pid == planet_id), {}
        )
        if existing:
            existing.etag = new_etag
            existing.response_json = _json_tasks.dumps(detail_data)
            existing.fetched_at = _dt.now(_tz.utc)
        else:
            db.add(PlanetEsiCache(
                eve_character_id=char.eve_character_id,
                planet_id=planet_id,
                etag=new_etag,
                response_json=_json_tasks.dumps(detail_data),
                fetched_at=_dt.now(_tz.utc),
            ))
    try:
        db.commit()
    except Exception as exc:
        logger.warning("tasks: ETag cache upsert failed: %s", exc)
        db.rollback()

    planet_data = [(info, detail) for info, detail, _, _, _ in _raw_planet_data]

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
        is_stalled = _check_factory_stall(pins) if expiry_time is None else None
        is_active = (expiry_hours is not None and expiry_hours > 0) if expiry_time is not None else (is_stalled is False)

        planet_id = colony.get("planet_id")
        planet_type = colony.get("planet_type", "unknown").capitalize()
        planet_name = info.get("name") or f"Planet {planet_id}"
        solar_system_id = info.get("system_id") or colony.get("solar_system_id")
        highest_tier_num = int(highest_tier[1]) if highest_tier else 0

        _sys_data = get_system_local(solar_system_id) if solar_system_id else {}
        solar_system_name = (_sys_data or {}).get("name") or ""
        region_name = (_sys_data or {}).get("region_name") or ""

        colonies.append({
            "character_name": char.character_name,
            "eve_character_id": char.eve_character_id,
            "character_portrait": char.portrait_url,
            "corporation_id": char.corporation_id,
            "corporation_name": char.corporation_name or "",
            "alliance_name": char.alliance_name or "",
            "planet_id": planet_id,
            "planet_name": planet_name,
            "planet_type": planet_type,
            "upgrade_level": colony.get("upgrade_level", 0),
            "num_pins": colony.get("num_pins", len(pins)),
            "last_update": colony.get("last_update", "—"),
            "solar_system_id": solar_system_id,
            "solar_system_name": solar_system_name,
            "region_name": region_name,
            "color": PLANET_TYPE_COLORS.get(planet_type, "#586e75"),
            "productions": productions,
            "prod_tiers": prod_tiers,
            "highest_tier": highest_tier,
            "highest_tier_num": highest_tier_num,
            "isk_day": 0.0,  # no price data in background task; dashboard router enriches if needed
            "expiry_hours": expiry_hours,
            "expiry_time": expiry_time.isoformat() if expiry_time else None,
            "expiry_iso": expiry_time.isoformat() if expiry_time else None,
            "is_stalled": is_stalled,
            "is_active": is_active,
            "vacation_mode": False,
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
        active_characters = [char for char in characters if not getattr(char, "vacation_mode", False)]
        vacation_char_names = {char.character_name for char in characters if getattr(char, "vacation_mode", False)}

        all_colonies: list[dict] = []
        char_errors = 0

        for char in active_characters:
            if not char.refresh_token:
                continue
            if char.esi_consecutive_errors >= ESI_ERROR_BACKOFF_LIMIT:
                # Auto-reset after 24 hours so characters are retried automatically
                last_refresh = char.last_esi_refresh_at
                if last_refresh is not None:
                    if last_refresh.tzinfo is None:
                        last_refresh = last_refresh.replace(tzinfo=timezone.utc)
                    hours_since = (datetime.now(timezone.utc) - last_refresh).total_seconds() / 3600.0
                    if hours_since >= 24:
                        logger.info(
                            "tasks: auto-resetting %s after %.1fh (was %d errors)",
                            char.character_name, hours_since, char.esi_consecutive_errors,
                        )
                        char.esi_consecutive_errors = 0
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
            col["vacation_mode"] = False

        if vacation_char_names:
            existing = db.query(DashboardCache).filter_by(account_id=account_id).first()
            if existing:
                try:
                    cached_colonies = json.loads(existing.colonies_json or "[]")
                except Exception:
                    cached_colonies = []
                for colony in cached_colonies:
                    if colony.get("character_name") in vacation_char_names:
                        item = dict(colony)
                        item["vacation_mode"] = True
                        all_colonies.append(item)

        total_isk_day = sum(col.get("isk_day", 0.0) for col in all_colonies if not col.get("vacation_mode"))

        payload_colonies = json.dumps(all_colonies)
        payload_meta = json.dumps({
            "total_isk_day": total_isk_day,
            "colony_count": len([col for col in all_colonies if not col.get("vacation_mode")]),
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
            # Save to DB cache — use raw upsert to avoid race condition when
            # multiple workers process the same account_id simultaneously.
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            stmt = pg_insert(DashboardCache).values(
                account_id=account_id,
                colonies_json=payload_colonies,
                meta_json=payload_meta,
                fetched_at=datetime.now(timezone.utc),
            ).on_conflict_do_update(
                index_elements=["account_id"],
                set_=dict(
                    colonies_json=payload_colonies,
                    meta_json=payload_meta,
                    fetched_at=datetime.now(timezone.utc),
                ),
            )
            db.execute(stmt)

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
            .filter(Character.refresh_token.isnot(None), Character.vacation_mode == False)
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
    except Exception as exc:
        logger.warning("_min_expiry_hours: failed to parse value: %s", exc)
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


# ── Task: webhook / Discord expiry alerts ─────────────────────────────────────

@celery_app.task(name="app.tasks.send_webhook_alerts_task")
def send_webhook_alerts_task() -> dict:
    """Send Discord/webhook alerts for colonies expiring within the configured threshold.

    Runs every 15 minutes via Celery Beat. Each account is alerted at most once
    per threshold window to avoid notification spam.
    """
    import requests as _requests
    from app.database import SessionLocal
    from app.models import Account, Character, DashboardCache, WebhookAlert

    now = datetime.now(timezone.utc)
    sent = 0
    skipped = 0

    with SessionLocal() as db:
        alerts = (
            db.query(WebhookAlert)
            .filter(WebhookAlert.enabled == True, WebhookAlert.webhook_url.isnot(None))  # noqa: E712
            .all()
        )

        for alert in alerts:
            if not alert.webhook_url:
                continue
            # SSRF guard — only call known Discord webhook endpoints
            _safe_prefixes = (
                "https://discord.com/api/webhooks/",
                "https://discordapp.com/api/webhooks/",
                "https://ptb.discord.com/api/webhooks/",
                "https://canary.discord.com/api/webhooks/",
            )
            if not any(alert.webhook_url.startswith(p) for p in _safe_prefixes):
                logger.warning("tasks: skipping webhook alert — unsafe URL for account %d", alert.account_id)
                continue

            threshold_hours = float(alert.alert_hours or 2)

            # Cooldown: don't re-alert within threshold window
            if alert.last_alert_at:
                last = alert.last_alert_at
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (now - last).total_seconds() < threshold_hours * 3600 * 0.9:
                    skipped += 1
                    continue

            cache = db.query(DashboardCache).filter_by(account_id=alert.account_id).first()
            if not cache:
                continue

            try:
                colonies = json.loads(cache.colonies_json or "[]")
            except Exception:
                continue

            vacation_names = {
                row.character_name
                for row in db.query(Character.character_name)
                .filter(Character.account_id == alert.account_id, Character.vacation_mode == True)
                .all()
            }

            expiring = [
                c for c in colonies
                if c.get("character_name") not in vacation_names
                if c.get("expiry_hours") is not None
                and 0 < c["expiry_hours"] <= threshold_hours
            ]
            if not expiring:
                continue

            # Build Discord-compatible embed message
            lines = []
            for c in sorted(expiring, key=lambda x: x.get("expiry_hours", 9999)):
                h = c["expiry_hours"]
                hh = int(h)
                mm = int((h - hh) * 60)
                name = c.get("planet_name") or f"Planet {c.get('planet_id')}"
                char = c.get("character_name", "?")
                lines.append(f"⏰ **{name}** ({char}) — {hh}h {mm}m")

            message = (
                f"🚨 **{len(expiring)} PI colony/colonies expiring within {threshold_hours:.0f}h**\n"
                + "\n".join(lines[:10])
                + ("\n…and more" if len(expiring) > 10 else "")
            )

            try:
                resp = _requests.post(
                    alert.webhook_url,
                    json={"content": message},
                    timeout=10,
                )
                if resp.status_code in (200, 204):
                    alert.last_alert_at = now
                    sent += 1
                elif resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 5))
                    logger.warning(
                        "tasks: Discord rate-limited account %d — Retry-After %ds",
                        alert.account_id, retry_after,
                    )
                    import time as _t
                    _t.sleep(min(retry_after, 30))
                else:
                    logger.warning("tasks: webhook alert returned %d for account %d", resp.status_code, alert.account_id)
            except Exception as exc:
                logger.warning("tasks: webhook alert failed for account %d: %s", alert.account_id, exc)

        db.commit()

    logger.info("tasks: webhook alerts — %d sent, %d skipped (cooldown)", sent, skipped)
    return {"sent": sent, "skipped": skipped}


# ── Billing tasks ─────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.sync_billing_wallets")
def sync_billing_wallets() -> dict:
    """
    Fetch wallet journal entries from ESI for all active BillingWalletReceiver characters.
    Stores raw transactions, deduplicating on the ESI journal_id (used as PK).
    Runs every 3 minutes.
    """
    import requests
    from decimal import Decimal
    from app.database import SessionLocal
    from app.models import BillingWalletReceiver, BillingWalletTransaction, Character

    imported = 0
    errors = 0

    with SessionLocal() as db:
        receivers = db.query(BillingWalletReceiver).filter(
            BillingWalletReceiver.is_active == True
        ).all()

        for receiver in receivers:
            # Find a character with a valid refresh_token for this receiver
            char = None
            if receiver.character_fk:
                char = db.get(Character, receiver.character_fk)
            if not char:
                char = db.query(Character).filter(
                    Character.eve_character_id == receiver.eve_character_id
                ).first()
            if not char or not char.refresh_token:
                logger.warning("billing: no token for receiver eve_char_id=%s", receiver.eve_character_id)
                errors += 1
                continue

            # Verify required receiver scopes are present
            _WALLET_SCOPE = "esi-wallet.read_character_wallet.v1"
            _MAIL_SCOPE = "esi-mail.send_mail.v1"
            missing_scopes = []
            if not char.scopes or _WALLET_SCOPE not in char.scopes:
                missing_scopes.append(_WALLET_SCOPE)
            if not char.scopes or _MAIL_SCOPE not in char.scopes:
                missing_scopes.append(_MAIL_SCOPE)
            if missing_scopes:
                logger.warning(
                    "billing: char %s (%s) is missing required scopes %s — re-login required",
                    char.eve_character_id, char.character_name, ",".join(missing_scopes),
                )
                errors += 1
                continue

            # Refresh access token if needed
            try:
                from app.esi import ensure_valid_token
                token = ensure_valid_token(char, db)
            except Exception as exc:
                logger.warning("billing: token refresh failed for char %s: %s", char.eve_character_id, exc)
                errors += 1
                continue

            # Fetch wallet journal from ESI
            try:
                url = f"https://esi.evetech.net/latest/characters/{char.eve_character_id}/wallet/journal/"
                resp = requests.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=15,
                )
                resp.raise_for_status()
                entries = resp.json()
            except Exception as exc:
                logger.warning("billing: ESI wallet fetch failed for char %s: %s", char.eve_character_id, exc)
                errors += 1
                continue

            for entry in entries:
                journal_id = entry.get("id")
                if not journal_id:
                    continue
                # Dedup: skip if already imported
                if db.get(BillingWalletTransaction, journal_id):
                    continue
                ref_type = entry.get("ref_type", "")
                # Only process donation types relevant for billing
                if ref_type not in ("player_donation", "corporation_account_withdrawal"):
                    continue
                amount_raw = entry.get("amount", 0)
                amount_isk = int(Decimal(str(amount_raw)).to_integral_value())
                if amount_isk <= 0:
                    continue
                first_id = entry.get("first_party_id")
                second_id = entry.get("second_party_id")
                first_name = entry.get("first_party_name")
                second_name = entry.get("second_party_name")
                first_type = str(entry.get("first_party_type") or "").lower()
                second_type = str(entry.get("second_party_type") or "").lower()

                def _pick_counterparty(expected_type: str | None = None):
                    candidates = [
                        (first_id, first_name, first_type),
                        (second_id, second_name, second_type),
                    ]
                    # Prefer explicit type match when ESI provides it.
                    if expected_type:
                        for pid, pname, ptype in candidates:
                            if pid and ptype == expected_type and int(pid) != int(receiver.eve_character_id):
                                return pid, pname
                    # Fallback: whichever party is not the receiver itself.
                    for pid, pname, _ptype in candidates:
                        if pid and int(pid) != int(receiver.eve_character_id):
                            return pid, pname
                    # Last fallback.
                    return first_id, first_name

                sender_char_id = None
                sender_char_name = None
                sender_corp_id = None
                if ref_type == "player_donation":
                    sender_char_id, sender_char_name = _pick_counterparty("character")
                elif ref_type == "corporation_account_withdrawal":
                    sender_corp_id, _ = _pick_counterparty("corporation")

                tx = BillingWalletTransaction(
                    id=journal_id,
                    receiver_id=receiver.id,
                    ref_type=ref_type,
                    sender_character_id=sender_char_id,
                    sender_character_name=sender_char_name,
                    sender_corporation_id=sender_corp_id,
                    amount_isk=amount_isk,
                    description=entry.get("description", "")[:1024],
                    occurred_at=datetime.fromisoformat(
                        entry["date"].replace("Z", "+00:00")
                    ) if entry.get("date") else datetime.now(timezone.utc),
                )
                db.add(tx)
                imported += 1

        db.commit()

    logger.info("billing: sync_billing_wallets imported=%d errors=%d", imported, errors)
    return {"imported": imported, "errors": errors}


@celery_app.task(name="app.tasks.match_billing_transactions")
def match_billing_transactions() -> dict:
    """
    Match unmatched wallet transactions to accounts/corps/alliances.
    Runs every 3 minutes after wallet sync.
    """
    from app.database import SessionLocal
    from app.esi import ensure_valid_token, send_character_mail
    from app.models import (
        BillingSubscriptionJoinCode,
        BillingSubscriptionPeriod,
        BillingTransactionMatch,
        BillingWalletReceiver,
        BillingWalletTransaction,
        Character,
    )
    from app.services.billing import create_subscription_join_code, match_wallet_transaction

    matched = 0
    unmatched = 0
    join_codes_created = 0
    join_code_mails_sent = 0

    with SessionLocal() as db:
        # Find transactions with no match record yet OR previously unmatched (retryable)
        matched_ids = db.query(BillingTransactionMatch.transaction_id).subquery()
        unmatched_ids = (
            db.query(BillingTransactionMatch.transaction_id)
            .filter(BillingTransactionMatch.match_status == "unmatched")
            .subquery()
        )
        pending = (
            db.query(BillingWalletTransaction)
            .filter(
                (BillingWalletTransaction.id.notin_(matched_ids))
                | (BillingWalletTransaction.id.in_(unmatched_ids))
            )
            .order_by(BillingWalletTransaction.occurred_at.asc())
            .all()
        )
        for tx in pending:
            success, msg = match_wallet_transaction(db, transaction_id=tx.id)
            if success:
                matched += 1
                match_row = db.query(BillingTransactionMatch).filter(
                    BillingTransactionMatch.transaction_id == tx.id,
                    BillingTransactionMatch.match_status == "matched",
                ).first()
                if match_row and match_row.subject_type in ("corporation", "alliance"):
                    existing_join_code = db.query(BillingSubscriptionJoinCode).filter(
                        BillingSubscriptionJoinCode.source_transaction_id == tx.id
                    ).first()
                    if not existing_join_code:
                        period = db.query(BillingSubscriptionPeriod).filter(
                            BillingSubscriptionPeriod.subject_type == match_row.subject_type,
                            BillingSubscriptionPeriod.subject_id == match_row.subject_id,
                            BillingSubscriptionPeriod.source_type == "payment",
                            BillingSubscriptionPeriod.note == f"Wallet tx {tx.id}",
                        ).order_by(BillingSubscriptionPeriod.id.desc()).first()
                        if period:
                            join_code = create_subscription_join_code(
                                db,
                                subject_type=match_row.subject_type,
                                subject_id=int(match_row.subject_id),
                                source_period_id=period.id,
                                source_transaction_id=tx.id,
                                issued_by_receiver_id=tx.receiver_id,
                                target_character_id=tx.sender_character_id,
                                expires_at=period.ends_at,
                                max_redemptions=None,
                                note=f"Auto-issued from wallet transaction {tx.id}",
                            )
                            join_codes_created += 1

                            receiver = db.get(BillingWalletReceiver, tx.receiver_id)
                            sender_char = None
                            if receiver:
                                if receiver.character_fk:
                                    sender_char = db.get(Character, receiver.character_fk)
                                if not sender_char:
                                    sender_char = db.query(Character).filter(
                                        Character.eve_character_id == receiver.eve_character_id
                                    ).first()
                            if sender_char and tx.sender_character_id:
                                has_mail_scope = "esi-mail.send_mail.v1" in (sender_char.scopes or "")
                                if has_mail_scope:
                                    token = ensure_valid_token(sender_char, db)
                                    if token:
                                        try:
                                            send_character_mail(
                                                sender_char.eve_character_id,
                                                token,
                                                recipient_character_id=int(tx.sender_character_id),
                                                subject=f"PlanetFlow Join Code ({match_row.subject_type})",
                                                body=(
                                                    f"Your payment was matched to {match_row.subject_type} {match_row.subject_id}.\n\n"
                                                    f"Join code: {join_code.code}\n"
                                                    f"Valid until: {period.ends_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                                                    "Only characters from the same corporation/alliance can redeem this code in PlanetFlow billing."
                                                ),
                                            )
                                            join_code_mails_sent += 1
                                        except Exception as exc:
                                            logger.warning("billing: failed to send join-code mail for tx %s: %s", tx.id, exc)
            else:
                unmatched += 1
        db.commit()

    logger.info(
        "billing: match_billing_transactions matched=%d unmatched=%d join_codes_created=%d mails_sent=%d",
        matched, unmatched, join_codes_created, join_code_mails_sent,
    )
    return {
        "matched": matched,
        "unmatched": unmatched,
        "join_codes_created": join_codes_created,
        "join_code_mails_sent": join_code_mails_sent,
    }


@celery_app.task(name="app.tasks.recompute_entitlements")
def recompute_entitlements() -> dict:
    """
    Recompute and cache entitlements for all accounts.
    Runs every 5 minutes. Also triggered on demand after billing events.
    """
    from app.database import SessionLocal
    from app.models import Account
    from app.services.entitlements import recompute_and_cache

    updated = 0
    errors = 0

    with SessionLocal() as db:
        accounts = db.query(Account).all()
        for account in accounts:
            try:
                recompute_and_cache(db, account=account)
                updated += 1
            except Exception as exc:
                logger.warning("billing: entitlement recompute failed for account %s: %s", account.id, exc)
                errors += 1
        db.commit()

    logger.info("billing: recompute_entitlements updated=%d errors=%d", updated, errors)
    return {"updated": updated, "errors": errors}


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


@celery_app.task(name="app.tasks.zkill_websocket_subscriber", bind=True)
def zkill_websocket_subscriber(self):
    from sqlalchemy import select

    from app import sde
    from app.database import SessionLocal
    from app.models import IntelKillEvent, IntelStreamState, KillActivityCache, RegionKillCache
    from app.zkill import append_intel_event_to_region_cache, normalize_kill

    import requests

    base_url = "https://r2z2.zkillboard.com/ephemeral"
    sequence_url = f"{base_url}/sequence.json"
    stream_key = "r2z2"
    max_sequences_per_run = 25
    ring_buffer_max = 500

    logger.debug("intel_live: R2Z2 poll tick starting")

    def _prune_ring_buffer(db):
        total = db.query(IntelKillEvent).count()
        overflow = total - ring_buffer_max
        if overflow <= 0:
            return
        oldest_ids = [
            row_id
            for (row_id,) in db.query(IntelKillEvent.id)
            .order_by(IntelKillEvent.created_at.asc(), IntelKillEvent.id.asc())
            .limit(overflow)
            .all()
        ]
        if oldest_ids:
            db.query(IntelKillEvent).filter(IntelKillEvent.id.in_(oldest_ids)).delete(synchronize_session=False)

    def _load_stream_sequence(db) -> int | None:
        state = db.get(IntelStreamState, stream_key)
        if state and state.last_sequence_id is not None:
            return int(state.last_sequence_id)
        return None

    def _store_stream_state(last_sequence_id: int | None = None, last_error: str | None = None):
        db = SessionLocal()
        try:
            state = db.get(IntelStreamState, stream_key)
            if state is None:
                state = IntelStreamState(stream_key=stream_key)
                db.add(state)
            if last_sequence_id is not None:
                state.last_sequence_id = int(last_sequence_id)
                state.last_success_at = datetime.now(timezone.utc)
            if last_error is not None:
                state.last_error = str(last_error)[:255]
            db.commit()
        except Exception:
            logger.exception("intel_live: failed to persist stream state")
            db.rollback()
        finally:
            db.close()

    def _fetch_start_sequence() -> int | None:
        try:
            response = requests.get(
                sequence_url,
                headers={"User-Agent": "EVE-PI-Manager/1.0 github.com/DrNightmareDev/PI_Manager"},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            sequence_id = int(payload.get("sequence") or 0)
            return sequence_id or None
        except Exception as exc:
            logger.exception("intel_live: failed to fetch R2Z2 sequence: %s", exc)
            _store_stream_state(last_error=f"sequence fetch failed: {exc}")
            return None

    def _store_kill(payload: dict):
        esi_payload = payload.get("esi") if isinstance(payload.get("esi"), dict) else None
        kill_payload = dict(esi_payload or payload or {})
        if payload.get("killmail_id") and not kill_payload.get("killmail_id"):
            kill_payload["killmail_id"] = payload.get("killmail_id")
        if payload.get("hash") and not kill_payload.get("hash"):
            kill_payload["hash"] = payload.get("hash")
        if payload.get("zkb") and not kill_payload.get("zkb"):
            kill_payload["zkb"] = payload.get("zkb")
        if payload.get("sequence_id") and not kill_payload.get("sequence_id"):
            kill_payload["sequence_id"] = payload.get("sequence_id")
        if payload.get("uploaded_at") and not kill_payload.get("uploaded_at"):
            kill_payload["uploaded_at"] = payload.get("uploaded_at")

        killmail_id = int(kill_payload.get("killmail_id") or kill_payload.get("killID") or 0)
        system_id = int(kill_payload.get("solar_system_id") or 0)
        kill_time = str(kill_payload.get("killmail_time") or "")
        if not killmail_id or not system_id or not kill_time:
            logger.warning(
                "intel_live: skipping R2Z2 payload without required fields (keys=%s)",
                sorted(list(payload.keys()))[:20] if isinstance(payload, dict) else type(payload).__name__,
            )
            return

        system_info = sde.get_system_local(system_id) or {}
        region_id = int(system_info.get("region_id") or 0)
        if not region_id:
            return

        normalized = normalize_kill(kill_payload, system_name=system_info.get("name"), name_map={})
        normalized_json = json.dumps(normalized)
        now_utc = datetime.now(timezone.utc)

        db = SessionLocal()
        try:
            if db.query(IntelKillEvent).filter_by(killmail_id=killmail_id).first():
                return

            db.add(IntelKillEvent(
                killmail_id=killmail_id,
                region_id=region_id,
                solar_system_id=system_id,
                killmail_time=kill_time,
                kill_json=normalized_json,
                created_at=now_utc,
            ))

            cache_row = db.get(KillActivityCache, system_id)
            if cache_row is None:
                db.add(KillActivityCache(
                    system_id=system_id,
                    kill_count=1,
                    latest_kills_json=json.dumps([normalized]),
                    window="ws",
                    fetched_at=now_utc,
                ))
            else:
                cache_row.kill_count = int(cache_row.kill_count or 0) + 1
                try:
                    latest_kills = json.loads(cache_row.latest_kills_json or "[]")
                except Exception:
                    latest_kills = []
                latest_kills = [item for item in latest_kills if int(item.get("killmail_id") or 0) != killmail_id]
                latest_kills.insert(0, normalized)
                cache_row.latest_kills_json = json.dumps(latest_kills[:10])
                cache_row.fetched_at = now_utc

            append_intel_event_to_region_cache(db, region_id, normalized)
            for row in db.scalars(select(RegionKillCache).where(RegionKillCache.region_id == region_id)).all():
                row.fetched_at = datetime(2000, 1, 1, tzinfo=timezone.utc)

            _prune_ring_buffer(db)
            db.commit()
        except Exception:
            logger.exception("zkill_ws: failed to persist kill %s", killmail_id)
            db.rollback()
        finally:
            db.close()

    db = SessionLocal()
    try:
        sequence_id = _load_stream_sequence(db)
    finally:
        db.close()
    if sequence_id is None:
        sequence_id = _fetch_start_sequence()

    try:
        latest_sequence = _fetch_start_sequence()
        if latest_sequence is None:
            return {"ok": False, "reason": "sequence_unavailable"}

        if sequence_id is None:
            sequence_id = int(latest_sequence)

        processed = 0
        current_sequence = int(sequence_id)
        max_target_sequence = min(int(latest_sequence), current_sequence + max_sequences_per_run - 1)

        while current_sequence <= max_target_sequence:
            response = requests.get(
                f"{base_url}/{int(current_sequence)}.json",
                headers={"User-Agent": "EVE-PI-Manager/1.0 github.com/DrNightmareDev/PI_Manager"},
                timeout=20,
            )
            if response.status_code == 404:
                break
            if response.status_code == 429:
                logger.warning("intel_live: R2Z2 rate limited at sequence %s", current_sequence)
                _store_stream_state(last_error=f"rate limited at {current_sequence}")
                return {"ok": False, "reason": "rate_limited", "sequence_id": current_sequence}
            if response.status_code == 403:
                logger.warning("intel_live: R2Z2 returned 403 at sequence %s", current_sequence)
                _store_stream_state(last_error=f"forbidden at {current_sequence}")
                return {"ok": False, "reason": "forbidden", "sequence_id": current_sequence}
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                _store_kill(payload)
                processed_sequence = int(payload.get("sequence_id") or current_sequence)
            else:
                processed_sequence = int(current_sequence)
            _store_stream_state(last_sequence_id=processed_sequence, last_error="")
            processed += 1
            current_sequence = processed_sequence + 1

        return {
            "ok": True,
            "processed": processed,
            "last_sequence_id": int(current_sequence - 1) if processed else int(sequence_id),
            "latest_sequence_id": int(latest_sequence),
        }
    except Exception:
        logger.exception("intel_live: R2Z2 poller failed at sequence %s", sequence_id)
        _store_stream_state(last_error=f"poller failure at {sequence_id}")
        return {"ok": False, "reason": "exception", "sequence_id": sequence_id}
