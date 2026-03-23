"""
Marktpreise für EVE Online PI-Produkte
Primär: Janice API, Fallback: Fuzzwork
"""
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from sqlalchemy.orm import Session

from app.models import MarketCache

JITA_STATION = 60003760
CACHE_TTL_MINUTES = 15
MARKET_FORCE_REFRESH_COOLDOWN = 300.0  # 5 Minuten Admin-Cooldown (server-weit)

# Server-weiter Zustand für Admin-Force-Refresh
_market_last_forced_refresh: float = 0.0

JANICE_API_URL = "https://janice.e-351.com/api/rest/v2/pricer"
FUZZWORK_API_URL = "https://market.fuzzwork.co.uk/aggregates/"
ESI_HISTORY_URL = "https://esi.evetech.net/latest/markets/10000002/history/"
HISTORY_CACHE_TTL = 86400.0  # 24h in-memory cache for market history

_history_cache: dict[int, tuple[float, list]] = {}  # type_id -> (timestamp, sorted_data)

# Vollständige PI Type-IDs (verifiziert via EVE ESI API) — P1 bis P4
PI_TYPE_IDS: dict[str, int] = {
    # P1 – Basic Commodities (group 1042)
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
    "Toxic Metals": 2400,
    "Water": 3645,
    # P2 – Refined Commodities (group 1034)
    "Biocells": 2329,
    "Construction Blocks": 3828,
    "Consumer Electronics": 9836,
    "Coolant": 9832,
    "Enriched Uranium": 44,
    "Fertilizer": 3693,
    "Genetically Enhanced Livestock": 15317,
    "Livestock": 3725,
    "Mechanical Parts": 3689,
    "Microfiber Shielding": 2327,
    "Miniature Electronics": 9842,
    "Nanites": 2463,
    "Oxides": 2317,
    "Polyaramids": 2321,
    "Polytextiles": 3695,
    "Rocket Fuel": 9830,
    "Silicate Glass": 3697,
    "Superconductors": 9838,
    "Supertensile Plastics": 2312,
    "Synthetic Oil": 3691,
    "Test Cultures": 2319,
    "Transmitter": 9840,
    "Viral Agent": 3775,
    "Water-Cooled CPU": 2328,
    # P3 – Specialized Commodities (group 1040)
    "Biotech Research Reports": 2358,
    "Camera Drones": 2345,
    "Condensates": 2344,
    "Cryoprotectant Solution": 2367,
    "Data Chips": 17392,
    "Gel-Matrix Biopaste": 2348,
    "Guidance Systems": 9834,
    "Hazmat Detection Systems": 2366,
    "Hermetic Membranes": 2361,
    "High-Tech Transmitters": 17898,
    "Industrial Explosives": 2360,
    "Neocoms": 2354,
    "Nuclear Reactors": 2352,
    "Planetary Vehicles": 9846,
    "Robotics": 9848,
    "Smartfab Units": 2351,
    "Supercomputers": 2349,
    "Synthetic Synapses": 2346,
    "Transcranial Microcontrollers": 12836,
    "Ukomi Super Conductors": 17136,
    "Vaccines": 28974,
    # P4 – Advanced Commodities (group 1041)
    "Broadcast Node": 2867,
    "Integrity Response Drones": 2868,
    "Nano-Factory": 2869,
    "Organic Mortar Applicators": 2870,
    "Recursive Computing Module": 2871,
    "Self-Harmonizing Power Core": 2872,
    "Sterile Conduits": 2875,
    "Wetware Mainframe": 2876,
}

# Umgekehrtes Mapping: id -> name
PI_TYPE_NAMES: dict[int, str] = {v: k for k, v in PI_TYPE_IDS.items()}

# Tier-Zuordnung pro Produkt
PI_TIERS: dict[str, str] = {
    "Bacteria": "P1", "Biofuels": "P1", "Biomass": "P1", "Chiral Structures": "P1",
    "Electrolytes": "P1", "Industrial Fibers": "P1", "Oxidizing Compound": "P1",
    "Oxygen": "P1", "Plasmoids": "P1", "Precious Metals": "P1", "Proteins": "P1",
    "Reactive Metals": "P1", "Silicon": "P1", "Toxic Metals": "P1", "Water": "P1",
    "Biocells": "P2", "Construction Blocks": "P2", "Consumer Electronics": "P2",
    "Coolant": "P2", "Enriched Uranium": "P2", "Fertilizer": "P2",
    "Genetically Enhanced Livestock": "P2", "Livestock": "P2", "Mechanical Parts": "P2",
    "Microfiber Shielding": "P2", "Miniature Electronics": "P2", "Nanites": "P2",
    "Oxides": "P2", "Polyaramids": "P2", "Polytextiles": "P2", "Rocket Fuel": "P2",
    "Silicate Glass": "P2", "Superconductors": "P2", "Supertensile Plastics": "P2",
    "Synthetic Oil": "P2", "Test Cultures": "P2", "Transmitter": "P2",
    "Viral Agent": "P2", "Water-Cooled CPU": "P2",
    "Biotech Research Reports": "P3", "Camera Drones": "P3", "Condensates": "P3",
    "Cryoprotectant Solution": "P3", "Data Chips": "P3", "Gel-Matrix Biopaste": "P3",
    "Guidance Systems": "P3", "Hazmat Detection Systems": "P3", "Hermetic Membranes": "P3",
    "High-Tech Transmitters": "P3", "Industrial Explosives": "P3", "Neocoms": "P3",
    "Nuclear Reactors": "P3", "Planetary Vehicles": "P3", "Robotics": "P3",
    "Smartfab Units": "P3", "Supercomputers": "P3", "Synthetic Synapses": "P3",
    "Transcranial Microcontrollers": "P3", "Ukomi Super Conductors": "P3", "Vaccines": "P3",
    "Broadcast Node": "P4", "Integrity Response Drones": "P4", "Nano-Factory": "P4",
    "Organic Mortar Applicators": "P4", "Recursive Computing Module": "P4",
    "Self-Harmonizing Power Core": "P4", "Sterile Conduits": "P4", "Wetware Mainframe": "P4",
}

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
            sell_volume = float(info.get("sell", {}).get("volume", 0) or 0)
            sell_order_count = int(info.get("sell", {}).get("orderCount", 0) or 0)
            result[tid] = {
                "buy": buy,
                "sell": sell,
                "sell_volume": sell_volume,
                "sell_order_count": sell_order_count,
            }
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


