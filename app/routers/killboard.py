from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_account
from app.models import KillActivityCache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/killboard", tags=["killboard"])

_CACHE_TTL = timedelta(minutes=15)
_HEADERS = {"User-Agent": "EVE-PI-Manager/1.0 github.com/DrNightmareDev/PI_Manager"}


def _danger_level(kill_count: int) -> str:
    if kill_count >= 5:
        return "danger"
    if kill_count >= 1:
        return "caution"
    return "safe"


@router.get("/system/{system_id}")
def get_system_kills(system_id: int, account=Depends(require_account), db: Session = Depends(get_db)):
    row = db.get(KillActivityCache, int(system_id))
    now = datetime.now(timezone.utc)
    if row and row.fetched_at:
        fetched_at = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)
        if now - fetched_at <= _CACHE_TTL:
            return JSONResponse({
                "system_id": int(system_id),
                "kill_count": int(row.kill_count or 0),
                "fetched_at_iso": fetched_at.astimezone(timezone.utc).isoformat(),
                "danger_level": _danger_level(int(row.kill_count or 0)),
                "system_url": f"https://zkillboard.com/system/{int(system_id)}/",
            })

    kill_count = 0
    try:
        resp = requests.get(
            f"https://zkillboard.com/api/kills/solarSystemID/{int(system_id)}/pastSeconds/3600/",
            headers=_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        kill_count = len(data) if isinstance(data, list) else 0
    except Exception as exc:
        logger.exception("killboard: failed to fetch kills for system %s", system_id)
        if row and row.fetched_at:
            fetched_at = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)
            return JSONResponse({
                "system_id": int(system_id),
                "kill_count": int(row.kill_count or 0),
                "fetched_at_iso": fetched_at.astimezone(timezone.utc).isoformat(),
                "danger_level": _danger_level(int(row.kill_count or 0)),
                "system_url": f"https://zkillboard.com/system/{int(system_id)}/",
            })
        raise HTTPException(status_code=502, detail="Killboard unavailable")

    if row is None:
        row = KillActivityCache(system_id=int(system_id), kill_count=kill_count, fetched_at=now)
        db.add(row)
    else:
        row.kill_count = kill_count
        row.fetched_at = now
    db.commit()

    return JSONResponse({
        "system_id": int(system_id),
        "kill_count": int(kill_count),
        "fetched_at_iso": now.astimezone(timezone.utc).isoformat(),
        "danger_level": _danger_level(int(kill_count)),
        "system_url": f"https://zkillboard.com/system/{int(system_id)}/",
    })
