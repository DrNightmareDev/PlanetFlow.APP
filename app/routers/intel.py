from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app import sde
from app.database import get_db
from app.dependencies import require_owner
from app.models import KillActivityCache
from app.templates_env import templates
from app.zkill import get_region_kills, get_system_kill_summary, normalize_kill, resolve_kill_names

router = APIRouter(prefix="/intel", tags=["intel"])

logger = logging.getLogger(__name__)
WINDOW_SECONDS = {"5m": 300, "15m": 900, "60m": 3600, "24h": 86400}
SYSTEM_DETAIL_TTL = timedelta(minutes=5)


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
        angle = (group_index / group_count) * 6.28318
        orbit_x = center_x + 320 * __import__("math").cos(angle)
        orbit_y = center_y + 250 * __import__("math").sin(angle)
        group_systems = sorted(group_systems, key=lambda item: item["name"].lower())
        inner_count = max(1, len(group_systems))
        for system_index, system in enumerate(group_systems):
            inner_angle = (system_index / inner_count) * 6.28318
            radius = 54 + (system_index % 5) * 16
            positions[int(system["id"])] = (
                round(orbit_x + radius * __import__("math").cos(inner_angle), 2),
                round(orbit_y + radius * __import__("math").sin(inner_angle), 2),
            )
    return positions


def _normalize_feed_entry(kill: dict, graph: dict, name_map: dict[int, str]) -> dict | None:
    killmail_id = int(kill.get("killmail_id") or 0)
    solar_system_id = int(kill.get("solar_system_id") or 0)
    system_info = next((system for system in graph["systems"] if system["id"] == solar_system_id), None)
    if not killmail_id:
        return None
    system_name = system_info["name"] if system_info else f"System {solar_system_id}"

    normalized = normalize_kill(
        kill,
        system_name=system_name,
        name_map=name_map,
    )

    return {
        "killmail_id": normalized["killmail_id"],
        "kill_url": normalized["kill_url"],
        "timestamp": normalized["killmail_time_utc"],
        "timestamp_utc": normalized["killmail_time_utc_label"],
        "system_id": normalized["system_id"],
        "system_name": normalized["system_name"],
        "region_name": graph["name"],
        "pilot_name": normalized["pilot_name"],
        "ship_type": normalized["ship_type_name"],
        "ship_image_url": normalized["ship_image_url"],
        "attackers": normalized["attackers"],
        "isk_value": normalized["isk_value"],
    }


def _fallback_feed(graph: dict, window: str, kill_type: str) -> tuple[list[dict], list[dict]]:
    systems = []
    for system in graph["systems"]:
        systems.append({
            "system_id": system["id"],
            "kill_count": 0,
            "heat": 0.0,
            "danger": "cold",
        })
    return systems, []


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
    graph = {
        **graph,
        "systems": systems,
    }
    raw_kills = get_region_kills(region_id, window=window, limit=200)
    if not raw_kills:
        activity, feed = _fallback_feed(graph, window, kill_type)
        return graph, activity, feed, {
            "source_state": "empty",
            "raw_kills": 0,
            "feed_kills": 0,
            "activity_systems": 0,
            "message": "No kill data returned from zKill/ESI for this region and time window.",
        }

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=WINDOW_SECONDS.get(window, 3600))
    name_map = resolve_kill_names(raw_kills)
    activity_counter: dict[int, int] = defaultdict(int)
    feed: list[dict] = []

    for kill in raw_kills:
        if kill_type == "pod" and int((kill.get("victim") or {}).get("ship_type_id") or 0) != 670:
            continue
        if kill_type == "ship" and int((kill.get("victim") or {}).get("ship_type_id") or 0) == 670:
            continue
        try:
            kill_time = datetime.fromisoformat(str(kill.get("killmail_time")).replace("Z", "+00:00"))
        except Exception:
            continue
        if kill_time < cutoff:
            continue
        entry = _normalize_feed_entry(kill, graph, name_map)
        if not entry:
            continue
        feed.append(entry)
        activity_counter[int(entry["system_id"])] += 1

    if not feed:
        activity, feed = _fallback_feed(graph, window, kill_type)
        return graph, activity, feed, {
            "source_state": "filtered_empty",
            "raw_kills": len(raw_kills),
            "feed_kills": 0,
            "activity_systems": 0,
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
        "raw_kills": len(raw_kills),
        "feed_kills": len(feed),
        "activity_systems": active_systems,
        "message": "Kill data loaded successfully.",
    }


