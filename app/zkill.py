from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import select

from app import sde
from app.database import SessionLocal
from app.esi import get_killmail, universe_names
from app.models import RegionKillCache

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "EVE-PI-Manager/1.0 github.com/DrNightmareDev/PI_Manager"}
WINDOW_SECONDS = {"5m": 300, "15m": 900, "60m": 3600, "24h": 86400}
REGION_DB_TTL = 900.0
_SYSTEM_CACHE_TTL = 300.0
_REGION_CACHE_TTL = 900.0
_SYSTEM_CACHE: dict[tuple[int, int], tuple[float, dict]] = {}
_REGION_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_LAST_REGION_FETCH: dict[int, float] = {}
ZKILL_MIN_INTERVAL = 12.0


def _ship_image(type_id: int) -> str:
    return f"https://images.evetech.net/types/{type_id}/render?size=64"


def _as_utc_label(timestamp: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except Exception:
        parsed = datetime.now(timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _fetch_json(url: str) -> list[dict]:
    try:
        response = requests.get(
            url,
            headers={**HEADERS, "Accept-Encoding": "gzip"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("error"):
            logger.warning("zkill: api error for %s: %s", url, payload.get("error"))
            return []
        return payload if isinstance(payload, list) else []
    except Exception:
        logger.exception("zkill: failed request for %s", url)
        return []


def _resolve_names(raw_kills: list[dict]) -> dict[int, str]:
    ids: set[int] = set()
    for kill in raw_kills:
        victim = kill.get("victim") or {}
        for key in ("character_id", "corporation_id", "alliance_id"):
            value = int(victim.get(key) or 0)
            if value:
                ids.add(value)
    names: dict[int, str] = {}
    id_list = list(ids)
    for start in range(0, len(id_list), 1000):
        for item in universe_names(id_list[start:start + 1000]):
            try:
                names[int(item["id"])] = item["name"]
            except Exception:
                continue
    return names


def resolve_kill_names(raw_kills: list[dict]) -> dict[int, str]:
    return _resolve_names(raw_kills)


def _resolve_stub(stub: dict) -> dict | None:
    killmail_id = int(stub.get("killmail_id") or 0)
    zkb = stub.get("zkb") or {}
    href = str(zkb.get("href") or "").strip()
    killmail_hash = str(zkb.get("hash") or "").strip()
    if href and not killmail_hash:
        try:
            killmail_hash = href.rstrip("/").split("/")[-1]
        except Exception:
            killmail_hash = ""
    if not killmail_id or not killmail_hash:
        return None
    killmail = get_killmail(killmail_id, killmail_hash)
    if not killmail:
        logger.warning("zkill: ESI killmail fetch failed for %s", killmail_id)
        return None
    return {
        **killmail,
        "zkb": zkb,
    }


def _resolve_stubs(stubs: list[dict], hydrate_limit: int | None = None) -> list[dict]:
    if not stubs:
        return []
    selected = stubs[:hydrate_limit] if hydrate_limit else stubs
    resolved: list[dict] = []
    max_workers = max(1, min(len(selected), 10))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_resolve_stub, stub) for stub in selected]
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception:
                logger.exception("zkill: killmail resolution task failed")
                continue
            if result:
                resolved.append(result)
    resolved.sort(key=lambda item: str(item.get("killmail_time") or ""), reverse=True)
    return resolved


def normalize_kill(kill: dict, system_name: str | None = None, name_map: dict[int, str] | None = None) -> dict:
    victim = kill.get("victim") or {}
    ship_type_id = int(victim.get("ship_type_id") or 0)
    character_id = int(victim.get("character_id") or 0)
    corporation_id = int(victim.get("corporation_id") or 0)
    alliance_id = int(victim.get("alliance_id") or 0)
    killmail_id = int(kill.get("killmail_id") or 0)
    system_id = int(kill.get("solar_system_id") or 0)
    resolved_names = name_map or _resolve_names([kill])
    resolved_system = sde.get_system_local(system_id) or {}
    kill_time = str(kill.get("killmail_time") or datetime.now(timezone.utc).isoformat())
    if kill_time.endswith("Z"):
        kill_time_utc = kill_time
    else:
        kill_time_utc = kill_time.replace("+00:00", "Z")

    return {
        "killmail_id": killmail_id,
        "kill_url": f"https://zkillboard.com/kill/{killmail_id}/",
        "system_id": system_id,
        "system_name": system_name or resolved_system.get("name") or f"System {system_id}",
        "region_id": int(resolved_system.get("region_id") or 0),
        "region_name": resolved_system.get("region_name") or "",
        "ship_type_id": ship_type_id,
        "ship_type_name": sde.get_type_name(ship_type_id) or f"Type {ship_type_id}",
        "ship_image_url": _ship_image(ship_type_id) if ship_type_id else "",
        "pilot_name": (
            victim.get("character_name")
            or victim.get("characterName")
            or resolved_names.get(character_id)
            or "Unknown Pilot"
        ),
        "killmail_time_utc": kill_time_utc,
        "killmail_time_utc_label": _as_utc_label(kill_time_utc),
        "corporation_name": resolved_names.get(corporation_id) or "",
        "alliance_name": resolved_names.get(alliance_id) or "",
        "damage_taken": int(victim.get("damage_taken") or 0),
        "attackers": len(kill.get("attackers") or []),
        "isk_value": float((kill.get("zkb") or {}).get("totalValue") or 0.0),
        "is_npc": bool((kill.get("zkb") or {}).get("npc", False)),
        "is_solo": bool((kill.get("zkb") or {}).get("solo", False)),
        "is_pod": ship_type_id == 670,
    }


def _danger_level(kill_count: int) -> str:
    if kill_count >= 5:
        return "danger"
    if kill_count >= 1:
        return "caution"
    return "safe"


def get_system_kill_summary(system_id: int, window: str = "60m", limit: int = 10) -> dict:
    past_seconds = int(WINDOW_SECONDS.get(window, 3600))
    cache_key = (int(system_id), past_seconds)
    cached = _SYSTEM_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] <= _SYSTEM_CACHE_TTL:
        return cached[1]

    stubs = _fetch_json(
        f"https://zkillboard.com/api/kills/solarSystemID/{int(system_id)}/pastSeconds/{past_seconds}/"
    )
    data = _resolve_stubs(stubs, hydrate_limit=limit)
    name_map = _resolve_names(data)
    summary = {
        "system_id": int(system_id),
        "kill_count": len(stubs),
        "danger_level": _danger_level(len(stubs)),
        "system_url": f"https://zkillboard.com/system/{int(system_id)}/",
        "window": window,
        "latest_kills": [normalize_kill(kill, name_map=name_map) for kill in data[:limit]],
        "fetched_at_iso": datetime.now(timezone.utc).isoformat(),
    }
    _SYSTEM_CACHE[cache_key] = (now, summary)
    return summary


def get_system_kill_summaries(system_ids: list[int], window: str = "60m", limit: int = 10) -> dict[int, dict]:
    results: dict[int, dict] = {}
    for system_id in system_ids:
        try:
            results[int(system_id)] = get_system_kill_summary(int(system_id), window=window, limit=limit)
        except Exception:
            logger.exception("zkill: failed summary for system %s", system_id)
    return results


def get_region_kills(region_id: int, window: str = "60m", limit: int = 200) -> list[dict]:
    past_seconds = int(WINDOW_SECONDS.get(window, 3600))
    cache_key = (int(region_id), past_seconds)
    cached = _REGION_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] <= _REGION_CACHE_TTL:
        return cached[1]
    last = _LAST_REGION_FETCH.get(int(region_id), 0.0)
    if now - last < ZKILL_MIN_INTERVAL:
        return cached[1] if cached else []
    _LAST_REGION_FETCH[int(region_id)] = now

    stubs = _fetch_json(
        f"https://zkillboard.com/api/kills/regionID/{int(region_id)}/pastSeconds/{past_seconds}/"
    )
    data = _resolve_stubs(stubs)
    _REGION_CACHE[cache_key] = (now, data)
    return data


