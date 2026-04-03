from __future__ import annotations

import logging
import re
import time
import io
import itertools
from collections import deque
from functools import lru_cache
from heapq import heappop, heappush
from urllib.parse import unquote, urlparse

import requests
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app import ansiblex as _ansiblex
from app import sde
from app.database import get_db
from app.dependencies import require_account, require_manager_or_admin
from app.esi import ensure_valid_token, get_character_location
from app.i18n import translate
from app.market import PI_TYPE_IDS
from app.models import Character, CorpBridgeConnection, HaulingPreference, MarketCache, StaticPlanet, SystemGateDistance
from app.routers.dashboard import _apply_price_mode, _load_colony_cache, _recompute_expiry
from app.templates_env import templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hauling", tags=["hauling"])

_LOCATION_CACHE_TTL = 300
_ROUTE_CACHE_TTL = 600
_location_cache: dict[int, tuple[dict, float]] = {}
_route_cache: dict[tuple[int, int], tuple[list[int], float]] = {}
_best_route_cache: dict[tuple[int, int, bool, str], tuple[dict, float]] = {}
_gate_distance_cache: tuple[dict[tuple[int, int, int], dict], float] | None = None
_DOTLAN_SYSTEM_LINK_RE = re.compile(r'href="/system/([^"/?#]+)"', re.IGNORECASE)
_PLANET_NUMBER_RE = re.compile(r"(?:\s|[-])([IVXLCDM]+|\d+)$", re.IGNORECASE)
_AU_IN_METERS = 149_597_870_700.0


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


@lru_cache(maxsize=256)
def _roman_to_int(value: str) -> int:
    roman = str(value or "").upper()
    if not roman:
        return 0
    numerals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    previous = 0
    for char in reversed(roman):
        current = numerals.get(char, 0)
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    return total


def _planet_sort_tuple(planet_name: str, system_name: str | None = None) -> tuple[int, int, str]:
    text = str(planet_name or "").strip()
    base = text
    sys_name = str(system_name or "").strip()
    if sys_name and text.lower().startswith(sys_name.lower()):
        base = text[len(sys_name):].strip(" -")
    match = _PLANET_NUMBER_RE.search(base)
    if match:
        raw = match.group(1)
        if raw.isdigit():
            return (0, int(raw), text.casefold())
        return (0, _roman_to_int(raw), text.casefold())
    return (1, 0, text.casefold())


def _system_info_or_404(system_id: int) -> dict:
    info = sde.get_system_local(int(system_id))
    if not info:
        raise HTTPException(status_code=400, detail=translate("hauling.error_unknown_system", system_id=system_id, default="Unknown system: {system_id}"))
    return info


def _normalize_bridge_pair(from_system_id: int, to_system_id: int) -> tuple[int, int]:
    from_id = int(from_system_id)
    to_id = int(to_system_id)
    if from_id == to_id:
        raise HTTPException(status_code=400, detail=translate("hauling.error_bridge_same_endpoints", default="Bridge endpoints must be different"))
    return (from_id, to_id) if from_id < to_id else (to_id, from_id)


def _invalidate_route_caches() -> None:
    global _gate_distance_cache
    _best_route_cache.clear()
    _gate_distance_cache = None


def _route_mode_value(route_mode: str | None) -> str:
    return "warp" if str(route_mode or "").lower() == "warp" else "jumps"


def _format_gate_distance_au(distance_au: float | None) -> str:
    value = float(distance_au or 0.0)
    if value <= 0:
        return ""
    if value >= 100:
        return f"{value:,.0f} AU"
    if value >= 10:
        return f"{value:,.1f} AU"
    return f"{value:,.2f} AU"


