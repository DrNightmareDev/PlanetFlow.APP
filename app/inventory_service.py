from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import timezone

from sqlalchemy.orm import Session

from app.market import get_prices_by_type_ids
from app import sde
from app.models import InventoryAdjustment, InventoryItemSummary, InventoryLot
from app.pi_data import ALL_P1, ALL_P2, ALL_P3, ALL_P4, P0_TO_P1

TIERS = ("P0", "P1", "P2", "P3", "P4")
TIER_SORT = {tier: index for index, tier in enumerate(TIERS)}


def _decimal_or_none(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _decimal_to_storage(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.quantize(Decimal("0.01"))
    return format(normalized, "f")


def build_pi_item_catalog() -> list[dict]:
    items: list[dict] = []
    for name in sorted(P0_TO_P1.keys()):
        items.append(_catalog_entry(name, "P0"))
    for name in ALL_P1:
        items.append(_catalog_entry(name, "P1"))
    for name in ALL_P2:
        items.append(_catalog_entry(name, "P2"))
    for name in ALL_P3:
        items.append(_catalog_entry(name, "P3"))
    for name in ALL_P4:
        items.append(_catalog_entry(name, "P4"))
    return items


def _catalog_entry(name: str, tier: str) -> dict:
    type_id = sde.find_type_id_by_name(name) or 0
    return {
        "name": name,
        "tier": tier,
        "type_id": type_id,
        "display_name": sde.get_type_name(type_id) or name,
    }


def get_pi_catalog_maps() -> tuple[list[dict], dict[int, dict], dict[str, dict]]:
    catalog = build_pi_item_catalog()
    by_type_id = {int(item["type_id"]): item for item in catalog if int(item["type_id"] or 0)}
    by_name = {str(item["name"]): item for item in catalog}
    return catalog, by_type_id, by_name


def format_utc_compact(value) -> str:
    if not value:
        return ""
    dt = value if getattr(value, "tzinfo", None) else value.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y.%m.%d %H:%M")


def _price_value_map(type_id: int, quantity: int, price_entry: dict | None) -> dict[str, float | None]:
    best_buy = float((price_entry or {}).get("best_buy") or 0.0)
    best_sell = float((price_entry or {}).get("best_sell") or 0.0)
    split = ((best_buy + best_sell) / 2.0) if (best_buy or best_sell) else 0.0
    return {
        "buy": best_buy if best_buy > 0 else None,
        "sell": best_sell if best_sell > 0 else None,
        "split": split if split > 0 else None,
        "value_buy": (best_buy * quantity) if best_buy > 0 and quantity > 0 else None,
        "value_sell": (best_sell * quantity) if best_sell > 0 and quantity > 0 else None,
        "value_split": (split * quantity) if split > 0 and quantity > 0 else None,
    }


def recalculate_inventory_summary(db: Session, account_id: int, type_id: int, fallback_item: dict | None = None) -> InventoryItemSummary | None:
    active_lots = (
        db.query(InventoryLot)
        .filter(
            InventoryLot.account_id == int(account_id),
            InventoryLot.type_id == int(type_id),
            InventoryLot.quantity_remaining > 0,
        )
        .all()
    )
    quantity_on_hand = sum(int(row.quantity_remaining or 0) for row in active_lots)
    total_cost_basis = Decimal("0")
    costed_quantity = 0
    for row in active_lots:
        unit_cost = _decimal_or_none(row.unit_cost)
        if unit_cost is None:
            continue
        qty = int(row.quantity_remaining or 0)
        if qty <= 0:
            continue
        total_cost_basis += unit_cost * Decimal(qty)
        costed_quantity += qty

    summary = (
        db.query(InventoryItemSummary)
        .filter(
            InventoryItemSummary.account_id == int(account_id),
            InventoryItemSummary.type_id == int(type_id),
        )
        .first()
    )
    if quantity_on_hand <= 0:
        if summary is not None:
            db.delete(summary)
        return None

    if summary is None:
        source = fallback_item or {"name": f"Type {type_id}", "tier": "P1"}
        summary = InventoryItemSummary(
            account_id=int(account_id),
            type_id=int(type_id),
            item_name=str(source["name"]),
            tier=str(source["tier"]),
        )
        db.add(summary)

    summary.quantity_on_hand = int(quantity_on_hand)
    summary.weighted_average_cost = _decimal_to_storage(
        total_cost_basis / Decimal(costed_quantity)
    ) if costed_quantity > 0 else None

    if fallback_item:
        summary.item_name = str(fallback_item["name"])
        summary.tier = str(fallback_item["tier"])
    return summary


def add_inventory_lot(
    db: Session,
    *,
    account_id: int,
    item: dict,
    quantity: int,
    source_kind: str,
    unit_cost,
    note: str | None = None,
) -> InventoryLot:
    quantity = int(quantity)
    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")
    unit_cost_decimal = _decimal_or_none(unit_cost)
    if source_kind == "purchase" and unit_cost_decimal is None:
        raise ValueError("Purchase batches require a unit cost.")

    total_cost = None
    if unit_cost_decimal is not None:
        total_cost = unit_cost_decimal * Decimal(quantity)

    lot = InventoryLot(
        account_id=int(account_id),
        type_id=int(item["type_id"]),
        item_name=str(item["name"]),
        tier=str(item["tier"]),
        quantity_added=quantity,
        quantity_remaining=quantity,
        unit_cost=_decimal_to_storage(unit_cost_decimal),
        total_cost=_decimal_to_storage(total_cost),
        source_kind=str(source_kind),
        note=(note or "").strip() or None,
    )
    db.add(lot)
    recalculate_inventory_summary(db, int(account_id), int(item["type_id"]), fallback_item=item)
    return lot


def consume_inventory(
    db: Session,
    *,
    account_id: int,
    item: dict,
    quantity: int,
    reason: str,
    note: str | None = None,
) -> None:
    quantity = int(quantity)
    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")

    summary = (
        db.query(InventoryItemSummary)
        .filter(
            InventoryItemSummary.account_id == int(account_id),
            InventoryItemSummary.type_id == int(item["type_id"]),
        )
        .first()
    )
    on_hand = int(summary.quantity_on_hand or 0) if summary else 0
    if quantity > on_hand:
        raise ValueError("Not enough stock available for this adjustment.")

    remaining = quantity
    lots = (
        db.query(InventoryLot)
        .filter(
            InventoryLot.account_id == int(account_id),
            InventoryLot.type_id == int(item["type_id"]),
            InventoryLot.quantity_remaining > 0,
        )
        .order_by(InventoryLot.created_at.asc(), InventoryLot.id.asc())
        .all()
    )
    for lot in lots:
        available = int(lot.quantity_remaining or 0)
        if available <= 0:
            continue
        taken = min(available, remaining)
        lot.quantity_remaining = available - taken
        remaining -= taken
        if remaining <= 0:
            break
    if remaining > 0:
        raise ValueError("Stock consumption could not be completed.")

    db.add(InventoryAdjustment(
        account_id=int(account_id),
        type_id=int(item["type_id"]),
        item_name=str(item["name"]),
        tier=str(item["tier"]),
        delta_quantity=-int(quantity),
        reason=str(reason),
        note=(note or "").strip() or None,
    ))
    recalculate_inventory_summary(db, int(account_id), int(item["type_id"]), fallback_item=item)


def adjust_inventory(
    db: Session,
    *,
    account_id: int,
    item: dict,
    direction: str,
    quantity: int,
    note: str | None = None,
    unit_cost=None,
) -> None:
    if direction == "add":
        add_inventory_lot(
            db,
            account_id=account_id,
            item=item,
            quantity=quantity,
            source_kind="adjustment",
            unit_cost=unit_cost,
            note=note,
        )
        db.add(InventoryAdjustment(
            account_id=int(account_id),
            type_id=int(item["type_id"]),
            item_name=str(item["name"]),
            tier=str(item["tier"]),
            delta_quantity=int(quantity),
            reason="manual_add",
            note=(note or "").strip() or None,
        ))
        return
    if direction == "remove":
        consume_inventory(
            db,
            account_id=account_id,
            item=item,
            quantity=quantity,
            reason="manual_remove",
            note=note,
        )
        return
    raise ValueError("Unknown adjustment direction.")


def get_inventory_rows(db: Session, account_id: int, tier: str | None = None) -> list[dict]:
    query = db.query(InventoryItemSummary).filter(
        InventoryItemSummary.account_id == int(account_id),
        InventoryItemSummary.quantity_on_hand > 0,
    )
    if tier and tier in TIER_SORT:
        query = query.filter(InventoryItemSummary.tier == tier)
    rows = query.all()
    price_map = get_prices_by_type_ids([int(row.type_id) for row in rows], db) if rows else {}
    result = []
    for row in rows:
        weighted = _decimal_or_none(row.weighted_average_cost)
        quantity = int(row.quantity_on_hand or 0)
        price_values = _price_value_map(int(row.type_id), quantity, price_map.get(int(row.type_id)))
        estimated = weighted * Decimal(quantity) if weighted is not None else None
        result.append({
            "type_id": int(row.type_id),
            "item_name": row.item_name,
            "tier": row.tier,
            "quantity_on_hand": quantity,
            "weighted_average_cost": float(weighted) if weighted is not None else None,
            "estimated_value": float(estimated) if estimated is not None else None,
            "jita_buy": price_values["buy"],
            "jita_sell": price_values["sell"],
            "jita_split": price_values["split"],
            "estimated_value_buy": price_values["value_buy"],
            "estimated_value_sell": price_values["value_sell"],
            "estimated_value_split": price_values["value_split"],
        })
    result.sort(key=lambda item: (TIER_SORT.get(item["tier"], 99), item["item_name"].casefold()))
    return result


def get_inventory_summary_map(db: Session, account_id: int) -> dict[str, dict]:
    rows = get_inventory_rows(db, account_id)
    return {
        row["item_name"]: {
            "type_id": row["type_id"],
            "tier": row["tier"],
            "quantity_on_hand": row["quantity_on_hand"],
            "weighted_average_cost": row["weighted_average_cost"],
            "estimated_value": row["estimated_value"],
        }
        for row in rows
    }


def get_inventory_item_detail(db: Session, account_id: int, type_id: int) -> dict | None:
    summary = (
        db.query(InventoryItemSummary)
        .filter(
            InventoryItemSummary.account_id == int(account_id),
            InventoryItemSummary.type_id == int(type_id),
        )
        .first()
    )
    if summary is None:
        return None

    quantity_on_hand = int(summary.quantity_on_hand or 0)
    weighted = _decimal_or_none(summary.weighted_average_cost)
    price_entry = get_prices_by_type_ids([int(type_id)], db).get(int(type_id), {})
    price_values = _price_value_map(int(type_id), quantity_on_hand, price_entry)

    lots = (
        db.query(InventoryLot)
        .filter(
            InventoryLot.account_id == int(account_id),
            InventoryLot.type_id == int(type_id),
        )
        .order_by(InventoryLot.created_at.desc(), InventoryLot.id.desc())
        .all()
    )
    adjustments = (
        db.query(InventoryAdjustment)
        .filter(
            InventoryAdjustment.account_id == int(account_id),
            InventoryAdjustment.type_id == int(type_id),
        )
        .order_by(InventoryAdjustment.created_at.desc(), InventoryAdjustment.id.desc())
        .all()
    )

    transactions: list[dict] = []
    for lot in lots:
        transactions.append({
            "kind": "batch",
            "label": lot.source_kind.replace("_", " ").title(),
            "quantity_delta": int(lot.quantity_added or 0),
            "quantity_remaining": int(lot.quantity_remaining or 0),
            "unit_cost": float(_decimal_or_none(lot.unit_cost)) if _decimal_or_none(lot.unit_cost) is not None else None,
            "total_cost": float(_decimal_or_none(lot.total_cost)) if _decimal_or_none(lot.total_cost) is not None else None,
            "note": lot.note or "",
            "created_at": format_utc_compact(lot.created_at),
            "created_sort": lot.created_at.isoformat() if lot.created_at else "",
        })
    for adjustment in adjustments:
        transactions.append({
            "kind": "adjustment",
            "label": adjustment.reason.replace("_", " ").title(),
            "quantity_delta": int(adjustment.delta_quantity or 0),
            "quantity_remaining": None,
            "unit_cost": None,
            "total_cost": None,
            "note": adjustment.note or "",
            "created_at": format_utc_compact(adjustment.created_at),
            "created_sort": adjustment.created_at.isoformat() if adjustment.created_at else "",
        })
    transactions.sort(key=lambda entry: entry["created_sort"], reverse=True)

    return {
        "type_id": int(summary.type_id),
        "item_name": summary.item_name,
        "tier": summary.tier,
        "quantity_on_hand": quantity_on_hand,
        "weighted_average_cost": float(weighted) if weighted is not None else None,
        "jita_buy": price_values["buy"],
        "jita_sell": price_values["sell"],
        "jita_split": price_values["split"],
        "estimated_value_buy": price_values["value_buy"],
        "estimated_value_sell": price_values["value_sell"],
        "estimated_value_split": price_values["value_split"],
        "transactions": transactions,
    }


def sync_inventory_summaries(db: Session, account_id: int) -> None:
    catalog, by_type_id, _by_name = get_pi_catalog_maps()
    type_ids = {
        int(row.type_id)
        for row in db.query(InventoryLot.type_id).filter(InventoryLot.account_id == int(account_id)).distinct().all()
    }
    type_ids |= {
        int(row.type_id)
        for row in db.query(InventoryItemSummary.type_id).filter(InventoryItemSummary.account_id == int(account_id)).distinct().all()
    }
    for type_id in type_ids:
        fallback = by_type_id.get(int(type_id)) or next((item for item in catalog if int(item["type_id"] or 0) == int(type_id)), None)
        recalculate_inventory_summary(db, int(account_id), int(type_id), fallback_item=fallback)
    db.flush()
