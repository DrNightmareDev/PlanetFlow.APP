import base64
import secrets
import time as _time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

from app.config import get_settings

settings = get_settings()

# ESI Basis-URLs
ESI_BASE = "https://esi.evetech.net/latest"
SSO_TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
SSO_VERIFY_URL = "https://esi.evetech.net/verify/"
SSO_AUTHORIZE_URL = "https://login.eveonline.com/v2/oauth/authorize"

HEADERS = {"Accept": "application/json", "User-Agent": "EVE PI Manager / contact: admin"}


def generate_auth_url(state: str) -> str:
    params = {
        "response_type": "code",
        "redirect_uri": settings.eve_callback_url,
        "client_id": settings.eve_client_id,
        "scope": settings.eve_scopes.replace(",", " "),  # EVE SSO erwartet Leerzeichen
        "state": state,
    }
    return f"{SSO_AUTHORIZE_URL}?{urlencode(params)}"


def _basic_auth_header() -> str:
    credentials = f"{settings.eve_client_id}:{settings.eve_client_secret}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return f"Basic {encoded}"


def exchange_code_for_tokens(code: str) -> dict:
    response = requests.post(
        SSO_TOKEN_URL,
        data={"grant_type": "authorization_code", "code": code},
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def refresh_access_token(refresh_token: str) -> dict:
    response = requests.post(
        SSO_TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        headers={
            "Authorization": _basic_auth_header(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def verify_token(access_token: str) -> dict:
    response = requests.get(
        SSO_VERIFY_URL,
        headers={**HEADERS, "Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def get_character_info(character_id: int) -> dict:
    response = requests.get(
        f"{ESI_BASE}/characters/{character_id}/",
        params={"datasource": "tranquility"},
        headers=HEADERS,
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def get_corporation_info(corporation_id: int) -> dict:
    response = requests.get(
        f"{ESI_BASE}/corporations/{corporation_id}/",
        params={"datasource": "tranquility"},
        headers=HEADERS,
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def get_alliance_info(alliance_id: int) -> dict:
    response = requests.get(
        f"{ESI_BASE}/alliances/{alliance_id}/",
        params={"datasource": "tranquility"},
        headers=HEADERS,
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def get_character_planets(character_id: int, access_token: str) -> list:
    try:
        response = requests.get(
            f"{ESI_BASE}/characters/{character_id}/planets/",
            params={"datasource": "tranquility"},
            headers={**HEADERS, "Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return []


def search_entities(character_id: int, access_token: str, query: str) -> dict:
    """Sucht Corporations und Allianzen via ESI character search."""
    try:
        response = requests.get(
            f"{ESI_BASE}/characters/{character_id}/search/",
            params={
                "categories": "corporation,alliance",
                "search": query,
                "datasource": "tranquility",
            },
            headers={**HEADERS, "Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return {}


def universe_ids(names: list[str]) -> dict:
    """Löst exakte EVE-Namen zu IDs auf (kein Auth erforderlich)."""
    try:
        response = requests.post(
            f"{ESI_BASE}/universe/ids/",
            json=names,
            params={"datasource": "tranquility", "language": "en"},
            headers=HEADERS,
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return {}


def search_systems(query: str) -> dict:
    """Deprecated: ESI /search/ removed. Use search_systems_auth instead."""
    return {}


def search_systems_auth(character_id: int, access_token: str, query: str) -> dict:
    """Sucht Systeme via ESI character search (erfordert Auth-Token)."""
    try:
        response = requests.get(
            f"{ESI_BASE}/characters/{character_id}/search/",
            params={
                "categories": "solar_system",
                "search": query,
                "datasource": "tranquility",
            },
            headers={**HEADERS, "Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return {}


def get_system_info(system_id: int) -> dict:
    try:
        response = requests.get(
            f"{ESI_BASE}/universe/systems/{system_id}/",
            params={"datasource": "tranquility"},
            headers=HEADERS,
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return {}


def get_planet_info(planet_id: int) -> dict:
    if planet_id in _planet_info_cache:
        return _planet_info_cache[planet_id]
    try:
        response = requests.get(
            f"{ESI_BASE}/universe/planets/{planet_id}/",
            params={"datasource": "tranquility"},
            headers=HEADERS,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        _planet_info_cache[planet_id] = data
        return data
    except Exception:
        return {}


_planet_info_cache: dict[int, dict] = {}  # planet_id -> data (permanent)
_planet_detail_cache = {}  # (char_id, planet_id) -> (data, timestamp)
PLANET_DETAIL_TTL = 600  # 10 Minuten (entspricht ESI-Cache von CCP)


def invalidate_planet_detail_cache(character_id: int) -> None:
    """Löscht alle gecachten Planet-Details eines Charakters."""
    keys = [k for k in _planet_detail_cache if k[0] == character_id]
    for k in keys:
        del _planet_detail_cache[k]

_schematic_cache = {}  # schematic_id -> data


def get_planet_detail(character_id: int, planet_id: int, access_token: str) -> dict:
    """Holt Planet-Details (Pins, Links, Routen) mit Cache."""
    cache_key = (character_id, planet_id)
    now = _time.time()
    if cache_key in _planet_detail_cache:
        data, ts = _planet_detail_cache[cache_key]
        if now - ts < PLANET_DETAIL_TTL:
            return data
    try:
        response = requests.get(
            f"{ESI_BASE}/characters/{character_id}/planets/{planet_id}/",
            headers={**HEADERS, "Authorization": f"Bearer {access_token}"},
            params={"datasource": "tranquility"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        _planet_detail_cache[cache_key] = (data, now)
        return data
    except Exception:
        return {}


def get_schematic(schematic_id: int) -> dict:
    """Gibt Schematic-Daten zurück: SDE bevorzugt, ESI als Fallback."""
    from app import sde
    local = sde.get_schematic(schematic_id)
    if local:
        return local
    # Fallback: ESI (nur wenn SDE nicht geladen)
    if schematic_id in _schematic_cache:
        return _schematic_cache[schematic_id]
    try:
        response = requests.get(
            f"{ESI_BASE}/universe/schematics/{schematic_id}/",
            headers=HEADERS,
            params={"datasource": "tranquility"},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        _schematic_cache[schematic_id] = data
        return data
    except Exception:
        return {}


def ensure_valid_token(character, db) -> str | None:
    """Prüft ob Token gültig ist und aktualisiert ihn ggf."""
    if not character.token_expires_at or not character.refresh_token:
        return character.access_token

    now = datetime.now(timezone.utc)
    expires_at = character.token_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if now >= expires_at:
        try:
            token_data = refresh_access_token(character.refresh_token)
            character.access_token = token_data["access_token"]
            character.refresh_token = token_data.get("refresh_token", character.refresh_token)
            expires_in = token_data.get("expires_in", 1200)
            from datetime import timedelta
            character.token_expires_at = now + timedelta(seconds=expires_in)
            db.commit()
        except Exception:
            return character.access_token

    return character.access_token
