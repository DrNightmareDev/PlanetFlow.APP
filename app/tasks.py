"""Celery tasks for background ESI refresh and scheduled maintenance."""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

from celery.signals import worker_ready

from app.celery_app import celery_app

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
STALE_AFTER_MINUTES = 30        # refresh accounts whose cache is older than this
MAX_ACCOUNTS_PER_RUN = 60       # max accounts dispatched per auto_refresh run
ESI_ERROR_BACKOFF_LIMIT = 3     # pause character after this many consecutive errors
PLANET_FETCH_WORKERS = 10       # parallel ESI planet-detail threads per account

_ws_task_started = False


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
    )
    from app.routers.dashboard import (
        _compute_colony_productions, _get_colony_expiry,
        _compute_extractor_rate_summary, _compute_extractor_balance,
        _compute_factories, _compute_storage,
        _compute_missing_inputs, _get_extractor_status,
    )

    token = ensure_valid_token(char, db)
    if not token:
        logger.warning("tasks: no valid token for %s — skipping", char.character_name)
        return None

    if not esi_error_budget_ok():
        logger.warning("tasks: ESI error budget low — skipping %s", char.character_name)
        return None

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


@worker_ready.connect
def _autostart_zkill_ws(sender=None, **kwargs):
    global _ws_task_started
    if _ws_task_started or os.getenv("CELERY_WS_AUTOSTART") != "1" or sender is None:
        return
    _ws_task_started = True
    try:
        sender.app.send_task("app.tasks.zkill_websocket_subscriber", queue="ws")
        logger.info("zkill_ws: queued subscriber task on worker startup")
    except Exception:
        logger.exception("zkill_ws: failed to auto-start subscriber")
        _ws_task_started = False


@celery_app.task(name="app.tasks.zkill_websocket_subscriber", bind=True)
def zkill_websocket_subscriber(self):
    import requests
    import websocket
    from sqlalchemy import select

    from app import sde
    from app.database import SessionLocal
    from app.models import IntelKillEvent, KillActivityCache, RegionKillCache
    from app.zkill import append_intel_event_to_region_cache, normalize_kill

    ws_url = "wss://zkillboard.com/websocket/"
    subscribe_msg = json.dumps({"action": "sub", "channel": "killstream"})
    reconnect_delay = 10
    ring_buffer_max = 500

    logger.info("zkill_ws: subscriber starting")

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

    def _store_kill(payload: dict):
        killmail_id = int(payload.get("killmail_id") or payload.get("killID") or 0)
        system_id = int(payload.get("solar_system_id") or 0)
        kill_time = str(payload.get("killmail_time") or "")
        if not killmail_id or not system_id or not kill_time:
            return

        system_info = sde.get_system_local(system_id) or {}
        region_id = int(system_info.get("region_id") or 0)
        if not region_id:
            return

        normalized = normalize_kill(payload, system_name=system_info.get("name"), name_map={})
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

    while True:
        ws = None
        try:
            ws = websocket.create_connection(
                ws_url,
                timeout=30,
                header=[
                    "User-Agent: EVE-PI-Manager/1.0 github.com/DrNightmareDev/PI_Manager",
                    "Origin: https://zkillboard.com",
                ],
            )
            ws.send(subscribe_msg)
            logger.info("zkill_ws: connected and subscribed")
            while True:
                raw = ws.recv()
                if not raw:
                    break
                try:
                    payload = json.loads(raw)
                except Exception:
                    logger.debug("zkill_ws: skipped non-json frame")
                    continue
                if isinstance(payload, dict):
                    _store_kill(payload)
        except (websocket.WebSocketException, requests.RequestException, OSError):
            logger.exception("zkill_ws: connection failed, retrying in %ss", reconnect_delay)
        except Exception:
            logger.exception("zkill_ws: subscriber crashed, retrying in %ss", reconnect_delay)
        finally:
            try:
                if ws is not None:
                    ws.close()
            except Exception:
                pass
        time.sleep(reconnect_delay)
