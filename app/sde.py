"""
Static Data Engine (SDE) für EVE Online PI Manager
Quelle: https://data.everef.net/reference-data/reference-data-latest.tar.xz

Enthält: schematics.json, types.json, meta.json
Wird beim Start geladen und wöchentlich automatisch aktualisiert.
"""
import io
import json
import logging
import tarfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
SDE_URL = "https://data.everef.net/reference-data/reference-data-latest.tar.xz"
UPDATE_INTERVAL_DAYS = 7

# In-memory stores
_schematics: dict[int, dict] = {}   # schematic_id -> normalized schematic
_types: dict[int, str] = {}         # type_id -> name (en)
_build_time: str | None = None


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


def get_schematic(schematic_id: int) -> dict | None:
    """Gibt normalisiertes Schematic-Dict zurück oder None wenn unbekannt."""
    return _schematics.get(schematic_id)


def get_type_name(type_id: int) -> str | None:
    """Gibt den englischen Typnamen zurück oder None."""
    return _types.get(type_id)
