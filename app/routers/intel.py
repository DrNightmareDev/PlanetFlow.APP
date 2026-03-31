from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app import sde
from app.database import SessionLocal, get_db
from app.dependencies import require_account
from app.esi import ensure_valid_token, get_character_location
from app.models import Character, IntelKillEvent, KillActivityCache
from app.templates_env import templates
from app.zkill import get_region_kills_db_first, get_system_kill_summary

router = APIRouter(prefix="/intel", tags=["intel"])

logger = logging.getLogger(__name__)
WINDOW_SECONDS = {"5m": 300, "15m": 900, "60m": 3600, "24h": 86400}
SYSTEM_DETAIL_TTL = timedelta(minutes=5)
_CHAR_LOCATION_CACHE: dict[int, tuple[dict, float]] = {}
_CHAR_LOCATION_CACHE_TTL = 60.0


def _get_cached_character_location(character: Character, db: Session) -> dict | None:
    cached = _CHAR_LOCATION_CACHE.get(int(character.id))
    if cached and time.time() - cached[1] < _CHAR_LOCATION_CACHE_TTL:
        return cached[0]
    token = ensure_valid_token(character, db)
    if not token:
        return None
    location = get_character_location(int(character.eve_character_id), token)
    if location:
        _CHAR_LOCATION_CACHE[int(character.id)] = (location, time.time())
    return location or None


def _resolve_region(region: str) -> dict:
    catalog = sde.get_region_catalog()
    if not catalog:
        raise RuntimeError("No region catalog available")
    try:
        region_id = int(region)
    except (TypeError, ValueError):
        region_id = int(catalog[0]["id"])
    graph = sde.get_region_system_graph(region_id)
    if graph:
        return graph
    return sde.get_region_system_graph(int(catalog[0]["id"])) or {
        "id": int(catalog[0]["id"]),
        "name": catalog[0]["name"],
        "systems": [],
        "connections": [],
        "neighbors": [],
    }


def _build_alt_layout(graph: dict) -> dict[int, tuple[float, float]]:
    systems = list(graph["systems"])
    constellation_groups: dict[int, list[dict]] = defaultdict(list)
    for system in systems:
        constellation_groups[int(system.get("constellation_id") or 0)].append(system)

    ordered_groups = sorted(
        constellation_groups.items(),
        key=lambda item: ((item[1][0].get("constellation_name") or "").lower(), item[0]),
    )
    group_count = max(1, len(ordered_groups))
    width = 1280.0
    height = 920.0
    center_x = width / 2
    center_y = height / 2
    positions: dict[int, tuple[float, float]] = {}

    for group_index, (_, group_systems) in enumerate(ordered_groups):
        angle = (group_index / group_count) * math.tau
        orbit_x = center_x + 320 * math.cos(angle)
        orbit_y = center_y + 250 * math.sin(angle)
        group_systems = sorted(group_systems, key=lambda item: item["name"].lower())
        inner_count = max(1, len(group_systems))
        for system_index, system in enumerate(group_systems):
            inner_angle = (system_index / inner_count) * math.tau
            radius = 54 + (system_index % 5) * 16
            positions[int(system["id"])] = (
                round(orbit_x + radius * math.cos(inner_angle), 2),
                round(orbit_y + radius * math.sin(inner_angle), 2),
            )
    return positions


def _normalize_system_kill_entry(kill: dict) -> dict:
    return {
        "killmail_id": int(kill.get("killmail_id") or 0),
        "kill_url": str(kill.get("kill_url") or ""),
        "timestamp": str(kill.get("killmail_time_utc") or kill.get("timestamp") or ""),
        "timestamp_utc": str(kill.get("killmail_time_utc_label") or kill.get("timestamp_utc") or ""),
        "system_id": int(kill.get("system_id") or 0),
        "system_name": str(kill.get("system_name") or ""),
        "pilot_name": str(kill.get("pilot_name") or "Unknown Pilot"),
        "ship_type": str(kill.get("ship_type_name") or kill.get("ship_type") or ""),
        "ship_image_url": str(kill.get("ship_image_url") or ""),
        "isk_value": float(kill.get("isk_value") or 0.0),
        "attackers": int(kill.get("attackers") or 0),
        "ship_type_id": int(kill.get("ship_type_id") or 0),
    }


