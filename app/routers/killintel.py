"""KillIntel router — local chat paste → pilot threat analysis."""
from __future__ import annotations

import json
import time
import threading

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.dependencies import require_account
from app.session import validate_csrf_header

# Per-account rate limit: 30s cooldown only when the time window changed.
_analyze_cooldown: dict[int, float] = {}   # account_id -> last run timestamp
_analyze_last_window: dict[int, str] = {}  # account_id -> last time_window_days value
_analyze_lock = threading.Lock()
_ANALYZE_COOLDOWN_SEC = 30
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
    validate_csrf_header(request)
    """
    Streams NDJSON: one JSON object per line, one per pilot as it completes.
    Frontend reads the stream and renders each card immediately.
    """
    body = await request.json()
    use_cache_only: bool = bool(body.get("use_cache_only", False))
    raw_days = body.get("time_window_days")
    window_key = str(raw_days) if raw_days is not None else "none"

    # Rate limit: 30s cooldown only when the time window changed vs last run.
    # Same window = no cooldown (re-running same search is fine).
    if not use_cache_only:
        now = time.monotonic()
        with _analyze_lock:
            last = _analyze_cooldown.get(account.id, 0)
            last_window = _analyze_last_window.get(account.id)
            window_changed = last_window != window_key
            wait = _ANALYZE_COOLDOWN_SEC - (now - last)
            if window_changed and wait > 0:
                return JSONResponse(
                    {"error": f"Please wait {int(wait) + 1}s before changing the time window again."},
                    status_code=429,
                )
            _analyze_cooldown[account.id] = now
            _analyze_last_window[account.id] = window_key

    raw_text: str = body.get("names", "")
    raw_days = body.get("time_window_days")
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
    validate_csrf_header(request)
    body = await request.json()
    raw_text: str = body.get("names", "")
    raw_days = body.get("time_window_days")
    time_window_days: int | None = int(raw_days) if raw_days and str(raw_days).isdigit() else None
    names = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not names:
        return JSONResponse({})
    result = check_names_in_cache(names, db, time_window_days=time_window_days)
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
