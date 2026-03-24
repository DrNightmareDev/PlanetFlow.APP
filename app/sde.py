"""
Static Data Engine (SDE) für EVE Online PI Manager
Quellen:
  - https://data.everef.net/reference-data/reference-data-latest.tar.xz  (Schematics, Types)
  - https://www.fuzzwork.co.uk/dump/latest/mapSolarSystems.sql.bz2       (Systeme für lokale Suche)

Wird beim Start geladen und regelmäßig automatisch aktualisiert.
"""
import bz2
import io
import json
import logging
import re
import tarfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
SDE_URL = "https://data.everef.net/reference-data/reference-data-latest.tar.xz"
UPDATE_INTERVAL_DAYS = 7

FUZZWORK_SYSTEMS_URL = "https://www.fuzzwork.co.uk/dump/latest/mapSolarSystems.sql.bz2"
FUZZWORK_REGIONS_URL = "https://www.fuzzwork.co.uk/dump/latest/mapRegions.sql.bz2"
FUZZWORK_CONSTELLATIONS_URL = "https://www.fuzzwork.co.uk/dump/latest/mapConstellations.sql.bz2"
FUZZWORK_DENORMALIZE_URL = "https://www.fuzzwork.co.uk/dump/latest/mapDenormalize.sql.bz2"
SYSTEMS_UPDATE_DAYS = 30

# In-memory stores
_schematics: dict[int, dict] = {}   # schematic_id -> normalized schematic
_types: dict[int, dict[str, str]] = {}         # type_id -> localized names
_type_ids_by_name_en: dict[str, int] = {}      # english_name_lower -> type_id
_build_time: str | None = None
_systems: dict[str, tuple[int, str, float]] = {}    # name_lower -> (system_id, name, security)
_systems_by_id: dict[int, dict] = {}                # system_id -> {name, security, region_id, constellation_id}
_regions: dict[int, str] = {}                       # region_id -> region_name
_constellations: dict[int, dict] = {}               # constellation_id -> {name, region_id}
_constellations_by_name: dict[str, dict] = {}       # name_lower -> {id, name, region_id}
_static_planets: dict[int, dict] = {}               # planet_id -> {system_id, planet_name, planet_number, radius}


# ─── Version & Update-Check ───────────────────────────────────────────────────

def get_build_time() -> str | None:
    """Gibt den build_time-Zeitstempel der geladenen SDE-Version zurück."""
    return _build_time


def _meta_path() -> Path:
    return DATA_DIR / "meta.json"


def _is_update_needed() -> bool:
    meta = _meta_path()
    if not meta.exists():
        return True
    try:
        data = json.loads(meta.read_text())
        bt = data.get("build_time", "")
        build_dt = datetime.fromisoformat(bt.replace("Z", "+00:00"))
        age = datetime.now(timezone.utc) - build_dt
        return age > timedelta(days=UPDATE_INTERVAL_DAYS)
    except Exception:
        return True


# ─── Download & Extraktion ────────────────────────────────────────────────────

def _download_and_extract() -> bool:
    logger.info(f"Lade EveRef SDE von {SDE_URL} ...")
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        resp = requests.get(SDE_URL, timeout=120, stream=True)
        resp.raise_for_status()
        raw = resp.content
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:xz") as tar:
            for name in ("meta.json", "schematics.json", "types.json"):
                try:
                    member = tar.getmember(name)
                    f = tar.extractfile(member)
                    if f:
                        # Atomisches Schreiben: erst Temp-Datei, dann umbenennen
                        tmp = DATA_DIR / f"{name}.tmp"
                        tmp.write_bytes(f.read())
                        tmp.replace(DATA_DIR / name)
                        logger.info(f"  Extrahiert: {name}")
                except KeyError:
                    logger.warning(f"  Nicht gefunden im Archiv: {name}")
        logger.info("EveRef SDE erfolgreich aktualisiert.")
        return True
    except Exception as e:
        logger.error(f"EveRef SDE Download fehlgeschlagen: {e}")
        return False


# ─── Daten laden ──────────────────────────────────────────────────────────────

def _load_meta():
    global _build_time
    path = _meta_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            _build_time = data.get("build_time")
        except Exception:
            pass


