"""
Marktpreise für EVE Online PI-Produkte
Primär: Janice API, Fallback: Fuzzwork
"""
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from sqlalchemy.orm import Session

from app.models import MarketCache

JITA_STATION = 60003760
CACHE_TTL_MINUTES = 15

JANICE_API_URL = "https://janice.e-351.com/api/rest/v2/pricer"
FUZZWORK_API_URL = "https://market.fuzzwork.co.uk/aggregates/"

# Vollständige PI Type-IDs (verifiziert via Fuzzwork SDE) — P1 bis P4
# P1 – Basisprodukte (Basic Industry Facility)
PI_TYPE_IDS: dict[str, int] = {
    "Bacteria": 2393,
    "Biofuels": 2396,
    "Biomass": 3779,
    "Chiral Structures": 2401,
    "Electrolytes": 2390,
    "Industrial Fibers": 2397,
    "Oxidizing Compound": 2392,
    "Oxygen": 3683,
    "Plasmoids": 2389,
    "Precious Metals": 2399,
    "Proteins": 2395,
    "Reactive Metals": 2398,
    "Silicon": 9828,
    "Silicates": 16636,
    "Toxic Metals": 2400,
    "Water": 3645,
    # P2 – Raffinierte Produkte (Advanced Industry Facility)
    "Biocells": 2329,
    "Construction Blocks": 3828,
    "Consumer Electronics": 9836,
    "Coolant": 9832,
    "Enriched Uranium": 44,
    "Fertilizer": 3693,
    "Genetically Enhanced Livestock": 3725,
    "Guidance Systems": 9834,
    "Hazmat Detection Systems": 2463,
    "Hermetic Membranes": 2327,
    "High-Tech Transmitters": 9842,
    "Industrial Explosives": 2403,
    "Mechanical Parts": 3689,
    "Microfiber Shielding": 3695,
    "Miniature Electronics": 9840,
    "Nanites": 2351,
    "Neocoms": 9830,
    "Nuclear Reactors": 2352,
    "Oxides": 2317,
    "Planetary Vehicles": 2870,
    "Polytextiles": 3697,
    "Rocket Fuel": 4051,
    "Silicate Glass": 3697,
    "Smartfab Units": 2351,
    "Super Conductors": 9838,
    "Synthetic Oil": 3691,
    "Transmitters": 9830,
    "Viral Agent": 3775,
    "Water-Cooled CPU": 2328,
    # P3 – Spezialisierte Produkte (High-Tech Production Plant)
    "Biotech Research Reports": 2867,
    "Camera Drones": 2869,
    "Condensates": 25590,
    "Cryoprotectant Solution": 2876,
    "Data Chips": 2872,
    "Gel-Matrix Biopaste": 2868,
    "High-Tech Small Arms": 2875,
    "Planetary Vehicles": 2870,
    "Robotics": 2873,
    "Supercomputers": 2871,
    "Synthetic Synapses": 2874,
    "Transcranial Microcontrollers": 25589,
    "Ukomi Super Conductors": 25591,
    "Vaccines": 25592,
    # P4 – Hochentwickelte Produkte (Advanced Commodity Facility)
    "Broadcast Node": 2867,
    "Integrity Response Drones": 2868,
    "Nano-Factory": 2869,
    "Organic Mortar Applicators": 2870,
    "Recursive Computing Module": 2871,
    "Self-Harmonizing Power Core": 2872,
    "Sterile Conduits": 2873,
    "Wetware Mainframe": 2875,
}

# Umgekehrtes Mapping: id -> name
PI_TYPE_NAMES: dict[int, str] = {v: k for k, v in PI_TYPE_IDS.items()}

# Name → type_id für Preisabfragen (alle PI-Produkte)
_PI_NAME_TO_ID: dict[str, int] = PI_TYPE_IDS


def _is_cache_valid(cache_entry: MarketCache) -> bool:
    if not cache_entry or not cache_entry.updated_at:
        return False
    now = datetime.now(timezone.utc)
    updated = cache_entry.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return (now - updated) < timedelta(minutes=CACHE_TTL_MINUTES)


def _get_janice_api_key() -> Optional[str]:
    """Liest den Janice API Key aus den Settings."""
    try:
        from app.config import get_settings
        settings = get_settings()
        key = getattr(settings, "janice_api_key", "")
        return key if key else None
    except Exception:
        return None