def _to_feed_entry(kill: dict, region_name: str) -> dict:
    item = _normalize_system_kill_entry(kill)
    return {
        "killmail_id": item["killmail_id"],
        "kill_url": item["kill_url"],
        "timestamp": item["timestamp"],
        "timestamp_utc": item["timestamp_utc"],
        "system_id": item["system_id"],
        "system_name": item["system_name"],
        "region_name": region_name,
        "pilot_name": item["pilot_name"],
        "ship_type": item["ship_type"],
        "ship_image_url": item["ship_image_url"],
        "attackers": item["attackers"],
        "isk_value": item["isk_value"],
        "ship_type_id": item["ship_type_id"],
    }


def _fallback_feed(graph: dict) -> tuple[list[dict], list[dict]]:
    systems = []
    for system in graph["systems"]:
        systems.append({
            "system_id": system["id"],
            "kill_count": 0,
            "heat": 0.0,
            "danger": "cold",
        })
    return systems, []


def _latest_ws_status() -> tuple[str, int]:
    db = SessionLocal()
    try:
        latest_event = db.query(IntelKillEvent).order_by(IntelKillEvent.created_at.desc(), IntelKillEvent.id.desc()).first()
        if not latest_event or not latest_event.created_at:
            return "disconnected", -1
        created_at = latest_event.created_at if latest_event.created_at.tzinfo else latest_event.created_at.replace(tzinfo=timezone.utc)
        age = int((datetime.now(timezone.utc) - created_at).total_seconds())
        if age < 60:
            return "connected", age
        if age < 300:
            return "degraded", age
        return "disconnected", age
    finally:
        db.close()


def _build_live_snapshot(region_id: int, window: str, kill_type: str) -> tuple[dict, list[dict], list[dict], dict]:
    graph = _resolve_region(str(region_id))
    alt_positions = _build_alt_layout(graph)
    systems = []
    for system in graph["systems"]:
        alt_x, alt_y = alt_positions.get(int(system["id"]), (system["x"], system["y"]))
        systems.append({
            **system,
            "geo_x": system["x"],
            "geo_y": system["y"],
            "alt_x": alt_x,
            "alt_y": alt_y,
        })
    graph = {**graph, "systems": systems}

    raw_kills, cache_meta = get_region_kills_db_first(region_id, window=window, limit=200)
    if not raw_kills:
        activity, feed = _fallback_feed(graph)
        return graph, activity, feed, {
            "source_state": "empty",
            "source": cache_meta.get("source", "db"),
            "raw_kills": 0,
            "feed_kills": 0,
            "activity_systems": 0,
            "cache_age_seconds": int(cache_meta.get("cache_age_seconds") or 0),
            "message": "No kill data returned from zKill/ESI for this region and time window.",
        }

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=WINDOW_SECONDS.get(window, 3600))
    activity_counter: dict[int, int] = defaultdict(int)
    feed: list[dict] = []

    for kill in raw_kills:
        ship_type_id = int(kill.get("ship_type_id") or 0)
        if kill_type == "pod" and ship_type_id != 670:
            continue
        if kill_type == "ship" and ship_type_id == 670:
            continue
        try:
            kill_time = datetime.fromisoformat(str(kill.get("killmail_time_utc") or kill.get("timestamp") or "").replace("Z", "+00:00"))
        except Exception:
            continue
        if kill_time < cutoff:
            continue
        entry = _to_feed_entry(kill, graph["name"])
        if not entry["killmail_id"]:
            continue
        feed.append(entry)
        activity_counter[int(entry["system_id"])] += 1

    if not feed:
        activity, feed = _fallback_feed(graph)
        return graph, activity, feed, {
            "source_state": "filtered_empty",
            "source": cache_meta.get("source", "db"),
            "raw_kills": len(raw_kills),
            "feed_kills": 0,
            "activity_systems": 0,
            "cache_age_seconds": int(cache_meta.get("cache_age_seconds") or 0),
            "message": "Kill data loaded, but nothing matched the selected filters.",
        }

    feed.sort(key=lambda item: item["timestamp"], reverse=True)
    activity = []
    active_systems = 0
    for system in graph["systems"]:
        count = int(activity_counter.get(system["id"], 0))
        if count > 0:
            active_systems += 1
        activity.append({
            "system_id": system["id"],
            "kill_count": count,
            "heat": min(1.0, count / 9.0),
            "danger": "hot" if count >= 7 else "warm" if count >= 3 else "cold",
        })
    return graph, activity, feed[:200], {
        "source_state": "ok",
        "source": cache_meta.get("source", "db"),
        "raw_kills": len(raw_kills),
        "feed_kills": len(feed),
        "activity_systems": active_systems,
        "cache_age_seconds": int(cache_meta.get("cache_age_seconds") or 0),
        "message": "Kill data loaded successfully.",
    }


