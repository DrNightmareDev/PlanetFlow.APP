import base64
import json
import secrets
import time as _time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

from app.config import get_settings
from app.security import decrypt_text, encrypt_text

settings = get_settings()

# ESI Basis-URLs
ESI_BASE = "https://esi.evetech.net/latest"
SSO_TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
SSO_VERIFY_URLS = (
    "https://login.eveonline.com/oauth/verify",
    "https://login.eveonline.com/v2/oauth/verify",
    "https://esi.evetech.net/verify/",
)
SSO_AUTHORIZE_URL = "https://login.eveonline.com/v2/oauth/authorize"

HEADERS = {"Accept": "application/json", "User-Agent": "PlanetFlow / contact: admin"}


def generate_auth_url(state: str, extra_scopes: list[str] | None = None) -> str:
    base_scopes = settings.eve_scopes.replace(",", " ")
    if extra_scopes:
        scope_str = base_scopes + " " + " ".join(extra_scopes)
    else:
        scope_str = base_scopes
    params = {
        "response_type": "code",
        "redirect_uri": settings.eve_callback_url,
        "client_id": settings.eve_client_id,
        "scope": scope_str,
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
    last_exc: Exception | None = None
    for attempt in range(3):
        if attempt:
            _time.sleep(2 ** attempt)  # 2s, 4s
        try:
            response = requests.post(
                SSO_TOKEN_URL,
                data={"grant_type": "refresh_token", "refresh_token": refresh_token},
                headers={
                    "Authorization": _basic_auth_header(),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=15,
            )
            # 4xx errors are permanent (bad token / revoked) — don't retry
            if response.status_code in (400, 401, 403):
                response.raise_for_status()
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (400, 401, 403):
                raise
            last_exc = exc
        except requests.RequestException as exc:
            last_exc = exc
    raise last_exc  # type: ignore[misc]


def verify_token(access_token: str) -> dict:
    if access_token.count(".") == 2:
        try:
            _, payload_b64, _ = access_token.split(".", 2)
            padding = "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8"))

            subject = payload.get("sub", "")
            character_id = None
            if subject.startswith("CHARACTER:EVE:"):
                character_id = int(subject.rsplit(":", 1)[-1])

            scopes = payload.get("scp", [])
            if isinstance(scopes, list):
                scopes = " ".join(scopes)

            if character_id:
                return {
                    "CharacterID": character_id,
                    "CharacterName": payload.get("name", "Unbekannt"),
                    "Scopes": scopes or "",
                    "TokenType": payload.get("token_type", "Bearer"),
                }
        except Exception:
            pass

    last_error = None
    for verify_url in SSO_VERIFY_URLS:
        try:
            response = requests.get(
                verify_url,
                headers={**HEADERS, "Authorization": f"Bearer {access_token}"},
                timeout=15,
            )
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as exc:
            # Some deployments still answer on older verify URLs.
            # Keep trying until one accepts the token.
            last_error = exc
        except requests.RequestException as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise RuntimeError("Token verification failed without a response")


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
        _update_esi_error_limit(response)
        response.raise_for_status()
        return response.json()
    except Exception:
        return []


