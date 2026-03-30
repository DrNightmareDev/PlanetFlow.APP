from __future__ import annotations

import logging
import time
import heapq
from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app import ansiblex as _ansiblex
from app import sde
from app.database import get_db
from app.dependencies import require_account
from app.esi import ensure_valid_token, get_character_location
from app.models import Character
from app.market import PI_TYPE_IDS
from app.models import MarketCache
from app.routers.dashboard import _apply_price_mode, _load_colony_cache, _recompute_expiry
from app.templates_env import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hauling", tags=["hauling"])

_LOCATION_CACHE_TTL = 300
_ROUTE_CACHE_TTL = 3600
_location_cache: dict[int, tuple[dict, float]] = {}
_route_cache: dict[tuple[int, int], tuple[list[int], float]] = {}
_best_route_cache: dict[tuple[int, int, bool], tuple[dict, float]] = {}


def _storage_has_items(storage: list[dict] | None) -> bool:
    return any(float(item.get("amount") or 0) > 0 for entry in (storage or []) for item in (entry.get("items") or []))


def _colony_needs_hauling_attention(colony: dict) -> bool:
    if _storage_has_items(colony.get("storage")):
        return True
    expiry_hours = colony.get("expiry_hours")
    if expiry_hours is not None and float(expiry_hours) < 0:
        return True
    return colony.get("is_stalled") is True


def _storage_summary(storage: list[dict] | None) -> tuple[list[str], int]:
    items = []
    for entry in storage or []:
        for item in entry.get("items") or []:
            amount = int(item.get("amount") or 0)
            if amount > 0:
                items.append(f"{item.get('name')} x{amount}")
    shown = items[:3]
    return shown, max(len(items) - len(shown), 0)


def _storage_value_details(storage: list[dict] | None, price_mode: str, db: Session) -> tuple[float, list[dict]]:
    breakdown = []
    total = 0.0
    for entry in storage or []:
        for item in entry.get("items") or []:
            amount = float(item.get("amount") or 0.0)
            if amount <= 0:
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            type_id = PI_TYPE_IDS.get(name) or sde.find_type_id_by_name(name)
            if not type_id:
                continue
            row = db.get(MarketCache, int(type_id))
            if not row:
                continue
            unit_price = float(getattr(row, "best_sell" if price_mode == "sell" else "best_buy") or 0.0)
            line_value = unit_price * amount
            total += line_value
            breakdown.append({
                "name": name,
                "amount": int(amount),
                "unit_price": unit_price,
                "line_value": line_value,
            })
    breakdown.sort(key=lambda item: item["line_value"], reverse=True)
    return total, breakdown


def _storage_fill_stats(storage: list[dict] | None) -> tuple[float, float, float, int]:
    total_capacity = 0.0
    total_used = 0.0
    for entry in storage or []:
        total_capacity += float(entry.get("capacity") or 0.0)
        total_used += float(entry.get("used_m3") or 0.0)
    fill_pct = (total_used / total_capacity * 100.0) if total_capacity > 0 else 0.0
    return total_used, total_capacity, max(0.0, min(100.0, fill_pct)), len(storage or [])


def _urgency_score(colony: dict) -> float:
    fill_pct = float(colony.get("storage_fill_pct") or 0.0)
    highest_tier_num = int(colony.get("highest_tier_num") or 0)
    tier_weight = {0: 1.0, 1: 1.0, 2: 1.08, 3: 1.16, 4: 1.28}.get(highest_tier_num, 1.0)
    return fill_pct * tier_weight


def _urgency_percent(fill_pct: float | None) -> int:
    if fill_pct is None:
        return 0
    return max(0, min(100, int(round(float(fill_pct)))))


def _storage_breakdown_title(breakdown: list[dict]) -> str:
    if not breakdown:
        return ""
    return "\n".join(
        f"{item['name']}: {item['amount']} x {item['unit_price']:.0f} = {item['line_value']:.0f} ISK"
        for item in breakdown[:10]
    )


def _system_name(system_id: int) -> str:
    info = sde.get_system_local(system_id) or {}
    return info.get("name", f"System {system_id}")


def _get_cached_location(character: Character, db: Session) -> dict | None:
    cached = _location_cache.get(character.id)
    if cached and time.time() - cached[1] < _LOCATION_CACHE_TTL:
        return cached[0]
    token = ensure_valid_token(character, db)
    if not token:
        return None
    location = get_character_location(int(character.eve_character_id), token)
    if location:
        _location_cache[character.id] = (location, time.time())
    return location or None


