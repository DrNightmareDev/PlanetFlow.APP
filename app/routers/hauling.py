from __future__ import annotations

import logging
import re
import time
from collections import deque
from urllib.parse import unquote, urlparse

import requests
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app import ansiblex as _ansiblex
from app import sde
from app.database import get_db
from app.dependencies import require_account, require_manager_or_admin
from app.esi import ensure_valid_token, get_character_location
from app.market import PI_TYPE_IDS
from app.models import Character, CorpBridgeConnection, MarketCache
from app.routers.dashboard import _apply_price_mode, _load_colony_cache, _recompute_expiry
from app.templates_env import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hauling", tags=["hauling"])

_LOCATION_CACHE_TTL = 300
_ROUTE_CACHE_TTL = 600
_location_cache: dict[int, tuple[dict, float]] = {}
_route_cache: dict[tuple[int, int], tuple[list[int], float]] = {}
_best_route_cache: dict[tuple[int, int, bool], tuple[dict, float]] = {}
_DOTLAN_SYSTEM_LINK_RE = re.compile(r'href="/system/([^"/?#]+)"', re.IGNORECASE)


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


def _system_info_or_404(system_id: int) -> dict:
    info = sde.get_system_local(int(system_id))
    if not info:
        raise HTTPException(status_code=400, detail=f"Unbekanntes System: {system_id}")
    return info


def _normalize_bridge_pair(from_system_id: int, to_system_id: int) -> tuple[int, int]:
    from_id = int(from_system_id)
    to_id = int(to_system_id)
    if from_id == to_id:
        raise HTTPException(status_code=400, detail="Bridge-Endpunkte muessen unterschiedlich sein")
    return (from_id, to_id) if from_id < to_id else (to_id, from_id)


def _invalidate_route_caches() -> None:
    _best_route_cache.clear()


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


def _all_known_corporations(db: Session) -> list[dict]:
    known: dict[int, str] = {}
    characters = db.query(Character).all()
    for char in characters:
        corp_id = int(char.corporation_id or 0)
        if not corp_id:
            continue
        known[corp_id] = char.corporation_name or known.get(corp_id) or f"Corp #{corp_id}"
    return [
        {"corporation_id": corp_id, "corporation_name": name}
        for corp_id, name in sorted(known.items(), key=lambda item: item[1].lower())
    ]


def _manageable_corporations(account, db: Session) -> list[dict]:
    chars = db.query(Character).filter(Character.account_id == account.id).all()
    own_corps: dict[int, str] = {}
    for char in chars:
        corp_id = int(char.corporation_id or 0)
        if not corp_id:
            continue
        own_corps[corp_id] = char.corporation_name or own_corps.get(corp_id) or f"Corp #{corp_id}"
    entries = [
        {"corporation_id": corp_id, "corporation_name": name}
        for corp_id, name in sorted(own_corps.items(), key=lambda item: item[1].lower())
    ]
    if account.is_owner:
        return _all_known_corporations(db)
    return entries


def _manageable_corporation_ids(account, db: Session) -> set[int]:
    return {int(item["corporation_id"]) for item in _manageable_corporations(account, db)}


def _can_manage_bridge(account, corporation_id: int, db: Session) -> bool:
    if account.is_owner:
        return True
    if not account.is_admin:
        return False
    return int(corporation_id) in _manageable_corporation_ids(account, db)


def _resolve_corporation_name(corporation_id: int, db: Session) -> str:
    for entry in _all_known_corporations(db):
        if int(entry["corporation_id"]) == int(corporation_id):
            return str(entry["corporation_name"] or f"Corp #{corporation_id}")
    return f"Corp #{int(corporation_id)}"


def _upsert_bridge_connection(
    *,
    db: Session,
    corporation_id: int,
    corporation_name: str,
    from_system_id: int,
    from_system_name: str,
    to_system_id: int,
    to_system_name: str,
    notes: str | None = None,
    created_by_account_id: int | None = None,
) -> tuple[CorpBridgeConnection, bool]:
    existing = (
        db.query(CorpBridgeConnection)
        .filter(
            CorpBridgeConnection.corporation_id == int(corporation_id),
            CorpBridgeConnection.from_system_id == int(from_system_id),
            CorpBridgeConnection.to_system_id == int(to_system_id),
        )
        .first()
    )
    created = False
    if existing is None:
        existing = CorpBridgeConnection(created_by_account_id=created_by_account_id)
        db.add(existing)
        created = True
    existing.corporation_id = int(corporation_id)
    existing.corporation_name = corporation_name
    existing.from_system_id = int(from_system_id)
    existing.from_system_name = from_system_name
    existing.to_system_id = int(to_system_id)
    existing.to_system_name = to_system_name
    existing.notes = notes
    return existing, created


