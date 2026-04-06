from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import sde
from app.database import SessionLocal, get_db
from app.dependencies import require_account
from app.esi import ensure_valid_token, get_character_location, get_sovereignty_map, get_sovereignty_structures, universe_names  # get_sovereignty_* used in fallback
from app.models import Character, CombatIntelPreference, IntelKillEvent, IntelStreamState, KillActivityCache
from app.session import validate_csrf_header
from app.templates_env import templates
from app.zkill import get_region_kills_db_first, get_system_kill_summary

router = APIRouter(prefix="/intel", tags=["intel"])

logger = logging.getLogger(__name__)
WINDOW_SECONDS = {"5m": 300, "15m": 900, "60m": 3600, "24h": 86400}
SYSTEM_DETAIL_TTL = timedelta(minutes=5)
_CHAR_LOCATION_CACHE: dict[int, tuple[dict, float]] = {}
_CHAR_LOCATION_CACHE_TTL = 60.0


def _default_region_for_account(account, db: Session) -> str:
    catalog = sde.get_region_catalog()
    fallback = str(catalog[0]["id"]) if catalog else "10000010"
    if not getattr(account, "main_character_id", None):
        return fallback
    main_character = (
        db.query(Character)
        .filter(Character.account_id == account.id, Character.id == int(account.main_character_id))
        .first()
    )
    if main_character is None:
        return fallback
    location = _get_cached_character_location(main_character, db)
    region_id = int((location or {}).get("region_id") or 0)
    return str(region_id or fallback)


def _resolve_initial_preferences(account, db: Session, region: str | None, window: str | None, kill_type: str | None, layout: str | None, tracked_character_id: str | None, follow: str | None) -> dict:
    pref = db.get(CombatIntelPreference, int(account.id))
    selected_region = str(region).strip() if region else ""
    selected_window = str(window).strip() if window else ""
    selected_kill_type = str(kill_type).strip() if kill_type else ""
    selected_layout = str(layout).strip() if layout else ""
    selected_tracked = str(tracked_character_id).strip() if tracked_character_id else ""

    resolved_region = selected_region or (str(pref.region_id) if pref and pref.region_id else "") or _default_region_for_account(account, db)
    resolved_window = selected_window if selected_window in WINDOW_SECONDS else (pref.window if pref and pref.window in WINDOW_SECONDS else "60m")
    resolved_kill_type = selected_kill_type if selected_kill_type in {"all", "ship", "pod", "other"} else (pref.kill_type if pref and pref.kill_type in {"all", "ship", "pod", "other"} else "all")
    resolved_layout = selected_layout if selected_layout in {"geo", "alt"} else (pref.layout if pref and pref.layout in {"geo", "alt"} else "geo")
    resolved_tracked = int(selected_tracked) if selected_tracked.isdigit() else int(pref.tracked_character_id) if pref and pref.tracked_character_id else None

    if resolved_tracked:
        owned = db.query(Character.id).filter(Character.account_id == account.id, Character.id == int(resolved_tracked)).first()
        if owned is None:
            resolved_tracked = None

    if follow in {"0", "1"}:
        resolved_follow = follow == "1"
    else:
        resolved_follow = bool(pref.follow_character) if pref else False

    return {
        "region": resolved_region,
        "window": resolved_window,
        "kill_type": resolved_kill_type,
        "layout": resolved_layout,
        "tracked_character_id": resolved_tracked,
        "follow_character": resolved_follow,
    }


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
    compact_positions = {
        int(system["id"]): (float(system["compact_x"]), float(system["compact_y"]))
        for system in graph["systems"]
        if system.get("compact_x") is not None and system.get("compact_y") is not None
    }
    if compact_positions:
        return compact_positions

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


def _latest_ws_status() -> tuple[str, int, int]:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        stream = db.get(IntelStreamState, "r2z2")
        latest_event = db.query(IntelKillEvent).order_by(IntelKillEvent.created_at.desc(), IntelKillEvent.id.desc()).first()

        poller_age = -1
        if stream and stream.last_success_at:
            success_at = stream.last_success_at if stream.last_success_at.tzinfo else stream.last_success_at.replace(tzinfo=timezone.utc)
            poller_age = int((now - success_at).total_seconds())

        event_age = -1
        if latest_event and latest_event.created_at:
            created_at = latest_event.created_at if latest_event.created_at.tzinfo else latest_event.created_at.replace(tzinfo=timezone.utc)
            event_age = int((now - created_at).total_seconds())

        if poller_age < 0:
            return "disconnected", poller_age, event_age
        if poller_age < 15:
            return "connected", poller_age, event_age
        if poller_age < 60:
            return "degraded", poller_age, event_age
        return "disconnected", poller_age, event_age
    finally:
        db.close()