def _get_route_systems(origin_system_id: int, destination_system_id: int) -> list[int]:
    if origin_system_id == destination_system_id:
        return [int(origin_system_id)]
    key = (int(origin_system_id), int(destination_system_id))
    cached = _route_cache.get(key)
    if cached and time.time() - cached[1] < _ROUTE_CACHE_TTL:
        return cached[0]
    resp = requests.get(
        f"https://esi.evetech.net/latest/route/{int(origin_system_id)}/{int(destination_system_id)}/",
        params={"datasource": "tranquility"},
        headers={"User-Agent": "EVE-PI-Manager/1.0 github.com/DrNightmareDev/PI_Manager"},
        timeout=15,
    )
    resp.raise_for_status()
    systems = [int(system_id) for system_id in (resp.json() or [])]
    _route_cache[key] = (systems, time.time())
    _route_cache[(key[1], key[0])] = (list(reversed(systems)), time.time())
    return systems


def _jump_count(origin_system_id: int, destination_system_id: int, use_ansiblex: bool = True) -> int:
    if origin_system_id == destination_system_id:
        return 0
    _, total_jumps = _best_leg(origin_system_id, destination_system_id, use_ansiblex=use_ansiblex)
    return total_jumps


def _discover_route_nodes(origin_system_id: int, destination_system_id: int, use_ansiblex: bool) -> list[int]:
    discovered = {int(origin_system_id), int(destination_system_id)}
    if not use_ansiblex:
        return sorted(discovered)

    explored_paths = {int(system_id) for system_id in _get_route_systems(origin_system_id, destination_system_id)}
    frontier = set(explored_paths)
    rounds = 0
    max_nodes = 48

    while frontier and rounds < 3 and len(discovered) < max_nodes:
        rounds += 1
        new_nodes: set[int] = set()
        bridges = _ansiblex.bridges_touching_systems(list(frontier))
        for gate in bridges:
            from_id = int(gate.get("from") or 0)
            to_id = int(gate.get("to") or 0)
            if from_id:
                new_nodes.add(from_id)
            if to_id:
                new_nodes.add(to_id)
        new_nodes -= discovered
        if not new_nodes:
            break
        if len(discovered) + len(new_nodes) > max_nodes:
            new_nodes = set(sorted(new_nodes)[: max_nodes - len(discovered)])
        discovered.update(new_nodes)

        next_frontier: set[int] = set()
        for node_id in new_nodes:
            for anchor in (int(origin_system_id), int(destination_system_id)):
                for sys_id in _get_route_systems(anchor, node_id):
                    if sys_id not in explored_paths:
                        next_frontier.add(int(sys_id))
                        explored_paths.add(int(sys_id))
        frontier = next_frontier

    return sorted(discovered)


def _expand_compressed_path(node_path: list[int], edge_types: list[str], destination_system_id: int, total_jumps: int) -> list[dict]:
    items: list[dict] = []
    destination_id = int(destination_system_id)
    for index, edge_type in enumerate(edge_types):
        start_id = int(node_path[index])
        end_id = int(node_path[index + 1])
        is_final = end_id == destination_id
        if edge_type == "bridge":
            items.append({
                "system_id": end_id,
                "system_name": _system_name(end_id),
                "jumps_from_prev": 1 if not is_final else total_jumps,
                "is_waypoint": is_final,
                "is_intermediate": not is_final,
                "via_bridge": True,
            })
            continue

        route_systems = _get_route_systems(start_id, end_id)
        for sys_id in route_systems[1:]:
            sys_id = int(sys_id)
            is_segment_end = sys_id == end_id
            items.append({
                "system_id": sys_id,
                "system_name": _system_name(sys_id),
                "jumps_from_prev": total_jumps if is_final and is_segment_end else 1,
                "is_waypoint": is_final and is_segment_end,
                "is_intermediate": not (is_final and is_segment_end),
                "via_bridge": False,
            })
    return items


