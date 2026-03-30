from __future__ import annotations

import logging
import time
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
    leg, total_jumps = _best_leg(origin_system_id, destination_system_id, use_ansiblex=use_ansiblex)
    return total_jumps


def _build_plain_leg(origin_system_id: int, destination_system_id: int) -> tuple[list[dict], int]:
    route_systems = _get_route_systems(origin_system_id, destination_system_id)
    jumps = max(len(route_systems) - 1, 0)
    items: list[dict] = []
    for sys_id in route_systems[1:-1]:
        items.append({
            "system_id": int(sys_id),
            "system_name": _system_name(int(sys_id)),
            "jumps_from_prev": 1,
            "is_waypoint": False,
            "is_intermediate": True,
            "via_bridge": False,
        })
    items.append({
        "system_id": int(destination_system_id),
        "system_name": _system_name(int(destination_system_id)),
        "jumps_from_prev": int(jumps),
        "is_waypoint": True,
        "is_intermediate": False,
        "via_bridge": False,
    })
    return items, jumps


def _append_gate_segment(items: list[dict], route_systems: list[int], destination_system_id: int) -> None:
    for sys_id in route_systems[1:]:
        items.append({
            "system_id": int(sys_id),
            "system_name": _system_name(int(sys_id)),
            "jumps_from_prev": 1,
            "is_waypoint": int(sys_id) == int(destination_system_id),
            "is_intermediate": int(sys_id) != int(destination_system_id),
            "via_bridge": False,
        })


def _build_one_bridge_leg(origin_system_id: int, destination_system_id: int, bridge_start: int, bridge_end: int) -> tuple[list[dict], int]:
    items: list[dict] = []
    total_jumps = 0

    to_bridge = _get_route_systems(origin_system_id, bridge_start)
    total_jumps += max(len(to_bridge) - 1, 0)
    _append_gate_segment(items, to_bridge, bridge_start)

    total_jumps += 1
    items.append({
        "system_id": int(bridge_end),
        "system_name": _system_name(int(bridge_end)),
        "jumps_from_prev": 1,
        "is_waypoint": int(bridge_end) == int(destination_system_id),
        "is_intermediate": int(bridge_end) != int(destination_system_id),
        "via_bridge": True,
    })

    if int(bridge_end) != int(destination_system_id):
        from_bridge = _get_route_systems(bridge_end, destination_system_id)
        total_jumps += max(len(from_bridge) - 1, 0)
        _append_gate_segment(items, from_bridge, destination_system_id)

    for idx in range(len(items) - 1, -1, -1):
        if items[idx]["is_waypoint"]:
            items[idx]["jumps_from_prev"] = total_jumps
            break
    return items, total_jumps


def _best_leg(origin_system_id: int, destination_system_id: int, use_ansiblex: bool = True) -> tuple[list[dict], int]:
    if origin_system_id == destination_system_id:
        return [], 0
    cache_key = (int(origin_system_id), int(destination_system_id), bool(use_ansiblex))
    cached = _best_route_cache.get(cache_key)
    if cached and time.time() - cached[1] < _ROUTE_CACHE_TTL:
        data = cached[0]
        return list(data["items"]), int(data["jumps"])

    best_items, best_jumps = _build_plain_leg(origin_system_id, destination_system_id)
    if use_ansiblex:
        base_route = _get_route_systems(origin_system_id, destination_system_id)
        for gate in _ansiblex.bridges_touching_systems(base_route):
            from_id = int(gate.get("from") or 0)
            to_id = int(gate.get("to") or 0)
            if not from_id or not to_id:
                continue
            candidate_a, jumps_a = _build_one_bridge_leg(origin_system_id, destination_system_id, from_id, to_id)
            if jumps_a < best_jumps:
                best_items, best_jumps = candidate_a, jumps_a
            candidate_b, jumps_b = _build_one_bridge_leg(origin_system_id, destination_system_id, to_id, from_id)
            if jumps_b < best_jumps:
                best_items, best_jumps = candidate_b, jumps_b

    _best_route_cache[cache_key] = ({"items": list(best_items), "jumps": int(best_jumps)}, time.time())
    return best_items, best_jumps


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
