from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_account
from app.inventory_service import (
    TIERS,
    add_inventory_lot,
    adjust_inventory,
    format_utc_compact,
    get_inventory_item_detail,
    get_inventory_rows,
    get_inventory_summary_map,
    get_pi_catalog_maps,
    soft_delete_inventory_transaction,
    soft_delete_inventory_summary,
    sync_inventory_summaries,
)
from app.models import InventoryAdjustment, InventoryLot
from app.templates_env import templates

router = APIRouter(prefix="/inventory", tags=["inventory"])


def _status_redirect(message: str, level: str = "success", tier: str | None = None) -> RedirectResponse:
    suffix = f"&tier={tier}" if tier else ""
    return RedirectResponse(url=f"/inventory?level={level}&message={message}{suffix}", status_code=303)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def inventory_page(
    request: Request,
    tier: str | None = Query(None),
    account=Depends(require_account),
    db: Session = Depends(get_db),
):
    catalog, _by_type_id, _by_name = get_pi_catalog_maps()
    selectable_catalog = [item for item in catalog if int(item["type_id"] or 0)]
    sync_inventory_summaries(db, int(account.id))
    rows = get_inventory_rows(db, int(account.id), tier=tier if tier in TIERS else None)
    lots = (
        db.query(InventoryLot)
        .filter(
            InventoryLot.account_id == int(account.id),
            InventoryLot.deleted_at.is_(None),
        )
        .order_by(InventoryLot.created_at.desc(), InventoryLot.id.desc())
        .limit(60)
        .all()
    )
    adjustments = (
        db.query(InventoryAdjustment)
        .filter(
            InventoryAdjustment.account_id == int(account.id),
            InventoryAdjustment.deleted_at.is_(None),
        )
        .order_by(InventoryAdjustment.created_at.desc(), InventoryAdjustment.id.desc())
        .limit(60)
        .all()
    )
    lot_rows = [
        {
            "tier": row.tier,
            "item_name": row.item_name,
            "source_kind": row.source_kind,
            "quantity_added": int(row.quantity_added or 0),
            "quantity_remaining": int(row.quantity_remaining or 0),
            "unit_cost": row.unit_cost,
            "created_at": format_utc_compact(row.created_at),
        }
        for row in lots
    ]
    adjustment_rows = [
        {
            "id": int(row.id),
            "tier": row.tier,
            "item_name": row.item_name,
            "reason": row.reason,
            "note": row.note,
            "delta_quantity": int(row.delta_quantity or 0),
            "created_at": format_utc_compact(row.created_at),
        }
        for row in adjustments
    ]
    distinct_items = len(rows)
    total_units = sum(int(item["quantity_on_hand"] or 0) for item in rows)
    estimated_value = sum(float(item["estimated_value"] or 0.0) for item in rows)
    estimated_value_buy = sum(float(item["estimated_value_buy"] or 0.0) for item in rows)
    estimated_value_sell = sum(float(item["estimated_value_sell"] or 0.0) for item in rows)
    estimated_value_split = sum(float(item["estimated_value_split"] or 0.0) for item in rows)

    return templates.TemplateResponse("inventory.html", {
        "request": request,
        "account": account,
        "inventory_catalog": selectable_catalog,
        "inventory_rows": rows,
        "inventory_lots": lot_rows,
        "inventory_adjustments": adjustment_rows,
        "inventory_tiers": TIERS,
        "selected_tier": tier if tier in TIERS else "",
        "inventory_message": request.query_params.get("message", ""),
        "inventory_level": request.query_params.get("level", "success"),
        "inventory_stats": {
            "distinct_items": distinct_items,
            "total_units": total_units,
            "estimated_value": estimated_value,
            "estimated_value_buy": estimated_value_buy,
            "estimated_value_sell": estimated_value_sell,
            "estimated_value_split": estimated_value_split,
        },
    })


