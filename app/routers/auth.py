import logging
import secrets
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_account, require_account
from app.esi import (
    generate_auth_url, exchange_code_for_tokens,
    verify_token, get_character_info, get_corporation_info, get_alliance_info
)
from app.models import Account, Character, SSOState, AccessPolicy
from app.session import create_session, clear_session

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


def _check_access_policy(db: Session, corporation_id, alliance_id) -> bool:
    """Gibt True zurÃƒÂ¼ck wenn Zugang erlaubt, False wenn verweigert."""
    policy = db.get(AccessPolicy, 1)
    if policy is None or policy.mode == "open":
        return True

    corp_ids     = {e.entity_id for e in policy.entries if e.entity_type == "corporation"}
    alliance_ids = {e.entity_id for e in policy.entries if e.entity_type == "alliance"}

    if policy.mode == "allowlist":
        return bool(
            (corporation_id and corporation_id in corp_ids) or
            (alliance_id and alliance_id in alliance_ids)
        )
    if policy.mode == "blocklist":
        return not bool(
            (corporation_id and corporation_id in corp_ids) or
            (alliance_id and alliance_id in alliance_ids)
        )
    return True


def _invalidate_account_dashboard_state(account_id: int, db: Session) -> None:
    from app.esi import invalidate_planet_detail_cache
    from app.models import DashboardCache
    from app.routers.dashboard import invalidate_dashboard_cache

    invalidate_dashboard_cache(account_id)
    db.query(DashboardCache).filter(DashboardCache.account_id == account_id).delete()
    for char in db.query(Character).filter(Character.account_id == account_id).all():
        invalidate_planet_detail_cache(char.eve_character_id)


def _generate_state(db: Session, flow: str, account_id: int | None = None) -> str:
    state = secrets.token_urlsafe(32)
    sso_state = SSOState(state=state, flow=flow, account_id=account_id)
    db.add(sso_state)
    db.commit()
    return state


@router.get("/login")
def login(db: Session = Depends(get_db)):
    state = _generate_state(db, flow="login")
    redirect_url = generate_auth_url(state)
    return RedirectResponse(url=redirect_url)


@router.get("/add-character")
def add_character(
    account=Depends(require_account),
    db: Session = Depends(get_db)
):
    state = _generate_state(db, flow="add_character", account_id=account.id)
    redirect_url = generate_auth_url(state)
    return RedirectResponse(url=redirect_url)


@router.get("/refresh-scopes")
def refresh_scopes(
    account=Depends(require_account),
    db: Session = Depends(get_db)
):
    state = _generate_state(db, flow="add_character", account_id=account.id)
    redirect_url = generate_auth_url(state)
    return RedirectResponse(url=redirect_url)