def _distance3(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    dx = float(a[0]) - float(b[0])
    dy = float(a[1]) - float(b[1])
    dz = float(a[2]) - float(b[2])
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def _load_gate_distance_map(db: Session) -> dict[tuple[int, int, int], dict]:
    global _gate_distance_cache
    if _gate_distance_cache and time.time() - _gate_distance_cache[1] < _ROUTE_CACHE_TTL:
        return _gate_distance_cache[0]
    rows = db.query(SystemGateDistance).all()
    mapping = {
        (int(row.system_id), int(row.from_system_id), int(row.to_system_id)): {
            "distance_m": float(row.distance_m or 0.0),
            "distance_au": float(row.distance_au or 0.0),
            "entry_gate_id": int(row.entry_gate_id or 0),
            "exit_gate_id": int(row.exit_gate_id or 0),
        }
        for row in rows
    }
    _gate_distance_cache = (mapping, time.time())
    return mapping


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


def _exportable_corporations(account, db: Session) -> list[dict]:
    if account.is_owner:
        return _all_known_corporations(db)
    return _manageable_corporations(account, db)


def _manageable_corporation_ids(account, db: Session) -> set[int]:
    return {int(item["corporation_id"]) for item in _manageable_corporations(account, db)}


def _is_corp_member(account, db: Session) -> bool:
    return bool(_manageable_corporation_ids(account, db))


def _can_manage_bridge(account, corporation_id: int, db: Session) -> bool:
    if account.is_owner:
        return True
    if not account.is_admin:
        return False
    return int(corporation_id) in _manageable_corporation_ids(account, db)


def _can_export_bridge(account, corporation_id: int, db: Session) -> bool:
    if account.is_owner:
        return True
    exportable_ids = {int(item["corporation_id"]) for item in _exportable_corporations(account, db)}
    return int(corporation_id) in exportable_ids


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
        raise HTTPException(status_code=400, detail=translate("hauling.error_dotlan_scheme", default="Dotlan link must start with http or https"))
    if parsed.netloc != "evemaps.dotlan.net":
        raise HTTPException(status_code=400, detail=translate("hauling.error_dotlan_host", default="Only evemaps.dotlan.net links are allowed"))
    if "/bridges/" not in parsed.path:
        raise HTTPException(status_code=400, detail=translate("hauling.error_dotlan_bridge_link", default="Please use a Dotlan bridge link"))

    response = requests.get(
        url,
        headers={"User-Agent": "EVE-PI-Manager/1.0 github.com/DrNightmareDev/PI_Manager"},
        timeout=20,
    )
    response.raise_for_status()
    systems = [unquote(match).strip() for match in _DOTLAN_SYSTEM_LINK_RE.findall(response.text or "")]
    if len(systems) < 2:
        raise HTTPException(status_code=400, detail=translate("hauling.error_dotlan_no_systems", default="No bridge systems found on the Dotlan page"))

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
        raise HTTPException(status_code=400, detail=translate("hauling.error_dotlan_no_pairs", default="No unique bridge pairs found on Dotlan"))
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


def _warp_weighted_steps(origin_system_id: int, destination_system_id: int, db: Session, use_ansiblex: bool) -> tuple[list[dict], int, float]:
    if origin_system_id == destination_system_id:
        return [], 0, 0.0
    if not sde.has_jump_graph():
        return [], 0, 0.0

    bridge_adjacency = _bridge_adjacency(db, use_ansiblex=use_ansiblex)
    gate_distance_map = _load_gate_distance_map(db)
    start_state = (int(origin_system_id), 0, "start")
    target_id = int(destination_system_id)

    queue: list[tuple[tuple[int, float], tuple[int, int, str]]] = [((0, 0.0), start_state)]
    distances: dict[tuple[int, int, str], tuple[int, float]] = {start_state: (0, 0.0)}
    previous: dict[tuple[int, int, str], tuple[tuple[int, int, str], dict]] = {}
    best_target_state: tuple[int, int, str] | None = None
    best_target_cost: tuple[int, float] | None = None

    while queue:
        current_cost, state = heappop(queue)
        if current_cost != distances.get(state):
            continue
        current_system, previous_system, arrival_type = state
        if current_system == target_id:
            best_target_state = state
            best_target_cost = current_cost
            break

        for edge in bridge_adjacency.get(current_system, []):
            next_system = int(edge["to"])
            next_state = (next_system, current_system, "bridge")
            next_cost = (current_cost[0] + 1, current_cost[1])
            if next_cost < distances.get(next_state, (10**9, float("inf"))):
                distances[next_state] = next_cost
                previous[next_state] = (state, {
                    "from": current_system,
                    "to": next_system,
                    "type": "bridge",
                    "bridge_label": edge.get("bridge_label"),
                    "bridge_source": edge.get("bridge_source"),
                    "bridge_corporation_name": edge.get("bridge_corporation_name"),
                    "gate_warp_distance_m": 0.0,
                    "gate_warp_distance_au": 0.0,
                })
                heappush(queue, (next_cost, next_state))

        for next_system in sde.get_system_neighbors(current_system):
            gate_warp_payload = {"distance_m": 0.0, "distance_au": 0.0}
            if previous_system and arrival_type == "gate":
                gate_warp_payload = gate_distance_map.get(
                    (int(current_system), int(previous_system), int(next_system)),
                    gate_warp_payload,
                )
            next_state = (int(next_system), int(current_system), "gate")
            next_cost = (
                current_cost[0] + 1,
                current_cost[1] + float(gate_warp_payload.get("distance_m") or 0.0),
            )
            if next_cost < distances.get(next_state, (10**9, float("inf"))):
                distances[next_state] = next_cost
                previous[next_state] = (state, {
                    "from": int(current_system),
                    "to": int(next_system),
                    "type": "gate",
                    "bridge_label": None,
                    "bridge_source": None,
                    "bridge_corporation_name": None,
                    "gate_warp_distance_m": float(gate_warp_payload.get("distance_m") or 0.0),
                    "gate_warp_distance_au": float(gate_warp_payload.get("distance_au") or 0.0),
                })
                heappush(queue, (next_cost, next_state))

    if best_target_state is None or best_target_cost is None:
        return [], 0, 0.0

    steps: list[dict] = []
    current_state = best_target_state
    while current_state != start_state:
        prev_payload = previous.get(current_state)
        if not prev_payload:
            break
        parent_state, step = prev_payload
        steps.append(step)
        current_state = parent_state
    steps.reverse()
    return steps, int(best_target_cost[0]), float(best_target_cost[1])


def _steps_to_items(steps: list[dict], destination_system_id: int) -> tuple[list[dict], int, float]:
    total_jumps = len(steps)
    total_gate_warp_m = sum(float(step.get("gate_warp_distance_m") or 0.0) for step in steps)
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
            "gate_warp_distance_m": 0.0,
            "gate_warp_distance_au": 0.0,
            "gate_warp_distance_label": "",
        })
    for index, step in enumerate(steps):
        if step.get("type") != "bridge":
            if step.get("type") == "gate" and index > 0:
                items[index - 1]["gate_warp_distance_m"] = float(step.get("gate_warp_distance_m") or 0.0)
                items[index - 1]["gate_warp_distance_au"] = float(step.get("gate_warp_distance_au") or 0.0)
                items[index - 1]["gate_warp_distance_label"] = _format_gate_distance_au(step.get("gate_warp_distance_au"))
            continue
        if index > 0:
            items[index - 1]["bridge_outgoing"] = True
            items[index - 1]["bridge_label"] = step.get("bridge_label")
    return items, total_jumps, total_gate_warp_m