def get_character_roles(character_id: int, access_token: str) -> dict:
    try:
        response = requests.get(
            f"{ESI_BASE}/characters/{character_id}/roles/",
            params={"datasource": "tranquility"},
            headers={**HEADERS, "Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return {}


def get_character_skills(character_id: int, access_token: str) -> dict:
    try:
        response = requests.get(
            f"{ESI_BASE}/characters/{character_id}/skills/",
            params={"datasource": "tranquility"},
            headers={**HEADERS, "Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return {}


def get_character_fittings(character_id: int, access_token: str) -> list:
    response = requests.get(
        f"{ESI_BASE}/characters/{character_id}/fittings/",
        params={"datasource": "tranquility"},
        headers={**HEADERS, "Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def get_character_location(character_id: int, token: str) -> dict:
    response = requests.get(
        f"{ESI_BASE}/characters/{character_id}/location/",
        params={"datasource": "tranquility"},
        headers={**HEADERS, "Authorization": f"Bearer {token}"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def send_character_mail(
    sender_character_id: int,
    access_token: str,
    *,
    recipient_character_id: int,
    subject: str,
    body: str,
) -> int | None:
    """Send an in-game mail from one character to another."""
    payload = {
        "approved_cost": 0,
        "subject": subject[:255],
        "body": body[:8000],
        "recipients": [{
            "recipient_id": int(recipient_character_id),
            "recipient_type": "character",
        }],
    }
    response = requests.post(
        f"{ESI_BASE}/characters/{sender_character_id}/mail/",
        params={"datasource": "tranquility"},
        json=payload,
        headers={**HEADERS, "Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    response.raise_for_status()
    try:
        return int(response.json())
    except Exception:
        return None


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


def universe_names(ids: list[int]) -> list[dict]:
    if not ids:
        return []
    try:
        response = requests.post(
            f"{ESI_BASE}/universe/names/",
            json=ids,
            params={"datasource": "tranquility"},
            headers=HEADERS,
            timeout=20,
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return []


def get_sovereignty_map() -> list[dict]:
    """Return list of {system_id, alliance_id, corporation_id, faction_id} for all sov systems."""
    try:
        response = requests.get(
            f"{ESI_BASE}/sovereignty/map/",
            params={"datasource": "tranquility"},
            headers=HEADERS,
            timeout=20,
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return []


def get_sovereignty_structures() -> list[dict]:
    """Return list of IHub sovereignty structures with vulnerability windows.
    Fields: alliance_id, solar_system_id, structure_id, structure_type_id,
            vulnerability_occupancy_level, vulnerable_end_time, vulnerable_start_time
    """
    try:
        response = requests.get(
            f"{ESI_BASE}/sovereignty/structures/",
            params={"datasource": "tranquility"},
            headers=HEADERS,
            timeout=20,
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return []


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


def get_constellation_info(constellation_id: int) -> dict:
    try:
        response = requests.get(
            f"{ESI_BASE}/universe/constellations/{constellation_id}/",
            params={"datasource": "tranquility"},
            headers=HEADERS,
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return {}


def get_killmail(killmail_id: int, killmail_hash: str) -> dict:
    """Fetch a public killmail from ESI without auth."""
    if not killmail_id or not killmail_hash:
        return {}
    try:
        response = requests.get(
            f"{ESI_BASE}/killmails/{int(killmail_id)}/{killmail_hash}/",
            params={"datasource": "tranquility"},
            headers=HEADERS,
            timeout=15,
        )
        _update_esi_error_limit(response)
        response.raise_for_status()
        return response.json()
    except Exception:
        return {}


def get_planet_info(planet_id: int) -> dict:
    entry = _planet_info_cache.get(planet_id)
    if entry is not None:
        data, ts = entry
        if _time.time() - ts < _PLANET_INFO_CACHE_TTL:
            return data
    try:
        response = requests.get(
            f"{ESI_BASE}/universe/planets/{planet_id}/",
            params={"datasource": "tranquility"},
            headers=HEADERS,
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        _planet_info_cache[planet_id] = (data, _time.time())
        return data
    except Exception:
        return {}


_planet_info_cache: dict[int, tuple[dict, float]] = {}  # planet_id -> (data, timestamp)
_PLANET_INFO_CACHE_TTL = 86400  # 24h — planet type/name never changes in practice
_planet_detail_cache = {}  # (char_id, planet_id) -> (data, timestamp)
PLANET_DETAIL_TTL = 600  # 10 Minuten (entspricht ESI-Cache von CCP)

# ESI error-limit tracking — back off when the shared error budget is low
_esi_error_limit_remain: int = 100  # start optimistic; updated from response headers
_ESI_ERROR_LIMIT_BACKOFF_THRESHOLD = 20  # pause if fewer than 20 errors remain

import logging as _esi_log
_esi_logger = _esi_log.getLogger(__name__)


def _update_esi_error_limit(response) -> None:
    """Update the shared ESI error limit counter from a response object."""
    global _esi_error_limit_remain
    try:
        remain = int(response.headers.get("X-ESI-Error-Limit-Remain", _esi_error_limit_remain))
        _esi_error_limit_remain = remain
        if remain < _ESI_ERROR_LIMIT_BACKOFF_THRESHOLD:
            import time as _t
            _esi_logger.warning("ESI error limit low (%d remain) — sleeping 10s", remain)
            _t.sleep(10)
    except Exception:
        pass


def esi_error_budget_ok() -> bool:
    """Return False when ESI error budget is critically low and calls should be deferred."""
    return _esi_error_limit_remain >= _ESI_ERROR_LIMIT_BACKOFF_THRESHOLD


def invalidate_planet_detail_cache(character_id: int) -> None:
    """Löscht alle gecachten Planet-Details eines Charakters."""
    keys = [k for k in _planet_detail_cache if k[0] == character_id]
    for k in keys:
        del _planet_detail_cache[k]

_schematic_cache = {}  # schematic_id -> data


def get_planet_detail_cached(
    character_id: int,
    planet_id: int,
    access_token: str,
    etag: str | None,
    cached_json: str | None,
) -> tuple[dict, str | None, bool]:
    """ETag-aware planet detail fetch — NO DB access, fully thread-safe.

    Returns (data, new_etag, changed) where:
      - data        : parsed planet detail dict (may be from cached_json on 304)
      - new_etag    : ETag to store (None if unchanged)
      - changed     : True when the server returned fresh data (200), False on 304
    The caller is responsible for persisting the new ETag + data to the DB.
    """
    import json as _json

    req_headers = {**HEADERS, "Authorization": f"Bearer {access_token}"}
    if etag:
        req_headers["If-None-Match"] = etag

    try:
        response = requests.get(
            f"{ESI_BASE}/characters/{character_id}/planets/{planet_id}/",
            headers=req_headers,
            params={"datasource": "tranquility"},
            timeout=15,
        )

        _update_esi_error_limit(response)
        if response.status_code == 304:
            data = _json.loads(cached_json or "{}") if cached_json else {}
            return data, None, False

        response.raise_for_status()
        data = response.json()
        new_etag = response.headers.get("ETag", "").strip('"') or None
        _planet_detail_cache[(character_id, planet_id)] = (data, _time.time())
        return data, new_etag, True

    except Exception:
        # Fall back to cached data silently
        data = _json.loads(cached_json or "{}") if cached_json else {}
        return data, None, False


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
    access_token = decrypt_text(character.access_token)
    refresh_token = decrypt_text(character.refresh_token)
    if not character.token_expires_at or not refresh_token:
        return access_token

    now = datetime.now(timezone.utc)
    expires_at = character.token_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if now >= expires_at:
        try:
            token_data = refresh_access_token(refresh_token)
            character.access_token = encrypt_text(token_data["access_token"])
            character.refresh_token = encrypt_text(token_data.get("refresh_token", refresh_token))
            expires_in = token_data.get("expires_in", 1200)
            from datetime import timedelta
            character.token_expires_at = now + timedelta(seconds=expires_in)
            db.commit()
            access_token = decrypt_text(character.access_token)
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "Token refresh failed for character %s (%s): %s",
                character.character_name, character.eve_character_id, exc,
            )
            # Track the failure so the UI and auto-retry logic see it
            character.esi_consecutive_errors = (character.esi_consecutive_errors or 0) + 1
            try:
                db.commit()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
            return None

    return access_token
