import json as _json
import logging
import threading as _threading
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Body
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_account
from app.esi import ensure_valid_token, get_character_planets, get_planet_detail, get_planet_info, get_schematic, invalidate_planet_detail_cache, get_character_roles, get_character_skills, get_corporation_info
from app.i18n import get_language_from_request, translate_type_name
from app.market import get_prices_by_mode, get_market_last_updated, PI_TYPE_IDS
from app.models import Account, Character, DashboardCache, IskSnapshot, SkyhookEntry, SkyhookItem
from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import joinedload as _joinedload
from app.pi_data import PLANET_TYPE_COLORS, ALL_P1, ALL_P2, ALL_P3, ALL_P4
from app import sde as _sde
from app.templates_env import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

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


_corp_load_running: dict[int, dict] = {}  # corp_id -> lock/status
_CORP_LOAD_LOCK_TTL = 60 * 30


def _get_corp_load_lock(corp_id: int | None) -> dict | None:
    if not corp_id:
        return None
    lock = _corp_load_running.get(corp_id)
    if not lock:
        return None
    started_at = float(lock.get("started_at") or 0.0)
    if started_at and (_time.time() - started_at) > _CORP_LOAD_LOCK_TTL:
        _corp_load_running.pop(corp_id, None)
        return None
    return lock


def invalidate_dashboard_cache(account_id: int) -> None:
    """Cache für einen Account sofort verwerfen (in-memory + DB)."""
    _dashboard_cache.pop(account_id, None)


def _touch_colony_cache(account_id: int, db: Session) -> None:
    """Nur fetched_at aktualisieren ohne Kolonie-Daten zu überschreiben."""
    try:
        row = db.query(DashboardCache).filter(DashboardCache.account_id == account_id).first()
        if row:
            row.fetched_at = datetime.now(timezone.utc)
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
            if not account or not chars:
                finished = True
                return
            pm = getattr(account, "price_mode", "sell")
            payload = _build_dashboard_payload(account, chars, newdb, price_mode=pm)
            colony_count = payload.get("colony_count", 0)
            if colony_count > 0:
                _save_colony_cache(account_id, payload, newdb)
                _dashboard_cache[account_id] = {**payload, "price_mode": pm}
                logger.info(f"BG-Refresh account {account_id}: {colony_count} Kolonien gespeichert")
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


