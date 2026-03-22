import logging
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_account
from app.esi import ensure_valid_token, get_character_planets, get_planet_detail, get_planet_info, get_schematic, invalidate_planet_detail_cache
from app.market import get_sell_prices_by_names
from app.models import Account, Character, IskSnapshot
from app.pi_data import PLANET_TYPE_COLORS
from app import sde as _sde
from app.templates_env import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

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


def invalidate_dashboard_cache(account_id: int) -> None:
    """Cache für einen Account sofort verwerfen (z.B. nach Char-Hinzufügen)."""
    _dashboard_cache.pop(account_id, None)

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


def _compute_colony_productions(pins: list) -> tuple[dict[str, float], dict[str, int], str | None]:
    """Gibt (productions, prod_tiers, highest_tier_label) zurück.
    prod_tiers: product_name -> tier_num (1–4).
    """
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
        tier_num = 1 if cycle_time <= 1800 else 2 if cycle_time <= 3600 else 3 if cycle_time <= 9000 else 4
        highest_tier_num = max(highest_tier_num, tier_num)
        qty_per_cycle = schematic.get("output_quantity") or _CYCLE_QTY_FALLBACK.get(cycle_time, 1)
        productions[product_name] = (
            productions.get(product_name, 0.0) + qty_per_cycle * (86400.0 / float(cycle_time))
        )
        prod_tiers[product_name] = tier_num
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
        tier_num = 1 if cycle_time <= 1800 else 2 if cycle_time <= 3600 else 3 if cycle_time <= 9000 else 4
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


def _build_dashboard_payload(account, characters: list, db: Session) -> dict:
    """Holt alle ESI/Markt-Daten frisch und gibt den vollständigen Payload zurück."""

    # Schritt 1: Alle (char, colony, access_token) sammeln
    char_colony_token: list[tuple] = []
    for char in characters:
        access_token = ensure_valid_token(char, db)
        raw_colonies = get_character_planets(char.eve_character_id, access_token or "")
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

    # Schritt 4: Eine einzige Batch-Preisabfrage
    prices = get_sell_prices_by_names(list(all_product_names)) if all_product_names else {}

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
            "expiry_hours": expiry_hours,
            "isk_day": isk_day,
            "highest_tier": highest_tier,
            "factories": _compute_factories(pins, prices),
            "storage": _compute_storage(pins),
            "extractor_status": _get_extractor_status(pins),
            "missing_inputs": _compute_missing_inputs(pins),
        })

    colony_count = len(colonies)
    _record_isk_snapshot(account.id, total_isk_day, colony_count, db)

    return {
        "colonies": colonies,
        "total_isk_day": total_isk_day,
        "next_expiry": next_expiry,
        "next_expiry_hours": _hours_until(next_expiry),
        "next_expiry_char": next_expiry_char,
        "char_count": len(characters),
        "colony_count": colony_count,
        "fetched_at": _time.time(),
    }


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    db.refresh(account)

    main_char = None
    if account.main_character_id:
        main_char = db.query(Character).filter(Character.id == account.main_character_id).first()

    characters = db.query(Character).filter(Character.account_id == account.id).all()

    # Cache prüfen
    now = _time.time()
    cached = _dashboard_cache.get(account.id)
    if cached and (now - cached["fetched_at"]) < DASHBOARD_CACHE_TTL:
        payload = cached
    else:
        payload = _build_dashboard_payload(account, characters, db)
        _dashboard_cache[account.id] = payload

    # ISK-Historie (immer frisch aus DB — billig)
    snapshots = (
        db.query(IskSnapshot)
        .filter(IskSnapshot.account_id == account.id)
        .order_by(IskSnapshot.recorded_at)
        .limit(60)
        .all()
    )
    isk_history = [
        {"date": s.recorded_at.strftime("%d.%m"), "isk": float(s.isk_day or 0)}
        for s in snapshots
    ]

    cache_age_sec = int(now - payload["fetched_at"])
    cooldown_remaining = max(0, int(REFRESH_COOLDOWN_SEC - (now - _refresh_cooldown.get(account.id, 0))))

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "account": account,
        "main_char": main_char,
        "characters": characters,
        "char_count": payload["char_count"],
        "colonies": payload["colonies"],
        "colony_count": payload["colony_count"],
        "planet_type_colors": PLANET_TYPE_COLORS,
        "total_isk_day": payload["total_isk_day"],
        "next_expiry": payload["next_expiry"],
        "next_expiry_hours": payload["next_expiry_hours"],
        "next_expiry_char": payload["next_expiry_char"],
        "cache_age_sec": cache_age_sec,
        "cooldown_remaining": cooldown_remaining,
        "isk_history": isk_history,
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
    finally:
        db.close()
    _refresh_cooldown[account.id] = now
    return JSONResponse({"ok": True})