@router.get("/map", response_class=HTMLResponse)
def intel_map(
    request: Request,
    region: str = Query("10000010"),
    window: str = Query("60m"),
    kill_type: str = Query("all"),
    layout: str = Query("geo"),
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    regions = sde.get_region_catalog()
    characters = (
        db.query(Character)
        .filter(Character.account_id == account.id)
        .order_by(Character.character_name.asc())
        .all()
    )
    selected_window = window if window in WINDOW_SECONDS else "60m"
    selected_kill_type = kill_type if kill_type in {"all", "ship", "pod"} else "all"
    selected_layout = layout if layout in {"geo", "alt"} else "geo"
    graph, system_activity, kill_feed, source_meta = _build_live_snapshot(
        int(region or regions[0]["id"]),
        selected_window,
        selected_kill_type,
    )
    ws_status, ws_last_kill_ago = _latest_ws_status()
    return templates.TemplateResponse("intel_map.html", {
        "request": request,
        "account": account,
        "regions": regions,
        "selected_region": str(graph["id"]),
        "selected_window": selected_window,
        "selected_kill_type": selected_kill_type,
        "selected_layout": selected_layout,
        "intel_characters": [{"id": int(char.id), "name": char.character_name} for char in characters],
        "region_data": graph,
        "initial_activity": system_activity,
        "initial_feed": kill_feed,
        "initial_source_meta": source_meta,
        "initial_ws_status": ws_status,
        "initial_ws_last_kill_ago": ws_last_kill_ago,
    })


@router.get("/map/live")
def intel_map_live(
    region: str = Query("10000010"),
    window: str = Query("60m"),
    kill_type: str = Query("all"),
    account=Depends(require_account),
):
    graph, system_activity, kill_feed, source_meta = _build_live_snapshot(int(region), window, kill_type)
    ws_status, ws_last_kill_ago = _latest_ws_status()
    return JSONResponse({
        "region": graph,
        "window": window,
        "kill_type": kill_type,
        "activity": system_activity,
        "feed": kill_feed,
        "source_meta": source_meta,
        "ws_status": ws_status,
        "ws_last_kill_ago": ws_last_kill_ago,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


@router.get("/system/{system_id}")
def intel_system_details(
    system_id: int,
    window: str = Query("60m"),
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    selected_window = window if window in WINDOW_SECONDS else "60m"
    now = datetime.now(timezone.utc)
    row = db.get(KillActivityCache, int(system_id))
    if row and row.fetched_at:
        fetched_at = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)
        age_seconds = int((now - fetched_at).total_seconds())
        if now - fetched_at <= SYSTEM_DETAIL_TTL:
            try:
                latest_kills = [_normalize_system_kill_entry(item) for item in json.loads(row.latest_kills_json or "[]")]
            except Exception:
                latest_kills = []
            return JSONResponse({
                "system_id": int(system_id),
                "kill_count": int(row.kill_count or 0),
                "window": selected_window,
                "latest_kills": latest_kills,
                "fetched_at_iso": fetched_at.astimezone(timezone.utc).isoformat(),
                "cache_age_seconds": age_seconds,
                "cache_state": "db",
            })

    try:
        summary = get_system_kill_summary(int(system_id), window=selected_window, limit=5)
        latest_kills = [_normalize_system_kill_entry(item) for item in (summary.get("latest_kills") or [])]
        summary["latest_kills"] = latest_kills
        if row is None:
            row = KillActivityCache(
                system_id=int(system_id),
                kill_count=int(summary.get("kill_count") or 0),
                latest_kills_json=json.dumps(latest_kills),
                window=selected_window,
                fetched_at=now,
            )
            db.add(row)
        else:
            row.kill_count = int(summary.get("kill_count") or 0)
            row.latest_kills_json = json.dumps(latest_kills)
            row.window = selected_window
            row.fetched_at = now
        db.commit()
        summary["fetched_at_iso"] = now.astimezone(timezone.utc).isoformat()
        summary["cache_age_seconds"] = 0
        summary["cache_state"] = "fresh"
        return JSONResponse(summary)
    except Exception:
        logger.exception("intel: failed system detail refresh for %s", system_id)
        if row and row.fetched_at:
            fetched_at = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)
            try:
                latest_kills = [_normalize_system_kill_entry(item) for item in json.loads(row.latest_kills_json or "[]")]
            except Exception:
                latest_kills = []
            return JSONResponse({
                "system_id": int(system_id),
                "kill_count": int(row.kill_count or 0),
                "window": row.window or selected_window,
                "latest_kills": latest_kills,
                "fetched_at_iso": fetched_at.astimezone(timezone.utc).isoformat(),
                "cache_age_seconds": int((now - fetched_at).total_seconds()),
                "cache_state": "stale_db",
            })
        return JSONResponse({
            "system_id": int(system_id),
            "kill_count": 0,
            "window": selected_window,
            "latest_kills": [],
            "fetched_at_iso": now.astimezone(timezone.utc).isoformat(),
            "cache_age_seconds": 0,
            "cache_state": "empty",
        })


@router.get("/events")
async def intel_events(
    region: str = Query("10000010"),
    since_id: int = Query(0),
    account=Depends(require_account),
):
    region_id = int(region)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + 25

    async def generate():
        last_id = int(since_id)
        while loop.time() < deadline:
            db = SessionLocal()
            try:
                rows = (
                    db.query(IntelKillEvent)
                    .filter(IntelKillEvent.region_id == region_id, IntelKillEvent.id > last_id)
                    .order_by(IntelKillEvent.id.asc())
                    .limit(10)
                    .all()
                )
                for row in rows:
                    last_id = int(row.id)
                    yield (
                        f"id:{row.id}\n"
                        f"data:{json.dumps({'id': row.id, 'killmail_id': row.killmail_id, 'solar_system_id': row.solar_system_id, 'kill': json.loads(row.kill_json)})}\n\n"
                    )
                if not rows:
                    yield ": heartbeat\n\n"
            except Exception:
                logger.exception("intel: SSE generate failed")
            finally:
                db.close()
            await asyncio.sleep(3)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/character-location")
def intel_character_location(
    character_id: int = Query(...),
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    character = (
        db.query(Character)
        .filter(Character.account_id == account.id, Character.id == int(character_id))
        .first()
    )
    if character is None:
        return JSONResponse({"ok": False, "location": None})
    location = _get_cached_character_location(character, db)
    if not location or not location.get("solar_system_id"):
        return JSONResponse({"ok": False, "location": None})
    system_id = int(location.get("solar_system_id") or 0)
    system_info = sde.get_system_local(system_id) or {}
    return JSONResponse({
        "ok": True,
        "character_id": int(character.id),
        "character_name": character.character_name,
        "solar_system_id": system_id,
        "system_name": system_info.get("name") or f"System {system_id}",
        "region_id": int(system_info.get("region_id") or 0),
        "region_name": system_info.get("region_name") or "",
    })
