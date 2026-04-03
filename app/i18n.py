import json
import logging
from pathlib import Path

from jinja2 import pass_context
from sqlalchemy import inspect

from app.database import SessionLocal, engine

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
DEFAULT_LANGUAGE = "de"
SUPPORTED_LANGUAGES = ("de", "en", "zh-Hans")

logger = logging.getLogger(__name__)

TYPE_TRANSLATION_PREFIX = "type"
_translations_cache: dict[str, dict[str, str]] | None = None


def _load_seed_translations() -> dict[str, dict[str, str]]:
    catalogs: dict[str, dict[str, str]] = {}
    for lang in SUPPORTED_LANGUAGES:
        path = LOCALES_DIR / f"{lang}.json"
        with path.open("r", encoding="utf-8") as handle:
            catalogs[lang] = json.load(handle)
    return catalogs


def _translation_table_exists() -> bool:
    try:
        return inspect(engine).has_table("translation_entries")
    except Exception:
        return False


def load_translations() -> dict[str, dict[str, str]]:
    global _translations_cache
    if _translations_cache is not None:
        return _translations_cache

    seeds = _load_seed_translations()
    if not _translation_table_exists():
        _translations_cache = seeds
        return seeds

    try:
        from app.models import TranslationEntry

        # Start with seed files so new keys always have a value even if
        # bootstrap_translations() hasn't run yet for that locale.
        catalogs: dict[str, dict[str, str]] = {lang: dict(seeds.get(lang, {})) for lang in SUPPORTED_LANGUAGES}
        with SessionLocal() as db:
            rows = db.query(TranslationEntry).all()
        for row in rows:
            if row.locale in catalogs and row.text:
                catalogs[row.locale][row.key] = row.text
        _translations_cache = catalogs
        return catalogs
    except Exception as exc:
        logger.warning("Translation load from DB failed, using seed files: %s", exc)
        _translations_cache = seeds
        return seeds


def _invalidate_translations_cache() -> None:
    global _translations_cache
    _translations_cache = None


def clear_translation_cache() -> None:
    _invalidate_translations_cache()


def normalize_language(value: str | None) -> str:
    if not value:
        return DEFAULT_LANGUAGE
    value = value.strip()
    if value in SUPPORTED_LANGUAGES:
        return value
    if value.startswith("zh"):
        return "zh-Hans"
    if value.startswith("en"):
        return "en"
    return DEFAULT_LANGUAGE


def get_language_from_request(request) -> str:
    if request is None:
        return DEFAULT_LANGUAGE
    cookie_lang = normalize_language(request.cookies.get("eve_lang"))
    if cookie_lang:
        return cookie_lang
    header = request.headers.get("accept-language", "")
    for part in header.split(","):
        lang = normalize_language(part.split(";")[0])
        if lang in SUPPORTED_LANGUAGES:
            return lang
    return DEFAULT_LANGUAGE


def translate(key: str, lang: str | None = None, **params) -> str:
    catalogs = load_translations()
    current_lang = normalize_language(lang)
    catalog = catalogs.get(current_lang, {})
    default_text = params.pop("default", key)
    text = catalog.get(key) or catalogs.get(DEFAULT_LANGUAGE, {}).get(key) or default_text
    if params:
        try:
            return text.format(**params)
        except Exception:
            return text
    return text


def get_client_catalog(lang: str | None = None) -> dict[str, str]:
    current_lang = normalize_language(lang)
    catalogs = load_translations()
    merged = dict(catalogs.get(DEFAULT_LANGUAGE, {}))
    merged.update(catalogs.get(current_lang, {}))
    return merged


def get_translation_rows() -> list[dict[str, str]]:
    catalogs = load_translations()
    keys = sorted({key for catalog in catalogs.values() for key in catalog.keys()})
    rows: list[dict[str, str]] = []
    for key in keys:
        row = {"key": key}
        for lang in SUPPORTED_LANGUAGES:
            row[lang] = catalogs.get(lang, {}).get(key, "")
        row["editable"] = is_editable_translation_key(key)
        row["source"] = get_translation_source(key)
        rows.append(row)
    return rows


