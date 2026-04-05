"""KillIntel service — fetch, cache and aggregate pilot kill data from zKillboard + ESI."""
from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Generator, Optional

from sqlalchemy.orm import Session

from app.esi import get_killmail, universe_names, universe_ids
from app import sde

logger = logging.getLogger(__name__)

ZKILL_BASE = "https://zkillboard.com/api"
HEADERS = {"User-Agent": "PlanetFlow/1.0 github.com/DrNightmareDev/PlanetFlow.APP", "Accept-Encoding": "gzip"}

# Cache tiers
TTL_FRESH   = timedelta(minutes=5)   # use DB as-is, zero zKill calls
TTL_PARTIAL = timedelta(hours=24)    # incremental refresh
# > 24h: drop all killmail/item data, run full fresh analysis

# EVE inventory flag → slot name
_FLAG_SLOT: dict[int, str] = {}
for _f in range(11, 19): _FLAG_SLOT[_f] = "low"
for _f in range(19, 27): _FLAG_SLOT[_f] = "mid"
for _f in range(27, 35): _FLAG_SLOT[_f] = "high"
for _f in range(92, 100): _FLAG_SLOT[_f] = "rig"
for _f in range(125, 133): _FLAG_SLOT[_f] = "sub"
_FLAG_SLOT[5] = "cargo"
_FLAG_SLOT[87] = "drone"
_FLAG_SLOT[158] = "drone"
_FLAG_SLOT[172] = "cargo"


def _fetch_json(url: str) -> object:
    import urllib.request
    import gzip as _gzip
    import json
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = _gzip.decompress(raw)
        return json.loads(raw)


def _zkill_stats(character_id: int) -> dict:
    try:
        return _fetch_json(f"{ZKILL_BASE}/stats/characterID/{character_id}/")  # type: ignore[return-value]
    except Exception as e:
        logger.warning("killintel: zkill stats failed for %d: %s", character_id, e)
        return {}


def _zkill_kills(character_id: int, start_time: Optional[datetime] = None) -> list[tuple[dict, bool]]:
    """Fetch up to 20 kills + 20 losses. start_time restricts via zKill's startTime filter."""
    result = []
    time_segment = ""
    if start_time is not None:
        # zKill startTime format: YYYYMMDDHHmm
        time_segment = f"startTime/{start_time.strftime('%Y%m%d%H%M')}/"
    for kind, is_loss in [("kills", False), ("losses", True)]:
        try:
            url = f"{ZKILL_BASE}/{kind}/characterID/{character_id}/{time_segment}page/1/"
            data = _fetch_json(url)
            if isinstance(data, list):
                result.extend([(k, is_loss) for k in data[:20]])
        except Exception as e:
            logger.warning("killintel: zkill %s failed for %d: %s", kind, character_id, e)
    return result