@router.get("/callback")
def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db)
):
    if not code or not state:
        raise HTTPException(status_code=400, detail="Fehlende Parameter")

    # State validieren (CSRF-Schutz)
    sso_state = db.query(SSOState).filter(SSOState.state == state).first()
    if not sso_state:
        raise HTTPException(status_code=400, detail="Ungueltiger State - moeglicher CSRF-Angriff")

    flow = sso_state.flow
    existing_account_id = sso_state.account_id
    # Code gegen Token tauschen
    try:
        token_data = exchange_code_for_tokens(code)
    except Exception as e:
        logger.warning("auth callback: token exchange failed for state=%s: %s", state, e)
        raise HTTPException(status_code=502, detail="Token-Austausch fehlgeschlagen. Bitte erneut versuchen.")

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 1200)
    token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    # Token verifizieren
    try:
        verified = verify_token(access_token)
    except Exception as e:
        logger.warning("auth callback: token verification failed: %s", e)
        raise HTTPException(status_code=502, detail="Token-Verifizierung fehlgeschlagen. Bitte erneut versuchen.")

    eve_character_id = verified.get("CharacterID")
    character_name = verified.get("CharacterName", "Unbekannt")
    scopes = verified.get("Scopes", "")

    # State erst nach erfolgreicher Verifikation verbrauchen.
    db.delete(sso_state)
    db.commit()
    # Charakterinfo von ESI holen
    corporation_id = None
    corporation_name = None
    alliance_id = None
    alliance_name = None

    try:
        char_info = get_character_info(eve_character_id)
        corporation_id = char_info.get("corporation_id")
        if corporation_id:
            corp_info = get_corporation_info(corporation_id)
            corporation_name = corp_info.get("name")
            alliance_id = char_info.get("alliance_id")
            if alliance_id:
                alliance_info = get_alliance_info(alliance_id)
                alliance_name = alliance_info.get("name")
    except Exception:
        pass  # Nicht fatal

    # Portrait URLs
    portrait_128 = f"https://images.evetech.net/characters/{eve_character_id}/portrait?size=128"
    portrait_256 = f"https://images.evetech.net/characters/{eve_character_id}/portrait?size=256"
    portrait_64 = f"https://images.evetech.net/characters/{eve_character_id}/portrait?size=64"

    # Charakter in DB suchen
    existing_char = db.query(Character).filter(
        Character.eve_character_id == eve_character_id
    ).first()

    response = RedirectResponse(url="/dashboard", status_code=302)

    if existing_char:
        old_account_id = existing_char.account_id
        # Tokens aktualisieren
        existing_char.access_token = access_token
        existing_char.refresh_token = refresh_token
        existing_char.token_expires_at = token_expires_at
        existing_char.scopes = scopes
        existing_char.last_login = datetime.now(timezone.utc)
        existing_char.character_name = character_name
        existing_char.corporation_id = corporation_id
        existing_char.corporation_name = corporation_name
        existing_char.alliance_id = alliance_id
        existing_char.alliance_name = alliance_name
        db.commit()

        # Zugangspolitik auch fÃƒÂ¼r bestehende Charaktere prÃƒÂ¼fen (corp/allianz kann sich geÃƒÂ¤ndert haben)
        # Ausnahmen: Owner immer erlaubt, add_character-Flow (bereits eingeloggt)
        if flow == "login":
            acc = db.get(Account, existing_char.account_id)
            if not (acc and acc.is_owner) and not _check_access_policy(db, corporation_id, alliance_id):
                return RedirectResponse(url="/?error=access_denied", status_code=302)

        if flow == "add_character" and existing_account_id:
            existing_char.account_id = existing_account_id
            target_account = db.get(Account, existing_account_id)
            if target_account and not target_account.main_character_id:
                target_account.main_character_id = existing_char.id
            db.commit()
            _invalidate_account_dashboard_state(existing_account_id, db)
            if old_account_id != existing_account_id:
                _invalidate_account_dashboard_state(old_account_id, db)
            db.commit()
            create_session(response, existing_account_id)
            return response

        create_session(response, existing_char.account_id)
        return response

    # Neuer Charakter
    if flow == "login":
        # Neuen Account erstellen
        total_accounts = db.query(Account).count()
        is_first = total_accounts == 0

        # Zugangspolitik prÃƒÂ¼fen (nicht fÃƒÂ¼r den ersten Account / Besitzer)
        if not is_first and not _check_access_policy(db, corporation_id, alliance_id):
            return RedirectResponse(url="/?error=access_denied", status_code=302)

        new_account = Account(is_admin=is_first, is_owner=is_first)
        db.add(new_account)
        db.flush()

        new_char = Character(
            eve_character_id=eve_character_id,
            character_name=character_name,
            corporation_id=corporation_id,
            corporation_name=corporation_name,
            alliance_id=alliance_id,
            alliance_name=alliance_name,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=token_expires_at,
            scopes=scopes,
            portrait_64=portrait_64,
            portrait_128=portrait_128,
            portrait_256=portrait_256,
            account_id=new_account.id,
            last_login=datetime.now(timezone.utc),
        )
        db.add(new_char)
        db.flush()

        new_account.main_character_id = new_char.id
        db.commit()

        create_session(response, new_account.id)

    elif flow == "add_character" and existing_account_id:
        # Alt hinzufÃƒÂ¼gen
        new_char = Character(
            eve_character_id=eve_character_id,
            character_name=character_name,
            corporation_id=corporation_id,
            corporation_name=corporation_name,
            alliance_id=alliance_id,
            alliance_name=alliance_name,
            access_token=access_token,
            refresh_token=refresh_token,
            token_expires_at=token_expires_at,
            scopes=scopes,
            portrait_64=portrait_64,
            portrait_128=portrait_128,
            portrait_256=portrait_256,
            account_id=existing_account_id,
            last_login=datetime.now(timezone.utc),
        )
        db.add(new_char)
        db.commit()

        # Dashboard- und Planetencache invalidieren damit neue Kolonien sofort erscheinen
        _invalidate_account_dashboard_state(existing_account_id, db)
        db.commit()

        # Trigger a background refresh so colonies appear without waiting for beat.
        # Small countdown avoids racing with any task already in-flight for this account.
        try:
            from app.tasks import refresh_account_task
            refresh_account_task.apply_async((existing_account_id,), countdown=3)
        except Exception:
            pass  # Celery not available — beat will pick it up within 5 min

        create_session(response, existing_account_id)
    else:
        raise HTTPException(status_code=400, detail="Unbekannter Flow")

    return response


@router.get("/become-admin")
def become_admin(
    account=Depends(require_account),
    db: Session = Depends(get_db)
):
    """Nur fuer den Owner: stellt Admin-Rechte wieder her."""
    if not account.is_owner:
        raise HTTPException(status_code=403, detail="Nur der Owner kann diesen Endpunkt nutzen")
    account.is_admin = True
    db.commit()
    return RedirectResponse(url="/dashboard", status_code=302)


@router.get("/logout")
def logout():
    response = RedirectResponse(url="/", status_code=302)
    clear_session(response)
    return response


@router.get("/set-main/{character_id}")
def set_main(
    character_id: int,
    account=Depends(require_account),
    db: Session = Depends(get_db)
):
    char = db.query(Character).filter(
        Character.id == character_id,
        Character.account_id == account.id
    ).first()
    if not char:
        raise HTTPException(status_code=404, detail="Charakter nicht gefunden")

    account.main_character_id = char.id
    db.commit()
    return RedirectResponse(url="/dashboard/characters", status_code=302)


@router.get("/remove-character/{character_id}")
def remove_character(
    character_id: int,
    account=Depends(require_account),
    db: Session = Depends(get_db)
):
    char = db.query(Character).filter(
        Character.id == character_id,
        Character.account_id == account.id
    ).first()
    if not char:
        raise HTTPException(status_code=404, detail="Charakter nicht gefunden")

    was_main = account.main_character_id == char.id

    # Main-Referenz entfernen falls nÃƒÂ¶tig
    if was_main:
        account.main_character_id = None
        db.flush()

    db.delete(char)
    db.flush()

    # Neuen Main setzen falls vorhanden
    if was_main:
        remaining = db.query(Character).filter(
            Character.account_id == account.id
        ).first()
        if remaining:
            account.main_character_id = remaining.id

    db.commit()
    return RedirectResponse(url="/dashboard/characters", status_code=302)
