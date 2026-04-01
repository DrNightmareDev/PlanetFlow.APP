import json as _json
import logging
import threading as _threading
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from math import ceil
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Body
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_account
from app.esi import ensure_valid_token, get_character_planets, get_planet_detail, get_planet_info, get_schematic, invalidate_planet_detail_cache, get_character_roles, get_character_skills, get_corporation_info
from app.i18n import get_language_from_request, translate_type_name
from app.market import get_prices_by_mode, get_market_last_updated, PI_TYPE_IDS
from app.models import Account, Character, DashboardCache, IskSnapshot, MarketCache, SkyhookEntry, SkyhookItem
from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import joinedload as _joinedload
from app.pi_data import PLANET_TYPE_COLORS, ALL_P1, ALL_P2, ALL_P3, ALL_P4
from app import sde as _sde
from app.templates_env import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Allowed webhook hostnames — prevents SSRF by ensuring the server only calls
# known external services, not internal IPs or arbitrary hosts.
_ALLOWED_WEBHOOK_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
    "https://ptb.discord.com/api/webhooks/",
    "https://canary.discord.com/api/webhooks/",
)

def _is_safe_webhook_url(url: str) -> bool:
    return any(url.startswith(p) for p in _ALLOWED_WEBHOOK_PREFIXES)

DASHBOARD_PAGE_SIZES: tuple[int, ...] = (6, 25, 100, 0)
DASHBOARD_PAGE_WINDOW_RADIUS = 2

PI_SKILL_NAMES = (
    "Command Center Upgrades",
    "Interplanetary Consolidation",
    "Remote Sensing",
    "Planetology",
    "Advanced Planetology",
)

PLANET_TYPE_NAMES = {
    "temperate": "Temperate",
    "barren": "Barren",
    "oceanic": "Oceanic",
    "ice": "Ice",
    "gas": "Gas",
    "lava": "Lava",
    "storm": "Storm",
    "plasma": "Plasma",
}

# ── Cache ──────────────────────────────────────────────────────────────────────
_dashboard_cache: dict[int, dict] = {}    # account_id -> {fetched_at, payload}
_refresh_cooldown: dict[int, float] = {}  # account_id -> timestamp of last force refresh
_bg_refresh_running: dict[int, bool] = {} # account_id -> True wenn Hintergrund-Refresh läuft
_bg_refresh_done: dict[int, bool] = {}    # account_id -> True wenn gerade fertig geworden
_bg_refresh_kicked_at: dict[int, float] = {}  # account_id -> timestamp when Celery task was dispatched


_corp_load_running: dict[int, dict] = {}  # corp_id -> lock/status
_CORP_LOAD_LOCK_TTL = 60 * 30
_CORP_RECENT_CACHE_SECONDS = 60 * 10

_corp_view_cache: dict[int, tuple[float, dict]] = {}  # corp_id -> (timestamp, data)
_CORP_VIEW_CACHE_TTL = 300.0  # 5 minutes

# Cache corp access flags (CEO/director check) per account to avoid ESI on every request
_corp_access_cache: dict[int, tuple[float, dict]] = {}  # account_id -> (ts, flags)
_CORP_ACCESS_CACHE_TTL = 300.0  # 5 minutes


def _get_corp_load_lock(corp_id: int | None) -> dict | None:
    if not corp_id:
        return None
    now = _time.time()
    # Sweep expired entries to prevent unbounded growth
    expired = [k for k, v in _corp_load_running.items()
               if (now - float(v.get("started_at") or 0.0)) > _CORP_LOAD_LOCK_TTL]
    for k in expired:
        _corp_load_running.pop(k, None)
    lock = _corp_load_running.get(corp_id)
    if not lock:
        return None
    return lock


def _invalidate_corp_view_cache_for_account(account_id: int, db: Session) -> None:
    """Corp-View-Cache für die Korporation dieses Accounts invalidieren."""
    try:
        char = db.query(Character).filter(Character.account_id == account_id).first()
        if char and char.corporation_id:
            _corp_view_cache.pop(int(char.corporation_id), None)
    except Exception:
        pass


def invalidate_dashboard_cache(account_id: int) -> None:
    """Cache für einen Account sofort verwerfen (in-memory + DB)."""
    _dashboard_cache.pop(account_id, None)


def _touch_colony_cache(account_id: int, db: Session) -> None:
    """Nur fetched_at aktualisieren ohne Kolonie-Daten zu überschreiben."""
    try:
        row = db.query(DashboardCache).filter(DashboardCache.account_id == account_id).first()
        if row:
            row.fetched_at = datetime.now(timezone.utc)
        else:
            # Create an explicit empty cache row so the dashboard can leave the
            # initial loading state even when ESI temporarily returns 0 colonies.
            db.add(DashboardCache(
                account_id=account_id,
                colonies_json="[]",
                meta_json="{}",
                fetched_at=datetime.now(timezone.utc),
            ))
        db.commit()
    except Exception as e:
        logger.warning(f"Colony-Cache touch error: {e}")
        db.rollback()


def _save_colony_cache(account_id: int, payload: dict, db: Session) -> None:
    """Speichert Colony-ESI-Daten persistent in DB."""
    _hydrate_price_cache(payload, db)
    next_expiry = payload.get("next_expiry")
    meta = {
        "char_count": payload.get("char_count", 0),
        "colony_count": payload.get("colony_count", 0),
        "total_isk_day": payload.get("total_isk_day", 0.0),
        "total_isk_day_modes": payload.get("total_isk_day_modes", {}),
        "next_expiry_iso": next_expiry.isoformat() if next_expiry else None,
        "next_expiry_char": payload.get("next_expiry_char"),
    }
    try:
        colonies_json = _json.dumps(payload["colonies"], default=str)
        meta_json = _json.dumps(meta)
    except Exception as e:
        logger.warning(f"Colony-Cache serialize error: {e}")
        return
    try:
        row = db.query(DashboardCache).filter(DashboardCache.account_id == account_id).first()
        if row:
            row.colonies_json = colonies_json
            row.meta_json = meta_json
            row.fetched_at = datetime.now(timezone.utc)
        else:
            db.add(DashboardCache(
                account_id=account_id,
                colonies_json=colonies_json,
                meta_json=meta_json,
                fetched_at=datetime.now(timezone.utc),
            ))
        db.commit()
    except Exception as e:
        logger.warning(f"Colony-Cache save error: {e}")
        db.rollback()
    _invalidate_corp_view_cache_for_account(account_id, db)


def _load_colony_cache(account_id: int, db: Session) -> dict | None:
    """Lädt Colony-Cache aus DB. Returns dict mit colonies/meta/fetched_at oder None."""
    try:
        row = db.query(DashboardCache).filter(DashboardCache.account_id == account_id).first()
        if not row:
            return None
        return {
            "colonies": _json.loads(row.colonies_json),
            "meta": _json.loads(row.meta_json),
            "fetched_at": row.fetched_at.timestamp() if row.fetched_at else 0.0,
        }
    except Exception as e:
        logger.warning(f"Colony-Cache load error: {e}")
        return None


def _recompute_expiry(colonies: list) -> tuple[datetime | None, str | None]:
    """Aktualisiert expiry_hours aus expiry_iso (relativ zur jetzigen Zeit)."""
    next_expiry: datetime | None = None
    next_expiry_char: str | None = None
    for c in colonies:
        expiry_iso = c.get("expiry_iso")
        if expiry_iso:
            expiry_dt = _parse_expiry(expiry_iso)
            if expiry_dt:
                c["expiry_hours"] = _hours_until(expiry_dt)
                c["is_active"] = c["expiry_hours"] is not None and c["expiry_hours"] > 0
                if next_expiry is None or expiry_dt < next_expiry:
                    next_expiry = expiry_dt
                    next_expiry_char = c.get("character_name")
    return next_expiry, next_expiry_char


def _recompute_isk(colonies: list, price_mode: str, db: Session) -> tuple[list, float]:
    """Recomputes isk_day für jede Kolonie aus DB-gecachten Preisen (schnell!)."""
    all_names = {name for c in colonies for name in (c.get("productions") or {})}
    prices = get_prices_by_mode(list(all_names), price_mode, db) if all_names else {}
    total = 0.0
    for c in colonies:
        prods = c.get("productions") or {}
        tiers = c.get("prod_tiers") or {}
        max_tier = c.get("highest_tier_num") or 0
        c["isk_day"] = sum(
            qty * prices.get(name, 0.0)
            for name, qty in prods.items()
            if tiers.get(name, 0) == max_tier
        )
        if c.get("is_active"):
            total += c["isk_day"]
    return colonies, total


def _compute_total_isk(colonies: list) -> float:
    total = 0.0
    for colony in colonies:
        if colony.get("is_active"):
            total += float(colony.get("isk_day", 0.0) or 0.0)
    return total


def _hydrate_price_cache(payload: dict, db: Session) -> dict:
    """Schreibt ISK/Tag fuer sell/buy/split in den Payload."""
    colonies = payload.get("colonies") or []
    all_names = {name for colony in colonies for name in (colony.get("productions") or {})}
    price_maps = {
        mode: (get_prices_by_mode(list(all_names), mode, db) if all_names else {})
        for mode in ("sell", "buy", "split")
    }
    totals = {"sell": 0.0, "buy": 0.0, "split": 0.0}
    for colony in colonies:
        prods = colony.get("productions") or {}
        tiers = colony.get("prod_tiers") or {}
        max_tier = colony.get("highest_tier_num") or 0
        isk_day_modes = {}
        for mode, prices in price_maps.items():
            isk_day = sum(
                qty * prices.get(name, 0.0)
                for name, qty in prods.items()
                if tiers.get(name, 0) == max_tier
            )
            isk_day_modes[mode] = isk_day
            if colony.get("is_active"):
                totals[mode] += isk_day
        colony["isk_day_modes"] = isk_day_modes
    payload["total_isk_day_modes"] = totals
    payload["total_isk_day"] = totals.get(payload.get("price_mode", "sell"), 0.0)
    return payload


def _apply_price_mode(colonies: list, meta: dict, price_mode: str) -> tuple[list, float]:
    """Setzt isk_day aus dem persistenten Preis-Cache fuer den gewaehlten Modus."""
    totals = meta.get("total_isk_day_modes") or {}
    total = float(totals.get(price_mode, 0.0) or 0.0)
    missing_mode = False
    for colony in colonies:
        modes = colony.get("isk_day_modes") or {}
        if price_mode not in modes:
            missing_mode = True
            colony["isk_day"] = float(colony.get("isk_day", 0.0) or 0.0)
        else:
            colony["isk_day"] = float(modes.get(price_mode, 0.0) or 0.0)
    if missing_mode:
        total = _compute_total_isk(colonies)
    return colonies, total


def refresh_dashboard_price_cache(db: Session, account_ids: list[int] | None = None) -> None:
    """Aktualisiert persistierte Dashboard-Werte aus dem Markt-DB-Cache."""
    query = db.query(DashboardCache)
    if account_ids:
        query = query.filter(DashboardCache.account_id.in_(account_ids))
    for row in query.all():
        try:
            colonies = _json.loads(row.colonies_json or "[]")
            meta = _json.loads(row.meta_json or "{}")
            payload = {"colonies": colonies, "meta": meta, "price_mode": "sell"}
            _hydrate_price_cache(payload, db)
            meta["total_isk_day_modes"] = payload.get("total_isk_day_modes", {})
            meta["total_isk_day"] = payload.get("total_isk_day", 0.0)
            row.colonies_json = _json.dumps(payload["colonies"], default=str)
            row.meta_json = _json.dumps(meta)
        except Exception as e:
            logger.warning(f"Dashboard-Preiscache fuer Account {row.account_id} fehlgeschlagen: {e}")
    db.commit()