def _best_leg(origin_system_id: int, destination_system_id: int, use_ansiblex: bool = True) -> tuple[list[dict], int]:
    if origin_system_id == destination_system_id:
        return [], 0
    cache_key = (int(origin_system_id), int(destination_system_id), bool(use_ansiblex))
    cached = _best_route_cache.get(cache_key)
    if cached and time.time() - cached[1] < _ROUTE_CACHE_TTL:
        data = cached[0]
        return list(data["items"]), int(data["jumps"])

    nodes = _discover_route_nodes(origin_system_id, destination_system_id, use_ansiblex=use_ansiblex)
    adjacency: dict[int, list[tuple[int, int, str]]] = {node_id: [] for node_id in nodes}

    for idx, start_id in enumerate(nodes):
        for end_id in nodes[idx + 1:]:
            jumps = max(len(_get_route_systems(start_id, end_id)) - 1, 0)
            adjacency[start_id].append((end_id, jumps, "gate"))
            adjacency[end_id].append((start_id, jumps, "gate"))

    if use_ansiblex:
        for gate in _ansiblex.bridges_touching_systems(nodes):
            from_id = int(gate.get("from") or 0)
            to_id = int(gate.get("to") or 0)
            if from_id in adjacency and to_id in adjacency:
                adjacency[from_id].append((to_id, 1, "bridge"))
                adjacency[to_id].append((from_id, 1, "bridge"))

    start_id = int(origin_system_id)
    target_id = int(destination_system_id)
    distances: dict[int, int] = {node_id: 10 ** 9 for node_id in nodes}
    previous: dict[int, tuple[int, str] | None] = {node_id: None for node_id in nodes}
    distances[start_id] = 0
    heap: list[tuple[int, int]] = [(0, start_id)]

    while heap:
        current_distance, node_id = heapq.heappop(heap)
        if current_distance != distances[node_id]:
            continue
        if node_id == target_id:
            break
        for neighbor_id, weight, edge_type in adjacency.get(node_id, []):
            candidate = current_distance + int(weight)
            if candidate < distances[neighbor_id]:
                distances[neighbor_id] = candidate
                previous[neighbor_id] = (node_id, edge_type)
                heapq.heappush(heap, (candidate, neighbor_id))

    total_jumps = int(distances.get(target_id, 10 ** 9))
    if total_jumps >= 10 ** 9:
        items = _expand_compressed_path([start_id, target_id], ["gate"], target_id, max(len(_get_route_systems(start_id, target_id)) - 1, 0))
        total_jumps = max(len(_get_route_systems(start_id, target_id)) - 1, 0)
    else:
        node_path = [target_id]
        edge_types: list[str] = []
        cursor = target_id
        while cursor != start_id:
            previous_entry = previous.get(cursor)
            if previous_entry is None:
                break
            prev_node, edge_type = previous_entry
            node_path.append(prev_node)
            edge_types.append(edge_type)
            cursor = prev_node
        node_path.reverse()
        edge_types.reverse()
        items = _expand_compressed_path(node_path, edge_types, target_id, total_jumps)

    _best_route_cache[cache_key] = ({"items": list(items), "jumps": int(total_jumps)}, time.time())
    return items, total_jumps


def _build_route(origin_system_id: int, system_ids: list[int], use_ansiblex: bool = True) -> tuple[list[dict], int]:
    remaining = list(dict.fromkeys(int(system_id) for system_id in system_ids if system_id and int(system_id) != int(origin_system_id)))[:20]
    ordered: list[dict] = [{
        "system_id": int(origin_system_id),
        "system_name": _system_name(int(origin_system_id)),
        "jumps_from_prev": 0,
        "is_waypoint": True,
        "is_intermediate": False,
        "via_bridge": False,
    }]
    total_jumps = 0
    current = int(origin_system_id)
    while remaining:
        next_id = min(remaining, key=lambda candidate: _jump_count(current, candidate, use_ansiblex=use_ansiblex))
        leg_items, jumps = _best_leg(current, next_id, use_ansiblex=use_ansiblex)
        total_jumps += jumps
        ordered.extend(leg_items)
        remaining.remove(next_id)
        current = next_id
    return ordered, total_jumps