def _parse_timestamp(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _slot_for_flag(flag: int) -> str:
    return _FLAG_SLOT.get(flag, "other")


def _age(pilot, now: datetime) -> Optional[timedelta]:
    if pilot is None or pilot.fetched_at is None:
        return None
    fetched = pilot.fetched_at
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return now - fetched


# ── Name resolution ──────────────────────────────────────────────────────────

def _sde_type_name(type_id: int) -> Optional[str]:
    raw = sde.get_type_name(type_id)
    if isinstance(raw, dict):
        return raw.get("en") or next(iter(raw.values()), None)
    return raw  # scalar or None


def _resolve_type_names(type_ids: set[int]) -> dict[int, str]:
    result: dict[int, str] = {}
    missing = []
    for tid in type_ids:
        name = _sde_type_name(tid)
        if name:
            result[tid] = name
        else:
            missing.append(tid)
    if missing:
        for chunk_start in range(0, len(missing), 1000):
            chunk = missing[chunk_start:chunk_start + 1000]
            for item in universe_names(chunk):
                if item.get("id") and item.get("name"):
                    result[int(item["id"])] = item["name"]
    return result


def _resolve_corp_alliance_names(corp_ids: set[int], alliance_ids: set[int]) -> dict[int, str]:
    all_ids = list((corp_ids | alliance_ids) - {0})
    result: dict[int, str] = {}
    for chunk_start in range(0, len(all_ids), 1000):
        chunk = all_ids[chunk_start:chunk_start + 1000]
        for item in universe_names(chunk):
            if item.get("id") and item.get("name"):
                result[int(item["id"])] = item["name"]
    return result


# ── Kill data ingestion ──────────────────────────────────────────────────────

def _ingest_stubs(
    char_id: int,
    stubs: list[tuple[dict, bool]],
    db: Session,
    now: datetime,
    since: Optional[datetime] = None,
    cutoff: Optional[datetime] = None,
    hydrate_cap: int = 20,
) -> set[int]:
    from app.models import KillIntelKillmail, KillIntelItem

    cutoff_90d = now - timedelta(days=90)
    effective_cutoff = cutoff if cutoff is not None else cutoff_90d
    type_ids_to_resolve: set[int] = set()
    hydrated_count = 0

    existing_ids: set[int] = set()
    if since is not None:
        existing_ids = {
            km_id for (km_id,) in db.query(KillIntelKillmail.killmail_id)
            .filter(KillIntelKillmail.character_id == char_id)
            .all()
        }

    for stub, is_loss in stubs:
        km_id = int(stub.get("killmail_id", 0))
        if not km_id:
            continue
        if since is not None and km_id in existing_ids:
            continue

        zkb = stub.get("zkb", {})
        km_hash = zkb.get("hash", "")
        total_value = int(zkb.get("totalValue", 0) or 0)

        existing_km = db.get(KillIntelKillmail, km_id)

        if existing_km and existing_km.hydrated:
            if existing_km.killmail_time:
                if existing_km.killmail_time.tzinfo is None:
                    km_t = existing_km.killmail_time.replace(tzinfo=timezone.utc)
                else:
                    km_t = existing_km.killmail_time
                if km_t < effective_cutoff:
                    continue
            if existing_km.ship_type_id:
                type_ids_to_resolve.add(existing_km.ship_type_id)
            continue

        if not km_hash:
            continue

        try:
            time.sleep(0.1)
            esi_km = get_killmail(km_id, km_hash)
        except Exception as e:
            logger.debug("killintel: ESI km %d failed: %s", km_id, e)
            continue

        if not esi_km:
            continue

        km_time = _parse_timestamp(esi_km.get("killmail_time", ""))
        if km_time:
            if km_time.tzinfo is None:
                km_time = km_time.replace(tzinfo=timezone.utc)
            if km_time < effective_cutoff:
                continue

        victim = esi_km.get("victim", {})
        attackers = esi_km.get("attackers", [])

        if is_loss:
            ship_type_id = victim.get("ship_type_id")
        else:
            our_attacker = next((a for a in attackers if a.get("character_id") == char_id), None)
            ship_type_id = our_attacker.get("ship_type_id") if our_attacker else None

        if ship_type_id:
            type_ids_to_resolve.add(ship_type_id)

        if existing_km is None:
            existing_km = KillIntelKillmail(killmail_id=km_id)
            db.add(existing_km)

        existing_km.character_id = char_id
        existing_km.ship_type_id = ship_type_id
        existing_km.is_loss = is_loss
        existing_km.killmail_time = km_time
        existing_km.total_value = total_value
        existing_km.hydrated = True
        existing_km.fetched_at = now

        if is_loss and hydrated_count < hydrate_cap:
            hydrated_count += 1
            db.query(KillIntelItem).filter(KillIntelItem.killmail_id == km_id).delete()
            for itm in victim.get("items", []):
                flag = itm.get("flag", 0)
                slot = _slot_for_flag(flag)
                if slot == "other":
                    continue
                tid = itm.get("item_type_id")
                if not tid:
                    continue
                type_ids_to_resolve.add(tid)
                db.add(KillIntelItem(
                    killmail_id=km_id,
                    type_id=tid,
                    slot=slot,
                    quantity=int(itm.get("quantity_destroyed", 0) or itm.get("quantity_dropped", 0) or 1),
                ))

    return type_ids_to_resolve


def _patch_names(char_id: int, type_name_map: dict[int, str], db: Session) -> None:
    from app.models import KillIntelKillmail, KillIntelItem

    km_rows = db.query(KillIntelKillmail).filter(
        KillIntelKillmail.character_id == char_id,
        KillIntelKillmail.ship_type_id.isnot(None),
        KillIntelKillmail.ship_name.is_(None),
    ).all()

    itm_rows = db.query(KillIntelItem).filter(
        KillIntelItem.killmail_id.in_(
            db.query(KillIntelKillmail.killmail_id).filter(
                KillIntelKillmail.character_id == char_id
            )
        ),
        KillIntelItem.type_name.is_(None),
    ).all()

    extra_ids = (
        {km.ship_type_id for km in km_rows if km.ship_type_id not in type_name_map} |
        {itm.type_id for itm in itm_rows if itm.type_id not in type_name_map}
    )
    if extra_ids:
        type_name_map.update(_resolve_type_names(extra_ids))

    for km in km_rows:
        if km.ship_type_id in type_name_map:
            km.ship_name = type_name_map[km.ship_type_id]
    for itm in itm_rows:
        if itm.type_id in type_name_map:
            itm.type_name = type_name_map[itm.type_id]


# ── Aggregation ──────────────────────────────────────────────────────────────

def _aggregate_pilot(
    pilot,
    char_id: int,
    now: datetime,
    db: Session,
    cutoff: Optional[datetime] = None,
) -> dict:
    from app.models import KillIntelKillmail, KillIntelItem

    effective_cutoff = cutoff if cutoff is not None else (now - timedelta(days=90))

    kms = (
        db.query(KillIntelKillmail)
        .filter(
            KillIntelKillmail.character_id == char_id,
            KillIntelKillmail.killmail_time >= effective_cutoff,
        )
        .all()
    )

    times = [km.killmail_time for km in kms if km.killmail_time]
    last_activity = max(times).isoformat() if times else None

    ship_counter: Counter = Counter()
    for km in kms:
        if km.ship_type_id and km.ship_type_id not in (670, 33328):
            ship_counter[(km.ship_type_id, km.ship_name or f"#{km.ship_type_id}")] += 1

    total_appearances = sum(ship_counter.values()) or 1

    top_ships = []
    for (ship_type_id, ship_name), count in ship_counter.most_common(3):
        usage_pct = round(count / total_appearances * 100)

        loss_km_ids = [
            km.killmail_id for km in kms
            if km.ship_type_id == ship_type_id and km.is_loss and km.hydrated
        ]
        module_slot_counter: dict[tuple[int, str, str], int] = defaultdict(int)
        loss_count = len(loss_km_ids) or 1

        if loss_km_ids:
            items = (
                db.query(KillIntelItem)
                .filter(
                    KillIntelItem.killmail_id.in_(loss_km_ids),
                    KillIntelItem.slot.in_(["low", "mid", "high", "rig", "sub"]),
                )
                .all()
            )
            for itm in items:
                key = (itm.type_id, itm.type_name or f"#{itm.type_id}", itm.slot)
                module_slot_counter[key] += 1

        typical_modules = []
        for (tid, tname, slot), freq_count in sorted(
            module_slot_counter.items(), key=lambda x: -x[1]
        )[:12]:
            freq = round(freq_count / loss_count, 2)
            if freq < 0.1:
                continue
            typical_modules.append({"type_id": tid, "name": tname, "slot": slot, "frequency": freq})

        slot_order = {"high": 0, "mid": 1, "low": 2, "rig": 3, "sub": 4}
        typical_modules.sort(key=lambda m: (slot_order.get(m["slot"], 9), -m["frequency"]))

        top_ships.append({
            "ship_type_id": ship_type_id,
            "ship_name": ship_name,
            "usage_percent": usage_pct,
            "appearances": count,
            "typical_modules": typical_modules[:10],
        })

    danger_ratio = pilot.danger_ratio or 0
    dangerous_score = round(danger_ratio / 20, 1)

    fetched_at = pilot.fetched_at
    if fetched_at and fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)

    return {
        "character_id": char_id,
        "name": pilot.name,
        "corporation": pilot.corporation_name or "—",
        "alliance": pilot.alliance_name or "—",
        "dangerous_score": dangerous_score,
        "danger_ratio": danger_ratio,
        "ships_destroyed": pilot.ships_destroyed or 0,
        "ships_lost": pilot.ships_lost or 0,
        "isk_destroyed": pilot.isk_destroyed or 0,
        "isk_lost": pilot.isk_lost or 0,
        "zkill_url": f"https://zkillboard.com/character/{char_id}/",
        "last_activity": last_activity,
        "top_ships": top_ships,
        "cached_at": fetched_at.isoformat() if fetched_at else None,
    }


