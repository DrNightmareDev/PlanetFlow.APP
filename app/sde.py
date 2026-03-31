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
from xml.etree import ElementTree as ET

import requests

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
SDE_URL = "https://data.everef.net/reference-data/reference-data-latest.tar.xz"
UPDATE_INTERVAL_DAYS = 7

FUZZWORK_SYSTEMS_URL = "https://www.fuzzwork.co.uk/dump/latest/mapSolarSystems.sql.bz2"
FUZZWORK_JUMPS_URL = "https://www.fuzzwork.co.uk/dump/latest/mapSolarSystemJumps.sql.bz2"
FUZZWORK_REGIONS_URL = "https://www.fuzzwork.co.uk/dump/latest/mapRegions.sql.bz2"
FUZZWORK_CONSTELLATIONS_URL = "https://www.fuzzwork.co.uk/dump/latest/mapConstellations.sql.bz2"
FUZZWORK_DENORMALIZE_URL = "https://www.fuzzwork.co.uk/dump/latest/mapDenormalize.sql.bz2"
DOTLAN_SVG_URL = "https://evemaps.dotlan.net/svg/{slug}.svg"
SYSTEMS_UPDATE_DAYS = 30

# In-memory stores
_schematics: dict[int, dict] = {}   # schematic_id -> normalized schematic
_types: dict[int, dict[str, str]] = {}         # type_id -> localized names
_type_ids_by_name_en: dict[str, int] = {}      # english_name_lower -> type_id
_build_time: str | None = None
_systems: dict[str, tuple[int, str, float]] = {}    # name_lower -> (system_id, name, security)
_systems_by_id: dict[int, dict] = {}                # system_id -> {name, security, region_id, constellation_id}
_jumps_by_system: dict[int, set[int]] = {}          # system_id -> connected system_ids
_regions: dict[int, str] = {}                       # region_id -> region_name
_constellations: dict[int, dict] = {}               # constellation_id -> {name, region_id}
_constellations_by_name: dict[str, dict] = {}       # name_lower -> {id, name, region_id}
_static_planets: dict[int, dict] = {}               # planet_id -> {system_id, planet_name, planet_number, radius}
_dotlan_layout_cache: dict[int, tuple[float, dict]] = {}
_DOTLAN_LAYOUT_TTL = 86400.0


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
        # col 0: regionID, col 2: solarSystemID, col 3: name, col 4/5: x/y, col 21: security
        pattern = re.compile(
            r"\((\d+),(\d+),(\d+),'([^']+)',(-?[\d.eE+\-]+),(-?[\d.eE+\-]+)(?:,[^,)]+){15},(-?[\d.eE+\-]+)"
        )
        result: dict[str, tuple[int, str, float]] = {}
        by_id: dict[int, dict] = {}
        for m in pattern.finditer(raw_sql):
            region_id = int(m.group(1))
            constellation_id = int(m.group(2))
            sys_id = int(m.group(3))
            name = m.group(4)
            x = float(m.group(5))
            y = float(m.group(6))
            security = float(m.group(7))
            result[name.lower()] = (sys_id, name, security)
            by_id[sys_id] = {
                "name": name,
                "security": security,
                "region_id": region_id,
                "constellation_id": constellation_id,
                "x": x,
                "y": y,
            }
        _systems = result
        _systems_by_id = by_id
        logger.info(f"SDE: {len(_systems)} Solar-Systeme geladen.")
    except Exception as e:
        logger.error(f"Fehler beim Laden von mapSolarSystems: {e}")


def _jumps_path() -> Path:
    return DATA_DIR / "mapSolarSystemJumps.sql.bz2"


def _is_jumps_update_needed() -> bool:
    path = _jumps_path()
    if not path.exists():
        return True
    return time.time() - path.stat().st_mtime > SYSTEMS_UPDATE_DAYS * 86400


def _download_jumps() -> bool:
    logger.info(f"Lade Fuzzwork mapSolarSystemJumps von {FUZZWORK_JUMPS_URL} ...")
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        resp = requests.get(FUZZWORK_JUMPS_URL, timeout=60, stream=True)
        resp.raise_for_status()
        tmp = DATA_DIR / "mapSolarSystemJumps.sql.bz2.tmp"
        tmp.write_bytes(resp.content)
        tmp.replace(_jumps_path())
        logger.info("mapSolarSystemJumps.sql.bz2 erfolgreich heruntergeladen.")
        return True
    except Exception as e:
        logger.error(f"Fuzzwork Jumps Download fehlgeschlagen: {e}")
        return False