def _load_schematics():
    global _schematics
    path = DATA_DIR / "schematics.json"
    if not path.exists():
        logger.warning("schematics.json nicht gefunden – ESI-Fallback aktiv.")
        return
    try:
        raw = json.loads(path.read_text())
        result = {}
        for sid_str, s in raw.items():
            products = s.get("products", {})
            if not products:
                continue
            # PI-Schematics haben genau 1 Produkt
            prod = next(iter(products.values()))
            # Inputs: type_id -> quantity
            input_type_ids: dict[int, int] = {}
            for tid_str, inp in s.get("inputs", {}).items():
                qty = inp.get("quantity", inp) if isinstance(inp, dict) else int(inp)
                input_type_ids[int(tid_str)] = qty
            result[int(sid_str)] = {
                "cycle_time": s.get("cycle_time", 0),
                "schematic_name": s.get("name", {}).get("en", ""),
                "output_quantity": prod.get("quantity", 1),
                "output_type_id": prod.get("type_id", 0),
                "input_type_ids": input_type_ids,
            }
        _schematics = result
        logger.info(f"SDE: {len(_schematics)} Schematics geladen (build: {_build_time})")
    except Exception as e:
        logger.error(f"Fehler beim Laden von schematics.json: {e}")


def _load_types():
    global _types, _type_ids_by_name_en
    path = DATA_DIR / "types.json"
    if not path.exists():
        logger.warning("types.json nicht gefunden.")
        return
    try:
        raw = json.loads(path.read_text())
        types: dict[int, dict[str, str]] = {}
        ids_by_name_en: dict[str, int] = {}
        for k, v in raw.items():
            names = {
                str(lang): str(text)
                for lang, text in (v.get("name") or {}).items()
                if text
            }
            if not names:
                continue
            type_id = int(k)
            types[type_id] = names
            en_name = names.get("en")
            if en_name:
                ids_by_name_en[en_name.lower()] = type_id
        _types = types
        _type_ids_by_name_en = ids_by_name_en
        logger.info(f"SDE: {len(_types)} Types geladen.")
    except Exception as e:
        logger.error(f"Fehler beim Laden von types.json: {e}")


# ─── Solar-Systeme (Fuzzwork) ─────────────────────────────────────────────────

def _systems_path() -> Path:
    return DATA_DIR / "mapSolarSystems.sql.bz2"


def _is_systems_update_needed() -> bool:
    path = _systems_path()
    if not path.exists():
        return True
    return time.time() - path.stat().st_mtime > SYSTEMS_UPDATE_DAYS * 86400


def _download_systems() -> bool:
    logger.info(f"Lade Fuzzwork mapSolarSystems von {FUZZWORK_SYSTEMS_URL} ...")
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        resp = requests.get(FUZZWORK_SYSTEMS_URL, timeout=60, stream=True)
        resp.raise_for_status()
        tmp = DATA_DIR / "mapSolarSystems.sql.bz2.tmp"
        tmp.write_bytes(resp.content)
        tmp.replace(_systems_path())
        logger.info("mapSolarSystems.sql.bz2 erfolgreich heruntergeladen.")
        return True
    except Exception as e:
        logger.error(f"Fuzzwork Systems Download fehlgeschlagen: {e}")
        return False


def _load_systems() -> None:
    global _systems, _systems_by_id
    path = _systems_path()
    if not path.exists():
        logger.warning("mapSolarSystems.sql.bz2 nicht gefunden – System-Suche nicht verfügbar.")
        return
    try:
        raw_sql = bz2.decompress(path.read_bytes()).decode("utf-8", errors="replace")
        # INSERT row: (regionID, constellationID, solarSystemID, 'name', x,y,z,xMin,xMax,yMin,yMax,zMin,zMax,
        #               luminosity, border, fringe, corridor, hub, international, regional, constellation,
        #               security, factionID, radius, sunTypeID, securityClass)
        # col 0: regionID, col 2: solarSystemID, col 3: name, cols 4-20: 17 numerics, col 21: security
        pattern = re.compile(
            r"\((\d+),(\d+),(\d+),'([^']+)'(?:,[^,)]+){17},(-?[\d.eE+\-]+)"
        )
        result: dict[str, tuple[int, str, float]] = {}
        by_id: dict[int, dict] = {}
        for m in pattern.finditer(raw_sql):
            region_id = int(m.group(1))
            constellation_id = int(m.group(2))
            sys_id = int(m.group(3))
            name = m.group(4)
            security = float(m.group(5))
            result[name.lower()] = (sys_id, name, security)
            by_id[sys_id] = {
                "name": name,
                "security": security,
                "region_id": region_id,
                "constellation_id": constellation_id,
            }
        _systems = result
        _systems_by_id = by_id
        logger.info(f"SDE: {len(_systems)} Solar-Systeme geladen.")
    except Exception as e:
        logger.error(f"Fehler beim Laden von mapSolarSystems: {e}")


