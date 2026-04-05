"""KillIntel router — local chat paste → pilot threat analysis."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_account
from app.models import KillIntelPilot, KillIntelKillmail, KillIntelItem
from app.services.killintel import analyze_pilots
from app.templates_env import templates

router = APIRouter(prefix="/intel/killintel", tags=["killintel"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def killintel_page(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse("killintel.html", {
        "request": request,
        "account": account,
    })


@router.post("/analyze")
async def killintel_analyze(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    body = await request.json()
    raw_text: str = body.get("names", "")
    names = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not names:
        return JSONResponse({"error": "No names provided"}, status_code=400)
    if len(names) > 50:
        return JSONResponse({"error": "Max 50 pilots per request"}, status_code=400)

    results = analyze_pilots(names, db)
    return JSONResponse({"results": results})


@router.get("/pilot/{character_id}")
def killintel_pilot(
    character_id: int,
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    pilot = db.get(KillIntelPilot, character_id)
    if not pilot:
        return JSONResponse({"error": "Pilot not in cache"}, status_code=404)

    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=90)

    kms = (
        db.query(KillIntelKillmail)
        .filter(
            KillIntelKillmail.character_id == character_id,
            KillIntelKillmail.killmail_time >= cutoff,
        )
        .all()
    )

    return JSONResponse({
        "character_id": pilot.character_id,
        "name": pilot.name,
        "corporation": pilot.corporation_name,
        "alliance": pilot.alliance_name,
        "danger_ratio": pilot.danger_ratio,
        "ships_destroyed": pilot.ships_destroyed,
        "ships_lost": pilot.ships_lost,
        "isk_destroyed": pilot.isk_destroyed,
        "isk_lost": pilot.isk_lost,
        "killmails_cached": len(kms),
        "fetched_at": pilot.fetched_at.isoformat() if pilot.fetched_at else None,
    })
