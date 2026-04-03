import logging
import secrets
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_account, require_account
from app.esi import (
    generate_auth_url, exchange_code_for_tokens,
    verify_token, get_character_info, get_corporation_info, get_alliance_info
)
from app.models import Account, Character, SSOState, AccessPolicy
from app.security import encrypt_text, rate_limit_auth
from app.session import clear_session, create_session, validate_csrf

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)
settings = get_settings()
_RECEIVER_REQUIRED_SCOPES = {
    "esi-wallet.read_character_wallet.v1",
    "esi-mail.send_mail.v1",
}


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
def login(request: Request, db: Session = Depends(get_db)):
    rate_limit_auth(request, "login")
    state = _generate_state(db, flow="login")
    redirect_url = generate_auth_url(state)
    return RedirectResponse(url=redirect_url)


@router.get("/add-character")
def add_character(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db)
):
    rate_limit_auth(request, "add_character")
    state = _generate_state(db, flow="add_character", account_id=account.id)
    redirect_url = generate_auth_url(state)
    return RedirectResponse(url=redirect_url)


@router.get("/refresh-scopes")
def refresh_scopes(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db)
):
    rate_limit_auth(request, "refresh_scopes")
    state = _generate_state(db, flow="add_character", account_id=account.id)
    redirect_url = generate_auth_url(state)
    return RedirectResponse(url=redirect_url)