def _best_leg(
    origin_system_id: int,
    destination_system_id: int,
    db: Session,
    use_ansiblex: bool = True,
    route_mode: str = "jumps",
) -> tuple[list[dict], int, float]:
    if origin_system_id == destination_system_id:
        return [], 0, 0.0
    resolved_route_mode = _route_mode_value(route_mode)
    cache_key = (int(origin_system_id), int(destination_system_id), bool(use_ansiblex), resolved_route_mode)
    cached = _best_route_cache.get(cache_key)
    if cached and time.time() - cached[1] < _ROUTE_CACHE_TTL:
        data = cached[0]
        return list(data["items"]), int(data["jumps"]), float(data.get("gate_warp_m") or 0.0)

    total_gate_warp_m = 0.0
    if resolved_route_mode == "warp":
        steps, total_jumps, total_gate_warp_m = _warp_weighted_steps(origin_system_id, destination_system_id, db, use_ansiblex=use_ansiblex)
        if steps:
            items, total_jumps, total_gate_warp_m = _steps_to_items(steps, destination_system_id)
        else:
            items = []
    else:
        steps = _graph_steps(origin_system_id, destination_system_id, db, use_ansiblex=use_ansiblex)
        if steps:
            items, total_jumps, total_gate_warp_m = _steps_to_items(steps, destination_system_id)
        else:
            items, total_jumps = _fallback_esi_leg(origin_system_id, destination_system_id)
            total_gate_warp_m = 0.0

    _best_route_cache[cache_key] = (
        {"items": list(items), "jumps": int(total_jumps), "gate_warp_m": float(total_gate_warp_m)},
        time.time(),
    )
    return items, total_jumps, total_gate_warp_m