def _start_bg_refresh(account_id: int, char_ids: list[int], price_mode: str) -> None:
    """Startet ESI-Refresh im Hintergrund-Thread. Aktualisiert DB-Cache wenn fertig."""
    if account_id in _bg_refresh_running:
        return
    _bg_refresh_running[account_id] = True
    _bg_refresh_done[account_id] = False

    def _worker():
        from app.database import SessionLocal
        newdb = SessionLocal()
        finished = False
        try:
            account = newdb.query(Account).filter(Account.id == account_id).first()
            chars = newdb.query(Character).filter(Character.id.in_(char_ids)).all()
            active_chars = [char for char in chars if not getattr(char, "vacation_mode", False)]
            if not account or not chars:
                finished = True
                return
            pm = getattr(account, "price_mode", "sell")
            payload = _build_dashboard_payload(account, chars, newdb, price_mode=pm)
            colony_count = payload.get("colony_count", 0)
            if colony_count > 0:
                _save_colony_cache(account_id, payload, newdb)
                _dashboard_cache[account_id] = {**payload, "price_mode": pm}
                # Reset error counter for chars that synced successfully
                for char in active_chars:
                    if (char.esi_consecutive_errors or 0) > 0:
                        char.esi_consecutive_errors = 0
                newdb.commit()
                logger.info("BG-Refresh account %d: %d Kolonien gespeichert", account_id, colony_count)
            else:
                # ESI returned 0 – keep existing colony data, only bump timestamp to
                # prevent the next page load from immediately triggering another refresh.
                _touch_colony_cache(account_id, newdb)
                logger.warning(f"BG-Refresh account {account_id}: ESI lieferte 0 Kolonien – Timestamp aktualisiert, Kolonie-Daten unverändert")
            finished = True
        except Exception as e:
            logger.error(f"BG-Refresh account {account_id} fehlgeschlagen: {e}")
            finished = True
        finally:
            newdb.close()
            _bg_refresh_running.pop(account_id, None)
            if finished:
                _bg_refresh_done[account_id] = True

    _threading.Thread(target=_worker, daemon=True).start()


def _kick_bg_refresh(account, characters: list) -> None:
    """Dispatch a background refresh via Celery (preferred) or in-process thread."""
    import os
    if os.getenv("CELERY_BROKER_URL"):
        try:
            from app.tasks import refresh_account_task
            refresh_account_task.delay(account.id)
            _bg_refresh_running[account.id] = True
            _bg_refresh_done[account.id] = False
            _bg_refresh_kicked_at[account.id] = _time.time()
            logger.info("dashboard: dispatched Celery refresh for account %d", account.id)
            return
        except Exception as exc:
            logger.warning("dashboard: Celery dispatch failed, falling back to thread: %s", exc)
    char_ids = [c.id for c in characters]
    price_mode = getattr(account, "price_mode", "sell")
    _start_bg_refresh(account.id, char_ids, price_mode)


DASHBOARD_CACHE_TTL = 600.0   # 10 Minuten (entspricht ESI-Cache von CCP)
REFRESH_COOLDOWN_SEC = 60.0   # 60 Sekunden

# ── Helpers ────────────────────────────────────────────────────────────────────

_CYCLE_QTY_FALLBACK: dict[int, int] = {1800: 20, 3600: 5, 9000: 3}


def _parse_expiry(expiry_str: str) -> datetime | None:
    if not expiry_str:
        return None
    try:
        return datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
    except Exception:
        return None


def _hours_until(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - now).total_seconds() / 3600.0


# Launchpad + Storage Facility type IDs je Planetentyp (aus SDE verifiziert)
_STORAGE_TYPE_IDS: dict[int, tuple[str, float]] = {
    # Launchpads (10.000 m³)
    2543: ("Launchpad", 10_000.0),  # Gas
    2544: ("Launchpad", 10_000.0),  # Barren
    2542: ("Launchpad", 10_000.0),  # Oceanic
    2552: ("Launchpad", 10_000.0),  # Ice
    2555: ("Launchpad", 10_000.0),  # Lava
    2556: ("Launchpad", 10_000.0),  # Plasma
    2557: ("Launchpad", 10_000.0),  # Storm
    2256: ("Launchpad", 10_000.0),  # Temperate
    # Storage Facilities (12.000 m³)
    2257: ("Storage", 12_000.0),     # Ice
    2535: ("Storage", 12_000.0),     # Oceanic
    2536: ("Storage", 12_000.0),     # Gas
    2541: ("Storage", 12_000.0),     # Barren
    2558: ("Storage", 12_000.0),     # Lava
    2560: ("Storage", 12_000.0),     # Plasma
    2561: ("Storage", 12_000.0),     # Storm
    2562: ("Storage", 12_000.0),     # Temperate
}

# PI Produktvolumen m³ nach Tier
_PI_VOLUMES: dict[str, float] = {}  # befüllt lazy

# PI Produktname → Tier-Nummer (0–4), befüllt lazy
_PRODUCT_TIERS: dict[str, int] = {}

def _get_product_tiers() -> dict[str, int]:
    global _PRODUCT_TIERS
    if _PRODUCT_TIERS:
        return _PRODUCT_TIERS
    from app.pi_data import P0_TO_P1, P1_TO_P2, P2_TO_P3, P3_TO_P4
    t: dict[str, int] = {}
    for n in P0_TO_P1:           t[n] = 0  # P0 Rohstoffe
    for n in P0_TO_P1.values():  t[n] = 1  # P1
    for n in P1_TO_P2:           t[n] = 2  # P2 (keys = P2-Outputs)
    for n in P2_TO_P3:           t[n] = 3  # P3
    for n in P3_TO_P4:           t[n] = 4  # P4
    _PRODUCT_TIERS = t
    return t


def _tier_from_schematic(schematic: dict) -> int:
    """Tier (1–4) des Schematic-Outputs via Produktname (cycle_time ist kein verlässlicher Indikator
    da P2/P3/P4 alle 3600s haben). Fallback auf cycle_time nur für P1."""
    name = schematic.get("schematic_name", "")
    tier = _get_product_tiers().get(name, 0)
    if tier > 0:
        return tier
    # Fallback: P1 hat 1800s, alles andere ≥ 2
    return 1 if schematic.get("cycle_time", 0) <= 1800 else 2

def _get_pi_volumes() -> dict[str, float]:
    global _PI_VOLUMES
    if _PI_VOLUMES:
        return _PI_VOLUMES
    from app.pi_data import P0_TO_P1, P1_TO_P2, P2_TO_P3, P3_TO_P4
    vols: dict[str, float] = {}
    for n in P0_TO_P1:           vols[n] = 0.005  # P0
    for n in P0_TO_P1.values():  vols[n] = 0.19   # P1
    for n in P1_TO_P2:           vols[n] = 0.75   # P2
    for n in P2_TO_P3:           vols[n] = 3.0    # P3
    for n in P3_TO_P4:           vols[n] = 50.0   # P4
    _PI_VOLUMES = vols
    return vols


def _compute_storage(pins: list) -> list[dict]:
    """Gibt Lagerstatus je Storage/Launchpad Pin zurück."""
    from app.sde import get_type_name
    vols = _get_pi_volumes()
    result = []
    type_counters: dict[int, int] = {}

    for pin in pins:
        type_id = pin.get("type_id")
        if type_id not in _STORAGE_TYPE_IDS:
            continue
        struct_label, capacity = _STORAGE_TYPE_IDS[type_id]
        struct_name = get_type_name(type_id) or struct_label
        type_counters[type_id] = type_counters.get(type_id, 0) + 1
        contents_raw = pin.get("contents") or []
        items = []
        used_m3 = 0.0
        for c in contents_raw:
            name = get_type_name(c["type_id"]) or f"Type {c['type_id']}"
            amt  = c.get("amount", 0)
            vol  = vols.get(name, 0.01) * amt
            used_m3 += vol
            items.append({"name": name, "amount": amt, "volume": round(vol, 1)})
        result.append({
            "struct": struct_name,
            "struct_num": type_counters[type_id],
            "type_id": type_id,
            "capacity": capacity,
            "used_m3": round(used_m3, 1),
            "fill_pct": round(min(used_m3 / capacity * 100, 100), 1) if capacity else 0,
            "items": sorted(items, key=lambda x: x["volume"], reverse=True),
        })
    # Nummer nur anzeigen wenn mehrere des gleichen Typs
    type_totals = type_counters
    for entry in result:
        entry["label"] = entry["struct"] + (f" {entry['struct_num']}" if type_totals.get(entry["type_id"], 1) > 1 else "")
    return result


def _compute_storage_value(storage: list[dict], price_mode: str, db: Session) -> float:
    total = 0.0
    for entry in storage or []:
        for item in entry.get("items") or []:
            product_name = item.get("name")
            if not product_name:
                continue
            type_id = PI_TYPE_IDS.get(product_name)
            if not type_id:
                continue
            row = db.get(MarketCache, int(type_id))
            if not row:
                continue
            price = float(getattr(row, "best_sell" if price_mode == "sell" else "best_buy") or 0.0)
            total += price * float(item.get("amount") or 0.0)
    return total


def _cached_vacation_colonies(account_id: int, characters: list[Character], db: Session) -> list[dict]:
    vacation_names = {char.character_name for char in characters if getattr(char, "vacation_mode", False)}
    if not vacation_names:
        return []
    cached = _load_colony_cache(account_id, db) or {}
    result = []
    for colony in cached.get("colonies") or []:
        if colony.get("character_name") in vacation_names:
            item = dict(colony)
            item["vacation_mode"] = True
            result.append(item)
    return result


def _normalize_dashboard_colony(colony: dict) -> dict:
    item = dict(colony or {})
    planet_type = item.get("planet_type") or "Unknown"
    item["planet_type"] = planet_type
    planet_name = item.get("planet_name")
    if not planet_name:
        planet_id = item.get("planet_id")
        planet_name = f"Planet {planet_id}" if planet_id else "Unknown planet"
    item["planet_name"] = planet_name
    item["character_name"] = item.get("character_name") or "Unknown character"
    item["color"] = item.get("color") or PLANET_TYPE_COLORS.get(planet_type, "#586e75")
    item["factories"] = item.get("factories") or []
    item["storage"] = item.get("storage") or []
    item["extractor_status"] = item.get("extractor_status") or {}
    if "extractor_balance" not in item:
        item["extractor_balance"] = None
    if "extractor_rate_summary" not in item:
        item["extractor_rate_summary"] = None
    item["missing_inputs"] = item.get("missing_inputs") or []
    return item


def _compute_missing_inputs(pins: list) -> list[dict]:
    """Materialien die täglich importiert werden müssen (nicht lokal produziert)."""
    from app.sde import get_type_name
    local_products: set[str] = set()
    daily_consumption: dict[str, float] = {}

    for pin in pins:
        factory = pin.get("factory_details") or {}
        schematic_id = factory.get("schematic_id") or pin.get("schematic_id")
        if not schematic_id:
            continue
        try:
            schematic = get_schematic(int(schematic_id))
        except Exception:
            continue
        cycle_time = schematic.get("cycle_time", 0)
        product_name = schematic.get("schematic_name", "")
        if not cycle_time or not product_name:
            continue
        local_products.add(product_name)
        cycles_per_day = 86400.0 / float(cycle_time)
        for type_id, qty in schematic.get("input_type_ids", {}).items():
            name = get_type_name(type_id) or f"Type {type_id}"
            daily_consumption[name] = daily_consumption.get(name, 0.0) + qty * cycles_per_day

    result = []
    for material, qty_day in sorted(daily_consumption.items(), key=lambda x: -x[1]):
        if material not in local_products:
            result.append({"name": material, "qty_per_day": round(qty_day)})
    return result


