import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session, joinedload

from app.database import engine, get_db
from app.dependencies import require_account
from app.session import validate_csrf_header
from app.i18n import get_language_from_request, translate_type_name
from app.market import get_prices_by_mode, PI_TYPE_IDS
from app.models import SkyhookEntry, SkyhookItem, SkyhookValueCache
from app.pi_data import ALL_P1, ALL_P2, ALL_P3, ALL_P4
from app import sde
from app.templates_env import templates

router = APIRouter(prefix="/skyhook", tags=["skyhook"])

# Tabellen anlegen falls noch nicht vorhanden
SkyhookEntry.__table__.create(bind=engine, checkfirst=True)
SkyhookItem.__table__.create(bind=engine, checkfirst=True)
SkyhookValueCache.__table__.create(bind=engine, checkfirst=True)

PI_PRODUCTS_BY_TIER = {"P4": ALL_P4, "P3": ALL_P3, "P2": ALL_P2, "P1": ALL_P1}
PRICE_MODES = ("sell", "buy", "split")


def _resolve_type_id(name: str) -> int | None:
    return PI_TYPE_IDS.get(name) or sde.find_type_id_by_name(name)


def _build_product_labels(names: set[str], lang: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for name in names:
        labels[name] = translate_type_name(_resolve_type_id(name), fallback=name, lang=lang)
    return labels


def _load_latest(account_id: int, planet_ids: list[int], db: Session) -> dict:
    """Returns {planet_id: [{"product_name": ..., "quantity": ...}]}."""
    if not planet_ids:
        return {}
    rows = (
        db.query(SkyhookEntry.planet_id, sqlfunc.max(SkyhookEntry.id).label("max_id"))
        .filter(SkyhookEntry.account_id == account_id, SkyhookEntry.planet_id.in_(planet_ids))
        .group_by(SkyhookEntry.planet_id)
        .all()
    )
    if not rows:
        return {}
    entry_id_to_planet = {r.max_id: r.planet_id for r in rows}
    if not entry_id_to_planet:
        return {}
    items = db.query(SkyhookItem).filter(SkyhookItem.entry_id.in_(entry_id_to_planet.keys())).all()

    result: dict[int, list[dict]] = {}
    for it in items:
        pid = entry_id_to_planet.get(it.entry_id)
        if pid is not None:
            result.setdefault(pid, []).append({"product_name": it.product_name, "quantity": it.quantity})
    return result


def _load_history(account_id: int, planet_ids: list[int], db: Session, limit: int = 3) -> dict:
    """Returns {planet_id: [entries with items, newest first]}."""
    if not planet_ids:
        return {}
    entries = (
        db.query(SkyhookEntry)
        .filter(SkyhookEntry.account_id == account_id, SkyhookEntry.planet_id.in_(planet_ids))
        .options(joinedload(SkyhookEntry.items))
        .order_by(SkyhookEntry.id.desc())
        .all()
    )
    if not entries:
        return {}
    result: dict[int, list[dict]] = {}
    planet_seen: dict[int, int] = {}
    for e in entries:
        pid = e.planet_id
        planet_seen[pid] = planet_seen.get(pid, 0) + 1
        if planet_seen[pid] == 1:
            continue
        result.setdefault(pid, [])
        if len(result[pid]) < limit:
            result[pid].append({
                "recorded_at": e.recorded_at.strftime("%d.%m.%Y %H:%M") if e.recorded_at else "-",
                "items": [{"product_name": i.product_name, "quantity": i.quantity} for i in e.items],
            })
    return result


def _save_value_cache(
    account_id: int,
    latest: dict[int, list[dict]],
    db: Session,
    prune_missing: bool = False,
) -> dict[str, dict[int, dict]]:
    """Rebuilds cached ISK values for the latest skyhook inventory per planet."""
    all_products = {it["product_name"] for items in latest.values() for it in items if it.get("product_name")}
    price_maps = {
        mode: (get_prices_by_mode(list(all_products), mode, db) if all_products else {})
        for mode in PRICE_MODES
    }
    now = datetime.now(timezone.utc)
    existing = {
        (row.planet_id, row.price_mode): row
        for row in db.query(SkyhookValueCache).filter(SkyhookValueCache.account_id == account_id).all()
    }
    valid_keys: set[tuple[int, str]] = set()
    cached: dict[str, dict[int, dict]] = {mode: {} for mode in PRICE_MODES}

    for planet_id, items in latest.items():
        for mode in PRICE_MODES:
            details = []
            total_value = 0.0
            for item in items:
                product_name = item["product_name"]
                quantity = int(item.get("quantity") or 0)
                unit_price = float(price_maps[mode].get(product_name, 0.0) or 0.0)
                line_value = quantity * unit_price
                total_value += line_value
                details.append({
                    "product_name": product_name,
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "line_value": line_value,
                })

            key = (planet_id, mode)
            valid_keys.add(key)
            row = existing.get(key)
            details_json = json.dumps(details)
            if row:
                row.total_value = str(total_value)
                row.details_json = details_json
                row.updated_at = now
            else:
                row = SkyhookValueCache(
                    account_id=account_id,
                    planet_id=planet_id,
                    price_mode=mode,
                    total_value=str(total_value),
                    details_json=details_json,
                    updated_at=now,
                )
                db.add(row)
            cached[mode][planet_id] = {
                "total_value": total_value,
                "details": details,
                "updated_at": now,
            }

    for key, row in existing.items():
        if prune_missing and key not in valid_keys:
            db.delete(row)

    db.commit()
    return cached


def refresh_skyhook_value_cache(db: Session, account_ids: list[int] | None = None) -> None:
    """Refreshes cached skyhook values from the latest inventory and current market DB cache."""
    query = db.query(SkyhookEntry.account_id).distinct()
    if account_ids:
        query = query.filter(SkyhookEntry.account_id.in_(account_ids))
    target_account_ids = [row.account_id for row in query.all()]
    for account_id in target_account_ids:
        rows = (
            db.query(SkyhookEntry.planet_id, sqlfunc.max(SkyhookEntry.id).label("max_id"))
            .filter(SkyhookEntry.account_id == account_id)
            .group_by(SkyhookEntry.planet_id)
            .all()
        )
        if not rows:
            db.query(SkyhookValueCache).filter(SkyhookValueCache.account_id == account_id).delete()
            db.commit()
            continue
        latest = _load_latest(account_id, [r.planet_id for r in rows], db)
        _save_value_cache(account_id, latest, db, prune_missing=True)


def _load_value_cache(
    account_id: int,
    planet_ids: list[int],
    price_mode: str,
    db: Session,
) -> tuple[dict[int, float], dict[int, list[dict]], datetime | None]:
    rows = (
        db.query(SkyhookValueCache)
        .filter(
            SkyhookValueCache.account_id == account_id,
            SkyhookValueCache.price_mode == price_mode,
            SkyhookValueCache.planet_id.in_(planet_ids),
        )
        .all()
    )
    values: dict[int, float] = {}
    details: dict[int, list[dict]] = {}
    last_updated: datetime | None = None
    for row in rows:
        values[row.planet_id] = float(row.total_value or 0)
        try:
            details[row.planet_id] = json.loads(row.details_json or "[]")
        except Exception:
            details[row.planet_id] = []
        if row.updated_at and (last_updated is None or row.updated_at > last_updated):
            last_updated = row.updated_at
    return values, details, last_updated


@router.get("", response_class=HTMLResponse)
def skyhook_page(
    request: Request,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    from app.routers.dashboard import _load_colony_cache

    db_cached = _load_colony_cache(account.id, db)
    colonies = db_cached["colonies"] if db_cached else []
    planet_ids = [c["planet_id"] for c in colonies if c.get("planet_id")]

    latest = _load_latest(account.id, planet_ids, db)
    history = _load_history(account.id, planet_ids, db)

    price_mode = getattr(account, "price_mode", "sell")
    values, value_details, market_last_updated = _load_value_cache(account.id, planet_ids, price_mode, db)
    if planet_ids and len(values) < len({pid for pid in planet_ids if pid in latest}):
        _save_value_cache(account.id, latest, db, prune_missing=True)
        values, value_details, market_last_updated = _load_value_cache(account.id, planet_ids, price_mode, db)

    total_value = sum(values.values())
    prices = {
        detail["product_name"]: detail["unit_price"]
        for planet_details in value_details.values()
        for detail in planet_details
    }
    lang = get_language_from_request(request)
    product_names = {
        item["product_name"]
        for items in latest.values()
        for item in items
        if item.get("product_name")
    }
    product_names.update(
        f.get("name")
        for colony in colonies
        for f in colony.get("factories", [])
        if f.get("name")
    )
    product_names.update(
        detail.get("product_name")
        for planet_details in value_details.values()
        for detail in planet_details
        if detail.get("product_name")
    )
    product_names.update(
        item.get("product_name")
        for planet_history in history.values()
        for entry in planet_history
        for item in entry.get("items", [])
        if item.get("product_name")
    )
    product_labels = _build_product_labels({name for name in product_names if name}, lang)

    return templates.TemplateResponse("skyhook.html", {
        "request": request,
        "account": account,
        "colonies": colonies,
        "latest": latest,
        "history": history,
        "pi_products": PI_PRODUCTS_BY_TIER,
        "prices": prices,
        "values": values,
        "value_details": value_details,
        "product_labels": product_labels,
        "total_value": total_value,
        "price_mode": price_mode,
        "market_last_updated_iso": (
            (market_last_updated.replace(tzinfo=timezone.utc) if market_last_updated and market_last_updated.tzinfo is None else market_last_updated)
            .astimezone(timezone.utc)
            .isoformat()
            if market_last_updated else ""
        ),
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
    request: Request,
    body: EntryIn,
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    validate_csrf_header(request)
    valid = [i for i in body.items if i.product_name and i.quantity >= 0]

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

    latest = _load_latest(account.id, [body.planet_id], db)
    if latest.get(body.planet_id):
        cached = _save_value_cache(account.id, latest, db, prune_missing=False)
    else:
        db.query(SkyhookValueCache).filter(
            SkyhookValueCache.account_id == account.id,
            SkyhookValueCache.planet_id == body.planet_id,
        ).delete()
        db.commit()
        cached = {mode: {} for mode in PRICE_MODES}
    mode = getattr(account, "price_mode", "sell")
    planet_cache = cached.get(mode, {}).get(body.planet_id, {"total_value": 0.0, "details": []})
    return JSONResponse({
        "ok": True,
        "total_value": planet_cache["total_value"],
        "details": planet_cache["details"],
    })