def _route_score(
    origin_system_id: int,
    destination_system_id: int,
    db: Session,
    use_ansiblex: bool = True,
    route_mode: str = "jumps",
) -> tuple[int, float]:
    if origin_system_id == destination_system_id:
        return (0, 0.0)
    items, total_jumps, total_gate_warp_m = _best_leg(
        origin_system_id,
        destination_system_id,
        db,
        use_ansiblex=use_ansiblex,
        route_mode=route_mode,
    )
    if not items:
        return (10**9, float("inf"))
    return (int(total_jumps), float(total_gate_warp_m))


def _optimize_planet_route(
    planets: list[dict],
    entry_point: tuple[float, float, float] | None,
    exit_point: tuple[float, float, float] | None,
    system_name: str,
) -> tuple[list[dict], float]:
    if len(planets) <= 1:
        return list(planets), 0.0

    def path_cost(sequence: list[dict]) -> float:
        total = 0.0
        current_point = entry_point
        if current_point is not None:
            total += _distance3(current_point, sequence[0]["coords"])
        for idx in range(1, len(sequence)):
            total += _distance3(sequence[idx - 1]["coords"], sequence[idx]["coords"])
        if exit_point is not None:
            total += _distance3(sequence[-1]["coords"], exit_point)
        return total

    if len(planets) <= 7:
        best_order = list(planets)
        best_cost = float("inf")
        for perm in itertools.permutations(planets):
            perm_list = list(perm)
            cost = path_cost(perm_list)
            if cost < best_cost:
                best_cost = cost
                best_order = perm_list
        return best_order, best_cost

    remaining = list(planets)
    ordered: list[dict] = []
    current_point = entry_point
    while remaining:
        if current_point is None:
            next_planet = min(remaining, key=lambda item: _planet_sort_tuple(item["planet_name"], system_name))
        else:
            next_planet = min(remaining, key=lambda item: _distance3(current_point, item["coords"]))
        ordered.append(next_planet)
        remaining.remove(next_planet)
        current_point = next_planet["coords"]
    return ordered, path_cost(ordered)


def _build_system_stop_map(hauling_colonies: list[dict], db: Session) -> dict[int, dict]:
    grouped: dict[int, dict] = {}
    system_ids: set[int] = set()
    for colony in hauling_colonies:
        system_id = int(colony.get("solar_system_id") or 0)
        if not system_id:
            continue
        planet_name = str(colony.get("planet_name") or "").strip()
        if not planet_name:
            continue
        system_ids.add(system_id)
        group = grouped.setdefault(system_id, {
            "system_name": colony.get("solar_system_name") or _system_name(system_id),
            "planets": [],
        })
        if planet_name not in group["planets"]:
            group["planets"].append(planet_name)

    if not system_ids:
        return grouped

    planet_rows = (
        db.query(StaticPlanet)
        .filter(StaticPlanet.system_id.in_(list(system_ids)))
        .all()
    )
    planet_map = {
        (int(row.system_id), str(row.planet_name or "").strip()): {
            "planet_id": int(row.planet_id),
            "planet_name": str(row.planet_name or ""),
            "planet_number": str(row.planet_number or ""),
            "coords": (
                float(row.x or 0.0),
                float(row.y or 0.0),
                float(row.z or 0.0),
            ) if row.x is not None and row.y is not None and row.z is not None else None,
        }
        for row in planet_rows
    }

    for system_id, group in grouped.items():
        system_name = str(group.get("system_name") or _system_name(system_id))
        ordered_names = sorted(group["planets"], key=lambda name: _planet_sort_tuple(name, system_name))
        planet_entries = []
        for planet_name in ordered_names:
            static_row = planet_map.get((int(system_id), planet_name))
            planet_entries.append({
                "planet_name": planet_name,
                "coords": static_row.get("coords") if static_row else None,
            })
        group["planets"] = ordered_names
        group["planet_entries"] = planet_entries
        group["planet_count"] = len(ordered_names)
        group["planet_route_label"] = " -> ".join(ordered_names)
        group["planet_route_distance_m"] = 0.0
        group["planet_route_distance_au"] = 0.0
        group["planet_route_distance_label"] = ""
    return grouped


