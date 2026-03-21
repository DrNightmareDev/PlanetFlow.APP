from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin, require_account
from app.models import Account, Character
from app.templates_env import templates

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def admin_panel(
    request: Request,
    account=Depends(require_admin),
    db: Session = Depends(get_db)
):
    accounts = db.query(Account).all()

    total_accounts = len(accounts)
    total_chars = db.query(Character).count()
    total_admins = sum(1 for a in accounts if a.is_admin)

    accounts_data = []
    for acc in accounts:
        chars = db.query(Character).filter(Character.account_id == acc.id).all()
        main = None
        if acc.main_character_id:
            main = db.query(Character).filter(Character.id == acc.main_character_id).first()
        accounts_data.append({
            "account": acc,
            "characters": chars,
            "main": main,
            "char_count": len(chars),
        })

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "account": account,
        "accounts_data": accounts_data,
        "total_accounts": total_accounts,
        "total_chars": total_chars,
        "total_admins": total_admins,
        "total_colonies": 0,  # Placeholder
    })


@router.get("/toggle-admin/{target_account_id}")
def toggle_admin(
    target_account_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db)
):
    if target_account_id == account.id:
        raise HTTPException(status_code=400, detail="Du kannst deine eigenen Admin-Rechte nicht entziehen")

    target = db.query(Account).filter(Account.id == target_account_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Account nicht gefunden")

    target.is_admin = not target.is_admin
    db.commit()
    return RedirectResponse(url="/admin", status_code=302)


@router.get("/delete-account/{target_account_id}")
def delete_account(
    target_account_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db)
):
    if target_account_id == account.id:
        raise HTTPException(status_code=400, detail="Du kannst deinen eigenen Account nicht löschen")

    target = db.query(Account).filter(Account.id == target_account_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Account nicht gefunden")

    db.delete(target)
    db.commit()
    return RedirectResponse(url="/admin", status_code=302)


@router.get("/set-main/{account_id}/{character_id}")
def admin_set_main(
    account_id: int,
    character_id: int,
    account=Depends(require_admin),
    db: Session = Depends(get_db)
):
    target_account = db.query(Account).filter(Account.id == account_id).first()
    if not target_account:
        raise HTTPException(status_code=404, detail="Account nicht gefunden")

    char = db.query(Character).filter(
        Character.id == character_id,
        Character.account_id == account_id
    ).first()
    if not char:
        raise HTTPException(status_code=404, detail="Charakter nicht gefunden")

    target_account.main_character_id = char.id
    db.commit()
    return RedirectResponse(url="/admin", status_code=302)
