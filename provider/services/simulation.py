"""Simulation services for provider app."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from provider.db import (
    EventRow,
    OrderRow,
    OrderStatus,
    PricingTierRow,
    ProductRow,
    SimStateRow,
    StockRow,
    get_current_day,
    log_event,
    set_current_day,
)


def advance_day(db: Session) -> dict:
    """Advance the provider simulation by one day."""
    current_day = get_current_day(db)

    _deliver_due_orders(db, current_day)
    _process_pending_orders(db, current_day)

    new_day = current_day + 1
    set_current_day(db, new_day)
    db.commit()
    return {"previous_day": current_day, "current_day": new_day}


def current_day(db: Session) -> int:
    return get_current_day(db)


def export_state(db: Session) -> dict:
    """Export provider state as JSON-serializable dict."""
    return {
        "version": "1.0",
        "exported_at": datetime.utcnow().isoformat(),
        "data": {
            "products": [
                {
                    "id": row.id,
                    "name": row.name,
                    "description": row.description,
                    "lead_time_days": row.lead_time_days,
                }
                for row in db.query(ProductRow).all()
            ],
            "pricing_tiers": [
                {
                    "id": row.id,
                    "product_id": row.product_id,
                    "min_quantity": row.min_quantity,
                    "unit_price": str(row.unit_price),
                }
                for row in db.query(PricingTierRow).all()
            ],
            "stock": [
                {
                    "product_id": row.product_id,
                    "quantity": row.quantity,
                }
                for row in db.query(StockRow).all()
            ],
            "orders": [
                {
                    "id": row.id,
                    "buyer": row.buyer,
                    "product_id": row.product_id,
                    "quantity": row.quantity,
                    "unit_price": str(row.unit_price),
                    "total_price": str(row.total_price),
                    "placed_day": row.placed_day,
                    "expected_delivery_day": row.expected_delivery_day,
                    "shipped_day": row.shipped_day,
                    "delivered_day": row.delivered_day,
                    "status": row.status,
                }
                for row in db.query(OrderRow).all()
            ],
            "events": [
                {
                    "id": row.id,
                    "sim_day": row.sim_day,
                    "event_type": row.event_type,
                    "entity_type": row.entity_type,
                    "entity_id": row.entity_id,
                    "detail": row.detail,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in db.query(EventRow).all()
            ],
            "sim_state": [
                {
                    "key": row.key,
                    "value": row.value,
                }
                for row in db.query(SimStateRow).all()
            ],
        },
    }


def import_state(db: Session, snapshot: dict) -> None:
    """Import provider state from snapshot dict."""
    required = {"products", "pricing_tiers", "stock", "orders", "events", "sim_state"}
    data = snapshot.get("data", {})
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"Snapshot missing required table(s): {sorted(missing)}")

    db.query(EventRow).delete()
    db.query(OrderRow).delete()
    db.query(StockRow).delete()
    db.query(PricingTierRow).delete()
    db.query(ProductRow).delete()
    db.query(SimStateRow).delete()
    db.flush()

    for row in data["products"]:
        db.add(
            ProductRow(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                lead_time_days=int(row["lead_time_days"]),
            )
        )

    for row in data["pricing_tiers"]:
        db.add(
            PricingTierRow(
                id=row["id"],
                product_id=row["product_id"],
                min_quantity=int(row["min_quantity"]),
                unit_price=Decimal(row["unit_price"]),
            )
        )

    for row in data["stock"]:
        db.add(
            StockRow(
                product_id=row["product_id"],
                quantity=int(row["quantity"]),
            )
        )

    for row in data["orders"]:
        db.add(
            OrderRow(
                id=row["id"],
                buyer=row["buyer"],
                product_id=row["product_id"],
                quantity=int(row["quantity"]),
                unit_price=Decimal(row["unit_price"]),
                total_price=Decimal(row["total_price"]),
                placed_day=int(row["placed_day"]),
                expected_delivery_day=int(row["expected_delivery_day"]),
                shipped_day=row.get("shipped_day"),
                delivered_day=row.get("delivered_day"),
                status=row["status"],
            )
        )

    for row in data["events"]:
        created_at = datetime.fromisoformat(row["created_at"]) if row.get("created_at") else datetime.utcnow()
        db.add(
            EventRow(
                id=row["id"],
                sim_day=int(row["sim_day"]),
                event_type=row["event_type"],
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
                detail=row["detail"],
                created_at=created_at,
            )
        )

    for row in data["sim_state"]:
        db.add(SimStateRow(key=row["key"], value=str(row["value"])))

    db.commit()


def _deliver_due_orders(db: Session, sim_day: int) -> None:
    orders = (
        db.query(OrderRow)
        .filter(
            OrderRow.status == OrderStatus.shipped.value,
            OrderRow.expected_delivery_day == sim_day,
        )
        .all()
    )

    for order in orders:
        order.status = OrderStatus.delivered.value
        order.delivered_day = sim_day
        log_event(
            db,
            sim_day=sim_day,
            event_type="ORDER_STATUS_CHANGED",
            entity_type="order",
            entity_id=order.id,
            detail=f"Order {order.id} transitioned shipped -> delivered",
        )


def _process_pending_orders(db: Session, sim_day: int) -> None:
    pending_orders = (
        db.query(OrderRow)
        .filter(OrderRow.status == OrderStatus.pending.value)
        .order_by(OrderRow.placed_day, OrderRow.id)
        .all()
    )

    for order in pending_orders:
        stock = db.query(StockRow).filter(StockRow.product_id == order.product_id).first()
        available = stock.quantity if stock else 0
        if available < order.quantity:
            continue

        _set_order_status(db, order, OrderStatus.confirmed, sim_day)
        _set_order_status(db, order, OrderStatus.in_progress, sim_day)

        if stock is not None:
            stock.quantity -= order.quantity

        _set_order_status(db, order, OrderStatus.shipped, sim_day)
        order.shipped_day = sim_day


def _set_order_status(db: Session, order: OrderRow, new_status: OrderStatus, sim_day: int) -> None:
    old_status = order.status
    order.status = new_status.value
    log_event(
        db,
        sim_day=sim_day,
        event_type="ORDER_STATUS_CHANGED",
        entity_type="order",
        entity_id=order.id,
        detail=f"Order {order.id} transitioned {old_status} -> {new_status.value}",
    )