@router.get("/map", response_class=HTMLResponse)
def intel_map(
    request: Request,
    region: str = Query("10000010"),
    window: str = Query("60m"),
    kill_type: str = Query("all"),
    layout: str = Query("geo"),
    account=Depends(require_owner),
):
    regions = sde.get_region_catalog()
    selected_window = window if window in WINDOW_SECONDS else "60m"
    selected_kill_type = kill_type if kill_type in {"all", "ship", "pod"} else "all"
    selected_layout = layout if layout in {"geo", "alt"} else "geo"
    graph, system_activity, kill_feed, source_meta = _build_live_snapshot(
        int(region or regions[0]["id"]),
        selected_window,
        selected_kill_type,
    )
    return templates.TemplateResponse("intel_map.html", {
        "request": request,
        "account": account,
        "regions": regions,
        "selected_region": str(graph["id"]),
        "selected_window": selected_window,
        "selected_kill_type": selected_kill_type,
        "selected_layout": selected_layout,
        "region_data": graph,
        "initial_activity": system_activity,
        "initial_feed": kill_feed,
        "initial_source_meta": source_meta,
    })


@router.get("/map/live")
def intel_map_live(
    region: str = Query("10000010"),
    window: str = Query("60m"),
    kill_type: str = Query("all"),
    account=Depends(require_owner),
):
    graph, system_activity, kill_feed, source_meta = _build_live_snapshot(int(region), window, kill_type)
    return JSONResponse({
        "region": graph,
        "window": window,
        "kill_type": kill_type,
        "activity": system_activity,
        "feed": kill_feed,
        "source_meta": source_meta,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


@router.get("/system/{system_id}")
def intel_system_details(
    system_id: int,
    window: str = Query("60m"),
    account=Depends(require_owner),
    db: Session = Depends(get_db),
):
    selected_window = window if window in WINDOW_SECONDS else "60m"
    now = datetime.now(timezone.utc)
    row = db.get(KillActivityCache, int(system_id))
    if row and row.fetched_at:
        fetched_at = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)
        if row.window == selected_window and now - fetched_at <= SYSTEM_DETAIL_TTL:
            try:
                latest_kills = json.loads(row.latest_kills_json or "[]")
            except Exception:
                latest_kills = []
            return JSONResponse({
                "system_id": int(system_id),
                "kill_count": int(row.kill_count or 0),
                "window": selected_window,
                "latest_kills": latest_kills,
                "fetched_at_iso": fetched_at.astimezone(timezone.utc).isoformat(),
                "cache_state": "db",
            })

    try:
        summary = get_system_kill_summary(int(system_id), window=selected_window, limit=5)
        latest_kills = summary.get("latest_kills") or []
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
        summary["cache_state"] = "fresh"
        return JSONResponse(summary)
    except Exception:
        logger.exception("intel: failed system detail refresh for %s", system_id)
        if row and row.fetched_at:
            fetched_at = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)
            try:
                latest_kills = json.loads(row.latest_kills_json or "[]")
            except Exception:
                latest_kills = []
            return JSONResponse({
                "system_id": int(system_id),
                "kill_count": int(row.kill_count or 0),
                "window": row.window or selected_window,
                "latest_kills": latest_kills,
                "fetched_at_iso": fetched_at.astimezone(timezone.utc).isoformat(),
                "cache_state": "stale_db",
            })
        return JSONResponse({
            "system_id": int(system_id),
            "kill_count": 0,
            "window": selected_window,
            "latest_kills": [],
            "fetched_at_iso": now.astimezone(timezone.utc).isoformat(),
            "cache_state": "empty",
        })