def _parse_dotlan_bridge_pairs(dotlan_url: str) -> list[tuple[str, str]]:
    url = str(dotlan_url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Dotlan-Link muss mit http oder https beginnen")
    if parsed.netloc != "evemaps.dotlan.net":
        raise HTTPException(status_code=400, detail="Nur evemaps.dotlan.net Links sind erlaubt")
    if "/bridges/" not in parsed.path:
        raise HTTPException(status_code=400, detail="Bitte einen Dotlan-Bridge-Link verwenden")

    response = requests.get(
        url,
        headers={"User-Agent": "EVE-PI-Manager/1.0 github.com/DrNightmareDev/PI_Manager"},
        timeout=20,
    )
    response.raise_for_status()
    systems = [unquote(match).strip() for match in _DOTLAN_SYSTEM_LINK_RE.findall(response.text or "")]
    if len(systems) < 2:
        raise HTTPException(status_code=400, detail="Keine Bridge-Systeme auf der Dotlan-Seite gefunden")

    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index in range(0, len(systems) - 1, 2):
        left = systems[index]
        right = systems[index + 1]
        if not left or not right or left == right:
            continue
        key = tuple(sorted((left, right)))
        if key in seen:
            continue
        seen.add(key)
        pairs.append((left, right))
    if not pairs:
        raise HTTPException(status_code=400, detail="Keine eindeutigen Bridge-Paare in Dotlan gefunden")
    return pairs


def _serialize_manual_bridge(entry: CorpBridgeConnection, account, db: Session) -> dict:
    return {
        "id": int(entry.id),
        "corporation_id": int(entry.corporation_id),
        "corporation_name": entry.corporation_name,
        "from_system_id": int(entry.from_system_id),
        "from_system_name": entry.from_system_name,
        "to_system_id": int(entry.to_system_id),
        "to_system_name": entry.to_system_name,
        "notes": entry.notes or "",
        "can_edit": _can_manage_bridge(account, int(entry.corporation_id), db),
    }


def _manual_bridge_entries(db: Session) -> list[CorpBridgeConnection]:
    return (
        db.query(CorpBridgeConnection)
        .order_by(
            CorpBridgeConnection.corporation_name.asc(),
            CorpBridgeConnection.from_system_name.asc(),
            CorpBridgeConnection.to_system_name.asc(),
        )
        .all()
    )


def _bridge_adjacency(db: Session, use_ansiblex: bool) -> dict[int, list[dict]]:
    adjacency: dict[int, list[dict]] = {}

    def add_edge(from_id: int, to_id: int, payload: dict) -> None:
        adjacency.setdefault(int(from_id), []).append({
            "to": int(to_id),
            **payload,
        })

    if use_ansiblex:
        for gate in _ansiblex.all_bridges():
            from_id = int(gate.get("from") or 0)
            to_id = int(gate.get("to") or 0)
            if not from_id or not to_id:
                continue
            payload = {
                "type": "bridge",
                "bridge_source": "ansiblex",
                "bridge_label": gate.get("name") or f"{_system_name(from_id)} -> {_system_name(to_id)}",
                "bridge_corporation_name": None,
            }
            add_edge(from_id, to_id, payload)
            add_edge(to_id, from_id, payload)

    if use_ansiblex:
        for entry in _manual_bridge_entries(db):
            from_id = int(entry.from_system_id)
            to_id = int(entry.to_system_id)
            payload = {
                "type": "bridge",
                "bridge_source": "manual",
                "bridge_label": f"{entry.corporation_name}: {entry.from_system_name} -> {entry.to_system_name}",
                "bridge_corporation_name": entry.corporation_name,
                "bridge_id": int(entry.id),
            }
            add_edge(from_id, to_id, payload)
            add_edge(to_id, from_id, payload)

    for edges in adjacency.values():
        edges.sort(key=lambda item: (str(item.get("bridge_source") or ""), int(item.get("to") or 0)))
    return adjacency


def _fallback_esi_leg(origin_system_id: int, destination_system_id: int) -> tuple[list[dict], int]:
    route_systems = _get_route_systems(origin_system_id, destination_system_id)
    total_jumps = max(len(route_systems) - 1, 0)
    items: list[dict] = []
    for index, system_id in enumerate(route_systems[1:], start=1):
        is_final = index == len(route_systems) - 1
        items.append({
            "system_id": int(system_id),
            "system_name": _system_name(int(system_id)),
            "jumps_from_prev": total_jumps if is_final else 1,
            "is_waypoint": is_final,
            "is_intermediate": not is_final,
            "via_bridge": False,
            "bridge_label": None,
            "bridge_source": None,
            "bridge_corporation_name": None,
            "bridge_incoming": False,
            "bridge_outgoing": False,
        })
    return items, total_jumps


def _graph_steps(origin_system_id: int, destination_system_id: int, db: Session, use_ansiblex: bool) -> list[dict]:
    if origin_system_id == destination_system_id:
        return []
    if not sde.has_jump_graph():
        return []

    bridge_adjacency = _bridge_adjacency(db, use_ansiblex=use_ansiblex)
    start_id = int(origin_system_id)
    target_id = int(destination_system_id)
    queue: deque[int] = deque([start_id])
    previous: dict[int, dict | None] = {start_id: None}

    while queue:
        current = queue.popleft()
        if current == target_id:
            break

        for edge in bridge_adjacency.get(current, []):
            nxt = int(edge["to"])
            if nxt in previous:
                continue
            previous[nxt] = {
                "from": current,
                "to": nxt,
                "type": "bridge",
                "bridge_label": edge.get("bridge_label"),
                "bridge_source": edge.get("bridge_source"),
                "bridge_corporation_name": edge.get("bridge_corporation_name"),
            }
            queue.append(nxt)

        for nxt in sde.get_system_neighbors(current):
            if nxt in previous:
                continue
            previous[nxt] = {
                "from": current,
                "to": int(nxt),
                "type": "gate",
                "bridge_label": None,
                "bridge_source": None,
                "bridge_corporation_name": None,
            }
            queue.append(int(nxt))

    if target_id not in previous:
        return []

    steps: list[dict] = []
    current = target_id
    while current != start_id:
        step = previous.get(current)
        if not step:
            break
        steps.append(step)
        current = int(step["from"])
    steps.reverse()
    return steps


def _steps_to_items(steps: list[dict], destination_system_id: int) -> tuple[list[dict], int]:
    total_jumps = len(steps)
    items: list[dict] = []
    destination_id = int(destination_system_id)
    for index, step in enumerate(steps):
        end_id = int(step["to"])
        is_final = index == len(steps) - 1 and end_id == destination_id
        items.append({
            "system_id": end_id,
            "system_name": _system_name(end_id),
            "jumps_from_prev": total_jumps if is_final else 1,
            "is_waypoint": is_final,
            "is_intermediate": not is_final,
            "via_bridge": step.get("type") == "bridge",
            "bridge_label": step.get("bridge_label"),
            "bridge_source": step.get("bridge_source"),
            "bridge_corporation_name": step.get("bridge_corporation_name"),
            "bridge_incoming": step.get("type") == "bridge",
            "bridge_outgoing": False,
        })
    for index, step in enumerate(steps):
        if step.get("type") != "bridge":
            continue
        if index == 0:
            continue
        items[index - 1]["bridge_outgoing"] = True
        items[index - 1]["bridge_label"] = step.get("bridge_label")
    return items, total_jumps


def _best_leg(origin_system_id: int, destination_system_id: int, db: Session, use_ansiblex: bool = True) -> tuple[list[dict], int]:
    if origin_system_id == destination_system_id:
        return [], 0
    cache_key = (int(origin_system_id), int(destination_system_id), bool(use_ansiblex))
    cached = _best_route_cache.get(cache_key)
    if cached and time.time() - cached[1] < _ROUTE_CACHE_TTL:
        data = cached[0]
        return list(data["items"]), int(data["jumps"])

    steps = _graph_steps(origin_system_id, destination_system_id, db, use_ansiblex=use_ansiblex)
    if steps:
        items, total_jumps = _steps_to_items(steps, destination_system_id)
    else:
        items, total_jumps = _fallback_esi_leg(origin_system_id, destination_system_id)

    _best_route_cache[cache_key] = ({"items": list(items), "jumps": int(total_jumps)}, time.time())
    return items, total_jumps


def _jump_count(origin_system_id: int, destination_system_id: int, db: Session, use_ansiblex: bool = True) -> int:
    if origin_system_id == destination_system_id:
        return 0
    _, total_jumps = _best_leg(origin_system_id, destination_system_id, db, use_ansiblex=use_ansiblex)
    return total_jumps


def _build_route(origin_system_id: int, system_ids: list[int], db: Session, use_ansiblex: bool = True) -> tuple[list[dict], int]:
    remaining = list(dict.fromkeys(int(system_id) for system_id in system_ids if system_id and int(system_id) != int(origin_system_id)))[:20]
    ordered: list[dict] = [{
        "system_id": int(origin_system_id),
        "system_name": _system_name(int(origin_system_id)),
        "jumps_from_prev": 0,
        "is_waypoint": True,
        "is_intermediate": False,
        "via_bridge": False,
        "bridge_label": None,
        "bridge_source": None,
        "bridge_corporation_name": None,
        "bridge_incoming": False,
        "bridge_outgoing": False,
    }]
    total_jumps = 0
    current = int(origin_system_id)
    while remaining:
        next_id = min(remaining, key=lambda candidate: _jump_count(current, candidate, db, use_ansiblex=use_ansiblex))
        steps = _graph_steps(current, next_id, db, use_ansiblex=use_ansiblex)
        if steps and steps[0].get("type") == "bridge":
            ordered[-1]["bridge_outgoing"] = True
            ordered[-1]["bridge_label"] = steps[0].get("bridge_label")
        leg_items, jumps = _best_leg(current, next_id, db, use_ansiblex=use_ansiblex)
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
                db,
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
        "can_manage_bridges": bool(account.is_admin or account.is_owner),
        "can_manage_all_bridges": bool(account.is_owner),
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


@router.get("/api/bridge-connections")
def get_bridge_connections(
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    manual = [_serialize_manual_bridge(entry, account, db) for entry in _manual_bridge_entries(db)]
    return JSONResponse({
        "manual": manual,
        "manageable_corporations": _manageable_corporations(account, db),
        "can_manage": bool(account.is_admin or account.is_owner),
        "can_manage_all": bool(account.is_owner),
    })


@router.post("/api/bridge-connections")
def save_bridge_connection(
    payload: dict = Body(...),
    account=Depends(require_manager_or_admin),
    db: Session = Depends(get_db),
):
    corporation_id = int(payload.get("corporation_id") or 0)
    if not corporation_id:
        raise HTTPException(status_code=400, detail="Korporation fehlt")
    if not _can_manage_bridge(account, corporation_id, db):
        raise HTTPException(status_code=403, detail="Nur eigene Corporation erlaubt")

    from_system_id, to_system_id = _normalize_bridge_pair(
        int(payload.get("from_system_id") or 0),
        int(payload.get("to_system_id") or 0),
    )
    from_info = _system_info_or_404(from_system_id)
    to_info = _system_info_or_404(to_system_id)
    notes = str(payload.get("notes") or "").strip()[:255] or None
    corporation_name = _resolve_corporation_name(corporation_id, db)

    bridge_id = int(payload.get("id") or 0)
    existing = (
        db.query(CorpBridgeConnection)
        .filter(
            CorpBridgeConnection.corporation_id == corporation_id,
            CorpBridgeConnection.from_system_id == from_system_id,
            CorpBridgeConnection.to_system_id == to_system_id,
        )
        .first()
    )

    if bridge_id:
        entry = db.query(CorpBridgeConnection).filter(CorpBridgeConnection.id == bridge_id).first()
        if not entry:
            raise HTTPException(status_code=404, detail="Bridge-Verbindung nicht gefunden")
        if not _can_manage_bridge(account, int(entry.corporation_id), db):
            raise HTTPException(status_code=403, detail="Keine Berechtigung fuer diese Verbindung")
        if existing and int(existing.id) != int(entry.id):
            raise HTTPException(status_code=409, detail="Diese Verbindung existiert bereits")
        entry, _ = _upsert_bridge_connection(
            db=db,
            corporation_id=corporation_id,
            corporation_name=corporation_name,
            from_system_id=from_system_id,
            from_system_name=from_info["name"],
            to_system_id=to_system_id,
            to_system_name=to_info["name"],
            notes=notes,
            created_by_account_id=entry.created_by_account_id,
        )
    else:
        if existing:
            raise HTTPException(status_code=409, detail="Diese Verbindung existiert bereits")
        entry, _ = _upsert_bridge_connection(
            db=db,
            corporation_id=corporation_id,
            corporation_name=corporation_name,
            from_system_id=from_system_id,
            from_system_name=from_info["name"],
            to_system_id=to_system_id,
            to_system_name=to_info["name"],
            notes=notes,
            created_by_account_id=account.id,
        )

    db.commit()
    db.refresh(entry)
    _invalidate_route_caches()
    return JSONResponse({"ok": True, "connection": _serialize_manual_bridge(entry, account, db)})


@router.post("/api/bridge-connections/import-dotlan")
def import_dotlan_bridge_connections(
    payload: dict = Body(...),
    account=Depends(require_manager_or_admin),
    db: Session = Depends(get_db),
):
    corporation_id = int(payload.get("corporation_id") or 0)
    if not corporation_id:
        raise HTTPException(status_code=400, detail="Korporation fehlt")
    if not _can_manage_bridge(account, corporation_id, db):
        raise HTTPException(status_code=403, detail="Nur eigene Corporation erlaubt")

    dotlan_url = str(payload.get("url") or "").strip()
    if not dotlan_url:
        raise HTTPException(status_code=400, detail="Dotlan-Link fehlt")

    corporation_name = _resolve_corporation_name(corporation_id, db)
    bridge_pairs = _parse_dotlan_bridge_pairs(dotlan_url)

    created_count = 0
    updated_count = 0
    unresolved: list[str] = []
    imported: list[dict] = []

    for from_name, to_name in bridge_pairs:
        from_system = sde.find_system(from_name)
        to_system = sde.find_system(to_name)
        if not from_system or not to_system:
            unresolved.append(f"{from_name} -> {to_name}")
            continue
        from_system_id, to_system_id = _normalize_bridge_pair(int(from_system["id"]), int(to_system["id"]))
        from_info = _system_info_or_404(from_system_id)
        to_info = _system_info_or_404(to_system_id)
        entry, created = _upsert_bridge_connection(
            db=db,
            corporation_id=corporation_id,
            corporation_name=corporation_name,
            from_system_id=from_system_id,
            from_system_name=from_info["name"],
            to_system_id=to_system_id,
            to_system_name=to_info["name"],
            notes=f"Imported from Dotlan: {dotlan_url}"[:255],
            created_by_account_id=account.id,
        )
        db.flush()
        imported.append(_serialize_manual_bridge(entry, account, db))
        if created:
            created_count += 1
        else:
            updated_count += 1

    db.commit()
    _invalidate_route_caches()
    return JSONResponse({
        "ok": True,
        "created": created_count,
        "updated": updated_count,
        "unresolved": unresolved,
        "imported": imported,
    })


@router.delete("/api/bridge-connections/{bridge_id}")
def delete_bridge_connection(
    bridge_id: int,
    account=Depends(require_manager_or_admin),
    db: Session = Depends(get_db),
):
    entry = db.query(CorpBridgeConnection).filter(CorpBridgeConnection.id == bridge_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Bridge-Verbindung nicht gefunden")
    if not _can_manage_bridge(account, int(entry.corporation_id), db):
        raise HTTPException(status_code=403, detail="Keine Berechtigung fuer diese Verbindung")
    db.delete(entry)
    db.commit()
    _invalidate_route_caches()
    return JSONResponse({"ok": True})


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
        ordered, total_jumps = _build_route(origin_system_id, system_ids, db, use_ansiblex=use_ansiblex)
    except Exception:
        logger.exception("hauling: route rebuild failed")
        return JSONResponse({"ordered": [], "total_jumps": 0, "ansiblex_status": _ansiblex.status()})
    return JSONResponse({"ordered": ordered, "total_jumps": total_jumps, "ansiblex_status": _ansiblex.status()})
