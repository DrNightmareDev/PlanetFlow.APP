from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Account
from app.session import read_session


def get_current_account(request: Request, db: Session = Depends(get_db)) -> Account | None:
    session = read_session(request)
    if not session:
        return None
    account_id = session.get("account_id")
    if not account_id:
        return None
    # Eager-load characters so that Account.is_owner (property) can check them
    account = (
        db.query(Account)
        .options(joinedload(Account.characters))
        .filter(Account.id == account_id)
        .first()
    )
    return account


def require_account(request: Request, db: Session = Depends(get_db)) -> Account:
    account = get_current_account(request, db)
    if account is None:
        raise HTTPException(
            status_code=303,
            headers={"Location": "/"},
            detail="Nicht angemeldet",
        )
    return account


def require_admin(request: Request, db: Session = Depends(get_db)) -> Account:
    """Requires is_admin or is_owner."""
    account = require_account(request, db)
    if not (account.is_admin or account.is_owner):
        raise HTTPException(status_code=403, detail="Zugriff verweigert - Admin-Rechte erforderlich")
    return account


# Legacy alias used in hauling and other routers
require_manager_or_admin = require_admin


def require_owner(request: Request, db: Session = Depends(get_db)) -> Account:
    """Requires the account to be the configured owner (EVE_OWNER_CHARACTER_ID)."""
    account = require_account(request, db)
    if not account.is_owner:
        raise HTTPException(status_code=403, detail="Zugriff verweigert - Owner erforderlich")
    return account


def require_director(request: Request, db: Session = Depends(get_db)) -> Account:
    """Requires is_director, is_corp_manager, is_fc DB flag OR CEO of their corp (detected via ESI)."""
    account = require_account(request, db)
    if account.is_director or account.is_corp_manager or account.is_fc:
        return account
    # Also allow CEOs — check via the cached corp access flags
    from app.models import Character
    from app.esi import get_corporation_info
    main_char = db.query(Character).filter(Character.id == account.main_character_id).first() if account.main_character_id else None
    all_chars = db.query(Character).filter(Character.account_id == account.id).all()
    corp_id = main_char.corporation_id if main_char else None
    if corp_id:
        try:
            corp_info = get_corporation_info(corp_id)
            ceo_id = corp_info.get("ceo_id")
            if ceo_id and any(c.eve_character_id == ceo_id for c in all_chars):
                return account
        except Exception:
            pass
    raise HTTPException(status_code=403, detail="Zugriff verweigert - Director-Rechte erforderlich")
