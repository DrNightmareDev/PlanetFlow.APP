"""KillIntel service — fetch, cache and aggregate pilot kill data from zKillboard + ESI."""
from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
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

def check_names_in_cache(names: list[str], db: Session) -> dict[str, bool]:
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

    out: dict[str, bool] = {}
    for name in names:
        char_id = char_map.get(name)
        if not char_id:
            out[name] = False
            continue
        pilot = db.get(KillIntelPilot, char_id)
        out[name] = pilot is not None
    return out


# ── Single-pilot analysis (for streaming) ────────────────────────────────────

def analyze_one_pilot(
    name: str,
    char_id: int,
    db: Session,
    now: datetime,
    use_cache_only: bool = False,
    time_window_days: Optional[int] = None,
) -> dict:
    """Fetch and return the result dict for a single pilot. Called per-pilot in the stream."""
    from app.models import KillIntelPilot, KillIntelKillmail, KillIntelItem

    # Cutoff for aggregation window
    cutoff: Optional[datetime] = (
        now - timedelta(days=time_window_days) if time_window_days else None
    )
    # zKill start_time for fetching (same window)
    zkill_start_time: Optional[datetime] = cutoff

    pilot = db.get(KillIntelPilot, char_id)

    if use_cache_only:
        if pilot is None:
            return {"name": name, "error": "Not in cache — run a live analysis first"}
        _patch_names(char_id, {}, db)
        db.commit()
        return _aggregate_pilot(pilot, char_id, now, db, cutoff=cutoff)

    age = _age(pilot, now)

    if age is not None and age < TTL_FRESH:
        # Tier 1: fresh — use DB as-is
        logger.debug("killintel: FRESH %s age=%s", name, age)
        _patch_names(char_id, {}, db)
        db.commit()

    elif age is not None and age < TTL_PARTIAL:
        # Tier 2: partial — refresh stats + new stubs only
        logger.info("killintel: PARTIAL %s age=%s", name, age)
        time.sleep(1.0)
        stats = _zkill_stats(char_id)
        info = stats.get("info") or {}
        corp_id = int(info.get("corporationID") or info.get("corporation_id") or 0) or None
        alliance_id = int(info.get("allianceID") or info.get("alliance_id") or 0) or None
        names_map = _resolve_corp_alliance_names(
            {corp_id} if corp_id else set(), {alliance_id} if alliance_id else set()
        )
        pilot.name = info.get("name") or name
        pilot.corporation_id = corp_id
        pilot.corporation_name = names_map.get(corp_id) if corp_id else pilot.corporation_name
        pilot.alliance_id = alliance_id
        pilot.alliance_name = names_map.get(alliance_id) if alliance_id else pilot.alliance_name
        pilot.danger_ratio = stats.get("dangerRatio") or pilot.danger_ratio
        pilot.ships_destroyed = stats.get("shipsDestroyed") or pilot.ships_destroyed
        pilot.ships_lost = stats.get("shipsLost") or pilot.ships_lost
        isk_d = stats.get("iskDestroyed")
        isk_l = stats.get("iskLost")
        if isk_d: pilot.isk_destroyed = int(isk_d)
        if isk_l: pilot.isk_lost = int(isk_l)
        pilot.fetched_at = now

        time.sleep(1.0)
        stubs = _zkill_kills(char_id, start_time=zkill_start_time)
        type_ids = _ingest_stubs(char_id, stubs, db, now, since=pilot.fetched_at, cutoff=cutoff)
        type_name_map = _resolve_type_names(type_ids)
        _patch_names(char_id, type_name_map, db)
        db.commit()

    else:
        # Tier 3: stale or new — full refresh
        logger.info("killintel: FULL %s age=%s", name, age)
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

        time.sleep(1.0)
        stats = _zkill_stats(char_id)
        info = stats.get("info") or {}
        corp_id = int(info.get("corporationID") or info.get("corporation_id") or 0) or None
        alliance_id = int(info.get("allianceID") or info.get("alliance_id") or 0) or None
        names_map = _resolve_corp_alliance_names(
            {corp_id} if corp_id else set(), {alliance_id} if alliance_id else set()
        )

        if pilot is None:
            pilot = KillIntelPilot(character_id=char_id)
            db.add(pilot)

        pilot.name = info.get("name") or name
        pilot.corporation_id = corp_id
        pilot.corporation_name = names_map.get(corp_id) if corp_id else None
        pilot.alliance_id = alliance_id
        pilot.alliance_name = names_map.get(alliance_id) if alliance_id else None
        pilot.danger_ratio = stats.get("dangerRatio")
        pilot.ships_destroyed = stats.get("shipsDestroyed")
        pilot.ships_lost = stats.get("shipsLost")
        isk_d = stats.get("iskDestroyed")
        isk_l = stats.get("iskLost")
        pilot.isk_destroyed = int(isk_d) if isk_d else None
        pilot.isk_lost = int(isk_l) if isk_l else None
        pilot.fetched_at = now

        time.sleep(1.0)
        stubs = _zkill_kills(char_id, start_time=zkill_start_time)
        type_ids = _ingest_stubs(char_id, stubs, db, now, since=None, cutoff=cutoff)
        type_name_map = _resolve_type_names(type_ids)
        _patch_names(char_id, type_name_map, db)
        db.commit()

    return _aggregate_pilot(pilot, char_id, now, db, cutoff=cutoff)


# ── Streaming entry point ────────────────────────────────────────────────────

def stream_pilots(
    names: list[str],
    db: Session,
    use_cache_only: bool = False,
    time_window_days: Optional[int] = None,
) -> Generator[dict, None, None]:
    """
    Yields one result dict per pilot as it completes.
    Frontend can render each card immediately without waiting for the full list.
    """
    names = [n.strip() for n in names if n.strip()]
    if not names:
        return

    # Bulk name → ID resolution up front (single ESI call for all names)
    char_map: dict[str, int] = {}
    for chunk_start in range(0, len(names), 1000):
        chunk = names[chunk_start:chunk_start + 1000]
        result = universe_ids(chunk)
        for item in result.get("characters", []):
            if item.get("id") and item.get("name"):
                char_map[item["name"]] = int(item["id"])

    now = datetime.now(timezone.utc)

    for name in names:
        char_id = char_map.get(name)
        if not char_id:
            yield {"name": name, "error": "Character not found"}
            continue
        try:
            result = analyze_one_pilot(
                name, char_id, db, now,
                use_cache_only=use_cache_only,
                time_window_days=time_window_days,
            )
            yield result
        except Exception as e:
            logger.error("killintel: error analyzing %s: %s", name, e)
            yield {"name": name, "error": f"Analysis failed: {e}"}