def _apply_system_stop_map(route_items: list[dict], system_stop_map: dict[int, dict]) -> list[dict]:
    annotated: list[dict] = []
    gate_distance_map = sde.get_system_gate_distances()
    gate_lookup = sde.get_static_stargates()
    for index, item in enumerate(route_items):
        enriched = dict(item)
        stop = system_stop_map.get(int(item.get("system_id") or 0), {})
        planets = list(stop.get("planets") or [])
        enriched["system_planets"] = planets
        enriched["system_planet_count"] = int(stop.get("planet_count") or len(planets))
        enriched["system_planet_route_label"] = str(stop.get("planet_route_label") or "")
        enriched["system_planet_route_distance_m"] = float(stop.get("planet_route_distance_m") or 0.0)
        enriched["system_planet_route_distance_au"] = float(stop.get("planet_route_distance_au") or 0.0)
        enriched["system_planet_route_distance_label"] = str(stop.get("planet_route_distance_label") or "")
        planet_entries = list(stop.get("planet_entries") or [])
        coords_ready = planets and all(entry.get("coords") is not None for entry in planet_entries)
        if coords_ready:
            previous_system_id = int(route_items[index - 1]["system_id"]) if index > 0 else 0
            next_system_id = int(route_items[index + 1]["system_id"]) if index + 1 < len(route_items) else 0
            entry_gate = None
            exit_gate = None
            if previous_system_id and not item.get("bridge_incoming"):
                entry_gate = gate_distance_map.get((int(item["system_id"]), previous_system_id, next_system_id or previous_system_id))
                if not entry_gate:
                    candidates = [
                        payload for key, payload in gate_distance_map.items()
                        if int(key[0]) == int(item["system_id"]) and int(key[1]) == previous_system_id
                    ]
                    entry_gate = candidates[0] if candidates else None
            if next_system_id and not item.get("bridge_outgoing"):
                exit_gate = gate_distance_map.get((int(item["system_id"]), previous_system_id or next_system_id, next_system_id))
                if not exit_gate:
                    candidates = [
                        payload for key, payload in gate_distance_map.items()
                        if int(key[0]) == int(item["system_id"]) and int(key[2]) == next_system_id
                    ]
                    exit_gate = candidates[0] if candidates else None
            entry_point = None
            exit_point = None
            if entry_gate:
                gate_row = gate_lookup.get(int(entry_gate.get("entry_gate_id") or 0))
                if gate_row:
                    entry_point = (float(gate_row["x"]), float(gate_row["y"]), float(gate_row["z"]))
            if exit_gate:
                gate_row = gate_lookup.get(int(exit_gate.get("exit_gate_id") or 0))
                if gate_row:
                    exit_point = (float(gate_row["x"]), float(gate_row["y"]), float(gate_row["z"]))
            optimized_entries, total_distance_m = _optimize_planet_route(
                planet_entries,
                entry_point,
                exit_point,
                str(stop.get("system_name") or item.get("system_name") or ""),
            )
            optimized_names = [entry["planet_name"] for entry in optimized_entries]
            enriched["system_planets"] = optimized_names
            enriched["system_planet_route_label"] = " -> ".join(optimized_names)
            enriched["system_planet_route_distance_m"] = total_distance_m
            enriched["system_planet_route_distance_au"] = total_distance_m / _AU_IN_METERS
            enriched["system_planet_route_distance_label"] = _format_gate_distance_au(total_distance_m / _AU_IN_METERS)
        annotated.append(enriched)
    return annotated


