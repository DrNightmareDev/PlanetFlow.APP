"""
Director router — corp/alliance officer view.
Routes:
  GET /director   → corp member overview scoped to account.director_corp_id
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_director
from app.models import Account, Character
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

    if not corp_id:
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
        })

    member_rows.sort(key=lambda r: r["colony_count"], reverse=True)
    total_colonies = sum(r["colony_count"] for r in member_rows)

    return templates.TemplateResponse("director/index.html", {
        "request": request,
        "account": account,
        "corp_id": corp_id,
        "corp_name": corp_name,
        "alliance_id": alliance_id,
        "alliance_name": alliance_name,
        "member_rows": member_rows,
        "total_colonies": total_colonies,
    })