def _load_jumps() -> None:
    global _jumps_by_system
    path = _jumps_path()
    if not path.exists():
        logger.warning("mapSolarSystemJumps.sql.bz2 nicht gefunden - Jump-Graph unbekannt.")
        return
    try:
        pattern = re.compile(r"\((\d+),(\d+),(\d+),(\d+),(\d+),(\d+)\)")
        by_system: dict[int, set[int]] = {}
        with bz2.open(str(path), "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                for m in pattern.finditer(line):
                    from_system_id = int(m.group(3))
                    to_system_id = int(m.group(4))
                    by_system.setdefault(from_system_id, set()).add(to_system_id)
                    by_system.setdefault(to_system_id, set()).add(from_system_id)
        _jumps_by_system = by_system
        logger.info(f"SDE: Jump-Graph fuer {len(_jumps_by_system)} Systeme geladen.")
    except Exception as e:
        logger.error(f"Fehler beim Laden von mapSolarSystemJumps: {e}")


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

    if _is_jumps_update_needed():
        _download_jumps()
    _load_jumps()

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
        try:
            from app.esi import get_constellation_info, get_system_info

            esi_system = get_system_info(int(system_id))
            constellation_id = int(esi_system.get("constellation_id") or 0)
            constellation = _constellations.get(constellation_id)
            if not constellation and constellation_id:
                esi_constellation = get_constellation_info(constellation_id)
                region_id = int(esi_constellation.get("region_id") or 0)
                if region_id:
                    constellation = {
                        "id": constellation_id,
                        "name": str(esi_constellation.get("name") or f"Constellation {constellation_id}"),
                        "region_id": region_id,
                    }
                    _constellations[constellation_id] = constellation
            region_id = int((constellation or {}).get("region_id") or 0)
            if esi_system and region_id:
                data = {
                    "name": str(esi_system.get("name") or f"System {int(system_id)}"),
                    "security": float(esi_system.get("security_status") or 0.0),
                    "region_id": region_id,
                    "constellation_id": constellation_id,
                    "x": 0.0,
                    "y": 0.0,
                }
                _systems_by_id[int(system_id)] = data
                _systems[data["name"].lower()] = (int(system_id), data["name"], data["security"])
        except Exception:
            data = None
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


def has_jump_graph() -> bool:
    return bool(_jumps_by_system)


def get_system_neighbors(system_id: int) -> list[int]:
    return sorted(_jumps_by_system.get(int(system_id), set()))


def get_region_catalog() -> list[dict]:
    return [
        {"id": region_id, "name": region_name}
        for region_id, region_name in sorted(_regions.items(), key=lambda item: item[1].lower())
    ]


def _dotlan_slug(region_name: str) -> str:
    return str(region_name or "").strip().replace(" ", "_")


def _dotlan_svg_dir() -> Path:
    return DATA_DIR / "dotlan_svg"


def _dotlan_svg_path(region_name: str) -> Path:
    return _dotlan_svg_dir() / f"{_dotlan_slug(region_name)}.svg"


def _parse_dotlan_layout(svg_text: str) -> dict:
    ns = {"svg": "http://www.w3.org/2000/svg"}
    root = ET.fromstring(svg_text)
    positions: dict[int, tuple[float, float]] = {}

    for use in root.findall(".//svg:use", ns):
        raw_id = str(use.attrib.get("id") or "")
        if not raw_id.startswith("sys"):
            continue
        try:
            system_id = int(raw_id[3:])
            base_x = float(use.attrib.get("x") or 0.0)
            base_y = float(use.attrib.get("y") or 0.0)
        except (TypeError, ValueError):
            continue
        # DOTLAN symbols place the system label roughly around 28/14 inside the symbol.
        positions[system_id] = (round(base_x + 28.0, 2), round(base_y + 14.0, 2))

    if not positions:
        return {"positions": {}, "view_box": "0 0 1024 768"}

    padding = 48.0
    xs = [point[0] for point in positions.values()]
    ys = [point[1] for point in positions.values()]
    min_x = min(xs) - padding
    max_x = max(xs) + padding
    min_y = min(ys) - padding
    max_y = max(ys) + padding
    width = max(320.0, max_x - min_x)
    height = max(240.0, max_y - min_y)

    return {
        "positions": positions,
        "view_box": f"{round(min_x, 2)} {round(min_y, 2)} {round(width, 2)} {round(height, 2)}",
    }


def _get_dotlan_layout(region_id: int, region_name: str) -> dict | None:
    cached = _dotlan_layout_cache.get(int(region_id))
    now = time.time()
    if cached and now - cached[0] <= _DOTLAN_LAYOUT_TTL:
        return cached[1]

    path = _dotlan_svg_path(region_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    layout: dict | None = None

    try:
        response = requests.get(
            DOTLAN_SVG_URL.format(slug=_dotlan_slug(region_name)),
            headers={"User-Agent": "EVE-PI-Manager/1.0 github.com/DrNightmareDev/PI_Manager"},
            timeout=20,
        )
        response.raise_for_status()
        svg_text = response.text
        if svg_text:
            path.write_text(svg_text, encoding="utf-8")
            layout = _parse_dotlan_layout(svg_text)
    except Exception as exc:
        logger.warning("DOTLAN SVG fetch failed for %s (%s): %s", region_name, region_id, exc)
        if path.exists():
            try:
                layout = _parse_dotlan_layout(path.read_text(encoding="utf-8"))
            except Exception as file_exc:
                logger.warning("DOTLAN SVG cache parse failed for %s: %s", region_name, file_exc)

    if layout and layout.get("positions"):
        _dotlan_layout_cache[int(region_id)] = (now, layout)
        return layout
    return None


def get_region_system_graph(region_id: int) -> dict | None:
    region_id = int(region_id)
    region_name = _regions.get(region_id)
    if not region_name:
        return None

    systems = []
    system_ids = []
    raw_x_values: list[float] = []
    raw_y_values: list[float] = []
    dotlan_layout = _get_dotlan_layout(region_id, region_name)
    dotlan_positions = (dotlan_layout or {}).get("positions") or {}

    for system_id, data in _systems_by_id.items():
        if int(data.get("region_id") or 0) != region_id:
            continue
        raw_x = float(data.get("x") or 0.0)
        raw_y = float(data.get("y") or 0.0)
        compact_x, compact_y = dotlan_positions.get(int(system_id), (None, None))
        systems.append({
            "id": system_id,
            "name": data.get("name") or f"System {system_id}",
            "security": round(float(data.get("security") or 0.0), 1),
            "constellation_id": int(data.get("constellation_id") or 0),
            "constellation_name": _constellations.get(int(data.get("constellation_id") or 0), {}).get("name"),
            "raw_x": raw_x,
            "raw_y": raw_y,
            "compact_x": compact_x,
            "compact_y": compact_y,
        })
        system_ids.append(system_id)
        raw_x_values.append(raw_x)
        raw_y_values.append(raw_y)

    system_set = set(system_ids)
    connections: list[list[int]] = []
    neighbor_region_ids: set[int] = set()
    for system_id in system_ids:
        for neighbor_id in _jumps_by_system.get(system_id, set()):
            if neighbor_id in system_set:
                if system_id < neighbor_id:
                    connections.append([system_id, neighbor_id])
                continue
            neighbor = _systems_by_id.get(neighbor_id)
            if neighbor:
                neighbor_region_id = int(neighbor.get("region_id") or 0)
                if neighbor_region_id and neighbor_region_id != region_id:
                    neighbor_region_ids.add(neighbor_region_id)

    neighbors = [
        {"id": rid, "name": _regions.get(rid, f"Region {rid}")}
        for rid in sorted(neighbor_region_ids, key=lambda rid: _regions.get(rid, "").lower())
    ]

    min_x = min(raw_x_values) if raw_x_values else 0.0
    max_x = max(raw_x_values) if raw_x_values else 1.0
    min_y = min(raw_y_values) if raw_y_values else 0.0
    max_y = max(raw_y_values) if raw_y_values else 1.0
    range_x = max(1.0, max_x - min_x)
    range_y = max(1.0, max_y - min_y)
    width = 1280.0
    padding = 80.0
    usable_width = width - (padding * 2)
    height = max(760.0, min(1480.0, (range_y / range_x) * usable_width + padding * 2))
    usable_height = height - (padding * 2)

    for system in systems:
        projected_x = padding + ((float(system["raw_x"]) - min_x) / range_x) * usable_width
        projected_y = padding + ((max_y - float(system["raw_y"])) / range_y) * usable_height
        system["x"] = round(projected_x, 2)
        system["y"] = round(projected_y, 2)

    systems.sort(key=lambda item: ((item.get("constellation_name") or "").lower(), item["name"].lower()))
    return {
        "id": region_id,
        "name": region_name,
        "systems": systems,
        "connections": connections,
        "neighbors": neighbors,
        "view_box": f"0 0 {int(width)} {int(height)}",
        "geo_view_box": f"0 0 {int(width)} {int(height)}",
        "compact_view_box": (dotlan_layout or {}).get("view_box") or f"0 0 {int(width)} {int(height)}",
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