def type_translation_key(type_id: int) -> str:
    return f"{TYPE_TRANSLATION_PREFIX}.{int(type_id)}.name"


def is_editable_translation_key(key: str) -> bool:
    return not key.startswith(f"{TYPE_TRANSLATION_PREFIX}.")


def get_translation_source(key: str) -> str:
    if key.startswith(f"{TYPE_TRANSLATION_PREFIX}."):
        return "sde"
    return "ui"


def translate_type_name(type_id: int | None, fallback: str, lang: str | None = None) -> str:
    if not type_id:
        return fallback
    key = type_translation_key(type_id)
    translated = translate(key, lang)
    return fallback if translated == key else translated


def save_translation(locale: str, key: str, value: str) -> None:
    current_lang = normalize_language(locale)
    if not is_editable_translation_key(key):
        raise RuntimeError("translation key is read-only")
    if not _translation_table_exists():
        raise RuntimeError("translation_entries table does not exist")

    from app.models import TranslationEntry

    with SessionLocal() as db:
        entry = (
            db.query(TranslationEntry)
            .filter(TranslationEntry.locale == current_lang, TranslationEntry.key == key)
            .first()
        )
        if entry is None:
            entry = TranslationEntry(locale=current_lang, key=key, text=value)
            db.add(entry)
        else:
            entry.text = value
        db.commit()
    _invalidate_translations_cache()


def bootstrap_translations() -> int:
    if not _translation_table_exists():
        return 0

    from app.models import TranslationEntry

    seeds = _load_seed_translations()
    inserted = 0
    with SessionLocal() as db:
        for locale, catalog in seeds.items():
            existing = {
                row.key
                for row in db.query(TranslationEntry.key).filter(TranslationEntry.locale == locale).all()
            }
            for key, text in catalog.items():
                if key in existing:
                    continue
                db.add(TranslationEntry(locale=locale, key=key, text=text))
                inserted += 1
        if inserted:
            db.commit()
    if inserted:
        clear_translation_cache()
    return inserted


def reseed_translations() -> dict[str, int]:
    """Upsert all seed-file translations into the DB (insert new, update changed).

    Unlike bootstrap_translations() this also updates existing rows so that
    changes in the JSON files (e.g. after a rename or new locale) propagate to
    the DB without requiring a manual SQL fix.
    Returns a dict with 'inserted' and 'updated' counts.
    """
    if not _translation_table_exists():
        return {"inserted": 0, "updated": 0}

    from app.models import TranslationEntry

    seeds = _load_seed_translations()
    inserted = 0
    updated = 0
    with SessionLocal() as db:
        for locale, catalog in seeds.items():
            existing: dict[str, TranslationEntry] = {
                row.key: row
                for row in db.query(TranslationEntry).filter(TranslationEntry.locale == locale).all()
            }
            for key, text in catalog.items():
                row = existing.get(key)
                if row is None:
                    db.add(TranslationEntry(locale=locale, key=key, text=text))
                    inserted += 1
                elif row.text != text:
                    row.text = text
                    updated += 1
        if inserted or updated:
            db.commit()
    if inserted or updated:
        clear_translation_cache()
    return {"inserted": inserted, "updated": updated}