def _get_extractor_status(pins: list) -> dict:
    """Zählt Extraktoren und abgelaufene."""
    now = datetime.now(timezone.utc)
    total = 0
    expired = 0
    for pin in pins:
        if pin.get("extractor_details") is None:
            continue
        total += 1
        exp_str = pin.get("expiry_time", "")
        exp_dt  = _parse_expiry(exp_str) if exp_str else None
        if exp_dt is None or exp_dt <= now:
            expired += 1
    return {"total": total, "expired": expired}


def _compute_extractor_balance(pins: list) -> dict | None:
    """Liefert Balance-Daten fuer genau zwei laufende Extraktoren auf einem Planeten."""
    from app.sde import get_type_name

    now = datetime.now(timezone.utc)
    running: list[dict] = []

    for pin in pins:
        details = pin.get("extractor_details") or {}
        if not details:
            continue

        exp_dt = _parse_expiry(pin.get("expiry_time", ""))
        if exp_dt is None or exp_dt <= now:
            continue

        cycle_time = float(details.get("cycle_time") or 0)
        product_type_id = details.get("product_type_id")
        product_name = get_type_name(product_type_id) if product_type_id else None
        qty_per_cycle = float(
            details.get("qty_per_cycle")
            or details.get("quantity_per_cycle")
            or details.get("output_per_cycle")
            or 0
        )
        install_dt = _parse_expiry(pin.get("install_time", "")) or _parse_expiry(pin.get("last_cycle_start", ""))
        program_cycles = None
        total_output = None
        if cycle_time > 0 and install_dt is not None and exp_dt > install_dt:
            duration_seconds = (exp_dt - install_dt).total_seconds()
            program_cycles = max(int(round(duration_seconds / cycle_time)), 1)
            if qty_per_cycle > 0:
                total_output = qty_per_cycle * program_cycles

        avg_per_hour = qty_per_cycle * (3600.0 / cycle_time) if cycle_time > 0 and qty_per_cycle > 0 else 0.0
        running.append({
            "name": product_name or "Unknown",
            "qty_per_cycle": round(qty_per_cycle, 1) if qty_per_cycle else 0.0,
            "cycle_time_minutes": round(cycle_time / 60.0, 1) if cycle_time else 0.0,
            "program_cycles": program_cycles,
            "total_output": round(total_output, 1) if total_output is not None else None,
            "avg_per_hour": round(avg_per_hour, 1),
            "expiry_iso": exp_dt.isoformat(),
            "expiry_hours": _hours_until(exp_dt),
        })

    if len(running) != 2 or any((entry.get("avg_per_hour") or 0) <= 0 for entry in running):
        return None

    running.sort(key=lambda entry: entry["name"].lower())
    first, second = running
    diff_per_hour = abs(first["avg_per_hour"] - second["avg_per_hour"])
    base = max(first["avg_per_hour"], second["avg_per_hour"], 1.0)
    diff_pct = (diff_per_hour / base) * 100.0

    return {
        "extractors": running,
        "diff_per_hour": round(diff_per_hour, 1),
        "diff_pct": round(diff_pct, 2),
    }


def _compute_extractor_rate_summary(pins: list) -> dict | None:
    """Liefert Kennzahlen fuer laufende Extraktoren eines Planeten."""
    from app.sde import get_type_name

    now = datetime.now(timezone.utc)
    rates: list[float] = []
    extractors: list[dict] = []

    for pin in pins:
        details = pin.get("extractor_details") or {}
        if not details:
            continue

        exp_dt = _parse_expiry(pin.get("expiry_time", ""))
        if exp_dt is None or exp_dt <= now:
            continue

        cycle_time = float(details.get("cycle_time") or 0)
        qty_per_cycle = float(
            details.get("qty_per_cycle")
            or details.get("quantity_per_cycle")
            or details.get("output_per_cycle")
            or 0
        )
        product_type_id = details.get("product_type_id")
        product_name = get_type_name(product_type_id) if product_type_id else None
        install_dt = _parse_expiry(pin.get("install_time", "")) or _parse_expiry(pin.get("last_cycle_start", ""))
        program_cycles = None
        total_output = None
        if cycle_time > 0 and install_dt is not None and exp_dt > install_dt:
            duration_seconds = (exp_dt - install_dt).total_seconds()
            program_cycles = max(int(round(duration_seconds / cycle_time)), 1)
            if qty_per_cycle > 0:
                total_output = qty_per_cycle * program_cycles

        avg_per_hour = qty_per_cycle * (3600.0 / cycle_time) if cycle_time > 0 and qty_per_cycle > 0 else 0.0
        if avg_per_hour > 0:
            rounded_avg = round(avg_per_hour, 1)
            rates.append(rounded_avg)
            extractors.append({
                "name": product_name or "Unknown",
                "qty_per_cycle": round(qty_per_cycle, 1) if qty_per_cycle else 0.0,
                "cycle_time_minutes": round(cycle_time / 60.0, 1) if cycle_time else 0.0,
                "program_cycles": program_cycles,
                "total_output": round(total_output, 1) if total_output is not None else None,
                "avg_per_hour": rounded_avg,
                "expiry_iso": exp_dt.isoformat(),
                "expiry_hours": _hours_until(exp_dt),
            })

    if not rates:
        return None

    return {
        "count": len(rates),
        "min_avg_per_hour": min(rates),
        "max_avg_per_hour": max(rates),
        "total_avg_per_hour": round(sum(rates), 1),
        "extractors": extractors,
    }


def _compute_colony_productions(pins: list) -> tuple[dict[str, float], dict[str, int], str | None]:
    """Gibt (productions, prod_tiers, highest_tier_label) zurück.
    prod_tiers: product_name -> tier_num (1–4).
    Fallback: wenn keine Fabrik konfiguriert ist, wird der höchste Tier aus den
    Storage/Launchpad-Inhalten ermittelt.
    """
    from app.sde import get_type_name
    productions: dict[str, float] = {}
    prod_tiers: dict[str, int] = {}
    highest_tier_num = 0
    for pin in pins:
        factory = pin.get("factory_details") or {}
        schematic_id = factory.get("schematic_id") or pin.get("schematic_id")
        if not schematic_id:
            continue
        try:
            schematic = get_schematic(int(schematic_id))
        except Exception:
            continue
        cycle_time = schematic.get("cycle_time", 0)
        product_name = schematic.get("schematic_name", "")
        if not cycle_time or not product_name:
            continue
        tier_num = _tier_from_schematic(schematic)
        highest_tier_num = max(highest_tier_num, tier_num)
        qty_per_cycle = schematic.get("output_quantity") or _CYCLE_QTY_FALLBACK.get(cycle_time, 1)
        productions[product_name] = (
            productions.get(product_name, 0.0) + qty_per_cycle * (86400.0 / float(cycle_time))
        )
        prod_tiers[product_name] = tier_num

    # Fallback: keine Fabrik konfiguriert → höchsten Tier aus Storage/Launchpad-Inhalten ableiten
    if highest_tier_num == 0:
        product_tiers = _get_product_tiers()
        for pin in pins:
            for item in (pin.get("contents") or []):
                name = get_type_name(item.get("type_id")) or ""
                t = product_tiers.get(name, -1)
                if t > highest_tier_num:
                    highest_tier_num = t

    return productions, prod_tiers, (f"P{highest_tier_num}" if highest_tier_num > 0 else None)


def _get_colony_expiry(pins: list) -> datetime | None:
    expiry: datetime | None = None
    for pin in pins:
        if pin.get("extractor_details") is None:
            continue
        exp_dt = _parse_expiry(pin.get("expiry_time", ""))
        if exp_dt is not None and (expiry is None or exp_dt < expiry):
            expiry = exp_dt
    return expiry


def _check_factory_stall(pins: list) -> bool | None:
    """Für factory-only Planeten (keine Extractors): prüft ob höchste-Tier Fabriken laufen.
    Returns True = stalled, False = läuft, None = kein reiner Factory-Planet."""
    from datetime import timedelta
    has_extractors = any(pin.get("extractor_details") is not None for pin in pins)
    if has_extractors:
        return None

    now = datetime.now(timezone.utc)
    factory_pins: list[tuple[dict, int, int]] = []  # (pin, tier_num, cycle_time)
    for pin in pins:
        factory = pin.get("factory_details") or {}
        schematic_id = factory.get("schematic_id") or pin.get("schematic_id")
        if not schematic_id:
            continue
        try:
            schematic = get_schematic(int(schematic_id))
        except Exception:
            continue
        cycle_time = schematic.get("cycle_time", 0)
        if not cycle_time:
            continue
        tier_num = _tier_from_schematic(schematic)
        factory_pins.append((pin, tier_num, cycle_time))

    if not factory_pins:
        return None  # keine konfigurierten Fabriken

    max_tier = max(t for _, t, _ in factory_pins)
    stalled = False
    for pin, tier, cycle_time in factory_pins:
        if tier != max_tier:
            continue
        last_start_str = pin.get("last_cycle_start", "")
        if not last_start_str:
            stalled = True
            continue
        last_start = _parse_expiry(last_start_str)
        if last_start is None:
            stalled = True
            continue
        from datetime import timedelta
        cycle_end = last_start + timedelta(seconds=cycle_time)
        if cycle_end <= now:
            stalled = True
    return stalled


def _compute_factories(pins: list, prices: dict) -> list:
    """Liste aktiver Fabriken mit Produkt, Tier, Menge/Tag und ISK/Tag."""
    tier_labels = {1: "P1", 2: "P2", 3: "P3", 4: "P4"}
    factories = []
    for pin in pins:
        factory = pin.get("factory_details") or {}
        schematic_id = factory.get("schematic_id") or pin.get("schematic_id")
        if not schematic_id:
            continue
        try:
            schematic = get_schematic(int(schematic_id))
        except Exception:
            continue
        cycle_time = schematic.get("cycle_time", 0)
        product_name = schematic.get("schematic_name", "")
        if not cycle_time or not product_name:
            continue
        tier_num = _tier_from_schematic(schematic)
        qty_per_cycle = schematic.get("output_quantity") or _CYCLE_QTY_FALLBACK.get(cycle_time, 1)
        qty_per_day = qty_per_cycle * (86400.0 / float(cycle_time))
        factories.append({
            "name": product_name,
            "tier": tier_labels[tier_num],
            "qty_per_day": round(qty_per_day, 1),
            "isk_per_day": round(qty_per_day * prices.get(product_name, 0.0)),
        })
    return factories


def _record_isk_snapshot(account_id: int, isk_day: float, colony_count: int, db: Session) -> None:
    """Speichert maximal einen ISK-Snapshot pro Tag und Account."""
    try:
        now_utc = datetime.now(timezone.utc)
        start_of_day = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timedelta(days=1)
        existing = db.query(IskSnapshot).filter(
            IskSnapshot.account_id == account_id,
            IskSnapshot.recorded_at >= start_of_day,
            IskSnapshot.recorded_at < end_of_day,
        ).first()
        if existing:
            existing.isk_day = str(round(isk_day))
            existing.colony_count = colony_count
        else:
            db.add(IskSnapshot(
                account_id=account_id,
                isk_day=str(round(isk_day)),
                colony_count=colony_count,
            ))
        db.commit()
    except Exception as e:
        logger.warning(f"ISK Snapshot fehlgeschlagen: {e}")
        try:
            db.rollback()
        except Exception:
            pass


def _update_character_colony_sync_status(
    characters: list[Character],
    fetch_results: dict[int, dict],
    db: Session,
) -> None:
    """Merkt sich den letzten bekannten Koloniestand pro Charakter und markiert Ausleseprobleme."""
    dirty = False
    now_utc = datetime.now(timezone.utc)

    for char in characters:
        result = fetch_results.get(char.id) or {"status": "not_processed", "count": 0}
        status = result.get("status")
        count = int(result.get("count") or 0)
        previous_count = int(getattr(char, "last_known_colony_count", 0) or 0)

        if status == "ok":
            if count == 0 and previous_count > 0:
                char.colony_sync_issue = True
                char.colony_sync_issue_note = "previous_colonies_missing"
            else:
                char.last_known_colony_count = count
                char.colony_sync_issue = False
                char.colony_sync_issue_note = None
        elif previous_count > 0 and status in {"error", "token_missing"}:
            char.colony_sync_issue = True
            char.colony_sync_issue_note = "fetch_failed" if status == "error" else "token_missing"

        char.last_colony_sync_at = now_utc
        dirty = True

    if not dirty:
        return

    try:
        db.commit()
    except Exception as e:
        logger.warning(f"Charakter-Kolonie-Syncstatus fehlgeschlagen: {e}")
        try:
            db.rollback()
        except Exception:
            pass


