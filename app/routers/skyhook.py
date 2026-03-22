from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session, joinedload

from app.database import engine, get_db
from app.dependencies import require_account
from app.models import SkyhookEntry, SkyhookItem
from app.pi_data import ALL_P1, ALL_P2, ALL_P3, ALL_P4
from app.templates_env import templates

router = APIRouter(prefix="/skyhook", tags=["skyhook"])

# Tabellen anlegen falls noch nicht vorhanden
SkyhookEntry.__table__.create(bind=engine, checkfirst=True)
SkyhookItem.__table__.create(bind=engine, checkfirst=True)

PI_PRODUCTS_BY_TIER = {"P4": ALL_P4, "P3": ALL_P3, "P2": ALL_P2, "P1": ALL_P1}


def _load_latest(account_id: int, planet_ids: list[int], db: Session) -> dict:
    """Returns {planet_id: [{"product_name": ..., "quantity": ...}]}"""
    if not planet_ids:
        return {}
    # Step 1: latest entry-id per planet
    rows = (
        db.query(SkyhookEntry.planet_id, sqlfunc.max(SkyhookEntry.id).label("max_id"))
        .filter(SkyhookEntry.account_id == account_id, SkyhookEntry.planet_id.in_(planet_ids))
        .group_by(SkyhookEntry.planet_id)
        .all()
    )
    if not rows:
        return {}
    entry_id_to_planet = {r.max_id: r.planet_id for r in rows}
    entry_ids = list(entry_id_to_planet.keys())

    # Step 2: all items for those entries
    items = db.query(SkyhookItem).filter(SkyhookItem.entry_id.in_(entry_ids)).all()

    result: dict = {}
    for it in items:
        pid = entry_id_to_planet.get(it.entry_id)
        if pid is not None:
            result.setdefault(pid, []).append({"product_name": it.product_name, "quantity": it.quantity})
    return result


def _load_history(account_id: int, planet_ids: list[int], db: Session, limit: int = 3) -> dict:
    """Returns {planet_id: [entries with items, newest first]}"""
    if not planet_ids:
        return {}
    entries = (
        db.query(SkyhookEntry)
        .filter(SkyhookEntry.account_id == account_id, SkyhookEntry.planet_id.in_(planet_ids))
        .options(joinedload(SkyhookEntry.items))
        .order_by(SkyhookEntry.id.desc())
        .all()
    )
    result: dict = {}
    planet_seen: dict = {}
    for e in entries:
        pid = e.planet_id
        planet_seen[pid] = planet_seen.get(pid, 0) + 1
        if planet_seen[pid] == 1:
            continue  # skip most recent — already visible in the form
        result.setdefault(pid, [])
        if len(result[pid]) < limit:
            result[pid].append({
                "recorded_at": e.recorded_at.strftime("%d.%m.%Y %H:%M") if e.recorded_at else "—",
                "items": [{"product_name": i.product_name, "quantity": i.quantity} for i in e.items],
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
    latest  = _load_latest(account.id, planet_ids, db)
    history = _load_history(account.id, planet_ids, db)

    return templates.TemplateResponse("skyhook.html", {
        "request": request,
        "account": account,
        "colonies": colonies,
        "latest": latest,
        "history": history,
        "pi_products": PI_PRODUCTS_BY_TIER,
    })


class ItemIn(BaseModel):
    product_name: str
    quantity: int

class EntryIn(BaseModel):
    planet_id: int
    character_name: str = ""
    items: list[ItemIn]


@router.get("/history/{planet_id}")
def get_history(
    planet_id: int,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    hist = _load_history(account.id, [planet_id], db, limit=3)
    return JSONResponse({"entries": hist.get(planet_id, [])})


@router.post("/entry")
def save_entry(
    body: EntryIn,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    valid = [i for i in body.items if i.product_name and i.quantity >= 0]
    if not valid:
        return JSONResponse({"ok": False, "error": "no valid items"}, status_code=400)

    entry = SkyhookEntry(
        account_id=account.id,
        planet_id=body.planet_id,
        character_name=body.character_name or None,
    )
    db.add(entry)
    db.flush()
    for it in valid:
        db.add(SkyhookItem(entry_id=entry.id, product_name=it.product_name, quantity=it.quantity))
    db.commit()
    return JSONResponse({"ok": True})