def _intel_debug_info(db: Session) -> dict:
    now = datetime.now(timezone.utc)
    stream = db.get(IntelStreamState, "r2z2")
    recent_5m = (
        db.query(func.count(IntelKillEvent.id))
        .filter(IntelKillEvent.created_at >= now - timedelta(minutes=5))
        .scalar()
        or 0
    )
    recent_15m = (
        db.query(func.count(IntelKillEvent.id))
        .filter(IntelKillEvent.created_at >= now - timedelta(minutes=15))
        .scalar()
        or 0
    )
    total_events = db.query(func.count(IntelKillEvent.id)).scalar() or 0
    latest_event = db.query(IntelKillEvent).order_by(IntelKillEvent.created_at.desc(), IntelKillEvent.id.desc()).first()
    latest_event_age = None
    if latest_event and latest_event.created_at:
        created_at = latest_event.created_at if latest_event.created_at.tzinfo else latest_event.created_at.replace(tzinfo=timezone.utc)
        latest_event_age = int((now - created_at).total_seconds())

    def _utc_iso(value):
        if value is None:
            return None
        if getattr(value, "tzinfo", None) is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    return {
        "stream_key": getattr(stream, "stream_key", "r2z2"),
        "last_sequence_id": getattr(stream, "last_sequence_id", None),
        "last_success_at": _utc_iso(getattr(stream, "last_success_at", None)),
        "last_error": getattr(stream, "last_error", ""),
        "updated_at": _utc_iso(getattr(stream, "updated_at", None)),
        "recent_events_5m": int(recent_5m),
        "recent_events_15m": int(recent_15m),
        "total_events": int(total_events),
        "latest_event_age_seconds": latest_event_age,
    }


def _build_live_snapshot(region_id: int, window: str, kill_type: str, force_refresh: bool = False) -> tuple[dict, list[dict], list[dict], dict]:
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
    graph["compact_view_box"] = graph.get("compact_view_box") or graph.get("view_box")
    graph["geo_view_box"] = graph.get("geo_view_box") or graph.get("view_box")

    raw_kills, cache_meta = get_region_kills_db_first(region_id, window=window, limit=200, force_refresh=force_refresh)
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
        if kill_type == "other" and ship_type_id in {0, 670}:
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
    region: str | None = Query(None),
    window: str | None = Query(None),
    kill_type: str | None = Query(None),
    layout: str | None = Query(None),
    character_id: str | None = Query(None),
    follow: str | None = Query(None),
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
    preferences = _resolve_initial_preferences(account, db, region, window, kill_type, layout, character_id, follow)
    selected_window = preferences["window"]
    selected_kill_type = preferences["kill_type"]
    selected_layout = preferences["layout"]
    graph, system_activity, kill_feed, source_meta = _build_live_snapshot(
        int(preferences["region"] or regions[0]["id"]),
        selected_window,
        selected_kill_type,
    )
    ws_status, ws_last_success_ago, ws_last_kill_ago = _latest_ws_status()
    can_view_debug = bool(getattr(account, "is_owner", False) or getattr(account, "is_admin", False))
    return templates.TemplateResponse("intel_map.html", {
        "request": request,
        "account": account,
        "regions": regions,
        "selected_region": str(graph["id"]),
        "selected_window": selected_window,
        "selected_kill_type": selected_kill_type,
        "selected_layout": selected_layout,
        "selected_character_id": preferences["tracked_character_id"],
        "selected_follow": preferences["follow_character"],
        "intel_characters": [{"id": int(char.id), "name": char.character_name} for char in characters],
        "region_data": graph,
        "initial_activity": system_activity,
        "initial_feed": kill_feed,
        "initial_source_meta": source_meta,
        "initial_ws_status": ws_status,
        "initial_ws_last_success_ago": ws_last_success_ago,
        "initial_ws_last_kill_ago": ws_last_kill_ago,
        "can_view_debug": can_view_debug,
        "intel_debug": _intel_debug_info(db) if can_view_debug else None,
    })


@router.get("/debug")
def intel_debug(account=Depends(require_account), db: Session = Depends(get_db)):
    if not (getattr(account, "is_owner", False) or getattr(account, "is_admin", False)):
        raise HTTPException(status_code=403, detail="Forbidden")
    return JSONResponse(_intel_debug_info(db))


