"""KillIntel service — fetch, cache and aggregate pilot kill data from zKillboard + ESI."""
from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.esi import get_killmail, universe_names, universe_ids
from app import sde

logger = logging.getLogger(__name__)

ZKILL_BASE = "https://zkillboard.com/api"
HEADERS = {"User-Agent": "PlanetFlow/1.0 github.com/DrNightmareDev/PlanetFlow.APP", "Accept-Encoding": "gzip"}

# Cache tiers
TTL_FRESH   = timedelta(minutes=5)   # use DB as-is, no zKill call
TTL_PARTIAL = timedelta(hours=24)    # fetch only new stubs since last fetch, append to DB
# > 24h: drop all killmail/item data, run full fresh analysis

# EVE inventory flag → slot name (only fitted slots we care about)
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


def _zkill_kills(character_id: int) -> list[tuple[dict, bool]]:
    """Fetch the 20 most recent kills and 20 most recent losses for a pilot."""
    result = []
    for kind, is_loss in [("kills", False), ("losses", True)]:
        try:
            data = _fetch_json(f"{ZKILL_BASE}/{kind}/characterID/{character_id}/page/1/")
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


# ── Name resolution ─────────────────────────────────────────────────────────

def _resolve_type_names(type_ids: set[int]) -> dict[int, str]:
    result: dict[int, str] = {}
    missing = []
    for tid in type_ids:
        # get_type_name returns a dict {lang: name} or None
        raw = sde.get_type_name(tid)
        if isinstance(raw, dict):
            name = raw.get("en") or next(iter(raw.values()), None)
        else:
            name = raw  # legacy scalar, or None
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
    hydrate_cap: int = 20,
) -> set[int]:
    """
    Process kill stubs into DB.  Returns set of type_ids that need name resolution.
    If `since` is set, skip stubs whose killmail_id is already in DB (incremental mode).
    """
    from app.models import KillIntelKillmail, KillIntelItem

    cutoff_90d = now - timedelta(days=90)
    type_ids_to_resolve: set[int] = set()
    hydrated_count = 0

    # Existing killmail IDs for this character (for dedup in incremental mode)
    if since is not None:
        existing_ids: set[int] = {
            km_id for (km_id,) in db.query(KillIntelKillmail.killmail_id)
            .filter(KillIntelKillmail.character_id == char_id)
            .all()
        }
    else:
        existing_ids = set()

    for stub, is_loss in stubs:
        km_id = int(stub.get("killmail_id", 0))
        if not km_id:
            continue

        # In incremental mode skip already-stored mails
        if since is not None and km_id in existing_ids:
            continue

        zkb = stub.get("zkb", {})
        km_hash = zkb.get("hash", "")
        total_value = int(zkb.get("totalValue", 0) or 0)

        existing_km = db.get(KillIntelKillmail, km_id)

        if existing_km and existing_km.hydrated:
            if existing_km.killmail_time and existing_km.killmail_time < cutoff_90d:
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
        if km_time and km_time < cutoff_90d:
            continue

        victim = esi_km.get("victim", {})
        attackers = esi_km.get("attackers", [])

        if is_loss:
            ship_type_id = victim.get("ship_type_id")
        else:
            our_attacker = next(
                (a for a in attackers if a.get("character_id") == char_id), None
            )
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
    """Fill in ship_name / type_name for rows that are still missing them.
    Also resolves any previously stored NULL names on the fly."""
    from app.models import KillIntelKillmail, KillIntelItem

    # Collect all type_ids with NULL names so we can resolve them too
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

    # Resolve any IDs not already in the map
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

def _aggregate_pilot(pilot, char_id: int, now: datetime, db: Session) -> dict:
    """Build the result dict from DB data for one pilot."""
    from app.models import KillIntelKillmail, KillIntelItem

    cutoff = now - timedelta(days=90)
    kms = (
        db.query(KillIntelKillmail)
        .filter(
            KillIntelKillmail.character_id == char_id,
            KillIntelKillmail.killmail_time >= cutoff,
        )
        .all()
    )

    times = [km.killmail_time for km in kms if km.killmail_time]
    last_activity = max(times).isoformat() if times else None

    ship_counter: Counter = Counter()
    for km in kms:
        if km.ship_type_id and km.ship_type_id not in (670, 33328):
            ship_counter[(km.ship_type_id, km.ship_name or f"Type {km.ship_type_id}")] += 1

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
                key = (itm.type_id, itm.type_name or f"Type {itm.type_id}", itm.slot)
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