def _backfill_character_colony_sync_status_from_cache(
    account_id: int,
    characters: list[Character],
    db: Session,
) -> None:
    """Leitet einen letzten bekannten erfolgreichen Sync aus dem Dashboard-Cache ab."""
    if not characters:
        return

    cached = _load_colony_cache(account_id, db)
    if not cached:
        return

    colonies = cached.get("colonies") or []
    colony_counts: dict[str, int] = {}
    for colony in colonies:
        char_name = colony.get("character_name")
        if not char_name:
            continue
        colony_counts[char_name] = colony_counts.get(char_name, 0) + 1

    dirty = False
    now_utc = datetime.now(timezone.utc)
    for char in characters:
        if getattr(char, "last_colony_sync_at", None):
            continue
        count = int(colony_counts.get(char.character_name, 0) or 0)
        char.last_known_colony_count = count
        char.colony_sync_issue = False
        char.colony_sync_issue_note = None
        char.last_colony_sync_at = now_utc
        dirty = True

    if not dirty:
        return

    try:
        db.commit()
    except Exception as e:
        logger.warning(f"Character colony cache backfill fehlgeschlagen: {e}")
        try:
            db.rollback()
        except Exception:
            pass


def _build_dashboard_payload(account, characters: list, db: Session, price_mode: str = "sell") -> dict:
    """Holt alle ESI/Markt-Daten frisch und gibt den vollständigen Payload zurück."""

    active_characters = [char for char in characters if not getattr(char, "vacation_mode", False)]
    vacation_colonies = _cached_vacation_colonies(account.id, characters, db)

    # Schritt 1a: Tokens sequentiell erneuern (DB-safe, schreibt ggf. neuen Token in DB)
    char_token_map: dict[int, str | None] = {}
    for char in characters:
        token = ensure_valid_token(char, db)
        if not token:
            logger.warning(f"Kein gültiges Token für Char {char.character_name} ({char.eve_character_id}) – übersprungen")
        char_token_map[char.id] = token

    # Schritt 1b: Kolonien parallel abrufen (reine HTTP-Calls, thread-sicher)
    def _fetch_char_colonies(args: tuple) -> tuple:
        char, token = args
        if not token:
            return char, [], None
        try:
            cols = get_character_planets(char.eve_character_id, token)
            logger.debug(f"ESI Kolonien für {char.character_name}: {len(cols)}")
            return char, cols, token
        except Exception as e:
            logger.warning(f"Kolonien konnten für Char {char.character_name} ({char.eve_character_id}) nicht geladen werden: {e}")
            return char, [], None

    _n_chars = len(active_characters)
    with ThreadPoolExecutor(max_workers=min(_n_chars, 8) if _n_chars else 1) as ex:
        _colony_results = list(ex.map(
            _fetch_char_colonies,
            [(c, char_token_map[c.id]) for c in active_characters],
        ))

    char_colony_token: list[tuple] = []
    char_fetch_results: dict[int, dict] = {}
    for char, raw_colonies, token in _colony_results:
        if char_token_map[char.id] is None:
            char_fetch_results[char.id] = {"status": "token_missing", "count": 0}
        elif token is None:
            char_fetch_results[char.id] = {"status": "error", "count": 0}
        else:
            char_fetch_results[char.id] = {"status": "ok", "count": len(raw_colonies)}
            for colony in raw_colonies:
                char_colony_token.append((char, colony, token))

    _update_character_colony_sync_status(active_characters, char_fetch_results, db)

    # Schritt 2: planet_info + planet_detail parallel abrufen
    # Extract plain values before threading — ORM objects must not be accessed from
    # multiple threads (SQLAlchemy session is not thread-safe; expire_on_commit=True
    # would trigger concurrent lazy-loads, causing the isce error).
    def _fetch_planet(args):
        char_eve_id, colony, token = args
        planet_id = colony.get("planet_id")
        info = get_planet_info(planet_id) if planet_id else {}
        detail = {}
        if token and planet_id:
            try:
                detail = get_planet_detail(char_eve_id, planet_id, token)
            except Exception as e:
                logger.warning(f"Fehler bei Planet {planet_id}: {e}")
        return info, detail

    planet_fetch_args = [
        (char.eve_character_id, colony, token)
        for char, colony, token in char_colony_token
    ]
    with ThreadPoolExecutor(max_workers=10) as ex:
        planet_data = list(ex.map(_fetch_planet, planet_fetch_args))

    # Schritt 3: Produktionen berechnen — ohne Preisabfrage
    colony_prods: list[tuple] = []
    all_product_names: set[str] = set()
    for info, detail in planet_data:
        pins = detail.get("pins", [])
        productions, prod_tiers, highest_tier = _compute_colony_productions(pins)
        expiry_time = _get_colony_expiry(pins)
        colony_prods.append((productions, prod_tiers, highest_tier, expiry_time, pins))
        all_product_names.update(productions.keys())

    # Schritt 4: Eine einzige Batch-Preisabfrage (DB-Cache-Pfad)
    prices = get_prices_by_mode(list(all_product_names), price_mode, db) if all_product_names else {}

    # Schritt 5: Kolonien-Liste aufbauen
    colonies = []
    total_isk_day = 0.0
    next_expiry: datetime | None = None
    next_expiry_char: str | None = None

    for (char, colony, _), (info, _detail), (productions, prod_tiers, highest_tier, expiry_time, pins) in zip(
        char_colony_token, planet_data, colony_prods
    ):
        planet_id = colony.get("planet_id")
        planet_type = colony.get("planet_type", "unknown").capitalize()
        planet_name = info.get("name") or f"Planet {planet_id}"
        # Nur das höchste Tier zählt — Vorprodukte werden intern verbraucht
        highest_tier_num = int(highest_tier[1]) if highest_tier else 0
        isk_day = sum(
            qty * prices.get(name, 0.0)
            for name, qty in productions.items()
            if prod_tiers.get(name, 0) == highest_tier_num
        )
        expiry_hours = _hours_until(expiry_time)
        is_stalled = _check_factory_stall(pins) if expiry_time is None else None
        is_active = (expiry_hours is not None and expiry_hours > 0) if expiry_time is not None \
                    else (is_stalled is False)
        if is_active:
            total_isk_day += isk_day

        if expiry_time is not None and (next_expiry is None or expiry_time < next_expiry):
            next_expiry = expiry_time
            next_expiry_char = char.character_name

        _sys_data = _sde.get_system_local(colony.get("solar_system_id")) or {}
        colonies.append({
            "planet_id": planet_id,
            "planet_name": planet_name,
            "planet_type": planet_type,
            "upgrade_level": colony.get("upgrade_level", 0),
            "num_pins": colony.get("num_pins", 0),
            "last_update": colony.get("last_update", "—"),
            "solar_system_id": colony.get("solar_system_id"),
            "solar_system_name": _sys_data.get("name", "—"),
            "region_name": _sys_data.get("region_name") or "—",
            "color": PLANET_TYPE_COLORS.get(planet_type, "#586e75"),
            "character_name": char.character_name,
            "character_portrait": char.portrait_url,
            "corporation_id": char.corporation_id,
            "corporation_name": char.corporation_name or "",
            "alliance_name": char.alliance_name or "",
            "expiry_iso": expiry_time.isoformat() if expiry_time else None,
            "expiry_hours": expiry_hours,
            "is_stalled": is_stalled,
            "is_active": is_active,
            "isk_day": isk_day,
            "highest_tier": highest_tier,
            "highest_tier_num": highest_tier_num,
            "productions": productions,
            "prod_tiers": prod_tiers,
            "factories": _compute_factories(pins, prices),
            "storage": _compute_storage(pins),
            "extractor_status": _get_extractor_status(pins),
            "extractor_balance": _compute_extractor_balance(pins),
            "extractor_rate_summary": _compute_extractor_rate_summary(pins),
            "missing_inputs": _compute_missing_inputs(pins),
            "vacation_mode": False,
        })

    colonies.extend(vacation_colonies)
    colony_count = len([colony for colony in colonies if not colony.get("vacation_mode")])
    payload = {
        "colonies": colonies,
        "total_isk_day": total_isk_day,
        "next_expiry": next_expiry,
        "next_expiry_hours": _hours_until(next_expiry),
        "next_expiry_char": next_expiry_char,
        "char_count": len(characters),
        "colony_count": colony_count,
        "fetched_at": _time.time(),
        "price_mode": price_mode,
    }
    _hydrate_price_cache(payload, db)
    _record_isk_snapshot(account.id, total_isk_day, colony_count, db)
    return payload


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    db.refresh(account)
    view_state = _get_dashboard_view_state(request)

    main_char = db.query(Character).filter(Character.id == account.main_character_id).first() if account.main_character_id else None

    characters = db.query(Character).filter(Character.account_id == account.id).all()
    now = _time.time()
    current_price_mode = getattr(account, "price_mode", "sell")

    # ── Schritt 1: DB-Cache laden (schnell, keine ESI-Calls) ──────────────────
    db_cached = _load_colony_cache(account.id, db)
    refreshing = account.id in _bg_refresh_running and not _bg_refresh_done.get(account.id, False)

    if db_cached:
        cache_age = now - db_cached["fetched_at"]
        colonies = [_normalize_dashboard_colony(colony) for colony in (db_cached["colonies"] or [])]
        meta = db_cached["meta"]

        needs_balance_refresh = any(
            ("extractor_balance" not in colony)
            or ("extractor_rate_summary" not in colony)
            or (
                colony.get("extractor_rate_summary") is not None
                and "extractors" not in (colony.get("extractor_rate_summary") or {})
            )
            or (
                colony.get("extractor_balance") is None
                and int((colony.get("extractor_status") or {}).get("total") or 0) == 2
                and int((colony.get("extractor_status") or {}).get("expired") or 0) == 0
            )
            or (
                colony.get("extractor_rate_summary") is None
                and int((colony.get("extractor_status") or {}).get("total") or 0) == 1
                and int((colony.get("extractor_status") or {}).get("expired") or 0) == 0
            )
            for colony in colonies
        )
        if needs_balance_refresh and not refreshing:
            # Trigger background refresh to populate missing fields — don't block the page
            _kick_bg_refresh(account, characters)
            refreshing = True

        # Show cached data immediately (fast path — no ESI calls)
        next_expiry_dt, next_expiry_char = _recompute_expiry(colonies)
        colonies, total_isk_day = _apply_price_mode(colonies, meta, current_price_mode)
        char_count = meta.get("char_count", len(characters))
        colony_count = len(colonies)
        next_expiry_hours = _hours_until(next_expiry_dt)
        if next_expiry_hours is None:
            next_expiry_hours = meta.get("next_expiry_hours")
        _dashboard_cache[account.id] = {
            "colonies": colonies,
            "total_isk_day": total_isk_day,
            "next_expiry": next_expiry_dt,
            "next_expiry_hours": next_expiry_hours,
            "next_expiry_char": next_expiry_char,
            "char_count": char_count,
            "colony_count": colony_count,
            "fetched_at": db_cached["fetched_at"],
            "price_mode": current_price_mode,
        }
        cache_age_sec = int(cache_age)

    else:
        # No cache yet — show loading state immediately and kick off background refresh
        if not refreshing:
            _kick_bg_refresh(account, characters)
            refreshing = True
        colonies = []
        total_isk_day = 0.0
        next_expiry_dt = None
        next_expiry_hours = None
        next_expiry_char = None
        char_count = len(characters)
        colony_count = 0
        cache_age_sec = 0

    all_colonies = colonies
    visible_stat_colonies = [colony for colony in all_colonies if not colony.get("vacation_mode")]
    all_colony_characters = sorted({c.get("character_name") for c in all_colonies if c.get("character_name")})
    filtered_colonies = [
        colony for colony in all_colonies
        if _colony_matches_dashboard_filters(colony, view_state)
    ]
    filtered_colonies = _sort_dashboard_colonies(filtered_colonies, view_state)
    filtered_colony_count = len(filtered_colonies)
    requested_page_size = view_state["page_size"]
    page_size = filtered_colony_count if requested_page_size == 0 else requested_page_size
    if page_size <= 0:
        page_size = max(1, filtered_colony_count)
    total_pages = max(1, ceil(filtered_colony_count / page_size)) if filtered_colony_count else 1
    current_page = min(view_state["page"], total_pages)
    page_start = (current_page - 1) * page_size
    page_end = page_start + page_size
    colonies = filtered_colonies[page_start:page_end]
    total_isk_day = sum(float(colony.get("isk_day") or 0.0) for colony in colonies)
    page_numbers = _build_dashboard_page_numbers(current_page, total_pages)
    page_colony_range_start = page_start + 1 if filtered_colony_count else 0
    page_colony_range_end = min(page_end, filtered_colony_count)
    pagination_base_path = str(request.url.path or "/dashboard")
    dashboard_pagination = {
        "current_page": current_page,
        "page_size": page_size,
        "total_pages": total_pages,
        "total_items": filtered_colony_count,
        "range_start": page_colony_range_start,
        "range_end": page_colony_range_end,
        "pages": [
            {
                "page": page_num,
                "url": _build_dashboard_page_url(pagination_base_path, view_state, page=page_num),
                "is_current": page_num == current_page,
            }
            for page_num in page_numbers
        ],
        "page_sizes": [
            {
                "value": size,
                "url": _build_dashboard_page_url(pagination_base_path, view_state, page=1, page_size=size),
                "is_current": size == requested_page_size,
                "label": "Alle" if size == 0 else str(size),
            }
            for size in DASHBOARD_PAGE_SIZES
        ],
        "prev_url": _build_dashboard_page_url(pagination_base_path, view_state, page=max(1, current_page - 1)),
        "next_url": _build_dashboard_page_url(pagination_base_path, view_state, page=min(total_pages, current_page + 1)),
        "first_url": _build_dashboard_page_url(pagination_base_path, view_state, page=1),
        "last_url": _build_dashboard_page_url(pagination_base_path, view_state, page=total_pages),
        "has_prev": current_page > 1,
        "has_next": current_page < total_pages,
    }

    # ── ISK-Historie (immer frisch aus DB) ───────────────────────────────────
    snapshots = (
        db.query(IskSnapshot)
        .filter(IskSnapshot.account_id == account.id)
        .order_by(IskSnapshot.recorded_at)
        .limit(365)
        .all()
    )
    isk_history = [
        {"date": s.recorded_at.strftime("%d.%m"), "date_iso": s.recorded_at.strftime("%Y-%m-%d"), "isk": float(s.isk_day or 0)}
        for s in snapshots
    ]

    cooldown_remaining = max(0, int(REFRESH_COOLDOWN_SEC - (now - _refresh_cooldown.get(account.id, 0))))
    market_last_updated = get_market_last_updated(db)
    market_last_updated_iso = None
    if market_last_updated:
        ts = market_last_updated.replace(tzinfo=timezone.utc) if market_last_updated.tzinfo is None else market_last_updated
        market_last_updated_iso = ts.astimezone(timezone.utc).isoformat()

    # ── Skyhook-Daten ─────────────────────────────────────────────────────────
    planet_ids = [c["planet_id"] for c in colonies if c.get("planet_id")]
    skyhook_data = {}
    if planet_ids:
        subq = (
            db.query(SkyhookEntry.planet_id, sqlfunc.max(SkyhookEntry.id).label("max_id"))
            .filter(SkyhookEntry.account_id == account.id, SkyhookEntry.planet_id.in_(planet_ids))
            .group_by(SkyhookEntry.planet_id)
            .subquery()
        )
        for e in (db.query(SkyhookEntry)
                    .join(subq, SkyhookEntry.id == subq.c.max_id)
                    .options(_joinedload(SkyhookEntry.items))
                    .all()):
            skyhook_data[e.planet_id] = [
                {"product_name": i.product_name, "quantity": i.quantity} for i in e.items
            ]
    expired_colony_count = sum(
        1
        for colony in visible_stat_colonies
        if colony.get("expiry_hours") is not None and colony.get("expiry_hours") < 0
    )
    active_colony_count = sum(1 for colony in visible_stat_colonies if colony.get("is_active") is True)
    stalled_colony_count = sum(
        1
        for colony in visible_stat_colonies
        if colony.get("expiry_hours") is None and colony.get("is_stalled") is True
    )
    lang = get_language_from_request(request)
    product_names = {
        f.get("name")
        for colony in colonies
        for f in colony.get("factories", [])
        if f.get("name")
    }
    product_names.update(
        item.get("product_name")
        for items in skyhook_data.values()
        for item in items
        if item.get("product_name")
    )
    product_names.update(
        extractor.get("name")
        for colony in colonies
        for extractor in ((colony.get("extractor_balance") or {}).get("extractors") or [])
        if extractor.get("name")
    )
    product_labels = {
        name: translate_type_name(PI_TYPE_IDS.get(name) or _sde.find_type_id_by_name(name), fallback=name, lang=lang)
        for name in product_names
        if name
    }
    # Token health: warn only when there is a genuine auth problem.
    # High esi_consecutive_errors alone is not enough — if the last sync
    # succeeded (colony_sync_issue=False, last_colony_sync_at set) the
    # counter is stale and should not trigger the banner.
    token_error_chars = [
        c for c in characters
        if not c.vacation_mode and (
            not c.refresh_token
        or (
            (c.esi_consecutive_errors or 0) >= 3
            and (c.colony_sync_issue or not c.last_colony_sync_at)
        )
        )
    ]
    token_error_count = len(token_error_chars)
    vacation_count = sum(1 for c in characters if c.vacation_mode)
    notification_colonies = [
        colony for colony in visible_stat_colonies
        if colony.get("expiry_hours") is not None and colony.get("expiry_hours") > 0
    ]

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "account": account,
        "main_char": main_char,
        "characters": characters,
        "all_colony_characters": all_colony_characters,
        "char_count": char_count,
        "colonies": colonies,
        "colony_count": colony_count,
        "filtered_colony_count": filtered_colony_count,
        "dashboard_pagination": dashboard_pagination,
        "dashboard_view_state": view_state,
        "planet_type_colors": PLANET_TYPE_COLORS,
        "total_isk_day": total_isk_day,
        "next_expiry": next_expiry_dt,
        "next_expiry_hours": next_expiry_hours,
        "next_expiry_char": next_expiry_char,
        "active_colony_count": active_colony_count,
        "expired_colony_count": expired_colony_count,
        "stalled_colony_count": stalled_colony_count,
        "cache_age_sec": cache_age_sec,
        "cooldown_remaining": cooldown_remaining,
        "isk_history": isk_history,
        "skyhook_data": skyhook_data,
        "product_labels": product_labels,
        "notification_colonies": notification_colonies,
        "price_mode": current_price_mode,
        "refreshing": refreshing,
        "market_last_updated_iso": market_last_updated_iso,
        "token_error_count": token_error_count,
        "token_error_chars": [c.character_name for c in token_error_chars],
        "vacation_count": vacation_count,
        "now_ts_ms": int(now * 1000),
    })


