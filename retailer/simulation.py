"""Day-advance simulation logic for the Retailer."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

try:
    from retailer.database import (
        CatalogRow,
        CustomerOrderRow,
        EventRow,
        PurchaseOrderRow,
        SalesHistoryRow,
        SimStateRow,
        StockRow,
    )
    from retailer.manufacturer_integration import poll_manufacturer_order
except ModuleNotFoundError:
    from database import (
        CatalogRow,
        CustomerOrderRow,
        EventRow,
        PurchaseOrderRow,
        SalesHistoryRow,
        SimStateRow,
        StockRow,
    )
    from manufacturer_integration import poll_manufacturer_order


def get_current_day(db: Session) -> int:
    row = db.query(SimStateRow).filter(SimStateRow.key == "current_day").first()
    return int(row.value) if row else 0


def advance_day(db: Session, manufacturer_url: str) -> int:
    day = _increment_day(db)
    _sync_purchase_orders(db, day, manufacturer_url)
    _auto_fulfill_backorders(db, day)
    db.commit()
    return day


def _increment_day(db: Session) -> int:
    row = db.query(SimStateRow).filter(SimStateRow.key == "current_day").first()
    current = int(row.value) if row else 0
    new_day = current + 1
    if row is None:
        db.add(SimStateRow(key="current_day", value=str(new_day)))
    else:
        row.value = str(new_day)
    return new_day


def _sync_purchase_orders(db: Session, day: int, manufacturer_url: str) -> None:
    active = (
        db.query(PurchaseOrderRow)
        .filter(
            PurchaseOrderRow.status.in_(["pending", "confirmed", "in_progress", "shipped"]),
            PurchaseOrderRow.manufacturer_order_id.isnot(None),
        )
        .all()
    )

    for po in active:
        try:
            remote = poll_manufacturer_order(manufacturer_url, po.manufacturer_order_id)
        except Exception as exc:
            db.add(EventRow(
                id=str(uuid.uuid4()),
                day=day,
                event_type="MANUFACTURER_SYNC_ERROR",
                entity_type="purchase_order",
                entity_id=po.id,
                description=f"Day {day}: Failed to poll manufacturer for order {po.manufacturer_order_id}: {exc}",
            ))
            continue

        remote_status = remote.get("status", po.status)
        if remote_status == po.status:
            continue

        po.status = remote_status

        if remote_status == "delivered":
            po.delivered_day = day
            stock = db.query(StockRow).filter(StockRow.model == po.model).first()
            if stock is None:
                stock = StockRow(model=po.model, quantity=0)
                db.add(stock)
            stock.quantity += po.quantity
            db.add(EventRow(
                id=str(uuid.uuid4()),
                day=day,
                event_type="PURCHASE_DELIVERED",
                entity_type="purchase_order",
                entity_id=po.id,
                description=f"Day {day}: Received {po.quantity}x {po.model} from manufacturer",
            ))


def _auto_fulfill_backorders(db: Session, day: int) -> None:
    backordered = (
        db.query(CustomerOrderRow)
        .filter(CustomerOrderRow.status == "backordered")
        .order_by(CustomerOrderRow.created_at)
        .all()
    )

    for order in backordered:
        stock = db.query(StockRow).filter(StockRow.model == order.model).first()
        if stock is None or stock.quantity < order.quantity:
            continue
        stock.quantity -= order.quantity
        order.status = "fulfilled"
        order.fulfilled_at = datetime.utcnow()
        db.add(EventRow(
            id=str(uuid.uuid4()),
            day=day,
            event_type="ORDER_FULFILLED",
            entity_type="customer_order",
            entity_id=order.id,
            description=f"Day {day}: Backordered auto-fulfilled — {order.quantity}x {order.model} to {order.customer}",
        ))


def export_state(db: Session) -> dict:
    def _dt(d):
        return d.isoformat() if d else None

    return {
        "version": "1.0",
        "data": {
            "sim_state": [{"key": r.key, "value": r.value} for r in db.query(SimStateRow).all()],
            "catalog": [{"model": r.model, "retail_price": r.retail_price} for r in db.query(CatalogRow).all()],
            "stock": [{"model": r.model, "quantity": r.quantity} for r in db.query(StockRow).all()],
            "customer_orders": [
                {
                    "id": r.id, "customer": r.customer, "model": r.model,
                    "quantity": r.quantity, "status": r.status,
                    "created_at": _dt(r.created_at), "fulfilled_at": _dt(r.fulfilled_at),
                }
                for r in db.query(CustomerOrderRow).all()
            ],
            "purchase_orders": [
                {
                    "id": r.id, "model": r.model, "quantity": r.quantity,
                    "unit_price": r.unit_price, "total_price": r.total_price,
                    "status": r.status, "placed_day": r.placed_day,
                    "manufacturer_order_id": r.manufacturer_order_id,
                    "delivered_day": r.delivered_day, "created_at": _dt(r.created_at),
                }
                for r in db.query(PurchaseOrderRow).all()
            ],
            "events": [
                {
                    "id": r.id, "day": r.day, "event_type": r.event_type,
                    "entity_type": r.entity_type, "entity_id": r.entity_id,
                    "description": r.description,
                }
                for r in db.query(EventRow).all()
            ],
        },
    }


def import_state(db: Session, snapshot: dict) -> None:
    data = snapshot.get("data", {})

    def _dt(s):
        return datetime.fromisoformat(s) if s else None

    db.query(EventRow).delete()
    db.query(SalesHistoryRow).delete()
    db.query(PurchaseOrderRow).delete()
    db.query(CustomerOrderRow).delete()
    db.query(StockRow).delete()
    db.query(CatalogRow).delete()
    db.query(SimStateRow).delete()
    db.flush()

    for r in data.get("sim_state", []):
        db.add(SimStateRow(key=r["key"], value=r["value"]))
    for r in data.get("catalog", []):
        db.add(CatalogRow(model=r["model"], retail_price=r["retail_price"]))
    for r in data.get("stock", []):
        db.add(StockRow(model=r["model"], quantity=r["quantity"]))
    for r in data.get("customer_orders", []):
        db.add(CustomerOrderRow(
            id=r["id"], customer=r["customer"], model=r["model"],
            quantity=r["quantity"], status=r["status"],
            created_at=_dt(r.get("created_at")), fulfilled_at=_dt(r.get("fulfilled_at")),
        ))
    for r in data.get("purchase_orders", []):
        db.add(PurchaseOrderRow(
            id=r["id"], model=r["model"], quantity=r["quantity"],
            unit_price=r["unit_price"], total_price=r["total_price"],
            status=r["status"], placed_day=r["placed_day"],
            manufacturer_order_id=r.get("manufacturer_order_id"),
            delivered_day=r.get("delivered_day"), created_at=_dt(r.get("created_at")),
        ))
    for r in data.get("events", []):
        db.add(EventRow(
            id=r["id"], day=r["day"], event_type=r["event_type"],
            entity_type=r["entity_type"], entity_id=r["entity_id"],
            description=r["description"],
        ))
    db.commit()