def _build_route(
    origin_system_id: int,
    system_ids: list[int],
    db: Session,
    use_ansiblex: bool = True,
    return_to_origin: bool = False,
    route_mode: str = "jumps",
) -> tuple[list[dict], int, float]:
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
        "is_return": False,
        "gate_warp_distance_m": 0.0,
        "gate_warp_distance_au": 0.0,
        "gate_warp_distance_label": "",
    }]
    total_jumps = 0
    total_gate_warp_m = 0.0
    current = int(origin_system_id)
    while remaining:
        next_id = min(
            remaining,
            key=lambda candidate: _route_score(current, candidate, db, use_ansiblex=use_ansiblex, route_mode=route_mode),
        )
        leg_items, jumps, gate_warp_m = _best_leg(current, next_id, db, use_ansiblex=use_ansiblex, route_mode=route_mode)
        if not leg_items:
            break
        if leg_items[0].get("bridge_incoming"):
            ordered[-1]["bridge_outgoing"] = True
            ordered[-1]["bridge_label"] = leg_items[0].get("bridge_label")
        total_jumps += jumps
        total_gate_warp_m += gate_warp_m
        ordered.extend(leg_items)
        remaining.remove(next_id)
        current = next_id
    if return_to_origin and len(ordered) > 1 and current != int(origin_system_id):
        leg_items, jumps, gate_warp_m = _best_leg(
            current,
            int(origin_system_id),
            db,
            use_ansiblex=use_ansiblex,
            route_mode=route_mode,
        )
        if leg_items and leg_items[0].get("bridge_incoming"):
            ordered[-1]["bridge_outgoing"] = True
            ordered[-1]["bridge_label"] = leg_items[0].get("bridge_label")
        total_jumps += jumps
        total_gate_warp_m += gate_warp_m
        if leg_items:
            leg_items[-1]["is_return"] = True
        ordered.extend(leg_items)
    return ordered, total_jumps, total_gate_warp_m


def _resolve_selected_character(account, db: Session, character_id: int | None):
    characters = db.query(Character).filter(Character.account_id == account.id).all()
    selected_character = None
    if character_id:
        if character_id == -1:
            selected_character = None
        else:
            selected_character = next((char for char in characters if char.id == character_id), None)
    return characters, selected_character


def _load_hauling_colonies(account, db: Session, selected_character=None) -> list[dict]:
    cached = _load_colony_cache(account.id, db) or {}
    colonies = list(cached.get("colonies") or [])
    cache_meta = dict(cached.get("meta") or {})
    if colonies:
        _recompute_expiry(colonies)
        colonies, _ = _apply_price_mode(colonies, cache_meta, getattr(account, "price_mode", "sell"))

    hauling_colonies: list[dict] = []
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
    return hauling_colonies