def normalize_region_kills(raw_kills: list[dict], limit: int = 200) -> list[dict]:
    name_map = _resolve_names(raw_kills[:limit])
    normalized: list[dict] = []
    for kill in raw_kills[:limit]:
        system_id = int(kill.get("solar_system_id") or 0)
        system_info = sde.get_system_local(system_id) or {}
        normalized.append(normalize_kill(kill, system_name=system_info.get("name"), name_map=name_map))
    return normalized


def get_region_kills_db_first(region_id: int, window: str = "60m", limit: int = 200) -> tuple[list[dict], dict]:
    cache_key = ("db", int(region_id), window)
    cached = _REGION_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] <= _REGION_CACHE_TTL:
        return cached[1], {
            "source": "memory",
            "cache_age_seconds": int(now - cached[0]),
        }

    db = SessionLocal()
    try:
        row = db.get(RegionKillCache, (int(region_id), window))
        now_utc = datetime.now(timezone.utc)

        if row and row.fetched_at:
            fetched_at = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)
            age = (now_utc - fetched_at).total_seconds()
            if age <= REGION_DB_TTL:
                try:
                    kills = json.loads(row.kills_json or "[]")
                    _REGION_CACHE[cache_key] = (time.time(), kills)
                    return kills, {
                        "source": "db",
                        "cache_age_seconds": int(age),
                    }
                except Exception:
                    logger.exception("zkill: failed to deserialize region cache for %s/%s", region_id, window)

        raw_kills = get_region_kills(region_id, window=window, limit=limit)
        kills = normalize_region_kills(raw_kills, limit=limit)
        newest_time = max((str(item.get("killmail_time_utc") or "") for item in kills), default=None)
        upsert = RegionKillCache(
            region_id=int(region_id),
            window=window,
            kill_count=len(kills),
            kills_json=json.dumps(kills),
            newest_kill_time=newest_time,
            fetched_at=now_utc,
        )
        db.merge(upsert)
        db.commit()
        _REGION_CACHE[cache_key] = (time.time(), kills)
        return kills, {
            "source": "fresh",
            "cache_age_seconds": 0,
        }
    except Exception:
        logger.exception("zkill: region DB cache upsert failed for %s/%s", region_id, window)
        raw_kills = get_region_kills(region_id, window=window, limit=limit)
        kills = normalize_region_kills(raw_kills, limit=limit)
        _REGION_CACHE[cache_key] = (time.time(), kills)
        return kills, {
            "source": "fallback",
            "cache_age_seconds": 0,
        }
    finally:
        db.close()


