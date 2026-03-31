from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app import sde
from app.dependencies import require_owner
from app.esi import universe_names
from app.templates_env import templates

router = APIRouter(prefix="/intel", tags=["intel"])

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "EVE-PI-Manager/1.0 github.com/DrNightmareDev/PI_Manager"}
WINDOW_SECONDS = {"5m": 300, "15m": 900, "60m": 3600, "24h": 86400}


def _ship_image(type_id: int) -> str:
    return f"https://images.evetech.net/types/{type_id}/render?size=64"


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


def _fetch_region_kills(region_id: int, window: str) -> list[dict]:
    past_seconds = int(WINDOW_SECONDS.get(window, 3600))
    try:
        response = requests.get(
            f"https://zkillboard.com/api/kills/regionID/{region_id}/pastSeconds/{past_seconds}/limit/200/",
            headers=HEADERS,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []
    except Exception:
        logger.exception("intel: failed to fetch kills for region %s", region_id)
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
        batch = id_list[start:start + 1000]
        for item in universe_names(batch):
            try:
                names[int(item["id"])] = item["name"]
            except Exception:
                continue
    return names


def _normalize_feed_entry(kill: dict, graph: dict, name_map: dict[int, str]) -> dict | None:
    killmail_id = int(kill.get("killmail_id") or 0)
    solar_system_id = int(kill.get("solar_system_id") or 0)
    system_info = next((system for system in graph["systems"] if system["id"] == solar_system_id), None)
    if not killmail_id or not system_info:
        return None

    victim = kill.get("victim") or {}
    ship_type_id = int(victim.get("ship_type_id") or 0)
    character_id = int(victim.get("character_id") or 0)
    corporation_id = int(victim.get("corporation_id") or 0)
    alliance_id = int(victim.get("alliance_id") or 0)

    victim_name = (
        victim.get("character_name")
        or name_map.get(character_id)
        or victim.get("characterName")
        or "Unknown Pilot"
    )
    corp_name = (
        victim.get("alliance_name")
        or name_map.get(alliance_id)
        or victim.get("corporation_name")
        or name_map.get(corporation_id)
        or "Unknown"
    )
    ship_name = sde.get_type_name(ship_type_id) or f"Type {ship_type_id}"
    attackers = kill.get("attackers") or []
    zkb = kill.get("zkb") or {}
    kill_time = kill.get("killmail_time") or datetime.now(timezone.utc).isoformat()

    return {
        "killmail_id": killmail_id,
        "kill_url": f"https://zkillboard.com/kill/{killmail_id}/",
        "timestamp": kill_time,
        "system_id": solar_system_id,
        "system_name": system_info["name"],
        "region_name": graph["name"],
        "victim_name": victim_name,
        "ship_type": ship_name,
        "ship_image_url": _ship_image(ship_type_id) if ship_type_id else "",
        "alliance_name": corp_name,
        "attackers": len(attackers),
        "isk_value": float(zkb.get("totalValue") or 0.0),
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


def _build_live_snapshot(region_id: int, window: str, kill_type: str) -> tuple[dict, list[dict], list[dict]]:
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
    raw_kills = _fetch_region_kills(region_id, window)
    if not raw_kills:
        activity, feed = _fallback_feed(graph, window, kill_type)
        return graph, activity, feed

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=WINDOW_SECONDS.get(window, 3600))
    name_map = _resolve_names(raw_kills)
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
        return graph, activity, feed

    feed.sort(key=lambda item: item["timestamp"], reverse=True)
    activity = []
    for system in graph["systems"]:
        count = int(activity_counter.get(system["id"], 0))
        activity.append({
            "system_id": system["id"],
            "kill_count": count,
            "heat": min(1.0, count / 9.0),
            "danger": "hot" if count >= 7 else "warm" if count >= 3 else "cold",
        })
    return graph, activity, feed[:200]


@router.get("/map", response_class=HTMLResponse)
def intel_map(
    request: Request,
    region: str = Query("10000010"),
    account=Depends(require_owner),
):
    regions = sde.get_region_catalog()
    graph, system_activity, kill_feed = _build_live_snapshot(int(region or regions[0]["id"]), "60m", "all")
    return templates.TemplateResponse("intel_map.html", {
        "request": request,
        "account": account,
        "regions": regions,
        "selected_region": str(graph["id"]),
        "region_data": graph,
        "initial_activity": system_activity,
        "initial_feed": kill_feed,
    })


@router.get("/map/live")
def intel_map_live(
    region: str = Query("10000010"),
    window: str = Query("60m"),
    kill_type: str = Query("all"),
    account=Depends(require_owner),
):
    graph, system_activity, kill_feed = _build_live_snapshot(int(region), window, kill_type)
    return JSONResponse({
        "region": graph,
        "window": window,
        "kill_type": kill_type,
        "activity": system_activity,
        "feed": kill_feed,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
