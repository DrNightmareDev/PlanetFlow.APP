from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_account
from app.models import KillActivityCache
from app.zkill import get_system_kill_summaries, get_system_kill_summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/killboard", tags=["killboard"])

_CACHE_TTL = timedelta(minutes=15)
@router.get("/system/{system_id}")
def get_system_kills(system_id: int, account=Depends(require_account), db: Session = Depends(get_db)):
    row = db.get(KillActivityCache, int(system_id))
    now = datetime.now(timezone.utc)
    if row and row.fetched_at:
        fetched_at = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)
        if now - fetched_at <= _CACHE_TTL:
            try:
                summary = get_system_kill_summary(int(system_id), window="60m", limit=5)
                summary["fetched_at_iso"] = fetched_at.astimezone(timezone.utc).isoformat()
                return JSONResponse(summary)
            except Exception:
                logger.exception("killboard: failed to enrich cached system %s", system_id)
                return JSONResponse({
                    "system_id": int(system_id),
                    "kill_count": int(row.kill_count or 0),
                    "fetched_at_iso": fetched_at.astimezone(timezone.utc).isoformat(),
                    "danger_level": "danger" if int(row.kill_count or 0) >= 5 else "caution" if int(row.kill_count or 0) >= 1 else "safe",
                    "system_url": f"https://zkillboard.com/system/{int(system_id)}/",
                    "window": "60m",
                    "latest_kills": [],
                })

    try:
        summary = get_system_kill_summary(int(system_id), window="60m", limit=5)
        kill_count = int(summary["kill_count"])
    except Exception:
        logger.exception("killboard: failed to fetch kills for system %s", system_id)
        if row and row.fetched_at:
            fetched_at = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)
            return JSONResponse({
                "system_id": int(system_id),
                "kill_count": int(row.kill_count or 0),
                "fetched_at_iso": fetched_at.astimezone(timezone.utc).isoformat(),
                "danger_level": "danger" if int(row.kill_count or 0) >= 5 else "caution" if int(row.kill_count or 0) >= 1 else "safe",
                "system_url": f"https://zkillboard.com/system/{int(system_id)}/",
                "window": "60m",
                "latest_kills": [],
            })
        raise HTTPException(status_code=502, detail="Killboard unavailable")

    if row is None:
        row = KillActivityCache(system_id=int(system_id), kill_count=kill_count, fetched_at=now)
        db.add(row)
    else:
        row.kill_count = kill_count
        row.fetched_at = now
    db.commit()

    summary["fetched_at_iso"] = now.astimezone(timezone.utc).isoformat()
    return JSONResponse(summary)


@router.get("/systems")
def get_many_system_kills(
    system_ids: str = Query(""),
    window: str = Query("60m"),
    limit: int = Query(3, ge=1, le=10),
    account=Depends(require_account),
):
    ids = [int(part) for part in system_ids.split(",") if part.strip().isdigit()]
    summaries = get_system_kill_summaries(ids, window=window, limit=limit)
    return JSONResponse({
        "window": window,
        "systems": [summaries[key] for key in sorted(summaries.keys())],
    })