@router.get("/pi-check")
def pi_check(
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """P4 production vs. P0 extraction balance check.

    Uses the hardcoded PI chain from pi_data.py — avoids relying on
    SDE schematic output_type_id which is stored as the dict key in
    EVERef schematics.json, not as a field, causing lookups to fail.

    Production ratios (per cycle):
      P0 → P1 :  3000 P0 → 20 P1    → 150 P0 per P1
      P1 → P2 :  40 P1A + 40 P1B → 5 P2   → 8 P1 per P2 per input
      P2 → P3 :  10 P2(each) → 3 P3        → 10/3 P2 per P3 per input
      P3 → P4 :  6 P3(each) → 1 P4         → 6 P3 per P4 per input
    """
    from app.pi_data import P0_TO_P1, P1_TO_P2, P2_TO_P3, P3_TO_P4

    cached = _load_colony_cache(account.id, db)
    colonies: list[dict] = (cached.get("colonies") or []) if cached else []

    # 1. Sum P4 productions across all colonies (units/day)
    p4_totals: dict[str, float] = {}
    for colony in colonies:
        productions = colony.get("productions") or {}
        prod_tiers = colony.get("prod_tiers") or {}
        for product, qty in productions.items():
            if prod_tiers.get(product, 0) == 4:
                p4_totals[product] = p4_totals.get(product, 0.0) + float(qty)

    # 2. Sum active extractor output per P0 material type (units/day)
    p0_gathered_raw: dict[str, dict] = {}
    for colony in colonies:
        ers = colony.get("extractor_rate_summary") or {}
        for ext in (ers.get("extractors") or []):
            name = (ext.get("name") or "Unknown").strip()
            per_day = float(ext.get("avg_per_hour") or 0) * 24.0
            if name not in p0_gathered_raw:
                p0_gathered_raw[name] = {"count": 0, "per_day": 0.0}
            p0_gathered_raw[name]["count"] += 1
            p0_gathered_raw[name]["per_day"] += per_day

    # Normalise P0 names: ESI may use "Micro Organisms" or "Microorganisms" etc.
    # Build key: stripped-lowercase → canonical pi_data name
    _p0_canon: dict[str, str] = {
        k.lower().replace(" ", ""): k for k in P0_TO_P1
    }
    def _norm_p0(name: str) -> str:
        return _p0_canon.get(name.lower().replace(" ", ""), name)

    p0_gathered: dict[str, dict] = {}
    for raw_name, v in p0_gathered_raw.items():
        canon = _norm_p0(raw_name)
        if canon not in p0_gathered:
            p0_gathered[canon] = {"count": 0, "per_day": 0.0}
        p0_gathered[canon]["count"] += v["count"]
        p0_gathered[canon]["per_day"] += v["per_day"]

    # Reverse maps
    _p1_to_p0: dict[str, str] = {v: k for k, v in P0_TO_P1.items()}

    def _p4_to_p0(p4_name: str, qty_day: float) -> dict[str, float]:
        """Return {p0_name: required_per_day} for producing qty_day units of p4_name."""
        result: dict[str, float] = {}
        for inp in P3_TO_P4.get(p4_name, []):
            inp_qty = qty_day * 6.0          # 6 of each input per P4 per cycle

            if inp in _p1_to_p0:
                # Input is a P1 (e.g. Reactive Metals, Bacteria, Water)
                p0 = _p1_to_p0[inp]
                result[p0] = result.get(p0, 0.0) + inp_qty * 150.0
            elif inp in P2_TO_P3:
                # Input is a P3 — trace P3 → P2 → P1 → P0
                for p2 in P2_TO_P3[inp]:
                    p2_qty = inp_qty * (10.0 / 3.0)  # 10/3 P2 per P3 per input type
                    for p1 in (P1_TO_P2.get(p2) or []):
                        p1_qty = p2_qty * 8.0        # 8 P1 per P2 per input type
                        p0 = _p1_to_p0.get(p1)
                        if p0:
                            result[p0] = result.get(p0, 0.0) + p1_qty * 150.0
        return result

    # 3. Compute P0 requirements per P4 product
    p4_results = []
    for p4_name, qty_day in sorted(p4_totals.items(), key=lambda x: -x[1]):
        p0_reqs = _p4_to_p0(p4_name, qty_day)
        p4_results.append({
            "name": p4_name,
            "qty_day": round(qty_day, 1),
            "p0_requirements": {
                k: round(v)
                for k, v in sorted(p0_reqs.items(), key=lambda x: -x[1])
            },
        })

    # 4. Sum total P0 needed across all P4 products
    p0_total_needed: dict[str, float] = {}
    for p4 in p4_results:
        for p0_name, qty in p4["p0_requirements"].items():
            p0_total_needed[p0_name] = p0_total_needed.get(p0_name, 0.0) + qty

    return JSONResponse({
        "p4_products": p4_results,
        "p0_gathered": {
            k: {"count": v["count"], "per_day": round(v["per_day"])}
            for k, v in sorted(p0_gathered.items())
        },
        "p0_total_needed": {k: round(v) for k, v in sorted(p0_total_needed.items())},
    })


@router.post("/refresh")
def force_refresh(account=Depends(require_account)):
    """Cache für diesen Account invalidieren — max. 1× pro 60 Sekunden."""
    now = _time.time()
    last = _refresh_cooldown.get(account.id, 0)
    wait = int(REFRESH_COOLDOWN_SEC - (now - last)) + 1
    if wait > 0 and (now - last) < REFRESH_COOLDOWN_SEC:
        return JSONResponse({"ok": False, "wait": wait})
    _dashboard_cache.pop(account.id, None)
    # Auch Planet-Detail-Cache für alle Chars dieses Accounts leeren
    from app.database import get_db as _get_db
    from app.models import Character as _Char
    db = next(_get_db())
    try:
        chars = db.query(_Char).filter(_Char.account_id == account.id).all()
        for char in chars:
            invalidate_planet_detail_cache(char.eve_character_id)
        if chars:
            _start_bg_refresh(
                account.id,
                [char.id for char in chars],
                getattr(account, "price_mode", "sell"),
            )
    finally:
        db.close()
    _refresh_cooldown[account.id] = now
    return JSONResponse({"ok": True})


@router.post("/price-mode")
def set_price_mode(
    mode: str = Body(..., embed=True),
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """Speichert den Preis-Modus (sell/buy/split) für den Account."""
    if mode not in ("sell", "buy", "split"):
        raise HTTPException(status_code=400, detail="Ungültiger Modus")
    account.price_mode = mode
    db.commit()
    # Kein Cache-Löschen nötig — nächster Load recomputed ISK direkt aus DB-Preisen
    return JSONResponse({"ok": True, "mode": mode})


@router.get("/refresh-status")
def refresh_status(
    since: float | None = None,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """Poll whether a background ESI refresh has completed.

    Uses the DB cache timestamp as the source of truth — safe across all
    gunicorn workers (no shared in-process state needed).

    `since` = Unix timestamp when the page was loaded. Done when the DB cache
    was updated after that point.
    """
    # Fast path: in-process thread finished on this exact worker
    done = _bg_refresh_done.pop(account.id, False)
    if done:
        _bg_refresh_running.pop(account.id, None)
        return JSONResponse({"done": True})

    # Primary path: check DB cache timestamp (works across all workers + Celery)
    ref_ts = since if since is not None else _bg_refresh_kicked_at.get(account.id, _time.time() - 5)
    row = db.query(DashboardCache).filter_by(account_id=account.id).first()
    if row and row.fetched_at:
        fetched = row.fetched_at
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        if fetched.timestamp() >= ref_ts:
            _bg_refresh_running.pop(account.id, None)
            _bg_refresh_kicked_at.pop(account.id, None)
            return JSONResponse({"done": True})

    return JSONResponse({"done": False})

def _corp_access_flags(account: Account, main_char: Character | None, db: Session) -> dict:
    # Serve from cache if fresh — avoids 2 live ESI calls per page load
    cached = _corp_access_cache.get(account.id)
    if cached and (_time.time() - cached[0]) < _CORP_ACCESS_CACHE_TTL:
        return cached[1]

    own_corp_id = main_char.corporation_id if main_char else None
    own_corp_name = main_char.corporation_name if main_char else None
    is_ceo = False
    is_director = False
    roles_scope_missing = False

    if own_corp_id and main_char:
        try:
            corp_info = get_corporation_info(own_corp_id)
            own_corp_name = corp_info.get("name", own_corp_name or f"Corp #{own_corp_id}")
            is_ceo = corp_info.get("ceo_id") == main_char.eve_character_id
        except Exception:
            pass

        scopes = set((main_char.scopes or "").split())
        if "esi-characters.read_corporation_roles.v1" in scopes:
            access_token = ensure_valid_token(main_char, db)
            if access_token:
                roles_data = get_character_roles(main_char.eve_character_id, access_token)
                roles = roles_data.get("roles", []) if isinstance(roles_data, dict) else []
                is_director = "Director" in roles
        else:
            roles_scope_missing = True

    has_access = bool(
        own_corp_id and (account.is_owner or account.is_admin or is_ceo or is_director)
    )
    result = {
        "corp_id": own_corp_id,
        "corp_name": own_corp_name,
        "is_ceo": is_ceo,
        "is_director": is_director,
        "roles_scope_missing": roles_scope_missing,
        "has_access": has_access,
        "can_manage": bool(own_corp_id and account.is_owner),
    }
    _corp_access_cache[account.id] = (_time.time(), result)
    return result


@router.get("/corp", response_class=HTMLResponse)
def corp_view_page(
    request: Request,
    corp_id: int | None = None,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """Corporation overview for the current character's corporation."""

    main_char = db.query(Character).filter(Character.id == account.main_character_id).first() if account.main_character_id else None
    access = _corp_access_flags(account, main_char, db)
    active_corp_id = access["corp_id"]
    if not access["has_access"]:
        raise HTTPException(
            status_code=403,
            detail="Kein Zugriff - nur fuer CEO, Direktoren, Manager und Administratoren der eigenen Corporation",
        )

    corp_name = access["corp_name"] or f"Corporation {active_corp_id}"
    corp_chars = db.query(Character).filter(Character.corporation_id == active_corp_id).all() if active_corp_id else []
    chars_by_account: dict[int, list[Character]] = {}
    for char in corp_chars:
        if char.account_id is not None:
            chars_by_account.setdefault(char.account_id, []).append(char)
    account_ids = sorted(chars_by_account)
    price_mode = getattr(account, "price_mode", "sell")

    # ── Corp view cache (5-minute TTL, invalidated on any account save) ──────
    _cv_cached = _corp_view_cache.get(active_corp_id) if active_corp_id else None
    if _cv_cached and (_time.time() - _cv_cached[0]) < _CORP_VIEW_CACHE_TTL:
        _cv_data = _cv_cached[1]
        # total_isk_day is price-mode dependent → recompute cheaply from cached colonies
        _total_isk = sum(
            c.get("isk_day_modes", {}).get(price_mode, c.get("isk_day", 0))
            for c in _cv_data["corp_colonies"] if c.get("is_active", True)
        )
        return templates.TemplateResponse("corp_view.html", {
            "request": request,
            "account": account,
            **_cv_data,
            "total_isk_day": _total_isk,
            "total_characters": len(corp_chars),
            "is_ceo": access["is_ceo"],
            "is_director": access["is_director"],
            "roles_scope_missing": access["roles_scope_missing"],
            "can_manage_cache": access["can_manage"],
        })

    # ── Batch-load accounts + mains (replaces N + N separate queries) ────────
    accounts_map: dict[int, Account] = {
        acc.id: acc
        for acc in db.query(Account).filter(Account.id.in_(account_ids)).all()
    }
    _main_ids = [acc.main_character_id for acc in accounts_map.values() if acc.main_character_id]
    mains_map: dict[int, Character] = {
        c.id: c
        for c in db.query(Character).filter(Character.id.in_(_main_ids)).all()
    } if _main_ids else {}

    corp_colonies: list[dict] = []
    corp_main_rows: list[dict] = []
    corp_product_rows: list[dict] = []
    uncached_count = 0
    for acc_id in account_ids:
        acc = accounts_map.get(acc_id)
        if not acc:
            continue
        main = mains_map.get(acc.main_character_id) if acc.main_character_id else None
        account_char_names = {c.character_name for c in chars_by_account.get(acc_id, [])}

        # ── Memory-first colony lookup (avoids DB query + JSON parse per account) ─
        mem = _dashboard_cache.get(acc_id)
        if mem:
            colonies: list[dict] | None = mem.get("colonies", [])
            # is_active already set at cache build time; stale by at most DASHBOARD_CACHE_TTL
        else:
            db_cached = _load_colony_cache(acc_id, db)
            if db_cached:
                colonies = db_cached.get("colonies", [])
                _recompute_expiry(colonies)  # update is_active from expiry_iso
            else:
                colonies = None
                uncached_count += 1
                # Kick Celery background refresh so the corp page gets data soon
                try:
                    import os as _os
                    if _os.getenv("CELERY_BROKER_URL"):
                        from app.tasks import refresh_account_task
                        refresh_account_task.delay(acc_id)
                except Exception as _exc:
                    logger.debug("corp: could not kick Celery for account %d: %s", acc_id, _exc)

        colony_count = 0
        planet_type_counts: dict[str, int] = {}
        if colonies is not None:
            for colony in colonies:
                if colony.get("character_name") in account_char_names:
                    colony_count += 1
                    corp_colony = dict(colony)
                    corp_colony["main_name"] = main.character_name if main else f"Account #{acc_id}"
                    corp_colony["main_portrait"] = main.portrait_url if main else "/static/img/default_char.svg"
                    corp_colonies.append(corp_colony)
                    for product_name in sorted((corp_colony.get("productions") or {}).keys()):
                        corp_product_rows.append({
                            "product_name": product_name,
                            "main_name": corp_colony["main_name"],
                            "main_portrait": corp_colony["main_portrait"],
                            "planet_name": corp_colony.get("planet_name") or "-",
                            "planet_type": corp_colony.get("planet_type") or "-",
                            "character_name": corp_colony.get("character_name") or "-",
                            "is_active": bool(corp_colony.get("is_active", True)),
                        })
                    planet_type = colony.get("planet_type")
                    if planet_type:
                        planet_type_counts[planet_type] = planet_type_counts.get(planet_type, 0) + 1

        corp_main_rows.append({
            "account_id": acc_id,
            "main_name": main.character_name if main else f"Account #{acc_id}",
            "main_portrait": main.portrait_url if main else "/static/img/default_char.svg",
            "colony_count": colony_count,
            "char_count": len(chars_by_account.get(acc_id, [])),
            "planet_types": [
                {
                    "type": planet_type,
                    "count": count,
                    "color": PLANET_TYPE_COLORS.get(planet_type, "#586e75"),
                }
                for planet_type, count in sorted(
                    planet_type_counts.items(),
                    key=lambda item: (-item[1], item[0].lower()),
                )
            ],
        })

    corp_colonies.sort(key=lambda x: (x.get("character_name", ""), x.get("planet_name", "")))
    corp_main_rows.sort(key=lambda x: (-x["colony_count"], x["main_name"].lower()))
    corp_product_rows.sort(key=lambda x: (x["product_name"].lower(), x["main_name"].lower(), x["planet_name"].lower()))
    # ISK uses pre-computed isk_day_modes to avoid _apply_price_mode mutation
    total_isk = sum(
        c.get("isk_day_modes", {}).get(price_mode, c.get("isk_day", 0))
        for c in corp_colonies if c.get("is_active", True)
    )
    market_last_updated = get_market_last_updated(db)
    market_last_updated_iso = None
    if market_last_updated:
        ts = market_last_updated.replace(tzinfo=timezone.utc) if market_last_updated.tzinfo is None else market_last_updated
        market_last_updated_iso = ts.astimezone(timezone.utc).isoformat()
    all_products = (
        [{"name": n, "tier": "P1"} for n in sorted(ALL_P1)] +
        [{"name": n, "tier": "P2"} for n in sorted(ALL_P2)] +
        [{"name": n, "tier": "P3"} for n in sorted(ALL_P3)] +
        [{"name": n, "tier": "P4"} for n in sorted(ALL_P4)]
    )

    # ── Store in corp view cache (excluding per-viewer fields) ───────────────
    _shared_ctx = {
        "corp_name": corp_name,
        "corp_id": active_corp_id,
        "corp_colonies": corp_colonies,
        "corp_main_rows": corp_main_rows,
        "corp_product_rows": corp_product_rows,
        "uncached_count": uncached_count,
        "total_colonies": len(corp_colonies),
        "total_mains": len(corp_main_rows),
        "market_last_updated_iso": market_last_updated_iso,
        "all_products": all_products,
    }
    if active_corp_id:
        _corp_view_cache[active_corp_id] = (_time.time(), _shared_ctx)

    return templates.TemplateResponse("corp_view.html", {
        "request": request,
        "account": account,
        **_shared_ctx,
        "total_isk_day": total_isk,
        "total_characters": len(corp_chars),
        "is_ceo": access["is_ceo"],
        "is_director": access["is_director"],
        "roles_scope_missing": access["roles_scope_missing"],
        "can_manage_cache": access["can_manage"],
    })


@router.get("/corp/accounts")
def corp_accounts_api(
    corp_id: int,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """Returns accounts in the current corp with cache status."""
    main_char = db.query(Character).filter(Character.id == account.main_character_id).first() if account.main_character_id else None
    access = _corp_access_flags(account, main_char, db)
    if not access["can_manage"] or access["corp_id"] != corp_id:
        raise HTTPException(status_code=403)
    corp_chars = db.query(Character).filter(Character.corporation_id == corp_id).all()
    account_ids = sorted({c.account_id for c in corp_chars if c.account_id is not None})
    # Batch-load accounts + mains
    accs_map = {a.id: a for a in db.query(Account).filter(Account.id.in_(account_ids)).all()}
    _mids = [a.main_character_id for a in accs_map.values() if a.main_character_id]
    mains_map = {c.id: c for c in db.query(Character).filter(Character.id.in_(_mids)).all()} if _mids else {}
    result = []
    now = _time.time()
    for acc_id in account_ids:
        acc = accs_map.get(acc_id)
        if not acc:
            continue
        main = mains_map.get(acc.main_character_id) if acc.main_character_id else None
        cached = _load_colony_cache(acc_id, db)
        fetched_at = float(cached.get("fetched_at") or 0.0) if cached else 0.0
        age_seconds = max(0, int(now - fetched_at)) if fetched_at else None
        is_recent_cached = bool(
            cached and fetched_at and age_seconds is not None and age_seconds < _CORP_RECENT_CACHE_SECONDS
        )
        result.append({
            "account_id": acc_id,
            "main_name": main.character_name if main else f"Account #{acc_id}",
            "is_cached": cached is not None,
            "is_recent_cached": is_recent_cached,
            "cache_age_seconds": age_seconds,
        })
    return JSONResponse({"accounts": result})


@router.get("/corp/load-all/status")
def corp_load_all_status(
    corp_id: int,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """Returns whether a corp-wide force-load is already running."""
    main_char = db.query(Character).filter(Character.id == account.main_character_id).first() if account.main_character_id else None
    access = _corp_access_flags(account, main_char, db)
    if not access["can_manage"] or access["corp_id"] != corp_id:
        raise HTTPException(status_code=403)
    lock = _get_corp_load_lock(corp_id)
    return JSONResponse({
        "running": bool(lock),
        "started_by": lock.get("started_by") if lock else None,
        "started_at": lock.get("started_at") if lock else None,
    })


@router.post("/corp/load-all/start")
def corp_load_all_start(
    corp_id: int,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """Acquires a corp-wide load lock before the UI starts processing accounts."""
    main_char = db.query(Character).filter(Character.id == account.main_character_id).first() if account.main_character_id else None
    access = _corp_access_flags(account, main_char, db)
    if not access["can_manage"] or access["corp_id"] != corp_id:
        raise HTTPException(status_code=403)
    lock = _get_corp_load_lock(corp_id)
    if lock:
        return JSONResponse({
            "ok": False,
            "running": True,
            "started_by": lock.get("started_by"),
            "started_at": lock.get("started_at"),
        }, status_code=409)
    _corp_load_running[corp_id] = {
        "started_by": account.id,
        "started_at": _time.time(),
    }
    return JSONResponse({"ok": True, "running": False})


@router.post("/corp/load-all/finish")
def corp_load_all_finish(
    corp_id: int,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """Releases the corp-wide load lock after the UI finishes processing."""
    main_char = db.query(Character).filter(Character.id == account.main_character_id).first() if account.main_character_id else None
    access = _corp_access_flags(account, main_char, db)
    if not access["can_manage"] or access["corp_id"] != corp_id:
        raise HTTPException(status_code=403)
    lock = _get_corp_load_lock(corp_id)
    if lock and lock.get("started_by") == account.id:
        _corp_load_running.pop(corp_id, None)
    return JSONResponse({"ok": True})


@router.post("/corp/load-account/{target_account_id}")
def force_load_account(
    target_account_id: int,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """Force-loads dashboard data for a target account in the viewer's corp."""
    main_char = db.query(Character).filter(Character.id == account.main_character_id).first() if account.main_character_id else None
    access = _corp_access_flags(account, main_char, db)
    if not access["can_manage"]:
        raise HTTPException(status_code=403)
    target = db.query(Account).filter(Account.id == target_account_id).first()
    if not target:
        raise HTTPException(status_code=404)
    chars = db.query(Character).filter(Character.account_id == target_account_id).all()
    if access["corp_id"] is None or not any(c.corporation_id == access["corp_id"] for c in chars):
        raise HTTPException(status_code=403)
    try:
        payload = _build_dashboard_payload(target, chars, db, price_mode=getattr(target, "price_mode", "sell"))
        _save_colony_cache(target_account_id, payload, db)
        _dashboard_cache[target_account_id] = payload
        return JSONResponse({"ok": True, "colony_count": payload["colony_count"]})
    except Exception as e:
        logger.exception("force_load_account %s failed", target_account_id)
        return JSONResponse({"ok": False, "error": "Laden fehlgeschlagen"}, status_code=500)


def _attach_pi_skills(characters: list[Character], db: Session) -> None:
    for char in characters:
        scopes = set((char.scopes or "").split())
        has_skill_scope = "esi-skills.read_skills.v1" in scopes
        skill_levels = {name: 0 for name in PI_SKILL_NAMES}
        skill_error = None

        if has_skill_scope:
            try:
                access_token = ensure_valid_token(char, db)
                if access_token:
                    data = get_character_skills(char.eve_character_id, access_token)
                    for skill in data.get("skills", []) if isinstance(data, dict) else []:
                        skill_name = _sde.get_type_name(skill.get("skill_id"))
                        if skill_name in skill_levels:
                            skill_levels[skill_name] = skill.get("active_skill_level", 0) or 0
                else:
                    skill_error = "token_missing"
            except Exception as exc:
                logger.warning(f"PI skill fetch failed for {char.character_name}: {exc}")
                skill_error = "fetch_failed"

        setattr(char, "pi_skills", [
            {"name": name, "level": skill_levels[name], "max_level": 5}
            for name in PI_SKILL_NAMES
        ])
        setattr(char, "pi_skill_total", sum(skill_levels.values()))
        setattr(char, "has_skill_scope", has_skill_scope)
        setattr(char, "pi_skill_error", skill_error)


def _parse_dashboard_float(value: str | None, default: float, minimum: float = 0.0, maximum: float | None = None) -> float:
    try:
        parsed = float(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _parse_dashboard_int(value: str | None, default: int, allowed: set[int] | None = None, minimum: int | None = None) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if allowed is not None and parsed not in allowed:
        return default
    return parsed


def _get_dashboard_view_state(request: Request) -> dict:
    params = request.query_params
    tiers_raw = (params.get("tiers") or "").strip()
    tiers = [tier for tier in tiers_raw.split(",") if tier in {"P1", "P2", "P3", "P4"}]
    sort_key = params.get("sort") or ""
    if sort_key not in {"char", "planet", "type", "level", "tier", "expiry", "isk", "storage"}:
        sort_key = ""
    sort_order = (params.get("order") or "asc").lower()
    if sort_order not in {"asc", "desc"}:
        sort_order = "asc"

    return {
        "page": _parse_dashboard_int(params.get("page"), 1, minimum=1),
        "page_size": _parse_dashboard_int(params.get("page_size"), 25, allowed=set(DASHBOARD_PAGE_SIZES)),
        "char": (params.get("char") or "").strip(),
        "tiers": tiers,
        "balanced": params.get("balanced") == "1",
        "unbalanced": params.get("unbalanced") == "1",
        "active": params.get("active") == "1",
        "expired": params.get("expired") == "1",
        "stalled": params.get("stalled") == "1",
        "balance_threshold": _parse_dashboard_float(params.get("balance_threshold"), 5.0, minimum=1.0, maximum=50.0),
        "extractor_rate_threshold": _parse_dashboard_float(params.get("extractor_rate_threshold"), 0.0, minimum=0.0, maximum=50000.0),
        "single_extractor_rate_threshold": _parse_dashboard_float(params.get("single_extractor_rate_threshold"), 0.0, minimum=0.0, maximum=100000.0),
        "sort": sort_key,
        "order": sort_order,
    }


def _build_dashboard_page_numbers(current_page: int, total_pages: int, radius: int = DASHBOARD_PAGE_WINDOW_RADIUS) -> list[int]:
    start = max(1, current_page - radius)
    end = min(total_pages, current_page + radius)
    return list(range(start, end + 1))


def _colony_matches_dashboard_filters(colony: dict, view_state: dict) -> bool:
    char_val = view_state["char"]
    if char_val and colony.get("character_name") != char_val:
        return False

    tiers = view_state["tiers"]
    if tiers and (colony.get("highest_tier") or "P0") not in tiers:
        return False

    balance_threshold = float(view_state["balance_threshold"])
    balance = colony.get("extractor_balance") or {}
    rate_summary = colony.get("extractor_rate_summary") or {}
    extractor_status = colony.get("extractor_status") or {}

    has_balance = bool(balance.get("extractors")) and len(balance.get("extractors") or []) == 2
    try:
        balance_diff_pct = float(balance.get("diff_pct", -1))
    except (TypeError, ValueError):
        balance_diff_pct = -1.0
    is_balanced = has_balance and balance_diff_pct >= 0 and balance_diff_pct <= balance_threshold
    is_unbalanced = has_balance and balance_diff_pct > balance_threshold

    try:
        min_rate = float(rate_summary.get("min_avg_per_hour", -1))
    except (TypeError, ValueError):
        min_rate = -1.0
    try:
        total_rate = float(rate_summary.get("total_avg_per_hour", -1))
    except (TypeError, ValueError):
        total_rate = -1.0
    extractor_count = int(extractor_status.get("total", 0) or 0)

    multi_rate_threshold = float(view_state["extractor_rate_threshold"])
    if multi_rate_threshold > 0 and extractor_count >= 2:
        if not (min_rate >= 0 and min_rate < multi_rate_threshold):
            return False

    single_rate_threshold = float(view_state["single_extractor_rate_threshold"])
    if single_rate_threshold > 0 and extractor_count == 1:
        if not (total_rate >= 0 and total_rate < single_rate_threshold):
            return False

    only_balanced = view_state["balanced"]
    only_unbalanced = view_state["unbalanced"]
    only_active = view_state["active"]
    only_expired = view_state["expired"]
    only_stalled = view_state["stalled"]
    has_state_filter = only_balanced or only_unbalanced or only_active or only_expired or only_stalled
    if has_state_filter:
        expiry_hours = colony.get("expiry_hours")
        is_expired = expiry_hours is not None and expiry_hours < 0
        is_stalled = expiry_hours is None and colony.get("is_stalled") is True
        is_active = (expiry_hours is not None and expiry_hours > 0) or colony.get("is_stalled") is False
        state_ok = (
            (only_balanced and is_balanced)
            or (only_unbalanced and is_unbalanced)
            or (only_active and is_active)
            or (only_expired and is_expired)
            or (only_stalled and is_stalled)
        )
        if not state_ok:
            return False

    return True


def _sort_dashboard_colonies(colonies: list[dict], view_state: dict) -> list[dict]:
    sort_key = view_state.get("sort") or ""
    if not sort_key:
        return list(colonies)

    reverse = view_state.get("order") == "desc"

    def _sort_value(colony: dict):
        if sort_key == "char":
            return (colony.get("character_name") or "").lower()
        if sort_key == "planet":
            return (colony.get("planet_name") or "").lower()
        if sort_key == "type":
            return (colony.get("planet_type") or "").lower()
        if sort_key == "level":
            return int(colony.get("upgrade_level") or 0)
        if sort_key == "tier":
            raw = str(colony.get("highest_tier") or "P0")
            return int(raw.replace("P", "") or 0)
        if sort_key == "expiry":
            expiry_hours = colony.get("expiry_hours")
            return float(expiry_hours) if expiry_hours is not None else 9_999_999.0
        if sort_key == "isk":
            return float(colony.get("isk_day") or 0.0)
        if sort_key == "storage":
            storage = colony.get("storage") or []
            return max((float(item.get("fill_pct") or 0.0) for item in storage), default=-1.0)
        return ""

    return sorted(colonies, key=_sort_value, reverse=reverse)


def _build_dashboard_page_url(base_path: str, view_state: dict, **overrides) -> str:
    merged = {**view_state, **overrides}
    query: dict[str, str] = {}
    if merged.get("page", 1) != 1:
        query["page"] = str(merged["page"])
    if merged.get("page_size", 25) != 25:
        query["page_size"] = str(merged["page_size"])
    if merged.get("char"):
        query["char"] = str(merged["char"])
    if merged.get("tiers"):
        query["tiers"] = ",".join(merged["tiers"])
    for flag in ("balanced", "unbalanced", "active", "expired", "stalled"):
        if merged.get(flag):
            query[flag] = "1"
    if float(merged.get("balance_threshold", 5.0)) != 5.0:
        query["balance_threshold"] = str(merged["balance_threshold"])
    if float(merged.get("extractor_rate_threshold", 0.0)) != 0.0:
        query["extractor_rate_threshold"] = str(merged["extractor_rate_threshold"])
    if float(merged.get("single_extractor_rate_threshold", 0.0)) != 0.0:
        query["single_extractor_rate_threshold"] = str(merged["single_extractor_rate_threshold"])
    if merged.get("sort"):
        query["sort"] = str(merged["sort"])
    if merged.get("order", "asc") != "asc":
        query["order"] = str(merged["order"])
    if not query:
        return base_path
    return f"{base_path}?{urlencode(query)}"


@router.get("/export.csv")
def export_colonies_csv(
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """Export detailed colony data as CSV for the current account."""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    cached = _load_colony_cache(account.id, db)
    colonies: list[dict] = (cached.get("colonies") or []) if cached else []
    _recompute_expiry(colonies)
    characters = db.query(Character).filter(Character.account_id == account.id).all()
    char_id_by_name = {char.character_name: char.eve_character_id for char in characters}

    def _csv_status(colony: dict) -> str:
        expiry_hours = colony.get("expiry_hours")
        if expiry_hours is not None and expiry_hours < 0:
            return "Expired"
        if colony.get("is_stalled"):
            return "Stalled"
        if colony.get("is_active"):
            return "Extracting"
        return "Idle"

    def _csv_is_working(colony: dict) -> bool:
        expiry_hours = colony.get("expiry_hours")
        if expiry_hours is not None:
            return expiry_hours > 0
        return colony.get("is_stalled") is False

    def _csv_final_product(colony: dict) -> str:
        highest_tier_num = int(colony.get("highest_tier_num") or 0)
        productions = colony.get("productions") or {}
        prod_tiers = colony.get("prod_tiers") or {}
        names = sorted(name for name in productions if prod_tiers.get(name, 0) == highest_tier_num)
        return " | ".join(names)

    def _csv_capacity_breakdown(colony: dict) -> tuple[float, float, float]:
        storage_entries = colony.get("storage") or []
        total_capacity = sum(float(entry.get("capacity") or 0.0) for entry in storage_entries)
        launchpad_used = sum(
            float(entry.get("used_m3") or 0.0)
            for entry in storage_entries
            if str(entry.get("struct") or "").lower().startswith("launchpad")
        )
        storage_used = sum(
            float(entry.get("used_m3") or 0.0)
            for entry in storage_entries
            if not str(entry.get("struct") or "").lower().startswith("launchpad")
        )
        return total_capacity, launchpad_used + storage_used, launchpad_used, storage_used

    def _csv_expiry_reason(colony: dict, total_capacity: float, total_used: float) -> str:
        expiry_hours = colony.get("expiry_hours")
        if expiry_hours is not None and expiry_hours < 0:
            return "Extractor expired"
        if total_capacity > 0 and total_used >= (total_capacity * 0.95):
            return "Storage full"
        if colony.get("is_stalled"):
            return "Factory stalled"
        missing_inputs = colony.get("missing_inputs") or []
        if missing_inputs:
            return "Missing inputs"
        return ""

    def _csv_avg_rates(colony: dict, count: int = 4) -> list[float]:
        extractors = ((colony.get("extractor_rate_summary") or {}).get("extractors") or [])
        values = []
        for extractor in extractors[:count]:
            values.append(round(float(extractor.get("avg_per_hour") or 0.0), 2))
        while len(values) < count:
            values.append(0.0)
        return values

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Character name",
        "Character ID",
        "Planet name",
        "Planet type",
        "System name",
        "System ID",
        "Status",
        "Is working",
        "Final product",
        "Product capacity",
        "Used capacity total",
        "Used capacity launchpad",
        "Used capacity storage",
        "Expires at",
        "Expiry reason",
        "Avg. per hour 1",
        "Avg. per hour 2",
        "Avg. per hour 3",
        "Avg. per hour 4",
    ])
    for c in colonies:
        total_capacity, total_used, launchpad_used, storage_used = _csv_capacity_breakdown(c)
        avg_rates = _csv_avg_rates(c)
        writer.writerow([
            c.get("character_name", ""),
            char_id_by_name.get(c.get("character_name", ""), ""),
            c.get("planet_name", ""),
            c.get("planet_type", ""),
            c.get("solar_system_name", ""),
            c.get("solar_system_id", ""),
            _csv_status(c),
            "true" if _csv_is_working(c) else "false",
            _csv_final_product(c),
            round(total_capacity, 2),
            round(total_used, 2),
            round(launchpad_used, 2),
            round(storage_used, 2),
            c.get("expiry_iso") or "",
            _csv_expiry_reason(c, total_capacity, total_used),
            *avg_rates,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=pi_colonies_detailed.csv"},
    )


@router.get("/webhook-settings")
def get_webhook_settings(
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """Return current webhook alert settings for the account."""
    from app.models import WebhookAlert
    row = db.query(WebhookAlert).filter_by(account_id=account.id).first()
    if not row:
        return JSONResponse({"webhook_url": "", "alert_hours": 2, "enabled": False})
    return JSONResponse({
        "webhook_url": row.webhook_url or "",
        "alert_hours": row.alert_hours or 2,
        "enabled": bool(row.enabled),
    })


@router.post("/webhook-settings")
def save_webhook_settings(
    data: dict = Body(...),
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """Save Discord/webhook alert settings."""
    from app.models import WebhookAlert
    webhook_url = (data.get("webhook_url") or "").strip()
    alert_hours = int(data.get("alert_hours") or 2)
    enabled = bool(data.get("enabled", True))

    if webhook_url and not _is_safe_webhook_url(webhook_url):
        raise HTTPException(status_code=400, detail="Webhook URL muss mit https://discord.com/, https://discord.gg/ oder https://discordapp.com/ beginnen.")
    if alert_hours < 1 or alert_hours > 72:
        raise HTTPException(status_code=400, detail="alert_hours must be between 1 and 72")

    row = db.query(WebhookAlert).filter_by(account_id=account.id).first()
    if row:
        row.webhook_url = webhook_url or None
        row.alert_hours = alert_hours
        row.enabled = enabled
    else:
        from app.models import WebhookAlert as _WA
        db.add(_WA(
            account_id=account.id,
            webhook_url=webhook_url or None,
            alert_hours=alert_hours,
            enabled=enabled,
        ))
    db.commit()
    return JSONResponse({"ok": True})


@router.post("/webhook-test")
def test_webhook(
    data: dict = Body(default={}),
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """Send a test message to the webhook URL.

    Accepts an optional ``webhook_url`` in the request body so the URL
    currently in the form can be tested before saving.
    Falls back to the saved DB URL when no URL is supplied.
    """
    import requests as _requests
    from app.models import WebhookAlert

    # Prefer URL from request body (unsaved form value) over DB
    webhook_url = (data.get("webhook_url") or "").strip()
    if not webhook_url:
        row = db.query(WebhookAlert).filter_by(account_id=account.id).first()
        webhook_url = (row.webhook_url or "").strip() if row else ""

    if not webhook_url:
        return JSONResponse({"ok": False, "error": "Keine Webhook-URL angegeben. Bitte URL eingeben und speichern."}, status_code=400)

    if not _is_safe_webhook_url(webhook_url):
        return JSONResponse({"ok": False, "error": "URL muss mit https://discord.com/ beginnen."}, status_code=400)

    try:
        resp = _requests.post(
            webhook_url,
            json={"content": "✅ **EVE PI Manager** — Webhook test erfolgreich!"},
            timeout=10,
        )
        if resp.status_code in (200, 204):
            return JSONResponse({"ok": True})
        if resp.status_code == 403:
            msg = "403 Forbidden — Webhook-URL ungültig oder gelöscht. Bitte neuen Webhook in Discord erstellen."
        elif resp.status_code == 404:
            msg = "404 Not Found — Webhook-URL existiert nicht. URL prüfen."
        else:
            msg = f"HTTP {resp.status_code}"
        return JSONResponse({"ok": False, "error": msg}, status_code=200)
    except Exception as exc:
        logger.warning("webhook test failed: %s", exc)
        return JSONResponse({"ok": False, "error": "Webhook-Test fehlgeschlagen. Verbindung prüfen."}, status_code=200)


@router.get("/characters", response_class=HTMLResponse)
def characters_page(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    _backfill_character_colony_sync_status_from_cache(account.id, [], db)
    characters = db.query(Character).filter(Character.account_id == account.id).all()
    _attach_pi_skills(characters, db)
    return templates.TemplateResponse("characters.html", {
        "request": request,
        "account": account,
        "characters": characters,
    })


@router.post("/characters/{character_id}/vacation")
def toggle_character_vacation(
    character_id: int,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    char = (
        db.query(Character)
        .filter(Character.id == character_id, Character.account_id == account.id)
        .first()
    )
    if not char:
        raise HTTPException(status_code=404, detail="Character not found")
    char.vacation_mode = not bool(char.vacation_mode)
    db.commit()
    invalidate_dashboard_cache(account.id)
    return JSONResponse({"ok": True, "vacation_mode": bool(char.vacation_mode)})