@router.get("/wallet-receiver")
def wallet_receiver_login(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    """Start SSO flow for billing receivers (wallet + mail scope)."""
    from app.dependencies import require_admin
    require_admin(request, db)
    rate_limit_auth(request, "add_character")
    state = _generate_state(db, flow="wallet_receiver", account_id=account.id)
    redirect_url = generate_auth_url(
        state,
        extra_scopes=[
            "esi-wallet.read_character_wallet.v1",
            "esi-mail.send_mail.v1",
        ],
    )
    return RedirectResponse(url=redirect_url)


@router.get("/callback")
def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db)
):
    rate_limit_auth(request, "callback")
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
    granted_scopes = {s.strip() for s in (scopes or "").split() if s.strip()}

    if flow == "wallet_receiver":
        missing = sorted(_RECEIVER_REQUIRED_SCOPES - granted_scopes)
        if missing:
            logger.warning(
                "auth callback: wallet_receiver missing required scopes for char %s (%s): %s",
                character_name, eve_character_id, ",".join(missing),
            )
            db.delete(sso_state)
            db.commit()
            return RedirectResponse(url="/admin/billing?msg=receiver_scope_missing", status_code=302)

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
        existing_char.access_token = encrypt_text(access_token)
        existing_char.refresh_token = encrypt_text(refresh_token)
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
        if flow == "wallet_receiver" and existing_account_id:
            from app.models import BillingWalletReceiver
            receiver = db.query(BillingWalletReceiver).filter(
                BillingWalletReceiver.eve_character_id == eve_character_id
            ).first()
            if receiver:
                receiver.character_name = character_name
                receiver.character_fk = existing_char.id
                receiver.is_active = True
            else:
                db.add(BillingWalletReceiver(
                    eve_character_id=eve_character_id,
                    character_name=character_name,
                    character_fk=existing_char.id,
                    is_active=True,
                ))
            db.commit()
            create_session(response, existing_account_id)
            return RedirectResponse(url="/admin/billing?msg=receiver_added", status_code=302)

        if flow == "login":
            from sqlalchemy.orm import joinedload as _jl
            acc = db.query(Account).options(_jl(Account.characters)).filter(Account.id == existing_char.account_id).first()
            if not (acc and acc.is_owner) and not _check_access_policy(db, corporation_id, alliance_id):
                return RedirectResponse(url="/?error=access_denied", status_code=302)

        if flow == "add_character" and existing_account_id:
            if not _check_access_policy(db, corporation_id, alliance_id):
                return RedirectResponse(url="/dashboard/characters?error=access_denied", status_code=302)
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
        if is_first and eve_character_id != settings.eve_owner_character_id:
            return RedirectResponse(url="/?error=owner_login_required", status_code=302)

        # Zugangspolitik prÃƒÂ¼fen (nicht fÃƒÂ¼r den ersten Account / Besitzer)
        if not is_first and not _check_access_policy(db, corporation_id, alliance_id):
            return RedirectResponse(url="/?error=access_denied", status_code=302)

        new_account = Account(is_admin=is_first)
        db.add(new_account)
        db.flush()

        new_char = Character(
            eve_character_id=eve_character_id,
            character_name=character_name,
            corporation_id=corporation_id,
            corporation_name=corporation_name,
            alliance_id=alliance_id,
            alliance_name=alliance_name,
            access_token=encrypt_text(access_token),
            refresh_token=encrypt_text(refresh_token),
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

    elif flow == "wallet_receiver" and existing_account_id:
        # Register/update this character as a billing wallet receiver
        from app.models import BillingWalletReceiver

        # Upsert the character record so token is stored
        if existing_char:
            existing_char.scopes = scopes
            existing_char.access_token = encrypt_text(access_token)
            existing_char.refresh_token = encrypt_text(refresh_token)
            existing_char.token_expires_at = token_expires_at
            char_db_id = existing_char.id
        else:
            new_char = Character(
                eve_character_id=eve_character_id,
                character_name=character_name,
                corporation_id=corporation_id,
                corporation_name=corporation_name,
                alliance_id=alliance_id,
                alliance_name=alliance_name,
                access_token=encrypt_text(access_token),
                refresh_token=encrypt_text(refresh_token),
                token_expires_at=token_expires_at,
                scopes=scopes,
                portrait_64=portrait_64,
                portrait_128=portrait_128,
                portrait_256=portrait_256,
                account_id=existing_account_id,
                last_login=datetime.now(timezone.utc),
            )
            db.add(new_char)
            db.flush()
            char_db_id = new_char.id

        # Upsert BillingWalletReceiver
        receiver = db.query(BillingWalletReceiver).filter(
            BillingWalletReceiver.eve_character_id == eve_character_id
        ).first()
        if receiver:
            receiver.character_name = character_name
            receiver.character_fk = char_db_id
            receiver.is_active = True
        else:
            db.add(BillingWalletReceiver(
                eve_character_id=eve_character_id,
                character_name=character_name,
                character_fk=char_db_id,
                is_active=True,
            ))
        db.commit()
        create_session(response, existing_account_id)
        return RedirectResponse(url="/admin/billing?msg=receiver_added", status_code=302)

    elif flow == "add_character" and existing_account_id:
        if not _check_access_policy(db, corporation_id, alliance_id):
            return RedirectResponse(url="/dashboard/characters?error=access_denied", status_code=302)
        # Alt hinzufügen
        new_char = Character(
            eve_character_id=eve_character_id,
            character_name=character_name,
            corporation_id=corporation_id,
            corporation_name=corporation_name,
            alliance_id=alliance_id,
            alliance_name=alliance_name,
            access_token=encrypt_text(access_token),
            refresh_token=encrypt_text(refresh_token),
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



@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    validate_csrf(request, csrf_token)
    response = RedirectResponse(url="/", status_code=302)
    clear_session(response)
    return response


@router.post("/set-main/{character_id}")
def set_main(
    character_id: int,
    request: Request,
    csrf_token: str = Form(...),
    account=Depends(require_account),
    db: Session = Depends(get_db)
):
    validate_csrf(request, csrf_token)
    char = db.query(Character).filter(
        Character.id == character_id,
        Character.account_id == account.id
    ).first()
    if not char:
        raise HTTPException(status_code=404, detail="Charakter nicht gefunden")

    account.main_character_id = char.id
    db.commit()
    return RedirectResponse(url="/dashboard/characters", status_code=302)


@router.post("/remove-character/{character_id}")
def remove_character(
    character_id: int,
    request: Request,
    csrf_token: str = Form(...),
    account=Depends(require_account),
    db: Session = Depends(get_db)
):
    validate_csrf(request, csrf_token)
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
