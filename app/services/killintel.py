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
PILOT_TTL = timedelta(hours=1)
KILLS_90D = 90 * 86400  # seconds

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


def _zkill_kills(character_id: int) -> list[dict]:
    """Fetch page 1 only (≤200 most recent stubs) of kills + losses for a pilot."""
    result = []
    for kind, is_loss in [("kills", False), ("losses", True)]:
        try:
            # page=1 returns the most recent ~200 entries; never paginate here
            data = _fetch_json(f"{ZKILL_BASE}/{kind}/characterID/{character_id}/page/1/")
            if isinstance(data, list):
                result.extend([(k, is_loss) for k in data])
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


# ── Name resolution ─────────────────────────────────────────────────────────

def _resolve_type_names(type_ids: set[int]) -> dict[int, str]:
    result: dict[int, str] = {}
    missing = []
    for tid in type_ids:
        name = sde.get_type_name(tid)
        if name:
            result[tid] = name
        else:
            missing.append(tid)
    # Bulk ESI for any not in SDE
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


# ── DB helpers ───────────────────────────────────────────────────────────────

def _pilot_is_fresh(pilot, now: datetime) -> bool:
    if pilot is None or pilot.fetched_at is None:
        return False
    fetched = pilot.fetched_at
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return (now - fetched) < PILOT_TTL


# ── Main entry point ─────────────────────────────────────────────────────────

