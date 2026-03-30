import logging
import time

import requests

logger = logging.getLogger(__name__)

_ANSIBLEX_URL = "https://ansiblex.com/api/gates"
_HEADERS = {"User-Agent": "EVE-PI-Manager/1.0 github.com/DrNightmareDev/PI_Manager"}
_CACHE_TTL = 3600
_cache: dict[tuple[int, int], bool] = {}
_gates: list[dict] = []
_cache_loaded_at: float = 0.0
_last_attempt_at: float = 0.0
_last_success_at: float = 0.0
_last_error: str | None = None


def _ensure_loaded() -> None:
    global _cache_loaded_at, _cache, _gates, _last_attempt_at, _last_success_at, _last_error
    if time.time() - _cache_loaded_at < _CACHE_TTL:
        return
    _last_attempt_at = time.time()
    try:
        resp = requests.get(_ANSIBLEX_URL, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        gates = resp.json() or []
        new_cache: dict[tuple[int, int], bool] = {}
        normalized_gates: list[dict] = []
        for gate in gates:
            if isinstance(gate, dict):
                from_id = gate.get("from") or gate.get("from_solar_system_id")
                to_id = gate.get("to") or gate.get("to_solar_system_id")
                if from_id and to_id:
                    from_int = int(from_id)
                    to_int = int(to_id)
                    new_cache[(int(from_id), int(to_id))] = True
                    new_cache[(int(to_id), int(from_id))] = True
                    normalized_gates.append({
                        "from": from_int,
                        "to": to_int,
                        "name": gate.get("name") or "",
                    })
        _cache = new_cache
        _gates = normalized_gates
        _cache_loaded_at = time.time()
        _last_success_at = _cache_loaded_at
        _last_error = None
        logger.info("ansiblex: loaded %d gate connections", len(_cache))
    except Exception as exc:
        _last_error = str(exc)
        logger.warning("ansiblex: failed to load gates, skipping")


def has_bridge(from_system: int, to_system: int) -> bool:
    _ensure_loaded()
    return _cache.get((int(from_system), int(to_system)), False)


def bridge_jumps(from_system: int, to_system: int) -> int | None:
    return 1 if has_bridge(from_system, to_system) else None


def bridges_touching_systems(system_ids: list[int]) -> list[dict]:
    _ensure_loaded()
    system_set = {int(system_id) for system_id in system_ids if system_id}
    if not system_set:
        return []
    return [
        gate for gate in _gates
        if int(gate.get("from") or 0) in system_set or int(gate.get("to") or 0) in system_set
    ]


def status(ensure_loaded: bool = False) -> dict:
    if ensure_loaded:
        _ensure_loaded()
    has_cache = bool(_cache)
    if _last_error and has_cache:
        state = "stale"
    elif _last_error:
        state = "down"
    elif _last_success_at:
        state = "up"
    else:
        state = "unknown"
    return {
        "state": state,
        "has_cache": has_cache,
        "last_attempt_at": _last_attempt_at,
        "last_success_at": _last_success_at,
        "last_error": _last_error,
        "gate_count": len(_cache),
    }