@router.get("/overview", response_class=HTMLResponse)
def overview_page(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """Gesamt-Übersicht aller Kolonien (nur für Owner und Admins)."""
    if not (account.is_owner or account.is_admin):
        raise HTTPException(status_code=403, detail="Kein Zugriff")

    all_accounts = db.query(Account).all()
    all_colonies: list[dict] = []
    uncached: list[dict] = []

    for acc in all_accounts:
        cached = _dashboard_cache.get(acc.id)
        if cached:
            for colony in cached.get("colonies", []):
                all_colonies.append(colony)
        else:
            chars = db.query(Character).filter(Character.account_id == acc.id).all()
            main = None
            if acc.main_character_id:
                main = db.query(Character).filter(Character.id == acc.main_character_id).first()
            uncached.append({
                "id": acc.id,
                "main_name": main.character_name if main else f"Account #{acc.id}",
                "char_count": len(chars),
            })

    all_colonies.sort(key=lambda x: (x.get("character_name", ""), x.get("planet_name", "")))
    total_isk = sum(c.get("isk_day", 0) for c in all_colonies)

    return templates.TemplateResponse("overview.html", {
        "request": request,
        "account": account,
        "all_colonies": all_colonies,
        "uncached": uncached,
        "total_colonies": len(all_colonies),
        "total_isk_day": total_isk,
    })


@router.get("/corp", response_class=HTMLResponse)
def corp_view_page(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """Korporation-Übersicht: nur für CEO, Owner und Admins."""
    from app.esi import get_corporation_info

    main_char = None
    if account.main_character_id:
        main_char = db.query(Character).filter(Character.id == account.main_character_id).first()

    corp_id = main_char.corporation_id if main_char else None
    corp_name = main_char.corporation_name if main_char else None
    is_ceo = False

    if corp_id:
        try:
            corp_info = get_corporation_info(corp_id)
            corp_name = corp_info.get("name", corp_name or f"Corp #{corp_id}")
            if corp_info.get("ceo_id") == (main_char.eve_character_id if main_char else None):
                is_ceo = True
        except Exception:
            pass

    if not (account.is_owner or account.is_admin or is_ceo):
        raise HTTPException(status_code=403, detail="Kein Zugriff — nur für CEO, Admins und den Besitzer")

    # Alle Chars in dieser Corp
    if corp_id:
        corp_chars = db.query(Character).filter(Character.corporation_id == corp_id).all()
    else:
        corp_chars = []

    corp_char_names = {c.character_name for c in corp_chars}
    account_ids = {c.account_id for c in corp_chars}

    corp_colonies: list[dict] = []
    uncached_count = 0
    for acc_id in account_ids:
        cached = _dashboard_cache.get(acc_id)
        if cached:
            for colony in cached.get("colonies", []):
                if colony.get("character_name") in corp_char_names:
                    corp_colonies.append(colony)
        else:
            uncached_count += 1

    corp_colonies.sort(key=lambda x: (x.get("character_name", ""), x.get("planet_name", "")))
    total_isk = sum(c.get("isk_day", 0) for c in corp_colonies)

    return templates.TemplateResponse("corp_view.html", {
        "request": request,
        "account": account,
        "corp_name": corp_name or "Unbekannte Korporation",
        "corp_id": corp_id,
        "corp_colonies": corp_colonies,
        "uncached_count": uncached_count,
        "total_colonies": len(corp_colonies),
        "total_isk_day": total_isk,
        "is_ceo": is_ceo,
    })


@router.get("/characters", response_class=HTMLResponse)
def characters_page(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    db.refresh(account)
    characters = db.query(Character).filter(Character.account_id == account.id).all()
    return templates.TemplateResponse("characters.html", {
        "request": request,
        "account": account,
        "characters": characters,
    })