@router.get("", response_class=HTMLResponse)
def hauling_page(
    request: Request,
    character_id: int | None = Query(default=None),
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    characters, selected_character = _resolve_selected_character(account, db, character_id)
    hauling_colonies = _load_hauling_colonies(account, db, selected_character)
    hauling_pref = db.get(HaulingPreference, int(account.id))
    return_to_origin = bool(hauling_pref.return_to_start) if hauling_pref else False
    route_mode = _route_mode_value(getattr(hauling_pref, "route_mode", "jumps"))
    system_stop_map = _build_system_stop_map(hauling_colonies, db)

    location = _get_cached_location(selected_character, db) if selected_character else None
    can_view_bridges = _is_corp_member(account, db)
    route_ordered: list[dict] = []
    route_total_jumps = 0
    route_total_gate_warp_m = 0.0
    ansiblex_status = _ansiblex.status(ensure_loaded=True)
    if location and hauling_colonies:
        try:
            route_ordered, route_total_jumps, route_total_gate_warp_m = _build_route(
                int(location.get("solar_system_id") or 0),
                [int(colony.get("solar_system_id") or 0) for colony in hauling_colonies if colony.get("solar_system_id")],
                db,
                use_ansiblex=True,
                return_to_origin=return_to_origin,
                route_mode=route_mode,
            )
            route_ordered = _apply_system_stop_map(route_ordered, system_stop_map)
            ansiblex_status = _ansiblex.status()
        except Exception:
            logger.exception("hauling: failed to build initial route")
            route_ordered = []
            route_total_jumps = 0
            route_total_gate_warp_m = 0.0
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
        "return_to_origin": return_to_origin,
        "route_mode": route_mode,
        "location": location,
        "location_name": location_name,
        "hauling_colonies": hauling_colonies,
        "route_ordered": route_ordered,
        "route_total_jumps": route_total_jumps,
        "route_total_gate_warp_au": route_total_gate_warp_m / _AU_IN_METERS,
        "route_total_gate_warp_label": _format_gate_distance_au(route_total_gate_warp_m / _AU_IN_METERS),
        "ansiblex_status": ansiblex_status,
        "dotlan_route_link": dotlan_route_link,
        "hauling_total_value": sum(float(colony.get("storage_value") or 0.0) for colony in hauling_colonies),
        "can_view_bridges": can_view_bridges,
        "can_manage_bridges": bool(account.is_admin or account.is_owner),
        "can_manage_all_bridges": bool(account.is_owner),
    })