def bootstrap_pi_type_translations() -> int:
    if not _translation_table_exists():
        return 0

    from app import sde
    from app.market import PI_TYPE_IDS
    from app.models import TranslationEntry
    from app.pi_data import P0_TO_P1, P1_TO_P2, P2_TO_P3, P3_TO_P4

    locale_map = {"de": "de", "en": "en", "zh-Hans": "zh"}
    names = set(P0_TO_P1.keys()) | set(P0_TO_P1.values()) | set(P1_TO_P2.keys()) | set(P2_TO_P3.keys()) | set(P3_TO_P4.keys())
    names |= {item for values in P1_TO_P2.values() for item in values}
    names |= {item for values in P2_TO_P3.values() for item in values}
    names |= {item for values in P3_TO_P4.values() for item in values}

    type_ids: set[int] = set()
    for name in names:
        type_id = PI_TYPE_IDS.get(name) or sde.find_type_id_by_name(name)
        if type_id:
            type_ids.add(type_id)

    inserted = 0
    with SessionLocal() as db:
        existing = {
            (locale, key)
            for locale, key in db.query(TranslationEntry.locale, TranslationEntry.key).all()
        }
        for type_id in sorted(type_ids):
            translations = sde.get_type_translations(type_id)
            if not translations:
                continue
            key = type_translation_key(type_id)
            for locale, sde_locale in locale_map.items():
                text = translations.get(sde_locale) or translations.get("en")
                if not text or (locale, key) in existing:
                    continue
                db.add(TranslationEntry(locale=locale, key=key, text=text))
                existing.add((locale, key))
                inserted += 1
        if inserted:
            db.commit()
    if inserted:
        clear_translation_cache()
    return inserted


def bootstrap_static_planets() -> int:
    if not _translation_table_exists():
        # translation table check is a convenient DB availability guard,
        # but static planet bootstrap should still depend on actual model table existence.
        pass

    try:
        from sqlalchemy import inspect
        from app.database import engine
        if not inspect(engine).has_table("static_planets"):
            return 0
    except Exception:
        return 0

    from app import sde
    from app.models import StaticPlanet

    planets = sde.get_static_planets()
    if not planets:
        return 0

    inserted = 0
    updated = 0
    with SessionLocal() as db:
        existing = {
            row.planet_id: row
            for row in db.query(StaticPlanet).all()
        }
        for planet_id, planet in planets.items():
            row = existing.get(int(planet_id))
            if row is None:
                db.add(StaticPlanet(
                    planet_id=int(planet_id),
                    system_id=int(planet.get("system_id") or 0),
                    planet_name=planet.get("planet_name") or f"Planet {planet_id}",
                    planet_number=str(planet.get("planet_number") or ""),
                    radius=int(planet.get("radius")) if planet.get("radius") is not None else None,
                    x=float(planet.get("x")) if planet.get("x") is not None else None,
                    y=float(planet.get("y")) if planet.get("y") is not None else None,
                    z=float(planet.get("z")) if planet.get("z") is not None else None,
                ))
                inserted += 1
                continue
            changed = False
            for attr, value in (
                ("system_id", int(planet.get("system_id") or 0)),
                ("planet_name", planet.get("planet_name") or f"Planet {planet_id}"),
                ("planet_number", str(planet.get("planet_number") or "")),
                ("radius", int(planet.get("radius")) if planet.get("radius") is not None else None),
                ("x", float(planet.get("x")) if planet.get("x") is not None else None),
                ("y", float(planet.get("y")) if planet.get("y") is not None else None),
                ("z", float(planet.get("z")) if planet.get("z") is not None else None),
            ):
                if getattr(row, attr) != value:
                    setattr(row, attr, value)
                    changed = True
            if changed:
                updated += 1
        if inserted or updated:
            db.commit()
    return inserted + updated


