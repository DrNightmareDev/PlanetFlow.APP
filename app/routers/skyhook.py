from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.database import engine, get_db
from app.dependencies import require_account
from app.models import SkyhookEntry
from app.pi_data import ALL_P1, ALL_P2, ALL_P3, ALL_P4
from app.templates_env import templates

router = APIRouter(prefix="/skyhook", tags=["skyhook"])

# Tabelle anlegen falls noch nicht vorhanden
SkyhookEntry.__table__.create(bind=engine, checkfirst=True)

PI_PRODUCTS_BY_TIER = {
    "P4": ALL_P4,
    "P3": ALL_P3,
    "P2": ALL_P2,
    "P1": ALL_P1,
}


def _latest_entries(account_id: int, planet_ids: list[int], db: Session) -> dict:
    if not planet_ids:
        return {}
    subq = (
        db.query(SkyhookEntry.planet_id, sqlfunc.max(SkyhookEntry.id).label("max_id"))
        .filter(SkyhookEntry.account_id == account_id, SkyhookEntry.planet_id.in_(planet_ids))
        .group_by(SkyhookEntry.planet_id)
        .subquery()
    )
    rows = db.query(SkyhookEntry).join(subq, SkyhookEntry.id == subq.c.max_id).all()
    return {r.planet_id: {"product_name": r.product_name, "quantity": r.quantity} for r in rows}


def _history(account_id: int, planet_ids: list[int], db: Session, limit: int = 3) -> dict:
    """Returns last `limit` entries per planet as dict {planet_id: [entries]}."""
    if not planet_ids:
        return {}
    all_rows = (
        db.query(SkyhookEntry)
        .filter(SkyhookEntry.account_id == account_id, SkyhookEntry.planet_id.in_(planet_ids))
        .order_by(SkyhookEntry.planet_id, SkyhookEntry.id.desc())
        .all()
    )
    result: dict = {}
    for r in all_rows:
        pid = r.planet_id
        if pid not in result:
            result[pid] = []
        if len(result[pid]) < limit:
            result[pid].append({
                "product_name": r.product_name,
                "quantity": r.quantity,
                "recorded_at": r.recorded_at.strftime("%d.%m.%Y %H:%M") if r.recorded_at else "—",
            })
    return result


@router.get("", response_class=HTMLResponse)
def skyhook_page(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    from app.routers.dashboard import _dashboard_cache
    cached = _dashboard_cache.get(account.id)
    colonies = cached["colonies"] if cached else []

    planet_ids = [c["planet_id"] for c in colonies if c.get("planet_id")]
    latest = _latest_entries(account.id, planet_ids, db)
    history = _history(account.id, planet_ids, db)

    return templates.TemplateResponse("skyhook.html", {
        "request": request,
        "account": account,
        "colonies": colonies,
        "latest": latest,
        "history": history,
        "pi_products": PI_PRODUCTS_BY_TIER,
    })


class EntryIn(BaseModel):
    planet_id: int
    character_name: str = ""
    product_name: str
    quantity: int


@router.post("/entry")
def save_entry(
    body: EntryIn,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    entry = SkyhookEntry(
        account_id=account.id,
        planet_id=body.planet_id,
        character_name=body.character_name or None,
        product_name=body.product_name,
        quantity=body.quantity,
    )
    db.add(entry)
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/history/{planet_id}")
def get_history(
    planet_id: int,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(SkyhookEntry)
        .filter(SkyhookEntry.account_id == account.id, SkyhookEntry.planet_id == planet_id)
        .order_by(SkyhookEntry.id.desc())
        .limit(3)
        .all()
    )
    return JSONResponse([{
        "product_name": r.product_name,
        "quantity": r.quantity,
        "recorded_at": r.recorded_at.strftime("%d.%m.%Y %H:%M") if r.recorded_at else "—",
    } for r in rows])