@router.post("/api/preferences")
def save_hauling_preferences(
    payload: dict = Body(...),
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    pref = db.get(HaulingPreference, int(account.id))
    if pref is None:
        pref = HaulingPreference(account_id=int(account.id))
        db.add(pref)
    pref.return_to_start = bool(payload.get("return_to_start", False))
    pref.route_mode = _route_mode_value(payload.get("route_mode", getattr(pref, "route_mode", "jumps")))
    db.commit()
    return JSONResponse({
        "ok": True,
        "return_to_start": bool(pref.return_to_start),
        "route_mode": pref.route_mode,
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
    if not _is_corp_member(account, db):
        raise HTTPException(status_code=403, detail=translate("hauling.error_bridge_permission", default="Only corporation members can view bridge connections"))
    manual = [_serialize_manual_bridge(entry, account, db) for entry in _manual_bridge_entries(db)]
    return JSONResponse({
        "manual": manual,
        "manageable_corporations": _manageable_corporations(account, db),
        "exportable_corporations": _exportable_corporations(account, db),
        "can_manage": bool(account.is_admin or account.is_owner),
        "can_manage_all": bool(account.is_owner),
    })


@router.get("/api/bridge-connections/export-smt")
def export_bridge_connections_smt(
    corporation_id: int = Query(...),
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    corporation_id = int(corporation_id or 0)
    if not corporation_id:
        raise HTTPException(status_code=400, detail=translate("hauling.error_missing_corporation", default="Corporation is required"))
    if not _can_export_bridge(account, corporation_id, db):
        raise HTTPException(status_code=403, detail=translate("hauling.error_export_corporation_scope", default="Export is only allowed for your own corporation"))

    corporation_name = _resolve_corporation_name(corporation_id, db)
    entries = (
        db.query(CorpBridgeConnection)
        .filter(CorpBridgeConnection.corporation_id == corporation_id)
        .order_by(CorpBridgeConnection.from_system_name.asc(), CorpBridgeConnection.to_system_name.asc())
        .all()
    )

    lines: list[str] = []
    for entry in entries:
        from_name = str(entry.from_system_name or "").strip()
        to_name = str(entry.to_system_name or "").strip()
        if not from_name or not to_name:
            continue
        # SMT clipboard import accepts "0" when the structure ID is unknown.
        lines.append(f"0 {from_name} \u2192 {to_name}")
        lines.append(f"0 {to_name} \u2192 {from_name}")

    filename_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", corporation_name).strip("_") or f"corp_{corporation_id}"
    body = "\n".join(lines) + ("\n" if lines else "")
    return StreamingResponse(
        io.BytesIO(body.encode("utf-8")),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename_slug}_bridges_smt.txt"'},
    )


@router.post("/api/bridge-connections")
def save_bridge_connection(
    payload: dict = Body(...),
    account=Depends(require_manager_or_admin),
    db: Session = Depends(get_db),
):
    corporation_id = int(payload.get("corporation_id") or 0)
    if not corporation_id:
        raise HTTPException(status_code=400, detail=translate("hauling.error_missing_corporation", default="Corporation is required"))
    if not _can_manage_bridge(account, corporation_id, db):
        raise HTTPException(status_code=403, detail=translate("hauling.error_manage_corporation_scope", default="Only your own corporation is allowed"))

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
            raise HTTPException(status_code=404, detail=translate("hauling.error_bridge_not_found", default="Bridge connection not found"))
        if not _can_manage_bridge(account, int(entry.corporation_id), db):
            raise HTTPException(status_code=403, detail=translate("hauling.error_bridge_permission", default="No permission for this connection"))
        if existing and int(existing.id) != int(entry.id):
            raise HTTPException(status_code=409, detail=translate("hauling.error_bridge_exists", default="This connection already exists"))
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
            raise HTTPException(status_code=409, detail=translate("hauling.error_bridge_exists", default="This connection already exists"))
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
        raise HTTPException(status_code=400, detail=translate("hauling.error_missing_corporation", default="Corporation is required"))
    if not _can_manage_bridge(account, corporation_id, db):
        raise HTTPException(status_code=403, detail=translate("hauling.error_manage_corporation_scope", default="Only your own corporation is allowed"))

    dotlan_url = str(payload.get("url") or "").strip()
    if not dotlan_url:
        raise HTTPException(status_code=400, detail=translate("hauling.error_missing_dotlan_link", default="Dotlan link is required"))

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
        raise HTTPException(status_code=404, detail=translate("hauling.error_bridge_not_found", default="Bridge connection not found"))
    if not _can_manage_bridge(account, int(entry.corporation_id), db):
        raise HTTPException(status_code=403, detail=translate("hauling.error_bridge_permission", default="No permission for this connection"))
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
    return_to_origin = bool(payload.get("return_to_origin", False))
    route_mode = _route_mode_value(payload.get("route_mode", "jumps"))
    character_id = int(payload.get("character_id") or 0) if payload.get("character_id") is not None else None
    if not origin_system_id or not system_ids:
        return JSONResponse({"ordered": [], "total_jumps": 0})
    try:
        _, selected_character = _resolve_selected_character(account, db, character_id)
        selected_character = selected_character if character_id not in (None, -1) else None
        hauling_colonies = _load_hauling_colonies(account, db, selected_character)
        system_stop_map = _build_system_stop_map(hauling_colonies, db)
        ordered, total_jumps, total_gate_warp_m = _build_route(
            origin_system_id,
            system_ids,
            db,
            use_ansiblex=use_ansiblex,
            return_to_origin=return_to_origin,
            route_mode=route_mode,
        )
        ordered = _apply_system_stop_map(ordered, system_stop_map)
    except Exception:
        logger.exception("hauling: route rebuild failed")
        return JSONResponse({"ordered": [], "total_jumps": 0, "ansiblex_status": _ansiblex.status()})
    dotlan_route_link = ""
    if ordered and len(ordered) > 1:
        names = [item["system_name"].replace(" ", "_") for item in ordered]
        dotlan_route_link = f"https://evemaps.dotlan.net/route/{':'.join(names)}"
    return JSONResponse({
        "ordered": ordered,
        "total_jumps": total_jumps,
        "total_gate_warp_au": total_gate_warp_m / _AU_IN_METERS,
        "total_gate_warp_label": _format_gate_distance_au(total_gate_warp_m / _AU_IN_METERS),
        "route_mode": route_mode,
        "ansiblex_status": _ansiblex.status(),
        "dotlan_route_link": dotlan_route_link,
    })