def _build_dashboard_payload(account, characters: list, db: Session, price_mode: str = "sell") -> dict:
    """Holt alle ESI/Markt-Daten frisch und gibt den vollständigen Payload zurück."""

    # Schritt 1: Alle (char, colony, access_token) sammeln
    char_colony_token: list[tuple] = []
    for char in characters:
        access_token = ensure_valid_token(char, db)
        if not access_token:
            logger.warning(f"Kein gültiges Token für Char {char.character_name} ({char.eve_character_id}) – übersprungen")
            continue
        raw_colonies = get_character_planets(char.eve_character_id, access_token)
        logger.debug(f"ESI Kolonien für {char.character_name}: {len(raw_colonies)}")
        for colony in raw_colonies:
            char_colony_token.append((char, colony, access_token))

    # Schritt 2: planet_info + planet_detail parallel abrufen
    def _fetch_planet(args):
        char, colony, token = args
        planet_id = colony.get("planet_id")
        info = get_planet_info(planet_id) if planet_id else {}
        detail = {}
        if token and planet_id:
            try:
                detail = get_planet_detail(char.eve_character_id, planet_id, token)
            except Exception as e:
                logger.warning(f"Fehler bei Planet {planet_id}: {e}")
        return info, detail

    with ThreadPoolExecutor(max_workers=10) as ex:
        planet_data = list(ex.map(_fetch_planet, char_colony_token))

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
        })

    colony_count = len(colonies)
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

    main_char = db.query(Character).filter(Character.id == account.main_character_id).first() if account.main_character_id else None

    characters = db.query(Character).filter(Character.account_id == account.id).all()
    now = _time.time()
    current_price_mode = getattr(account, "price_mode", "sell")

    # ── Schritt 1: DB-Cache laden (schnell, keine ESI-Calls) ──────────────────
    db_cached = _load_colony_cache(account.id, db)
    refreshing = account.id in _bg_refresh_running and not _bg_refresh_done.get(account.id, False)

    if db_cached:
        cache_age = now - db_cached["fetched_at"]
        colonies = db_cached["colonies"]
        meta = db_cached["meta"]

        needs_balance_refresh = any(
            ("extractor_balance" not in colony)
            or ("extractor_rate_summary" not in colony)
            or (
                colony.get("extractor_balance") is None
                and int((colony.get("extractor_status") or {}).get("total") or 0) == 2
                and int((colony.get("extractor_status") or {}).get("expired") or 0) == 0
            )
            for colony in colonies
        )
        if needs_balance_refresh:
            payload = _build_dashboard_payload(account, characters, db, price_mode=current_price_mode)
            _save_colony_cache(account.id, payload, db)
            _dashboard_cache[account.id] = {**payload, "price_mode": current_price_mode}

            colonies = payload["colonies"]
            total_isk_day = payload["total_isk_day"]
            next_expiry_dt = payload["next_expiry"]
            next_expiry_hours = payload["next_expiry_hours"]
            next_expiry_char = payload["next_expiry_char"]
            char_count = payload["char_count"]
            colony_count = payload["colony_count"]
            cache_age_sec = 0
        else:
            # Ablaufzeiten relativ zur jetzigen Zeit neu berechnen
            next_expiry_dt, next_expiry_char = _recompute_expiry(colonies)

            # ISK/Tag aus DB-Preiscache (MarketCache) neu berechnen — sehr schnell
            colonies, total_isk_day = _apply_price_mode(colonies, meta, current_price_mode)
            char_count = meta.get("char_count", len(characters))
            colony_count = len(colonies)
            next_expiry_hours = _hours_until(next_expiry_dt)
            if next_expiry_hours is None:
                next_expiry_hours = meta.get("next_expiry_hours")

            # In-Memory-Cache synchron halten (für Skyhook)
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
        # Erster Start: synchron laden und sofort in DB speichern
        payload = _build_dashboard_payload(account, characters, db, price_mode=current_price_mode)
        _save_colony_cache(account.id, payload, db)
        _dashboard_cache[account.id] = {**payload, "price_mode": current_price_mode}

        colonies = payload["colonies"]
        total_isk_day = payload["total_isk_day"]
        next_expiry_dt = payload["next_expiry"]
        next_expiry_hours = payload["next_expiry_hours"]
        next_expiry_char = payload["next_expiry_char"]
        char_count = payload["char_count"]
        colony_count = payload["colony_count"]
        cache_age_sec = 0

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
        for colony in colonies
        if colony.get("expiry_hours") is not None and colony.get("expiry_hours") < 0
    )
    active_colony_count = sum(1 for colony in colonies if colony.get("is_active") is True)
    stalled_colony_count = sum(
        1
        for colony in colonies
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
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "account": account,
        "main_char": main_char,
        "characters": characters,
        "char_count": char_count,
        "colonies": colonies,
        "colony_count": colony_count,
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
        "price_mode": current_price_mode,
        "refreshing": refreshing,
        "market_last_updated_iso": market_last_updated_iso,
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
def refresh_status(account=Depends(require_account)):
    """Liefert ob ein Hintergrund-ESI-Refresh läuft oder gerade fertig wurde."""
    done = _bg_refresh_done.pop(account.id, False)
    running = (account.id in _bg_refresh_running) and not done
    return JSONResponse({"running": running, "done": done})

def _corp_access_flags(account: Account, main_char: Character | None, db: Session) -> dict:
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
    return {
        "corp_id": own_corp_id,
        "corp_name": own_corp_name,
        "is_ceo": is_ceo,
        "is_director": is_director,
        "roles_scope_missing": roles_scope_missing,
        "has_access": has_access,
        "can_manage": bool(own_corp_id and account.is_owner),
    }


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

    corp_colonies: list[dict] = []
    corp_main_rows: list[dict] = []
    corp_accounts: list[dict] = []
    corp_product_rows: list[dict] = []
    uncached_count = 0
    for acc_id in account_ids:
        acc = db.query(Account).filter(Account.id == acc_id).first()
        if not acc:
            continue
        main = db.query(Character).filter(Character.id == acc.main_character_id).first() if acc.main_character_id else None
        account_char_names = {c.character_name for c in chars_by_account.get(acc_id, [])}
        cached = _load_colony_cache(acc_id, db)
        colony_count = 0
        planet_type_counts: dict[str, int] = {}
        if cached:
            colonies = cached.get("colonies", [])
            meta = cached.get("meta", {})
            _recompute_expiry(colonies)
            colonies, _ = _apply_price_mode(colonies, meta, price_mode)
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
        else:
            uncached_count += 1

        corp_accounts.append({
            "account_id": acc_id,
            "main_name": main.character_name if main else f"Account #{acc_id}",
            "is_cached": cached is not None,
        })
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
    total_isk = sum(c.get("isk_day", 0) for c in corp_colonies if c.get("is_active", True))
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

    return templates.TemplateResponse("corp_view.html", {
        "request": request,
        "account": account,
        "corp_name": corp_name,
        "corp_id": active_corp_id,
        "corp_colonies": corp_colonies,
        "corp_main_rows": corp_main_rows,
        "corp_product_rows": corp_product_rows,
        "corp_accounts": corp_accounts,
        "uncached_count": uncached_count,
        "total_colonies": len(corp_colonies),
        "total_isk_day": total_isk,
        "total_mains": len(corp_main_rows),
        "total_characters": len(corp_chars),
        "is_ceo": access["is_ceo"],
        "is_director": access["is_director"],
        "roles_scope_missing": access["roles_scope_missing"],
        "can_manage_cache": access["can_manage"],
        "market_last_updated_iso": market_last_updated_iso,
        "all_products": all_products,
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
    account_ids = {c.account_id for c in corp_chars if c.account_id is not None}
    result = []
    for acc_id in sorted(account_ids):
        acc = db.query(Account).filter(Account.id == acc_id).first()
        if not acc:
            continue
        main = db.query(Character).filter(Character.id == acc.main_character_id).first() \
               if acc.main_character_id else None
        result.append({
            "account_id": acc_id,
            "main_name": main.character_name if main else f"Account #{acc_id}",
            "is_cached": _load_colony_cache(acc_id, db) is not None,
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
        logger.warning(f"force_load_account {target_account_id}: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


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


@router.get("/characters", response_class=HTMLResponse)
def characters_page(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    db.refresh(account)
    characters = db.query(Character).filter(Character.account_id == account.id).all()
    _attach_pi_skills(characters, db)
    return templates.TemplateResponse("characters.html", {
        "request": request,
        "account": account,
        "characters": characters,
    })
