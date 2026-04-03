"""
Director router — corp/alliance officer view.
Routes:
  GET /director           → overview of own corp/alliance members
  GET /director/members   → full member list with PI stats
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.dependencies import require_director
from app.models import Account, Character
from app.templates_env import templates

router = APIRouter(prefix="/director", tags=["director"])


def _get_scope(account: Account, db: Session):
    """Return the corp_id/alliance_id this director manages, and all member accounts in scope."""
    main = account.main_character
    if not main:
        return None, None, None, []

    corp_id = main.corporation_id
    alliance_id = main.alliance_id

    # Gather all characters in the same corp
    corp_chars = (
        db.query(Character)
        .filter(Character.corporation_id == corp_id)
        .all()
    ) if corp_id else []

    # Unique accounts from those characters
    account_ids = {c.account_id for c in corp_chars}
    accounts = db.query(Account).filter(Account.id.in_(account_ids)).all() if account_ids else []

    return corp_id, alliance_id, main, accounts


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def director_panel(
    request: Request,
    account: Account = Depends(require_director),
    db: Session = Depends(get_db),
):
    corp_id, alliance_id, main_char, corp_accounts = _get_scope(account, db)

    # Per-account aggregates
    member_rows = []
    for acc in corp_accounts:
        chars = [c for c in acc.characters if c.corporation_id == corp_id]
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

    # Sort by colony count desc
    member_rows.sort(key=lambda r: r["colony_count"], reverse=True)

    corp_name = main_char.corporation_name if main_char else "—"
    alliance_name = main_char.alliance_name if main_char else None
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
