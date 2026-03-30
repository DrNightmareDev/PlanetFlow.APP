import logging
import time

import requests

logger = logging.getLogger(__name__)

_ANSIBLEX_URL = "https://ansiblex.com/api/gates"
_HEADERS = {"User-Agent": "EVE-PI-Manager/1.0 github.com/DrNightmareDev/PI_Manager"}
_CACHE_TTL = 3600
_cache: dict[tuple[int, int], bool] = {}
_cache_loaded_at: float = 0.0


def _ensure_loaded() -> None:
    global _cache_loaded_at, _cache
    if time.time() - _cache_loaded_at < _CACHE_TTL:
        return
    try:
        resp = requests.get(_ANSIBLEX_URL, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        gates = resp.json() or []
        new_cache: dict[tuple[int, int], bool] = {}
        for gate in gates:
            if isinstance(gate, dict):
                from_id = gate.get("from") or gate.get("from_solar_system_id")
                to_id = gate.get("to") or gate.get("to_solar_system_id")
                if from_id and to_id:
                    new_cache[(int(from_id), int(to_id))] = True
                    new_cache[(int(to_id), int(from_id))] = True
        _cache = new_cache
        _cache_loaded_at = time.time()
        logger.info("ansiblex: loaded %d gate connections", len(_cache))
    except Exception:
        logger.warning("ansiblex: failed to load gates, skipping")


def has_bridge(from_system: int, to_system: int) -> bool:
    _ensure_loaded()
    return _cache.get((int(from_system), int(to_system)), False)


def bridge_jumps(from_system: int, to_system: int) -> int | None:
    return 1 if has_bridge(from_system, to_system) else None
