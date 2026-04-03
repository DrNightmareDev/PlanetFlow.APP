"""
Director router — corp/alliance officer view.
Routes:
  GET  /director                        → corp member overview scoped to account.director_corp_id
  POST /director/role/toggle            → grant/revoke manager or FC role for an account
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_director
from app.models import Account, Character
from app.session import validate_csrf
from app.templates_env import templates

router = APIRouter(prefix="/director", tags=["director"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def director_panel(
    request: Request,
    account: Account = Depends(require_director),
    db: Session = Depends(get_db),
):
    corp_id = account.director_corp_id
    corp_name = account.director_corp_name or "—"

    # CEO fallback: if no explicit director_corp_id, use the CEO's own corp
    if not corp_id:
        main_char = db.query(Character).filter(Character.id == account.main_character_id).first() if account.main_character_id else None
        if not main_char:
            # Try any character on the account
            main_char = db.query(Character).filter(Character.account_id == account.id).first()
        if main_char and main_char.corporation_id:
            corp_id = main_char.corporation_id
            corp_name = main_char.corporation_name or f"Corp #{corp_id}"
        else:
            return templates.TemplateResponse("director/index.html", {
                "request": request,
                "account": account,
                "corp_id": None,
                "corp_name": corp_name,
                "alliance_id": None,
                "alliance_name": None,
                "member_rows": [],
                "total_colonies": 0,
            })

    # All characters in this corp
    corp_chars = db.query(Character).filter(Character.corporation_id == corp_id).all()

    # Grab alliance from first char that has one
    alliance_id = next((c.alliance_id for c in corp_chars if c.alliance_id), None)
    alliance_name = next((c.alliance_name for c in corp_chars if c.alliance_name), None)

    # Group by account
    account_ids = {c.account_id for c in corp_chars}
    accounts = db.query(Account).filter(Account.id.in_(account_ids)).all() if account_ids else []
    chars_by_account: dict[int, list[Character]] = {}
    for c in corp_chars:
        chars_by_account.setdefault(c.account_id, []).append(c)

    member_rows = []
    for acc in accounts:
        chars = chars_by_account.get(acc.id, [])
        main = acc.main_character
        colonies = sum(c.last_known_colony_count for c in acc.characters)
        has_issue = any(c.colony_sync_issue for c in acc.characters)
        member_rows.append({
            "account": acc,
            "main": main,
            "char_count": len(acc.characters),
            "colony_count": colonies,
            "has_issue": has_issue,
            "is_corp_manager": acc.is_corp_manager,
            "is_fc": acc.is_fc,
        })

    member_rows.sort(key=lambda r: r["colony_count"], reverse=True)
    total_colonies = sum(r["colony_count"] for r in member_rows)

    # Determine if current viewer is CEO (not just director flag) — limits who can promote
    is_viewer_ceo = False
    if not account.is_director:
        from app.esi import get_corporation_info
        all_chars = db.query(Character).filter(Character.account_id == account.id).all()
        try:
            corp_info = get_corporation_info(corp_id)
            ceo_eve_id = corp_info.get("ceo_id")
            if ceo_eve_id and any(c.eve_character_id == ceo_eve_id for c in all_chars):
                is_viewer_ceo = True
        except Exception:
            pass

    can_promote = account.is_director or is_viewer_ceo

    return templates.TemplateResponse("director/index.html", {
        "request": request,
        "account": account,
        "corp_id": corp_id,
        "corp_name": corp_name,
        "alliance_id": alliance_id,
        "alliance_name": alliance_name,
        "member_rows": member_rows,
        "total_colonies": total_colonies,
        "can_promote": can_promote,
    })


@router.post("/role/toggle")
async def toggle_role(
    request: Request,
    director: Account = Depends(require_director),
    db: Session = Depends(get_db),
):
    """Grant or revoke manager/FC role for an account. Only director/CEO may call this."""
    form = await request.form()
    validate_csrf(request, form.get("csrf_token", ""))

    target_account_id = int(form.get("account_id", 0))
    role = (form.get("role") or "").strip()

    if role not in ("is_corp_manager", "is_fc"):
        raise HTTPException(status_code=400, detail="Invalid role")

    target = db.get(Account, target_account_id)
    if not target:
        raise HTTPException(status_code=404, detail="Account not found")

    # Verify target is in the same corp as the director
    director_corp_id = director.director_corp_id
    if not director_corp_id:
        main_char = db.query(Character).filter(Character.id == director.main_character_id).first() if director.main_character_id else None
        if main_char:
            director_corp_id = main_char.corporation_id

    if director_corp_id:
        target_chars = db.query(Character).filter(Character.account_id == target.id).all()
        in_corp = any(c.corporation_id == director_corp_id for c in target_chars)
        if not in_corp:
            raise HTTPException(status_code=403, detail="Target is not in your corporation")

    # Toggle the role
    current = getattr(target, role)
    setattr(target, role, not current)
    db.commit()

    return RedirectResponse(url="/director", status_code=303)