def get_prices_by_mode(names: list[str], mode: str) -> dict[str, float]:
    """
    Holt Preise für Item-Namen je nach Modus: 'sell', 'buy' oder 'split' (Mittelwert).
    Returns: dict von name -> price (ISK)
    """
    if not names:
        return {}
    name_to_id = {n: _PI_NAME_TO_ID[n] for n in names if n in _PI_NAME_TO_ID}
    if not name_to_id:
        return {}
    try:
        fuzz = _fetch_fuzzwork_prices(list(name_to_id.values()))
        result = {}
        for name, tid in name_to_id.items():
            d = fuzz.get(tid, {})
            sell = d.get("sell", 0.0)
            buy = d.get("buy", 0.0)
            if mode == "buy":
                result[name] = buy
            elif mode == "split":
                result[name] = (sell + buy) / 2.0 if (sell or buy) else 0.0
            else:
                result[name] = sell
        return result
    except Exception:
        return {}


def get_prices_by_names(names: list[str]) -> dict[str, dict]:
    """
    Holt Sell+Buy+Angebot für Item-Namen via Fuzzwork (eine Batch-Anfrage).
    Returns: dict von name -> {sell, buy, sell_volume, sell_order_count}
    """
    if not names:
        return {}
    name_to_id = {n: _PI_NAME_TO_ID[n] for n in names if n in _PI_NAME_TO_ID}
    if not name_to_id:
        return {}
    try:
        fuzz = _fetch_fuzzwork_prices(list(name_to_id.values()))
        result = {}
        for name, tid in name_to_id.items():
            d = fuzz.get(tid, {})
            result[name] = {
                "sell": d.get("sell", 0.0),
                "buy": d.get("buy", 0.0),
                "sell_volume": d.get("sell_volume", 0.0),
                "sell_order_count": d.get("sell_order_count", 0),
            }
        return result
    except Exception:
        return {}


def _get_market_history(type_id: int) -> list[dict]:
    """Holt ESI Markthistorie für The Forge (Jita). Cached 24h in-memory."""
    now = _time.time()
    cached = _history_cache.get(type_id)
    if cached and (now - cached[0]) < HISTORY_CACHE_TTL:
        return cached[1]
    try:
        resp = requests.get(
            ESI_HISTORY_URL,
            params={"type_id": type_id, "datasource": "tranquility"},
            headers={"Accept": "application/json", "User-Agent": "EVE PI Manager"},
            timeout=15,
        )
        resp.raise_for_status()
        data = sorted(resp.json(), key=lambda x: x.get("date", ""))
        _history_cache[type_id] = (now, data)
        return data
    except Exception:
        return []


def _calc_trend(history: list[dict], days: int) -> float | None:
    """Berechnet %-Preisänderung über 'days' Tage anhand ESI-Durchschnittspreis."""
    if len(history) < 2:
        return None
    current = history[-1].get("average", 0)
    if not current:
        return None
    target_idx = max(0, len(history) - 1 - days)
    past = history[target_idx].get("average", 0)
    if not past:
        return None
    return round((current - past) / past * 100, 2)


def get_market_trends(type_ids: list[int]) -> dict[int, dict]:
    """
    Holt Preistrends (24h/7T/30T) für mehrere Type-IDs via ESI Market History.
    Cached 24h in-memory. Parallele Requests für nicht-gecachte IDs (max 10 Threads).
    Returns: dict von type_id -> {trend_1d, trend_7d, trend_30d}
    """
    now = _time.time()
    unique_ids = list(set(type_ids))
    uncached = [
        tid for tid in unique_ids
        if tid not in _history_cache or (now - _history_cache[tid][0]) >= HISTORY_CACHE_TTL
    ]
    if uncached:
        with ThreadPoolExecutor(max_workers=min(len(uncached), 10)) as executor:
            list(executor.map(_get_market_history, uncached))
    result = {}
    for type_id in type_ids:
        history = _get_market_history(type_id)
        result[type_id] = {
            "trend_1d": _calc_trend(history, 1),
            "trend_7d": _calc_trend(history, 7),
            "trend_30d": _calc_trend(history, 30),
        }
    return result


def can_force_market_refresh() -> tuple[bool, int]:
    """Prüft ob ein Admin-Force-Refresh erlaubt ist (server-weite 5-Min-Sperre)."""
    import time as t
    elapsed = t.time() - _market_last_forced_refresh
    if elapsed >= MARKET_FORCE_REFRESH_COOLDOWN:
        return True, 0
    return False, int(MARKET_FORCE_REFRESH_COOLDOWN - elapsed)


def record_force_refresh() -> None:
    global _market_last_forced_refresh
    import time as t
    _market_last_forced_refresh = t.time()


def get_market_last_updated(db: Session) -> Optional[datetime]:
    """Gibt den Zeitpunkt des letzten Marktdaten-Updates zurück."""
    from sqlalchemy import desc
    cache = db.query(MarketCache).order_by(desc(MarketCache.updated_at)).first()
    if cache and cache.updated_at:
        ts = cache.updated_at
        return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
    return None


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
