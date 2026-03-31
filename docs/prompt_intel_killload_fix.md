# Combat Intel Map — Kill Loading Fix
**Optimized for Codex / GPT-4o — Implementation Prompt**
**Date:** 2026-03-31

---

## Context & Goal

You are working on **EVE PI Manager**, a FastAPI + Jinja2 web application for EVE Online planetary-industry management. A **Combat Intel Map** page (`/intel/map`) exists and renders correctly (SVG map, sidebar, kill feed panel), but **kills never load** — the map always shows "0 visible kills" regardless of region or time window.

Your task is to **diagnose and fix the kill-loading pipeline** end-to-end. All changes must stay consistent with the existing project style described below.

---

## Project Tech Stack

- **Backend:** Python 3.11, FastAPI, SQLAlchemy 2, PostgreSQL, Celery 5 + RabbitMQ
- **Frontend:** Jinja2 templates, Bootstrap 5.3 dark theme, vanilla JS (no bundler)
- **ESI client:** `app/esi.py` — synchronous `requests`, ETag caching, error-budget guard
- **zKillboard client:** `app/zkill.py` — synchronous `requests`, in-process dict cache

---

## Existing File Map (read these before touching anything)

| File | Purpose |
|---|---|
| `app/routers/intel.py` | `/intel/map` page + `/intel/map/live` JSON endpoint |
| `app/zkill.py` | zKillboard fetch helpers, `normalize_kill()`, `get_region_kills()` |
| `app/esi.py` | ESI HTTP helpers — `universe_names()`, `get_killmail()` (may not exist yet) |
| `app/sde.py` | Static data — `get_region_system_graph()`, `get_type_name()`, `get_system_local()` |
| `app/models.py` | `KillActivityCache` (system_id PK, kill_count, fetched_at) |
| `app/templates/intel_map.html` | Full map UI — SVG canvas, control panel, JS poll loop (20 s) |

---

## Current Kill-Fetch Flow (what is broken)

### Step 1 — `_fetch_region_kills()` in `app/routers/intel.py`

```python
def _fetch_region_kills(region_id: int, window: str) -> list[dict]:
    past_seconds = int(WINDOW_SECONDS.get(window, 3600))
    response = requests.get(
        f"https://zkillboard.com/api/kills/regionID/{region_id}/pastSeconds/{past_seconds}/limit/200/",
        headers=HEADERS,
        timeout=20,
    )
    payload = response.json()
    return payload if isinstance(payload, list) else []
```

### Step 2 — `normalize_kill()` in `app/zkill.py`

Reads `victim.ship_type_id`, `victim.character_id`, `zkb.totalValue`, `killmail_time` directly from the zKillboard response.

---

## Root Cause Analysis

### Problem 1 — zKillboard returns **stub objects**, not full killmails

The zKillboard public API (`/api/kills/regionID/…`) returns **lightweight stubs**:

```json
[
  {
    "killmail_id": 123456789,
    "zkb": {
      "locationID": 40000001,
      "hash": "abc123def456...",
      "fittedValue": 1500000.0,
      "totalValue": 3200000.0,
      "points": 4,
      "npc": false,
      "solo": false,
      "awox": false
    }
  }
]
```

**There is no `victim`, no `attackers`, no `solar_system_id`, no `killmail_time` in the stub.**
All of that lives in the full killmail on ESI.

### Problem 2 — Rate limiting / User-Agent rejection

zKillboard enforces:
- **Accept-Encoding: gzip** — must be sent (requests sends it by default, but verify)
- **User-Agent** — must be non-empty and identify your app (already set, keep it)
- **10 req/s hard cap** — sequential fetches of many systems will get 429s
- **Repeated identical requests** — zKillboard caches aggressively; the same endpoint called multiple times in <10 s returns `[]`

### Problem 3 — `solar_system_id` filter has no fallback

`_normalize_feed_entry()` drops kills where `system_info` is `None`. If SDE data is missing for a system, the kill is silently discarded.

---

## Required Fix: Two-Stage Fetch (zKillboard stub → ESI full killmail)

### Stage A — Fetch stubs from zKillboard

```
GET https://zkillboard.com/api/kills/regionID/{regionID}/pastSeconds/{seconds}/limit/200/
Headers:
  User-Agent: EVE-PI-Manager/1.0 github.com/DrNightmareDev/PI_Manager
  Accept-Encoding: gzip
```

This gives you a list of `{killmail_id, zkb: {hash, totalValue, ...}}`.

### Stage B — Resolve each stub via ESI killmail endpoint

```
GET https://esi.evetech.net/latest/killmails/{killmail_id}/{killmail_hash}/?datasource=tranquility
```

**ESI killmail response shape:**