def append_intel_event_to_region_cache(db, region_id: int, normalized_kill: dict) -> None:
    kill_time = str(normalized_kill.get("killmail_time_utc") or "")
    for row in db.scalars(select(RegionKillCache).where(RegionKillCache.region_id == int(region_id))).all():
        try:
            existing = json.loads(row.kills_json or "[]")
        except Exception:
            existing = []
        if any(int(item.get("killmail_id") or 0) == int(normalized_kill.get("killmail_id") or 0) for item in existing):
            continue
        window_seconds = WINDOW_SECONDS.get(row.window, 3600)
        if kill_time:
            try:
                kill_dt = datetime.fromisoformat(kill_time.replace("Z", "+00:00"))
                if kill_dt < datetime.now(timezone.utc) - timedelta(seconds=window_seconds):
                    continue
            except Exception:
                pass
        existing.insert(0, normalized_kill)
        existing.sort(key=lambda item: str(item.get("killmail_time_utc") or ""), reverse=True)
        row.kills_json = json.dumps(existing[:200])
        row.kill_count = len(existing[:200])
        row.newest_kill_time = max(
            (str(item.get("killmail_time_utc") or "") for item in existing[:200]),
            default=row.newest_kill_time,
        )
        row.fetched_at = datetime(2000, 1, 1, tzinfo=timezone.utc)


def get_region_feed(region_id: int, window: str = "60m", limit: int = 200) -> list[dict]:
    normalized, _meta = get_region_kills_db_first(region_id, window=window, limit=limit)
    return normalized[:limit]