# ─── Regionen (Fuzzwork) ──────────────────────────────────────────────────────

def _regions_path() -> Path:
    return DATA_DIR / "mapRegions.sql.bz2"


def _is_regions_update_needed() -> bool:
    path = _regions_path()
    if not path.exists():
        return True
    return time.time() - path.stat().st_mtime > SYSTEMS_UPDATE_DAYS * 86400


def _download_regions() -> bool:
    logger.info(f"Lade Fuzzwork mapRegions von {FUZZWORK_REGIONS_URL} ...")
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        resp = requests.get(FUZZWORK_REGIONS_URL, timeout=30, stream=True)
        resp.raise_for_status()
        tmp = DATA_DIR / "mapRegions.sql.bz2.tmp"
        tmp.write_bytes(resp.content)
        tmp.replace(_regions_path())
        logger.info("mapRegions.sql.bz2 heruntergeladen.")
        return True
    except Exception as e:
        logger.error(f"Fuzzwork Regions Download fehlgeschlagen: {e}")
        return False


def _load_regions() -> None:
    global _regions
    path = _regions_path()
    if not path.exists():
        logger.warning("mapRegions.sql.bz2 nicht gefunden – Regionen unbekannt.")
        return
    try:
        pattern = re.compile(r"\((\d+),'([^']+)'")
        result: dict[int, str] = {}
        with bz2.open(str(path), "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                for m in pattern.finditer(line):
                    result[int(m.group(1))] = m.group(2)
        _regions = result
        logger.info(f"SDE: {len(_regions)} Regionen geladen.")
    except Exception as e:
        logger.error(f"Fehler beim Laden von mapRegions: {e}")


def _constellations_path() -> Path:
    return DATA_DIR / "mapConstellations.sql.bz2"


def _is_constellations_update_needed() -> bool:
    path = _constellations_path()
    if not path.exists():
        return True
    return time.time() - path.stat().st_mtime > SYSTEMS_UPDATE_DAYS * 86400


def _download_constellations() -> bool:
    logger.info(f"Lade Fuzzwork mapConstellations von {FUZZWORK_CONSTELLATIONS_URL} ...")
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        resp = requests.get(FUZZWORK_CONSTELLATIONS_URL, timeout=30, stream=True)
        resp.raise_for_status()
        tmp = DATA_DIR / "mapConstellations.sql.bz2.tmp"
        tmp.write_bytes(resp.content)
        tmp.replace(_constellations_path())
        logger.info("mapConstellations.sql.bz2 heruntergeladen.")
        return True
    except Exception as e:
        logger.error(f"Fuzzwork Constellations Download fehlgeschlagen: {e}")
        return False


def _load_constellations() -> None:
    global _constellations, _constellations_by_name
    path = _constellations_path()
    if not path.exists():
        logger.warning("mapConstellations.sql.bz2 nicht gefunden - Konstellationen unbekannt.")
        return
    try:
        pattern = re.compile(r"\((\d+),(\d+),'([^']+)'")
        by_id: dict[int, dict] = {}
        by_name: dict[str, dict] = {}
        with bz2.open(str(path), "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                for m in pattern.finditer(line):
                    region_id = int(m.group(1))
                    constellation_id = int(m.group(2))
                    name = m.group(3)
                    entry = {"id": constellation_id, "name": name, "region_id": region_id}
                    by_id[constellation_id] = entry
                    by_name[name.lower()] = entry
        _constellations = by_id
        _constellations_by_name = by_name
        logger.info(f"SDE: {len(_constellations)} Konstellationen geladen.")
    except Exception as e:
        logger.error(f"Fehler beim Laden von mapConstellations: {e}")


def _denormalize_path() -> Path:
    return DATA_DIR / "mapDenormalize.sql.bz2"


def _is_denormalize_update_needed() -> bool:
    path = _denormalize_path()
    if not path.exists():
        return True
    return time.time() - path.stat().st_mtime > SYSTEMS_UPDATE_DAYS * 86400


def _download_denormalize() -> bool:
    logger.info(f"Lade Fuzzwork mapDenormalize von {FUZZWORK_DENORMALIZE_URL} ...")
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        resp = requests.get(FUZZWORK_DENORMALIZE_URL, timeout=120, stream=True)
        resp.raise_for_status()
        tmp = DATA_DIR / "mapDenormalize.sql.bz2.tmp"
        tmp.write_bytes(resp.content)
        tmp.replace(_denormalize_path())
        logger.info("mapDenormalize.sql.bz2 heruntergeladen.")
        return True
    except Exception as e:
        logger.error(f"Fuzzwork mapDenormalize Download fehlgeschlagen: {e}")
        return False


def _load_static_planets() -> None:
    global _static_planets
    path = _denormalize_path()
    if not path.exists():
        logger.warning("mapDenormalize.sql.bz2 nicht gefunden - statische Planetendaten unbekannt.")
        return
    try:
        pattern = re.compile(
            r"\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+),(NULL|\d+),"
            r"(-?[\d.eE+\-]+),(-?[\d.eE+\-]+),(-?[\d.eE+\-]+),"
            r"(-?[\d.eE+\-]+),'([^']+)',(-?[\d.eE+\-]+|NULL),(NULL|\d+),(NULL|\d+)\)"
        )
        result: dict[int, dict] = {}
        with bz2.open(str(path), "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                for m in pattern.finditer(line):
                    item_id = int(m.group(1))
                    system_id = int(m.group(4))
                    radius = int(float(m.group(11)))
                    item_name = m.group(12)
                    celestial_index = m.group(14)
                    orbit_index = m.group(15)
                    if celestial_index == "NULL" or orbit_index != "NULL":
                        continue
                    result[item_id] = {
                        "planet_id": item_id,
                        "system_id": system_id,
                        "planet_name": item_name,
                        "planet_number": celestial_index,
                        "radius": radius,
                    }
        _static_planets = result
        logger.info(f"SDE: {len(_static_planets)} statische Planeten geladen.")
    except Exception as e:
        logger.error(f"Fehler beim Laden von mapDenormalize: {e}")


# ─── Öffentliche API ──────────────────────────────────────────────────────────

def init():
    """
    Initialisiert den SDE: Update-Check, Download (falls nötig), Daten laden.
    Wird beim App-Start aufgerufen.
    """
    if _is_update_needed():
        _download_and_extract()
    _load_meta()
    _load_schematics()
    _load_types()

    if _is_systems_update_needed():
        _download_systems()
    _load_systems()

    if _is_regions_update_needed():
        _download_regions()
    _load_regions()

    if _is_constellations_update_needed():
        _download_constellations()
    _load_constellations()

    if _is_denormalize_update_needed():
        _download_denormalize()
    _load_static_planets()


def find_system(query: str) -> dict | None:
    """
    Sucht ein System per Name (exakt, case-insensitiv) oder System-ID (numerisch).
    Returns: {id, name, security, region} oder None wenn nicht gefunden.
    """
    # Numerische ID?
    try:
        system_id = int(query)
        data = _systems_by_id.get(system_id)
        if data:
            return {
                "id": system_id,
                "name": data["name"],
                "security": round(data["security"], 1),
                "region": _regions.get(data.get("region_id", 0), ""),
                "constellation": _constellations.get(data.get("constellation_id", 0), {}).get("name", ""),
            }
        return None
    except ValueError:
        pass
    # Exakter Namens-Treffer (case-insensitiv)
    result = _systems.get(query.lower())
    if result:
        sys_id, name, security = result
        sys_data = _systems_by_id.get(sys_id, {})
        return {
            "id": sys_id,
            "name": name,
            "security": round(security, 1),
            "region": _regions.get(sys_data.get("region_id", 0), ""),
            "constellation": _constellations.get(sys_data.get("constellation_id", 0), {}).get("name", ""),
        }
    return None


def get_schematic(schematic_id: int) -> dict | None:
    """Gibt normalisiertes Schematic-Dict zurück oder None wenn unbekannt."""
    return _schematics.get(schematic_id)


def get_type_name(type_id: int) -> str | None:
    """Gibt den englischen Typnamen zurück oder None."""
    return _types.get(type_id)


def get_type_name(type_id: int, lang: str = "en") -> str | None:
    """Gibt den Typnamen in der gewünschten Sprache oder englisch zurück."""
    names = _types.get(type_id) or {}
    lookup_lang = "zh" if lang == "zh-Hans" else lang
    return names.get(lookup_lang) or names.get("en")


def get_type_translations(type_id: int) -> dict[str, str]:
    """Gibt alle verfügbaren Übersetzungen eines Typs zurück."""
    return dict(_types.get(type_id) or {})


def find_type_id_by_name(name: str) -> int | None:
    """Sucht eine Type-ID anhand des englischen SDE-Namens."""
    if not name:
        return None
    return _type_ids_by_name_en.get(name.lower())


def get_system_local(system_id: int) -> dict | None:
    """Gibt lokale System-Infos zurück: name, security, true_sec, region_name."""
    data = _systems_by_id.get(system_id)
    if not data:
        return None
    return {
        "name": data["name"],
        "security": data["security"],
        "region_id": data.get("region_id", 0),
        "region_name": _regions.get(data.get("region_id", 0), None),
        "constellation_id": data.get("constellation_id", 0),
        "constellation_name": _constellations.get(data.get("constellation_id", 0), {}).get("name"),
    }


def search_systems_local(query: str, limit: int = 10) -> list[dict]:
    """
    Lokale System-Suche in den Fuzzwork-Daten.
    Gibt eine Liste von {id, name, security, region} zurück, Prefix-Treffer zuerst.
    """
    if not _systems or len(query) < 3:
        return []
    q = query.lower()
    prefix: list[dict] = []
    contains: list[dict] = []
    for name_lower, (sys_id, name, security) in _systems.items():
        sys_data = _systems_by_id.get(sys_id, {})
        region_name = _regions.get(sys_data.get("region_id", 0), "")
        entry = {
            "id": sys_id,
            "name": name,
            "security": round(security, 1),
            "region": region_name,
            "constellation": _constellations.get(sys_data.get("constellation_id", 0), {}).get("name", ""),
        }
        if name_lower.startswith(q):
            prefix.append(entry)
        elif q in name_lower:
            contains.append(entry)
        if len(prefix) >= limit:
            break
    results = prefix + contains
    return results[:limit]


def search_constellations_local(query: str, limit: int = 10) -> list[dict]:
    if not _constellations_by_name or len(query) < 3:
        return []
    q = query.lower()
    prefix: list[dict] = []
    contains: list[dict] = []
    for name_lower, constellation in _constellations_by_name.items():
        region_name = _regions.get(constellation.get("region_id", 0), "")
        entry = {
            "id": constellation["id"],
            "name": constellation["name"],
            "region": region_name,
        }
        if name_lower.startswith(q):
            prefix.append(entry)
        elif q in name_lower:
            contains.append(entry)
        if len(prefix) >= limit:
            break
    return (prefix + contains)[:limit]


def get_constellation_systems_local(constellation_id: int) -> list[dict]:
    result: list[dict] = []
    constellation = _constellations.get(constellation_id)
    if not constellation:
        return result
    for system_id, system in _systems_by_id.items():
        if system.get("constellation_id") != constellation_id:
            continue
        result.append({
            "id": system_id,
            "name": system["name"],
            "security": round(system["security"], 1),
            "region": _regions.get(system.get("region_id", 0), ""),
            "constellation": constellation["name"],
        })
    result.sort(key=lambda item: item["name"])
    return result


def get_static_planets() -> dict[int, dict]:
    return dict(_static_planets)
