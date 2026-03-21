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
SYSTEMS_UPDATE_DAYS = 30

# In-memory stores
_schematics: dict[int, dict] = {}   # schematic_id -> normalized schematic
_types: dict[int, str] = {}         # type_id -> name (en)
_build_time: str | None = None
_systems: dict[str, tuple[int, str, float]] = {}  # name_lower -> (system_id, name, security)


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
            result[int(sid_str)] = {
                "cycle_time": s.get("cycle_time", 0),
                "schematic_name": s.get("name", {}).get("en", ""),
                "output_quantity": prod.get("quantity", 1),
                "output_type_id": prod.get("type_id", 0),
            }
        _schematics = result
        logger.info(f"SDE: {len(_schematics)} Schematics geladen (build: {_build_time})")
    except Exception as e:
        logger.error(f"Fehler beim Laden von schematics.json: {e}")


def _load_types():
    global _types
    path = DATA_DIR / "types.json"
    if not path.exists():
        logger.warning("types.json nicht gefunden.")
        return
    try:
        raw = json.loads(path.read_text())
        _types = {
            int(k): v.get("name", {}).get("en", "")
            for k, v in raw.items()
            if v.get("name", {}).get("en")
        }
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
    global _systems
    path = _systems_path()
    if not path.exists():
        logger.warning("mapSolarSystems.sql.bz2 nicht gefunden – System-Suche nicht verfügbar.")
        return
    try:
        raw_sql = bz2.decompress(path.read_bytes()).decode("utf-8", errors="replace")
        # INSERT row: (regionID, constellationID, solarSystemID, 'name', x,y,z,xMin,xMax,yMin,yMax,zMin,zMax,
        #               luminosity, border, fringe, corridor, hub, international, regional, constellation,
        #               security, factionID, radius, sunTypeID, securityClass)
        # Cols 0-2: region/constellation/solarSystemID, col 3: name, cols 4-20: 17 numerics, col 21: security
        pattern = re.compile(
            r"\(\d+,\d+,(\d+),'([^']+)'(?:,[^,)]+){17},(-?[\d.eE+\-]+)"
        )
        result: dict[str, tuple[int, str, float]] = {}
        for m in pattern.finditer(raw_sql):
            sys_id = int(m.group(1))
            name = m.group(2)
            security = float(m.group(3))
            result[name.lower()] = (sys_id, name, security)
        _systems = result
        logger.info(f"SDE: {len(_systems)} Solar-Systeme geladen.")
    except Exception as e:
        logger.error(f"Fehler beim Laden von mapSolarSystems: {e}")


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


def get_schematic(schematic_id: int) -> dict | None:
    """Gibt normalisiertes Schematic-Dict zurück oder None wenn unbekannt."""
    return _schematics.get(schematic_id)


def get_type_name(type_id: int) -> str | None:
    """Gibt den englischen Typnamen zurück oder None."""
    return _types.get(type_id)


def search_systems_local(query: str, limit: int = 10) -> list[dict]:
    """
    Lokale System-Suche in den Fuzzwork-Daten.
    Gibt eine Liste von {id, name, security} zurück, Prefix-Treffer zuerst.
    """
    if not _systems or len(query) < 3:
        return []
    q = query.lower()
    prefix: list[dict] = []
    contains: list[dict] = []
    for name_lower, (sys_id, name, security) in _systems.items():
        if name_lower.startswith(q):
            prefix.append({"id": sys_id, "name": name, "security": round(security, 1)})
        elif q in name_lower:
            contains.append({"id": sys_id, "name": name, "security": round(security, 1)})
        if len(prefix) >= limit:
            break
    results = prefix + contains
    return results[:limit]