# ── Cache check ──────────────────────────────────────────────────────────────

def check_names_in_cache(names: list[str], db: Session) -> dict[str, str]:
    """
    Returns {name: status} where status is one of:
      "fresh"  — in DB, fetched_at < 5 min ago  → green  (instant from cache)
      "stale"  — in DB, fetched_at >= 5 min ago → orange (partial refresh needed)
      "none"   — not in DB                       → red    (full fetch needed)
    """
    from app.models import KillIntelPilot

    names = [n.strip() for n in names if n.strip()]
    if not names:
        return {}

    char_map: dict[str, int] = {}
    for chunk_start in range(0, len(names), 1000):
        chunk = names[chunk_start:chunk_start + 1000]
        result = universe_ids(chunk)
        for item in result.get("characters", []):
            if item.get("id") and item.get("name"):
                char_map[item["name"]] = int(item["id"])

    now = datetime.now(timezone.utc)
    out: dict[str, str] = {}
    for name in names:
        char_id = char_map.get(name)
        if not char_id:
            out[name] = "none"
            continue
        pilot = db.get(KillIntelPilot, char_id)
        if pilot is None:
            out[name] = "none"
        else:
            age = _age(pilot, now)
            out[name] = "fresh" if (age is not None and age < TTL_FRESH) else "stale"
    return out