def analyze_pilots(names: list[str], db: Session) -> list[dict]:
    """
    Resolve names → character IDs, fetch/cache pilot data, return aggregated profiles.
    Rate-limited to ~1 req/s against zKill.
    """
    from app.models import KillIntelPilot, KillIntelKillmail, KillIntelItem

    names = [n.strip() for n in names if n.strip()]
    if not names:
        return []

    # 1. Resolve names → character IDs via ESI /universe/ids/
    char_map: dict[str, int] = {}  # name → char_id
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

        # 2. Check DB cache
        pilot = db.get(KillIntelPilot, char_id)
        if _pilot_is_fresh(pilot, now):
            logger.debug("killintel: cache hit for %s (%d)", name, char_id)
        else:
            # 3. Fetch from zKill
            time.sleep(1.0)  # rate limit: 1 req/s
            stats = _zkill_stats(char_id)
            info = stats.get("info") or {}

            corp_id = int(info.get("corporationID") or info.get("corporation_id") or 0) or None
            alliance_id = int(info.get("allianceID") or info.get("alliance_id") or 0) or None

            # Resolve corp/alliance names
            names_map = _resolve_corp_alliance_names(
                {corp_id} if corp_id else set(),
                {alliance_id} if alliance_id else set(),
            )

            # Upsert pilot
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

            # 4. Fetch kill stubs
            time.sleep(1.0)
            stubs = _zkill_kills(char_id)

            # Cutoff: last 90 days
            cutoff = now - timedelta(days=90)

            # Hydrate losses (we need items for fit analysis)
            # Limit to 50 loss hydrations to avoid hammering ESI
            hydrated_count = 0
            type_ids_to_resolve: set[int] = set()

            for stub, is_loss in stubs:
                km_id = int(stub.get("killmail_id", 0))
                if not km_id:
                    continue

                zkb = stub.get("zkb", {})
                km_hash = zkb.get("hash", "")
                total_value = int(zkb.get("totalValue", 0) or 0)

                # Check if already in DB
                existing_km = db.get(KillIntelKillmail, km_id)

                if existing_km and existing_km.hydrated:
                    # Already have full data, just check timestamp vs cutoff
                    if existing_km.killmail_time and existing_km.killmail_time < cutoff:
                        continue
                    if existing_km.ship_type_id:
                        type_ids_to_resolve.add(existing_km.ship_type_id)
                    continue

                # Hydrate from ESI
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

                km_time_str = esi_km.get("killmail_time", "")
                km_time = _parse_timestamp(km_time_str)
                if km_time and km_time < cutoff:
                    continue  # too old

                victim = esi_km.get("victim", {})
                attackers = esi_km.get("attackers", [])

                # Determine ship_type_id: if loss → victim ship; if kill → pilot's attacker ship
                if is_loss:
                    ship_type_id = victim.get("ship_type_id")
                else:
                    our_attacker = next(
                        (a for a in attackers if a.get("character_id") == char_id), None
                    )
                    ship_type_id = our_attacker.get("ship_type_id") if our_attacker else None

                if ship_type_id:
                    type_ids_to_resolve.add(ship_type_id)

                # Store killmail
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

                # Store items for losses (fit analysis) — cap at 20 to stay fast
                if is_loss and hydrated_count < 20:
                    hydrated_count += 1
                    # Remove old items for this km
                    db.query(KillIntelItem).filter(KillIntelItem.killmail_id == km_id).delete()
                    items = victim.get("items", [])
                    for itm in items:
                        flag = itm.get("flag", 0)
                        slot = _slot_for_flag(flag)
                        if slot == "other":
                            continue
                        tid = itm.get("item_type_id")
                        if not tid:
                            continue
                        qty = int(itm.get("quantity_destroyed", 0) or itm.get("quantity_dropped", 0) or 1)
                        type_ids_to_resolve.add(tid)
                        db.add(KillIntelItem(
                            killmail_id=km_id,
                            type_id=tid,
                            slot=slot,
                            quantity=qty,
                        ))

            # Bulk resolve type names
            type_name_map = _resolve_type_names(type_ids_to_resolve)

            # Patch names into stored killmails and items
            for km in db.query(KillIntelKillmail).filter(
                KillIntelKillmail.character_id == char_id,
                KillIntelKillmail.ship_type_id.isnot(None),
                KillIntelKillmail.ship_name.is_(None),
            ).all():
                if km.ship_type_id and km.ship_type_id in type_name_map:
                    km.ship_name = type_name_map[km.ship_type_id]

            for itm in db.query(KillIntelItem).filter(
                KillIntelItem.killmail_id.in_(
                    db.query(KillIntelKillmail.killmail_id).filter(
                        KillIntelKillmail.character_id == char_id
                    )
                ),
                KillIntelItem.type_name.is_(None),
            ).all():
                if itm.type_id in type_name_map:
                    itm.type_name = type_name_map[itm.type_id]

            db.commit()

        # 5. Aggregate from DB
        cutoff = now - timedelta(days=90)
        kms = (
            db.query(KillIntelKillmail)
            .filter(
                KillIntelKillmail.character_id == char_id,
                KillIntelKillmail.killmail_time >= cutoff,
            )
            .all()
        )

        # Last activity
        times = [km.killmail_time for km in kms if km.killmail_time]
        last_activity = max(times).isoformat() if times else None

        # Ship usage (all kms, both kills and losses, last 90d)
        ship_counter: Counter = Counter()
        for km in kms:
            if km.ship_type_id and km.ship_type_id not in (670, 33328):  # skip pods/capsules
                ship_counter[(km.ship_type_id, km.ship_name or f"Type {km.ship_type_id}")] += 1

        total_appearances = sum(ship_counter.values()) or 1
        top_ships_raw = ship_counter.most_common(3)

        top_ships = []
        for (ship_type_id, ship_name), count in top_ships_raw:
            usage_pct = round(count / total_appearances * 100)

            # Gather modules from loss killmails with this ship
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

            # Top modules by frequency across losses
            typical_modules = []
            for (tid, tname, slot), freq_count in sorted(
                module_slot_counter.items(), key=lambda x: -x[1]
            )[:12]:
                freq = round(freq_count / loss_count, 2)
                if freq < 0.1:
                    continue
                typical_modules.append({
                    "type_id": tid,
                    "name": tname,
                    "slot": slot,
                    "frequency": freq,
                })

            # Sort by slot order then frequency
            slot_order = {"high": 0, "mid": 1, "low": 2, "rig": 3, "sub": 4}
            typical_modules.sort(key=lambda m: (slot_order.get(m["slot"], 9), -m["frequency"]))

            top_ships.append({
                "ship_type_id": ship_type_id,
                "ship_name": ship_name,
                "usage_percent": usage_pct,
                "appearances": count,
                "typical_modules": typical_modules[:10],
            })

        # Dangerous score: danger_ratio from zKill (0-100), rescaled to 0-5
        danger_ratio = pilot.danger_ratio or 0
        dangerous_score = round(danger_ratio / 20, 1)  # 0-100 → 0-5

        results.append({
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
        })

    # Sort by dangerous score descending
    results.sort(key=lambda r: r.get("dangerous_score", 0), reverse=True)
    return results