```json
{
  "killmail_id": 123456789,
  "killmail_time": "2026-03-30T17:42:11Z",
  "solar_system_id": 30000142,
  "victim": {
    "character_id": 90000001,
    "corporation_id": 98000001,
    "alliance_id": 99000001,
    "ship_type_id": 33468,
    "damage_taken": 52000,
    "items": [],
    "position": {"x": 0, "y": 0, "z": 0}
  },
  "attackers": [
    {
      "character_id": 90000002,
      "corporation_id": 98000002,
      "ship_type_id": 17918,
      "weapon_type_id": 2881,
      "damage_done": 52000,
      "final_blow": true,
      "security_status": -1.5
    }
  ]
}
```

**Merge stub + ESI result:**

```python
full = {
    **esi_killmail,          # solar_system_id, killmail_time, victim, attackers
    "zkb": stub["zkb"],      # totalValue, hash, npc, solo, awox
}
```

---

## ESI Reference

### Killmail endpoint

```
GET /latest/killmails/{killmail_id}/{killmail_hash}/
```

- **No auth required** — public endpoint
- Returns 200 with full killmail JSON (shape above)
- Returns 422 if hash is wrong
- ETag cacheable — add `If-None-Match` / `304` handling in `app/esi.py`

### Universe names bulk resolve

```
POST /latest/universe/names/
Body: [integer, ...]   (max 1000 IDs per call)
```

Already implemented in `app/esi.py` as `universe_names(ids: list[int]) -> list[dict]`.

---

## Implementation Instructions

### 1. Add `get_killmail()` to `app/esi.py`

```python
def get_killmail(killmail_id: int, killmail_hash: str) -> dict:
    """Fetch a full killmail from ESI. No auth required."""
    url = f"https://esi.evetech.net/latest/killmails/{killmail_id}/{killmail_hash}/?datasource=tranquility"
    resp = requests.get(url, headers={"User-Agent": settings.user_agent}, timeout=15)
    resp.raise_for_status()
    return resp.json()
```

Follow the existing ETag pattern used for other ESI endpoints if the function for that is centralised.

### 2. Rewrite `get_region_kills()` in `app/zkill.py`

Replace the single-request fetch with a two-stage pipeline:

```python
def get_region_kills(region_id: int, window: str = "60m", limit: int = 200) -> list[dict]:
    # Stage A: fetch stubs
    stubs = _fetch_json(
        f"https://zkillboard.com/api/kills/regionID/{region_id}"
        f"/pastSeconds/{WINDOW_SECONDS[window]}/limit/{limit}/"
    )
    if not stubs:
        return []

    # Stage B: resolve each stub via ESI (thread pool, cap concurrency)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from app.esi import get_killmail

    results: list[dict] = []

    def _resolve(stub: dict) -> dict | None:
        km_id = int(stub.get("killmail_id") or 0)
        km_hash = str((stub.get("zkb") or {}).get("hash") or "")
        if not km_id or not km_hash:
            return None
        try:
            esi_km = get_killmail(km_id, km_hash)
            return {**esi_km, "zkb": stub.get("zkb") or {}}
        except Exception:
            logger.warning("zkill: ESI killmail fetch failed for %s", km_id)
            return None

    with ThreadPoolExecutor(max_workers=min(len(stubs), 10)) as ex:
        futures = {ex.submit(_resolve, stub): stub for stub in stubs}
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)

    # Sort descending by time
    results.sort(key=lambda k: k.get("killmail_time") or "", reverse=True)

    # Update region cache
    _REGION_CACHE[(int(region_id), WINDOW_SECONDS[window])] = (time.time(), results)
    return results
```

### 3. Update `normalize_kill()` in `app/zkill.py`

After the two-stage fetch, the `kill` dict now has the full ESI shape. Verify these field paths:

| Field | Path in merged dict |
|---|---|
| `killmail_id` | `kill["killmail_id"]` |
| `solar_system_id` | `kill["solar_system_id"]` |
| `killmail_time` | `kill["killmail_time"]` |
| `ship_type_id` | `kill["victim"]["ship_type_id"]` |
| `character_id` | `kill["victim"]["character_id"]` |
| `corporation_id` | `kill["victim"]["corporation_id"]` |
| `alliance_id` | `kill["victim"].get("alliance_id")` |
| `damage_taken` | `kill["victim"]["damage_taken"]` |
| `attacker_count` | `len(kill["attackers"])` |
| `total_value` | `kill["zkb"]["totalValue"]` |
| `is_npc` | `kill["zkb"].get("npc", False)` |
| `is_solo` | `kill["zkb"].get("solo", False)` |

Update `normalize_kill()` to read from these paths instead of assuming the old flat shape.

### 4. Fix `_normalize_feed_entry()` in `app/routers/intel.py`

Add fallback for unknown systems (allow kills from unknown/wormhole systems to pass through):

```python
if not killmail_id:
    return None
# Allow kills even if system not in local SDE graph
system_name = system_info["name"] if system_info else f"System {solar_system_id}"
```