def bootstrap_static_stargates() -> int:
    try:
        from sqlalchemy import inspect
        from app.database import engine
        if not inspect(engine).has_table("static_stargates") or not inspect(engine).has_table("system_gate_distances"):
            return 0
    except Exception:
        return 0

    from app import sde
    from app.models import StaticStargate, SystemGateDistance

    stargates = sde.get_static_stargates()
    gate_distances = sde.get_system_gate_distances()
    if not stargates and not gate_distances:
        return 0

    inserted = 0
    updated = 0
    with SessionLocal() as db:
        existing_gates = {row.gate_id: row for row in db.query(StaticStargate).all()}
        for gate_id, gate in stargates.items():
            row = existing_gates.get(int(gate_id))
            if row is None:
                db.add(StaticStargate(
                    gate_id=int(gate_id),
                    system_id=int(gate.get("system_id") or 0),
                    system_name=str(gate.get("system_name") or f"System {gate.get('system_id') or 0}"),
                    gate_name=str(gate.get("gate_name") or f"Gate {gate_id}"),
                    destination_system_id=int(gate.get("destination_system_id")) if gate.get("destination_system_id") else None,
                    destination_system_name=gate.get("destination_system_name"),
                    x=float(gate.get("x") or 0.0),
                    y=float(gate.get("y") or 0.0),
                    z=float(gate.get("z") or 0.0),
                ))
                inserted += 1
                continue
            changed = False
            values = (
                ("system_id", int(gate.get("system_id") or 0)),
                ("system_name", str(gate.get("system_name") or f"System {gate.get('system_id') or 0}")),
                ("gate_name", str(gate.get("gate_name") or f"Gate {gate_id}")),
                ("destination_system_id", int(gate.get("destination_system_id")) if gate.get("destination_system_id") else None),
                ("destination_system_name", gate.get("destination_system_name")),
                ("x", float(gate.get("x") or 0.0)),
                ("y", float(gate.get("y") or 0.0)),
                ("z", float(gate.get("z") or 0.0)),
            )
            for attr, value in values:
                if getattr(row, attr) != value:
                    setattr(row, attr, value)
                    changed = True
            if changed:
                updated += 1

        existing_distances = {
            (int(row.system_id), int(row.from_system_id), int(row.to_system_id)): row
            for row in db.query(SystemGateDistance).all()
        }
        for key, distance in gate_distances.items():
            row = existing_distances.get((int(key[0]), int(key[1]), int(key[2])))
            if row is None:
                db.add(SystemGateDistance(
                    system_id=int(distance.get("system_id") or 0),
                    system_name=str(distance.get("system_name") or f"System {distance.get('system_id') or 0}"),
                    entry_gate_id=int(distance.get("entry_gate_id") or 0),
                    exit_gate_id=int(distance.get("exit_gate_id") or 0),
                    from_system_id=int(distance.get("from_system_id") or 0),
                    to_system_id=int(distance.get("to_system_id") or 0),
                    from_system_name=str(distance.get("from_system_name") or f"System {distance.get('from_system_id') or 0}"),
                    to_system_name=str(distance.get("to_system_name") or f"System {distance.get('to_system_id') or 0}"),
                    distance_m=float(distance.get("distance_m") or 0.0),
                    distance_au=float(distance.get("distance_au") or 0.0),
                ))
                inserted += 1
                continue
            changed = False
            values = (
                ("system_name", str(distance.get("system_name") or f"System {distance.get('system_id') or 0}")),
                ("entry_gate_id", int(distance.get("entry_gate_id") or 0)),
                ("exit_gate_id", int(distance.get("exit_gate_id") or 0)),
                ("from_system_name", str(distance.get("from_system_name") or f"System {distance.get('from_system_id') or 0}")),
                ("to_system_name", str(distance.get("to_system_name") or f"System {distance.get('to_system_id') or 0}")),
                ("distance_m", float(distance.get("distance_m") or 0.0)),
                ("distance_au", float(distance.get("distance_au") or 0.0)),
            )
            for attr, value in values:
                if getattr(row, attr) != value:
                    setattr(row, attr, value)
                    changed = True
            if changed:
                updated += 1

        if inserted or updated:
            db.commit()
    return inserted + updated


@pass_context
def t(context, key: str, **params) -> str:
    request = context.get("request")
    lang = get_language_from_request(request)
    return translate(key, lang, **params)


@pass_context
def current_lang(context) -> str:
    request = context.get("request")
    return get_language_from_request(request)


@pass_context
def client_i18n(context) -> dict[str, str]:
    request = context.get("request")
    return get_client_catalog(get_language_from_request(request))