@router.post("/preferences")
async def intel_preferences_save(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    validate_csrf_header(request)
    payload = await request.json()
    region_id = payload.get("region")
    window = str(payload.get("window") or "60m")
    kill_type = str(payload.get("kill_type") or "all")
    layout = str(payload.get("layout") or "geo")
    tracked_character_id = payload.get("character_id")
    follow_character = bool(payload.get("follow"))

    if window not in WINDOW_SECONDS:
        window = "60m"
    if kill_type not in {"all", "ship", "pod", "other"}:
        kill_type = "all"
    if layout not in {"geo", "alt"}:
        layout = "geo"

    valid_region_id = None
    try:
        candidate_region = int(region_id)
        if sde.get_region_system_graph(candidate_region):
            valid_region_id = candidate_region
    except (TypeError, ValueError):
        valid_region_id = None

    valid_character_id = None
    try:
        if tracked_character_id is not None and str(tracked_character_id).strip():
            candidate_character = int(tracked_character_id)
            owned = db.query(Character.id).filter(Character.account_id == account.id, Character.id == candidate_character).first()
            if owned is not None:
                valid_character_id = candidate_character
    except (TypeError, ValueError):
        valid_character_id = None

    pref = db.get(CombatIntelPreference, int(account.id))
    if pref is None:
        pref = CombatIntelPreference(account_id=int(account.id))
        db.add(pref)

    pref.region_id = valid_region_id
    pref.window = window
    pref.kill_type = kill_type
    pref.layout = layout
    pref.tracked_character_id = valid_character_id
    pref.follow_character = follow_character and valid_character_id is not None
    db.commit()

    return JSONResponse({"ok": True})


@router.get("/map/live")
def intel_map_live(
    region: str = Query("10000010"),
    window: str = Query("60m"),
    kill_type: str = Query("all"),
    force: int = Query(0),
    account=Depends(require_account),
):
    force_refresh = bool(force) and bool(getattr(account, "is_owner", False))
    graph, system_activity, kill_feed, source_meta = _build_live_snapshot(int(region), window, kill_type, force_refresh=force_refresh)
    ws_status, ws_last_success_ago, ws_last_kill_ago = _latest_ws_status()
    return JSONResponse({
        "region": graph,
        "window": window,
        "kill_type": kill_type,
        "activity": system_activity,
        "feed": kill_feed,
        "source_meta": source_meta,
        "force_refresh": force_refresh,
        "ws_status": ws_status,
        "ws_last_success_ago": ws_last_success_ago,
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


# ── Sovereignty Timers ────────────────────────────────────────────────────────

def _sov_rows_from_db(db: Session) -> tuple[list[dict], datetime | None]:
    """Read sov structures from DB and return (rows, fetched_at)."""
    from app.models import SovStructure
    db_rows = db.query(SovStructure).order_by(SovStructure.system_name).all()
    if not db_rows:
        return [], None

    fetched_at = max((r.fetched_at for r in db_rows if r.fetched_at), default=None)
    now_utc = datetime.now(timezone.utc)
    rows = []
    for s in db_rows:
        vuln_start = s.vuln_start
        vuln_end = s.vuln_end
        vuln_start_str = vuln_start.isoformat() if vuln_start else ""
        vuln_end_str = vuln_end.isoformat() if vuln_end else ""
        is_vulnerable = bool(vuln_start and vuln_end and vuln_start <= now_utc <= vuln_end)

        if is_vulnerable and vuln_end:
            next_event, next_event_label = vuln_end, "closes"
        elif vuln_start and vuln_start > now_utc:
            next_event, next_event_label = vuln_start, "opens"
        else:
            next_event, next_event_label = None, ""

        secs_until = int((next_event - now_utc).total_seconds()) if next_event else None

        rows.append({
            "system_id": s.system_id,
            "system_name": s.system_name or f"System {s.system_id}",
            "region_name": s.region_name or "",
            "region_id": s.region_id or 0,
            "alliance_id": s.alliance_id or 0,
            "alliance_name": s.alliance_name or "—",
            "adm": float(s.adm or 0),
            "vuln_start": vuln_start_str,
            "vuln_end": vuln_end_str,
            "is_vulnerable": is_vulnerable,
            "next_event": next_event.isoformat() if next_event else "",
            "next_event_label": next_event_label,
            "secs_until": secs_until,
        })
    return rows, fetched_at


def _sov_rows_from_esi_fallback() -> list[dict]:
    """Live ESI fetch as fallback when DB is empty (e.g. first boot before task runs)."""
    try:
        sov_map_raw = get_sovereignty_map()
        sov_structs_raw = get_sovereignty_structures()
    except Exception:
        return []

    system_alliance: dict[int, int] = {}
    for row in sov_map_raw:
        aid = row.get("alliance_id")
        if aid:
            system_alliance[int(row["system_id"])] = int(aid)

    alliance_ids: set[int] = set(system_alliance.values())
    for s in sov_structs_raw:
        if s.get("alliance_id"):
            alliance_ids.add(int(s["alliance_id"]))

    alliance_names: dict[int, str] = {}
    for chunk_start in range(0, len(list(alliance_ids)), 1000):
        chunk = list(alliance_ids)[chunk_start:chunk_start + 1000]
        try:
            for item in universe_names(chunk):
                if item.get("category") == "alliance":
                    alliance_names[int(item["id"])] = item["name"]
        except Exception:
            pass

    now_utc = datetime.now(timezone.utc)
    rows = []
    for s in sov_structs_raw:
        sys_id = int(s["solar_system_id"])
        alliance_id = int(s.get("alliance_id") or system_alliance.get(sys_id) or 0)
        sys_info = sde.get_system_local(sys_id) or {}
        system_name = sys_info.get("name") or f"System {sys_id}"
        region_name = sys_info.get("region_name") or ""
        region_id = int(sys_info.get("region_id") or 0)
        alliance_name = alliance_names.get(alliance_id, f"Alliance {alliance_id}") if alliance_id else "—"
        adm = float(s.get("vulnerability_occupancy_level") or 0)

        vuln_start_str = s.get("vulnerable_start_time") or ""
        vuln_end_str = s.get("vulnerable_end_time") or ""
        try:
            vuln_start = datetime.fromisoformat(vuln_start_str.replace("Z", "+00:00"))
        except Exception:
            vuln_start = None
        try:
            vuln_end = datetime.fromisoformat(vuln_end_str.replace("Z", "+00:00"))
        except Exception:
            vuln_end = None

        is_vulnerable = bool(vuln_start and vuln_end and vuln_start <= now_utc <= vuln_end)
        if is_vulnerable and vuln_end:
            next_event, next_event_label = vuln_end, "closes"
        elif vuln_start and vuln_start > now_utc:
            next_event, next_event_label = vuln_start, "opens"
        else:
            next_event, next_event_label = None, ""

        secs_until = int((next_event - now_utc).total_seconds()) if next_event else None
        rows.append({
            "system_id": sys_id,
            "system_name": system_name,
            "region_name": region_name,
            "region_id": region_id,
            "alliance_id": alliance_id,
            "alliance_name": alliance_name,
            "adm": adm,
            "vuln_start": vuln_start_str,
            "vuln_end": vuln_end_str,
            "is_vulnerable": is_vulnerable,
            "next_event": next_event.isoformat() if next_event else "",
            "next_event_label": next_event_label,
            "secs_until": secs_until,
        })
    return rows


@router.get("/ess", response_class=HTMLResponse)
def sov_timers_page(
    request: Request,
    character_id: str | None = Query(None),
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    characters = (
        db.query(Character)
        .filter(Character.account_id == account.id)
        .order_by(Character.character_name.asc())
        .all()
    )

    selected_char = None
    if character_id:
        for c in characters:
            if str(c.id) == character_id:
                selected_char = c
                break
    if selected_char is None and account.main_character_id:
        for c in characters:
            if c.id == account.main_character_id:
                selected_char = c
                break
    if selected_char is None and characters:
        selected_char = characters[0]

    char_location: dict = {}
    if selected_char:
        loc = _get_cached_character_location(selected_char, db)
        if loc:
            sys_id = int(loc.get("solar_system_id") or 0)
            sys_info = sde.get_system_local(sys_id) or {}
            char_location = {
                "system_id": sys_id,
                "system_name": sys_info.get("name") or f"System {sys_id}",
                "region_id": int(sys_info.get("region_id") or 0),
                "region_name": sys_info.get("region_name") or "",
            }

    rows, fetched_at = _sov_rows_from_db(db)
    if not rows:
        # First boot — DB empty, fall back to live ESI fetch
        rows = _sov_rows_from_esi_fallback()
        fetched_at = None

    alliances = sorted({(r["alliance_id"], r["alliance_name"]) for r in rows if r["alliance_id"]}, key=lambda x: x[1].lower())

    return templates.TemplateResponse("ess.html", {
        "request": request,
        "account": account,
        "characters": characters,
        "selected_char": selected_char,
        "char_location": char_location,
        "rows": rows,
        "alliances": alliances,
        "fetched_at": fetched_at.isoformat() if fetched_at else "",
    })


@router.get("/ess/data")
def sov_timers_data(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """JSON endpoint for auto-refresh — always DB-first."""
    rows, fetched_at = _sov_rows_from_db(db)
    return JSONResponse({
        "rows": rows,
        "fetched_at": fetched_at.isoformat() if fetched_at else datetime.now(timezone.utc).isoformat(),
    })