@router.get("", response_class=HTMLResponse)
def hauling_page(
    request: Request,
    character_id: int | None = Query(default=None),
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    characters = db.query(Character).filter(Character.account_id == account.id).all()
    selected_character = None
    if character_id:
        if character_id == -1:
            selected_character = None
        else:
            selected_character = next((char for char in characters if char.id == character_id), None)

    cached = _load_colony_cache(account.id, db) or {}
    colonies = list(cached.get("colonies") or [])
    cache_meta = dict(cached.get("meta") or {})
    if colonies:
        _recompute_expiry(colonies)
        colonies, _ = _apply_price_mode(colonies, cache_meta, getattr(account, "price_mode", "sell"))
    hauling_colonies = []
    for colony in colonies:
        if selected_character is not None and colony.get("character_name") != selected_character.character_name:
            continue
        if not _colony_needs_hauling_attention(colony):
            continue
        storage_value, storage_breakdown = _storage_value_details(colony.get("storage") or [], getattr(account, "price_mode", "sell"), db)
        summary_items, extra_count = _storage_summary(colony.get("storage"))
        total_used, total_capacity, fill_pct, storage_slots = _storage_fill_stats(colony.get("storage"))
        entry = dict(colony)
        entry["storage_value"] = storage_value
        entry["storage_breakdown"] = storage_breakdown
        entry["storage_breakdown_title"] = _storage_breakdown_title(storage_breakdown)
        entry["storage_summary_items"] = summary_items
        entry["storage_extra_count"] = extra_count
        entry["storage_used_m3"] = total_used
        entry["storage_capacity_m3"] = total_capacity
        entry["storage_fill_pct"] = fill_pct
        entry["storage_slot_count"] = storage_slots
        entry["urgency_score"] = _urgency_score(entry)
        entry["urgency_pct"] = _urgency_percent(fill_pct)
        hauling_colonies.append(entry)

    hauling_colonies.sort(key=lambda colony: colony.get("urgency_score", 0.0), reverse=True)

    location = _get_cached_location(selected_character, db) if selected_character else None
    route_ordered: list[dict] = []
    route_total_jumps = 0
    ansiblex_status = _ansiblex.status(ensure_loaded=True)
    if location and hauling_colonies:
        try:
            route_ordered, route_total_jumps = _build_route(
                int(location.get("solar_system_id") or 0),
                [int(colony.get("solar_system_id") or 0) for colony in hauling_colonies if colony.get("solar_system_id")],
                use_ansiblex=True,
            )
            ansiblex_status = _ansiblex.status()
        except Exception:
            logger.exception("hauling: failed to build initial route")
            route_ordered = []
            route_total_jumps = 0
            ansiblex_status = _ansiblex.status()

    location_name = _system_name(int(location.get("solar_system_id"))) if location and location.get("solar_system_id") else None
    dotlan_route_link = ""
    if route_ordered and len(route_ordered) > 1:
        names = [item["system_name"].replace(" ", "_") for item in route_ordered]
        dotlan_route_link = f"https://evemaps.dotlan.net/route/{':'.join(names)}"

    return templates.TemplateResponse("hauling.html", {
        "request": request,
        "account": account,
        "characters": characters,
        "selected_character_id": selected_character.id if selected_character else None,
        "selected_character_name": selected_character.character_name if selected_character else None,
        "location": location,
        "location_name": location_name,
        "hauling_colonies": hauling_colonies,
        "route_ordered": route_ordered,
        "route_total_jumps": route_total_jumps,
        "ansiblex_status": ansiblex_status,
        "dotlan_route_link": dotlan_route_link,
        "hauling_total_value": sum(float(colony.get("storage_value") or 0.0) for colony in hauling_colonies),
    })


@router.get("/api/location")
def get_location(
    character_id: int | None = Query(default=None),
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    characters = db.query(Character).filter(Character.account_id == account.id).all()
    if character_id == -1:
        return JSONResponse({"ok": False, "location": None})
    character = next((char for char in characters if char.id == character_id), None) if character_id else None
    if character is None:
        character = next((char for char in characters if char.id == account.main_character_id), None) or (characters[0] if characters else None)
    if character is None:
        return JSONResponse({"ok": False, "location": None})
    location = _get_cached_location(character, db)
    if not location or not location.get("solar_system_id"):
        return JSONResponse({"ok": False, "location": None})
    system_id = int(location["solar_system_id"])
    return JSONResponse({
        "ok": True,
        "character_id": character.id,
        "solar_system_id": system_id,
        "system_name": _system_name(system_id),
    })


@router.post("/api/route")
def get_route(
    payload: dict = Body(...),
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    origin_system_id = int(payload.get("origin_system_id") or 0)
    system_ids = [int(system_id) for system_id in (payload.get("system_ids") or []) if system_id]
    use_ansiblex = bool(payload.get("use_ansiblex", True))
    if not origin_system_id or not system_ids:
        return JSONResponse({"ordered": [], "total_jumps": 0})
    try:
        ordered, total_jumps = _build_route(origin_system_id, system_ids, use_ansiblex=use_ansiblex)
    except Exception:
        logger.exception("hauling: route rebuild failed")
        return JSONResponse({"ordered": [], "total_jumps": 0, "ansiblex_status": _ansiblex.status()})
    return JSONResponse({"ordered": ordered, "total_jumps": total_jumps, "ansiblex_status": _ansiblex.status()})
