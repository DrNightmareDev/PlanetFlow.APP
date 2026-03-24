import json
import logging
from functools import lru_cache
from pathlib import Path

from jinja2 import pass_context
from sqlalchemy import inspect

from app.database import SessionLocal, engine

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
DEFAULT_LANGUAGE = "de"
SUPPORTED_LANGUAGES = ("de", "en", "zh-Hans")

logger = logging.getLogger(__name__)

TYPE_TRANSLATION_PREFIX = "type"


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


@lru_cache(maxsize=1)
def load_translations() -> dict[str, dict[str, str]]:
    seeds = _load_seed_translations()
    if not _translation_table_exists():
        return seeds

    try:
        from app.models import TranslationEntry

        catalogs: dict[str, dict[str, str]] = {lang: {} for lang in SUPPORTED_LANGUAGES}
        with SessionLocal() as db:
            rows = db.query(TranslationEntry).all()
        if not rows:
            return seeds
        for row in rows:
            if row.locale in catalogs:
                catalogs[row.locale][row.key] = row.text or ""
        return catalogs
    except Exception as exc:
        logger.warning("Translation load from DB failed, using seed files: %s", exc)
        return seeds


def clear_translation_cache() -> None:
    load_translations.cache_clear()


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
    text = catalog.get(key) or catalogs.get(DEFAULT_LANGUAGE, {}).get(key) or key
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
    clear_translation_cache()


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