# ── Cache check (for UI button) ──────────────────────────────────────────────

def check_names_in_cache(names: list[str], db: Session) -> dict[str, bool]:
    """
    Returns {name: True/False} — True if the pilot is in the DB cache at all
    (regardless of age). Used by the frontend to enable the 'Use Cache' button.
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

    out: dict[str, bool] = {}
    for name in names:
        char_id = char_map.get(name)
        if not char_id:
            out[name] = False
            continue
        pilot = db.get(KillIntelPilot, char_id)
        out[name] = pilot is not None
    return out


# ── Main entry point ─────────────────────────────────────────────────────────

def analyze_pilots(names: list[str], db: Session, use_cache_only: bool = False) -> list[dict]:
    """
    Resolve names → character IDs, fetch/cache pilot data, return aggregated profiles.

    Cache tiers (ignored when use_cache_only=True):
      < 5 min   → use DB as-is, no zKill call
      5–24 h    → fetch new stubs only (incremental), append to existing DB data
      > 24 h    → drop all killmail/item data, run full fresh analysis
    """
    from app.models import KillIntelPilot, KillIntelKillmail, KillIntelItem

    names = [n.strip() for n in names if n.strip()]
    if not names:
        return []

    # 1. Resolve names → character IDs via ESI /universe/ids/
    char_map: dict[str, int] = {}
    for chunk_start in range(0, len(names), 1000):
        chunk = names[chunk_start:chunk_start + 1000]
        result = universe_ids(chunk)
        for item in result.get("characters", []):
            if item.get("id") and item.get("name"):
                char_map[item["name"]] = int(item["id"])

    now = datetime.now(timezone.utc)
    results = []

    for name in names:
        char_id = char_map.get(name)
        if not char_id:
            results.append({"name": name, "error": "Character not found"})
            continue

        pilot = db.get(KillIntelPilot, char_id)

        if use_cache_only:
            # Ignore age — just aggregate whatever is in DB
            if pilot is None:
                results.append({"name": name, "error": "Not in cache — run a live analysis first"})
                continue
            results.append(_aggregate_pilot(pilot, char_id, now, db))
            continue

        age = _age(pilot, now)

        if age is not None and age < TTL_FRESH:
            # ── Tier 1: fresh — use DB as-is ────────────────────────────────
            logger.debug("killintel: FRESH cache hit for %s (%d), age=%s", name, char_id, age)

        elif age is not None and age < TTL_PARTIAL:
            # ── Tier 2: partial — refresh stats + fetch only new stubs ──────
            logger.info("killintel: PARTIAL refresh for %s (%d), age=%s", name, char_id, age)
            time.sleep(1.0)
            stats = _zkill_stats(char_id)
            info = stats.get("info") or {}

            corp_id = int(info.get("corporationID") or info.get("corporation_id") or 0) or None
            alliance_id = int(info.get("allianceID") or info.get("alliance_id") or 0) or None
            names_map = _resolve_corp_alliance_names(
                {corp_id} if corp_id else set(),
                {alliance_id} if alliance_id else set(),
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

            # Fetch new stubs — incremental (skip IDs already in DB)
            time.sleep(1.0)
            stubs = _zkill_kills(char_id)
            since = pilot.fetched_at  # we already updated it above, use now as marker
            type_ids = _ingest_stubs(char_id, stubs, db, now, since=since)
            type_name_map = _resolve_type_names(type_ids)
            _patch_names(char_id, type_name_map, db)
            db.commit()

        else:
            # ── Tier 3: stale (>24 h) or never fetched — full refresh ───────
            logger.info("killintel: FULL refresh for %s (%d), age=%s", name, char_id, age)

            # Drop existing killmail/item data for this pilot
            if pilot is not None:
                existing_km_ids = [
                    km_id for (km_id,) in db.query(KillIntelKillmail.killmail_id)
                    .filter(KillIntelKillmail.character_id == char_id)
                    .all()
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
                {corp_id} if corp_id else set(),
                {alliance_id} if alliance_id else set(),
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
            stubs = _zkill_kills(char_id)
            type_ids = _ingest_stubs(char_id, stubs, db, now, since=None)
            type_name_map = _resolve_type_names(type_ids)
            _patch_names(char_id, type_name_map, db)
            db.commit()

        # Opportunistically patch any NULL names left over from prior runs
        _patch_names(char_id, {}, db)
        db.commit()

        results.append(_aggregate_pilot(pilot, char_id, now, db))

    results.sort(key=lambda r: r.get("dangerous_score", 0), reverse=True)
    return results
