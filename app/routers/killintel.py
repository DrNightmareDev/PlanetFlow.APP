"""KillIntel router — local chat paste → pilot threat analysis."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.dependencies import require_account
from app.models import KillIntelPilot, KillIntelKillmail, KillIntelItem
from app.services.killintel import check_names_in_cache, stream_pilots
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
):
    """
    Streams NDJSON: one JSON object per line, one per pilot as it completes.
    Frontend reads the stream and renders each card immediately.
    """
    body = await request.json()
    raw_text: str = body.get("names", "")
    use_cache_only: bool = bool(body.get("use_cache_only", False))
    raw_days = body.get("time_window_days")
    time_window_days: int | None = int(raw_days) if raw_days and str(raw_days).isdigit() else None

    names = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not names:
        return JSONResponse({"error": "No names provided"}, status_code=400)
    if len(names) > 50:
        return JSONResponse({"error": "Max 50 pilots per request"}, status_code=400)

    def generate():
        # Use a dedicated DB session for the streaming generator
        db = SessionLocal()
        try:
            for result in stream_pilots(
                names, db,
                use_cache_only=use_cache_only,
                time_window_days=time_window_days,
            ):
                yield json.dumps(result, default=str) + "\n"
        finally:
            db.close()

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@router.post("/check-cache")
async def killintel_check_cache(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    body = await request.json()
    raw_text: str = body.get("names", "")
    names = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not names:
        return JSONResponse({})
    result = check_names_in_cache(names, db)
    return JSONResponse(result)


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