@router.post("/lots")
def create_inventory_lot(
    account=Depends(require_account),
    db: Session = Depends(get_db),
    type_id: int = Form(...),
    quantity: int = Form(...),
    source_kind: str = Form(...),
    unit_cost: str = Form(""),
    note: str = Form(""),
):
    _catalog, by_type_id, _by_name = get_pi_catalog_maps()
    item = by_type_id.get(int(type_id))
    if not item:
        return _status_redirect("Unknown PI item selected.", "danger")
    try:
        add_inventory_lot(
            db,
            account_id=int(account.id),
            item=item,
            quantity=int(quantity),
            source_kind=str(source_kind or "manual"),
            unit_cost=unit_cost,
            note=note,
        )
        db.commit()
        return _status_redirect("Inventory batch added.")
    except Exception as exc:
        db.rollback()
        return _status_redirect(str(exc), "danger")


@router.post("/adjust")
def adjust_inventory_stock(
    account=Depends(require_account),
    db: Session = Depends(get_db),
    type_id: int = Form(...),
    direction: str = Form(...),
    quantity: int = Form(...),
    unit_cost: str = Form(""),
    note: str = Form(""),
):
    _catalog, by_type_id, _by_name = get_pi_catalog_maps()
    item = by_type_id.get(int(type_id))
    if not item:
        return _status_redirect("Unknown PI item selected.", "danger")
    try:
        adjust_inventory(
            db,
            account_id=int(account.id),
            item=item,
            direction=str(direction),
            quantity=int(quantity),
            unit_cost=unit_cost,
            note=note,
        )
        db.commit()
        return _status_redirect("Inventory adjusted.")
    except Exception as exc:
        db.rollback()
        return _status_redirect(str(exc), "danger")


@router.post("/remove")
def remove_inventory_row(
    account=Depends(require_account),
    db: Session = Depends(get_db),
    type_id: int = Form(...),
    tier: str = Form(""),
):
    try:
        if not soft_delete_inventory_summary(db, int(account.id), int(type_id)):
            db.rollback()
            return _status_redirect("Inventory row not found.", "danger", tier=tier or None)
        db.commit()
        return _status_redirect("Inventory row hidden from current stock.", "success", tier=tier or None)
    except Exception as exc:
        db.rollback()
        return _status_redirect(str(exc), "danger", tier=tier or None)


@router.post("/transaction/remove")
def remove_inventory_transaction(
    account=Depends(require_account),
    db: Session = Depends(get_db),
    transaction_kind: str = Form(...),
    transaction_id: int = Form(...),
):
    try:
        type_id = soft_delete_inventory_transaction(db, int(account.id), str(transaction_kind), int(transaction_id))
        if type_id is None:
            db.rollback()
            return JSONResponse({"error": "Transaction not found."}, status_code=404)
        db.commit()
        return JSONResponse({"ok": True, "type_id": int(type_id)})
    except Exception as exc:
        db.rollback()
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.post("/transaction/remove-page")
def remove_inventory_transaction_page(
    account=Depends(require_account),
    db: Session = Depends(get_db),
    transaction_kind: str = Form(...),
    transaction_id: int = Form(...),
    tier: str = Form(""),
):
    try:
        type_id = soft_delete_inventory_transaction(db, int(account.id), str(transaction_kind), int(transaction_id))
        if type_id is None:
            db.rollback()
            return _status_redirect("Transaction not found.", "danger", tier=tier or None)
        db.commit()
        return _status_redirect("Transaction hidden.", "success", tier=tier or None)
    except Exception as exc:
        db.rollback()
        return _status_redirect(str(exc), "danger", tier=tier or None)


@router.get("/summary")
def inventory_summary(account=Depends(require_account), db: Session = Depends(get_db)):
    sync_inventory_summaries(db, int(account.id))
    return JSONResponse(get_inventory_summary_map(db, int(account.id)))


@router.get("/item/{type_id}")
def inventory_item_detail(type_id: int, account=Depends(require_account), db: Session = Depends(get_db)):
    sync_inventory_summaries(db, int(account.id))
    detail = get_inventory_item_detail(db, int(account.id), int(type_id))
    if detail is None:
        return JSONResponse({"error": "Inventory item not found."}, status_code=404)
    return JSONResponse(detail)