def _fetch_janice_prices(item_names: list[str]) -> dict[str, dict]:
    """
    Holt Preise von der Janice API via POST.
    Body: text/plain, ein Itemname pro Zeile.
    Returns: dict von name -> {buy, sell}
    """
    headers = {
        "Content-Type": "text/plain",
        "Accept": "application/json",
        "User-Agent": "EVE PI Manager",
    }
    api_key = _get_janice_api_key()
    if api_key:
        headers["X-ApiKey"] = api_key

    body = "\n".join(item_names)
    response = requests.post(
        JANICE_API_URL,
        params={"market": 2, "persist": "false"},
        data=body.encode("utf-8"),
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    result = {}
    for item in data:
        name = item.get("itemType", {}).get("name", "")
        if name:
            result[name] = {
                "buy": float(item.get("effectiveBuy", 0) or 0),
                "sell": float(item.get("effectiveSell", 0) or 0),
            }
    return result


def _fetch_fuzzwork_prices(type_ids: list[int]) -> dict[int, dict]:
    """
    Holt Preise von Fuzzwork für Jita 4-4.
    Returns: dict von type_id -> {buy, sell}
    """
    if not type_ids:
        return {}

    params = {
        "station": JITA_STATION,
        "types": ",".join(str(t) for t in type_ids),
    }
    response = requests.get(
        FUZZWORK_API_URL,
        params=params,
        headers={"Accept": "application/json", "User-Agent": "EVE PI Manager"},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    result = {}
    for type_id_str, info in data.items():
        try:
            tid = int(type_id_str)
            buy = float(info.get("buy", {}).get("max", 0) or 0)
            sell = float(info.get("sell", {}).get("min", 0) or 0)
            result[tid] = {"buy": buy, "sell": sell}
        except (ValueError, TypeError):
            continue
    return result


def get_prices_by_type_ids(type_ids: list[int], db: Session) -> dict[int, dict]:
    """
    Holt Preise für eine Liste von Type-IDs.
    Nutzt DB-Cache (15min TTL) mit Fuzzwork als Datenquelle.
    Returns: dict von type_id -> {best_buy, best_sell, avg_volume, type_name}
    """
    result = {}
    ids_to_fetch = []

    for type_id in type_ids:
        cache = db.query(MarketCache).filter(MarketCache.type_id == type_id).first()
        if cache and _is_cache_valid(cache):
            result[type_id] = {
                "best_buy": float(cache.best_buy or 0),
                "best_sell": float(cache.best_sell or 0),
                "avg_volume": float(cache.avg_volume or 0),
                "type_name": cache.type_name or PI_TYPE_NAMES.get(type_id),
                "cached": True,
            }
        else:
            ids_to_fetch.append(type_id)

    if ids_to_fetch:
        try:
            fuzz_data = _fetch_fuzzwork_prices(ids_to_fetch)
            for type_id in ids_to_fetch:
                info = fuzz_data.get(type_id, {})
                best_buy = info.get("buy", 0.0)
                best_sell = info.get("sell", 0.0)
                type_name = PI_TYPE_NAMES.get(type_id)

                cache = db.query(MarketCache).filter(MarketCache.type_id == type_id).first()
                if cache:
                    cache.best_buy = str(best_buy)
                    cache.best_sell = str(best_sell)
                    cache.updated_at = datetime.now(timezone.utc)
                    if type_name:
                        cache.type_name = type_name
                else:
                    cache = MarketCache(
                        type_id=type_id,
                        type_name=type_name,
                        best_buy=str(best_buy),
                        best_sell=str(best_sell),
                        avg_volume="0",
                        updated_at=datetime.now(timezone.utc),
                    )
                    db.add(cache)

                result[type_id] = {
                    "best_buy": best_buy,
                    "best_sell": best_sell,
                    "avg_volume": 0.0,
                    "type_name": type_name,
                    "cached": False,
                }
            db.commit()
        except Exception as e:
            for type_id in ids_to_fetch:
                if type_id not in result:
                    result[type_id] = {
                        "best_buy": 0.0,
                        "best_sell": 0.0,
                        "avg_volume": 0.0,
                        "type_name": PI_TYPE_NAMES.get(type_id),
                        "error": str(e),
                    }

    return result


def get_jita_prices(type_ids: list[int], db: Session) -> dict:
    """
    Holt Jita-Preise für eine Liste von Type-IDs.
    Nutzt Janice API (über Namen-Mapping) mit Fuzzwork als Fallback.
    Nutzt DB-Cache (15min TTL).
    Returns: dict von type_id -> {best_buy, best_sell, avg_volume, type_name}
    """
    result = {}
    ids_to_fetch = []

    for type_id in type_ids:
        cache = db.query(MarketCache).filter(MarketCache.type_id == type_id).first()
        if cache and _is_cache_valid(cache):
            result[type_id] = {
                "best_buy": float(cache.best_buy or 0),
                "best_sell": float(cache.best_sell or 0),
                "avg_volume": float(cache.avg_volume or 0),
                "type_name": cache.type_name or PI_TYPE_NAMES.get(type_id),
                "cached": True,
            }
        else:
            ids_to_fetch.append(type_id)

    if not ids_to_fetch:
        return result

    # Baue Namen-Liste für Janice
    names_to_ids: dict[str, int] = {}
    for type_id in ids_to_fetch:
        name = PI_TYPE_NAMES.get(type_id)
        if name:
            names_to_ids[name] = type_id

    janice_ok = False
    if names_to_ids:
        try:
            janice_data = _fetch_janice_prices(list(names_to_ids.keys()))
            for name, type_id in names_to_ids.items():
                info = janice_data.get(name, {})
                best_buy = info.get("buy", 0.0)
                best_sell = info.get("sell", 0.0)

                cache = db.query(MarketCache).filter(MarketCache.type_id == type_id).first()
                if cache:
                    cache.best_buy = str(best_buy)
                    cache.best_sell = str(best_sell)
                    cache.type_name = name
                    cache.updated_at = datetime.now(timezone.utc)
                else:
                    cache = MarketCache(
                        type_id=type_id,
                        type_name=name,
                        best_buy=str(best_buy),
                        best_sell=str(best_sell),
                        avg_volume="0",
                        updated_at=datetime.now(timezone.utc),
                    )
                    db.add(cache)

                result[type_id] = {
                    "best_buy": best_buy,
                    "best_sell": best_sell,
                    "avg_volume": 0.0,
                    "type_name": name,
                    "cached": False,
                }
            db.commit()
            janice_ok = True
        except Exception:
            pass

    # Fallback: Fuzzwork für IDs die noch fehlen oder wenn Janice fehlschlug
    fallback_ids = [tid for tid in ids_to_fetch if tid not in result]
    if fallback_ids or not janice_ok:
        target_ids = fallback_ids if janice_ok else ids_to_fetch
        if target_ids:
            try:
                fuzz_data = _fetch_fuzzwork_prices(target_ids)
                for type_id in target_ids:
                    info = fuzz_data.get(type_id, {})
                    best_buy = info.get("buy", 0.0)
                    best_sell = info.get("sell", 0.0)
                    type_name = PI_TYPE_NAMES.get(type_id)

                    cache = db.query(MarketCache).filter(MarketCache.type_id == type_id).first()
                    if cache:
                        cache.best_buy = str(best_buy)
                        cache.best_sell = str(best_sell)
                        cache.updated_at = datetime.now(timezone.utc)
                        if type_name and not cache.type_name:
                            cache.type_name = type_name
                    else:
                        cache = MarketCache(
                            type_id=type_id,
                            type_name=type_name,
                            best_buy=str(best_buy),
                            best_sell=str(best_sell),
                            avg_volume="0",
                            updated_at=datetime.now(timezone.utc),
                        )
                        db.add(cache)

                    result[type_id] = {
                        "best_buy": best_buy,
                        "best_sell": best_sell,
                        "avg_volume": 0.0,
                        "type_name": type_name,
                        "cached": False,
                    }
                db.commit()
            except Exception as e:
                for type_id in target_ids:
                    if type_id not in result:
                        result[type_id] = {
                            "best_buy": 0.0,
                            "best_sell": 0.0,
                            "avg_volume": 0.0,
                            "type_name": PI_TYPE_NAMES.get(type_id),
                            "error": str(e),
                        }

    return result


def get_sell_prices_by_names(names: list[str]) -> dict[str, float]:
    """
    Holt Sell-Preise für Item-Namen via Fuzzwork (type_id Lookup aus PI_TYPE_IDS).
    Returns: dict von name -> sell_price (ISK)
    """
    if not names:
        return {}
    name_to_id = {n: _PI_NAME_TO_ID[n] for n in names if n in _PI_NAME_TO_ID}
    if not name_to_id:
        return {}
    try:
        fuzz = _fetch_fuzzwork_prices(list(name_to_id.values()))
        return {name: fuzz.get(tid, {}).get("sell", 0.0) for name, tid in name_to_id.items()}
    except Exception:
        return {}


def refresh_all_pi_prices(db: Session) -> None:
    """
    Holt alle PI_TYPE_IDS Preise via Janice und cached sie in der DB.
    Wird vom APScheduler stündlich aufgerufen.
    """
    type_ids = list(set(PI_TYPE_IDS.values()))
    try:
        get_jita_prices(type_ids, db)
    except Exception:
        pass