### 5. Rate-limit guard in `app/zkill.py`

Add a simple per-endpoint cooldown so repeated calls to the same region endpoint don't get 429d:

```python
_LAST_REGION_FETCH: dict[int, float] = {}
ZKILL_MIN_INTERVAL = 12.0  # seconds between fetches of the same region

def get_region_kills(region_id: int, window: str = "60m", limit: int = 200) -> list[dict]:
    cache_key = (int(region_id), WINDOW_SECONDS[window])
    now = time.time()
    cached = _REGION_CACHE.get(cache_key)
    if cached and now - cached[0] <= _CACHE_TTL:
        return cached[1]
    last = _LAST_REGION_FETCH.get(region_id, 0)
    if now - last < ZKILL_MIN_INTERVAL:
        return cached[1] if cached else []
    _LAST_REGION_FETCH[region_id] = now
    # ... proceed with fetch
```

---

## zKillboard API Reference (from wiki)

Source: https://github.com/zKillboard/zKillboard/wiki/API-(Killmails)

### Endpoint pattern

```
https://zkillboard.com/api/{modifiers}/
```

### Modifiers (chain in any order before the trailing slash)

| Modifier | Example | Notes |
|---|---|---|
| `kills` | `/kills/` | Only kills (not losses from zkill's perspective — use for intel) |
| `losses` | `/losses/` | Only losses |
| `regionID/{id}` | `/regionID/10000010/` | Filter by region |
| `solarSystemID/{id}` | `/solarSystemID/30000142/` | Filter by system |
| `pastSeconds/{n}` | `/pastSeconds/3600/` | Max lookback |
| `limit/{n}` | `/limit/200/` | Max results (cap: 200) |
| `page/{n}` | `/page/2/` | Pagination (each page = 200 results) |

### Headers required

```
User-Agent: <your-app-name/version contact-url>
Accept-Encoding: gzip
```

### Response shape

```json
[
  {
    "killmail_id": 123456789,
    "zkb": {
      "locationID": 40000001,
      "hash": "abc123...",
      "fittedValue": 1500000.0,
      "droppedValue": 800000.0,
      "destroyedValue": 700000.0,
      "totalValue": 3200000.0,
      "points": 4,
      "npc": false,
      "solo": false,
      "awox": false,
      "labels": [],
      "href": "https://esi.evetech.net/latest/killmails/123456789/abc123.../"
    }
  }
]
```

**Note:** `zkb.href` contains the exact ESI killmail URL. Use it directly instead of constructing it from id + hash.

### Rate limits

- 10 requests/second global
- Same endpoint: minimum 10-second interval recommended
- 429 response = back off for 30 seconds
- `X-Rate-Limit-Remaining` response header (if present) — respect it

---

## Look & Feel Rules (do not change the UI)

1. All Python code uses `snake_case`; classes use `PascalCase`
2. No new npm packages, no TypeScript, no build step
3. JavaScript stays in `<script>` blocks inside the Jinja2 template
4. CSS classes follow the existing Bootstrap 5.3 dark theme (`bg-dark`, `text-secondary`, etc.)
5. Do not add new DB migrations — `KillActivityCache` and `CorpBridgeConnection` already exist
6. Do not add new router files — extend `app/zkill.py` and `app/routers/intel.py`
7. Logging via `logger = logging.getLogger(__name__)` — use `logger.warning` / `logger.exception`
8. Timeouts: ESI calls → 15 s, zKillboard calls → 20 s
9. Never raise unhandled exceptions from fetch helpers — catch, log, return empty/fallback
10. All new env vars go in `app/config.py` as `Field(default=..., alias="UPPER_SNAKE")`

---

## Acceptance Criteria

- [ ] `/intel/map/live?region=10000010&window=60m` returns `activity` list with non-zero `kill_count` for active systems
- [ ] `feed` list contains entries with `pilot_name`, `ship_type`, `isk_value`, `timestamp_utc` populated
- [ ] No 500 errors when zKillboard returns `[]` (empty region, quiet period)
- [ ] No 500 errors when ESI killmail fetch fails for individual kills (skip and log)
- [ ] Map nodes change colour (cold/warm/hot) based on kill count
- [ ] Kill feed rows show ship image, pilot name, system name, ISK value, time

---

## What NOT to do

- Do NOT fetch full killmails from zKillboard's website HTML — use ESI only
- Do NOT store resolved killmails in the database — in-process cache (`_REGION_CACHE`) is sufficient
- Do NOT add authentication to `/intel/map/live` — it already uses `require_owner`
- Do NOT change the template's JS poll loop or SVG layout logic — only the data shape matters
- Do NOT call `universe_names()` for every kill individually — batch all IDs in one call per region fetch
- Do NOT add `time.sleep()` in the fetch path — use the cooldown guard instead