# ── Streaming entry point ────────────────────────────────────────────────────

def stream_pilots(
    names: list[str],
    db: Session,
    use_cache_only: bool = False,
    time_window_days: Optional[int] = None,
) -> Generator[dict, None, None]:
    """
    Three-phase streaming:

    Phase 0 — cache hits: yield full cards immediately (no network).
    Phase 1 — ALL non-fresh pilots: fetch stats IN PARALLEL (ThreadPoolExecutor).
              As each stat completes, yield a partial card immediately.
              Cards appear ~1–2s after request starts, sorted live by danger score.
    Phase 2 — sequential kill stub fetch + ESI hydration (rate-limited 1 req/s).
              Each pilot's card updates in-place with ship data as it arrives.
    """
    from app.models import KillIntelPilot, KillIntelKillmail, KillIntelItem

    names = [n.strip() for n in names if n.strip()]
    if not names:
        return

    # Bulk name → ID resolution (single ESI call for all names at once)
    char_map: dict[str, int] = {}
    for chunk_start in range(0, len(names), 1000):
        chunk = names[chunk_start:chunk_start + 1000]
        result = universe_ids(chunk)
        for item in result.get("characters", []):
            if item.get("id") and item.get("name"):
                char_map[item["name"]] = int(item["id"])

    now = datetime.now(timezone.utc)
    cutoff: Optional[datetime] = (
        now - timedelta(days=time_window_days) if time_window_days else None
    )
    zkill_start_time: Optional[datetime] = cutoff

    # Classify pilots by cache state
    fresh_names: list[tuple[str, int]] = []       # TTL < 5 min → serve from DB immediately
    needs_fetch: list[tuple[str, int]] = []        # needs zKill stats call
    not_found: list[str] = []

    for name in names:
        char_id = char_map.get(name)
        if not char_id:
            not_found.append(name)
            continue
        pilot = db.get(KillIntelPilot, char_id)
        age = _age(pilot, now)
        if not use_cache_only and (age is None or age >= TTL_FRESH):
            needs_fetch.append((name, char_id))
        else:
            fresh_names.append((name, char_id))

    # ── Phase 0: yield errors and fresh cache hits immediately ────────────────
    for name in not_found:
        yield {"name": name, "error": "Character not found"}

    for name, char_id in fresh_names:
        pilot = db.get(KillIntelPilot, char_id)
        if pilot is None:
            yield {"name": name, "error": "Not in cache — run a live analysis first"}
            continue
        _patch_names(char_id, {}, db)
        db.commit()
        yield _aggregate_pilot(pilot, char_id, now, db, cutoff=cutoff)

    if not needs_fetch:
        return

    # ── Phase 1: parallel stats fetch for all stale/new pilots ───────────────
    # Each thread fetches stats + resolves corp/alliance names for one pilot.
    # We cap at 10 workers — zKill can handle parallel reads; the 1 req/s limit
    # is per-endpoint on the SAME character, not across different characters.
    def fetch_stats_for(name: str, char_id: int) -> dict:
        """Returns a dict with stats + metadata for one pilot. Thread-safe (read-only)."""
        try:
            stats = _zkill_stats(char_id)
            info = stats.get("info") or {}
            corp_id = int(info.get("corporationID") or info.get("corporation_id") or 0) or None
            alliance_id = int(info.get("allianceID") or info.get("alliance_id") or 0) or None
            names_map = _resolve_corp_alliance_names(
                {corp_id} if corp_id else set(), {alliance_id} if alliance_id else set()
            )
            return {
                "name": name,
                "char_id": char_id,
                "stats": stats,
                "info": info,
                "corp_id": corp_id,
                "alliance_id": alliance_id,
                "corp_name": names_map.get(corp_id) if corp_id else None,
                "alliance_name": names_map.get(alliance_id) if alliance_id else None,
                "error": None,
            }
        except Exception as e:
            return {"name": name, "char_id": char_id, "error": str(e)}

    # Submit all stats fetches in parallel
    pilot_data: dict[int, dict] = {}  # char_id → fetched data, for Phase 2
    with ThreadPoolExecutor(max_workers=min(len(needs_fetch), 10)) as pool:
        futures = {
            pool.submit(fetch_stats_for, name, char_id): (name, char_id)
            for name, char_id in needs_fetch
        }
        for future in as_completed(futures):
            result = future.result()
            name = result["name"]
            char_id = result["char_id"]

            if result.get("error") and not result.get("stats"):
                yield {"name": name, "error": result["error"]}
                continue

            stats = result.get("stats") or {}
            info = result.get("info") or {}
            pilot = db.get(KillIntelPilot, char_id)
            age = _age(pilot, now)

            # Drop stale killmail data
            if age is None or age >= TTL_PARTIAL:
                if pilot is not None:
                    existing_km_ids = [
                        km_id for (km_id,) in db.query(KillIntelKillmail.killmail_id)
                        .filter(KillIntelKillmail.character_id == char_id).all()
                    ]
                    if existing_km_ids:
                        db.query(KillIntelItem).filter(
                            KillIntelItem.killmail_id.in_(existing_km_ids)
                        ).delete(synchronize_session=False)
                        db.query(KillIntelKillmail).filter(
                            KillIntelKillmail.character_id == char_id
                        ).delete(synchronize_session=False)

            if pilot is None:
                pilot = KillIntelPilot(character_id=char_id)
                db.add(pilot)

            corp_id = result.get("corp_id")
            alliance_id = result.get("alliance_id")
            old_partial = age is not None and age < TTL_PARTIAL

            pilot.name = info.get("name") or name
            pilot.corporation_id = corp_id
            pilot.corporation_name = result.get("corp_name") or (pilot.corporation_name if old_partial else None)
            pilot.alliance_id = alliance_id
            pilot.alliance_name = result.get("alliance_name") or (pilot.alliance_name if old_partial else None)
            pilot.danger_ratio = stats.get("dangerRatio") or (pilot.danger_ratio if old_partial else None)
            pilot.ships_destroyed = stats.get("shipsDestroyed") or pilot.ships_destroyed
            pilot.ships_lost = stats.get("shipsLost") or pilot.ships_lost
            isk_d = stats.get("iskDestroyed")
            isk_l = stats.get("iskLost")
            if isk_d: pilot.isk_destroyed = int(isk_d)
            if isk_l: pilot.isk_lost = int(isk_l)
            pilot.fetched_at = now
            db.flush()

            # Store for Phase 2
            pilot_data[char_id] = {"name": name, "age": age}

            # Yield Phase 1 partial card — appears as soon as stats complete
            danger_ratio = pilot.danger_ratio or 0
            yield {
                "character_id": char_id,
                "name": pilot.name,
                "corporation": pilot.corporation_name or "—",
                "alliance": pilot.alliance_name or "—",
                "dangerous_score": round(danger_ratio / 20, 1),
                "danger_ratio": danger_ratio,
                "ships_destroyed": pilot.ships_destroyed or 0,
                "ships_lost": pilot.ships_lost or 0,
                "isk_destroyed": pilot.isk_destroyed or 0,
                "isk_lost": pilot.isk_lost or 0,
                "zkill_url": f"https://zkillboard.com/character/{char_id}/",
                "last_activity": None,
                "top_ships": [],
                "cached_at": now.isoformat(),
                "partial": True,
            }

    db.commit()

    # ── Phase 2: sequential kill stub fetch + ESI hydration ──────────────────
    # Rate-limited: track elapsed since last zKill request
    last_zkill: float = 0.0

    def zkill_gap():
        nonlocal last_zkill
        elapsed = time.monotonic() - last_zkill
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        last_zkill = time.monotonic()

    for name, char_id in needs_fetch:
        if char_id not in pilot_data:
            continue  # stats failed, already yielded error
        pilot = db.get(KillIntelPilot, char_id)
        if pilot is None:
            continue
        age = pilot_data[char_id]["age"]
        try:
            zkill_gap()
            since = pilot.fetched_at if (age is not None and age < TTL_PARTIAL) else None
            stubs = _zkill_kills(char_id, start_time=zkill_start_time)
            type_ids = _ingest_stubs(char_id, stubs, db, now, since=since, cutoff=cutoff)
            type_name_map = _resolve_type_names(type_ids)
            _patch_names(char_id, type_name_map, db)
            db.commit()
            yield _aggregate_pilot(pilot, char_id, now, db, cutoff=cutoff)
        except Exception as e:
            logger.error("killintel: phase2 error for %s: %s", name, e, exc_info=True)
