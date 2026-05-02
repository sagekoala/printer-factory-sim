"""Sales order management — Week 7 addition.

Handles orders received from retailers (inbound). Completely separate from:
- ManufacturingOrder: internal auto-generated demand
- OutboundPurchaseOrder: manufacturer buying parts from providers
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

try:
    from manufacturer.database import (
        EventRow,
        FactoryConfigRow,
        FinishedPrinterStockRow,
        ManufacturingOrderRow,
        SalesOrderRow,
        WholesalePriceRow,
    )
    from manufacturer.models import ManufacturingOrderStatus
except ModuleNotFoundError:
    from database import (
        EventRow,
        FactoryConfigRow,
        FinishedPrinterStockRow,
        ManufacturingOrderRow,
        SalesOrderRow,
        WholesalePriceRow,
    )
    from models import ManufacturingOrderStatus

PRODUCT_NAME = "Pro 3D Printer"
DEFAULT_WHOLESALE_PRICE = 1000.0


def ensure_defaults(db: Session) -> None:
    """Seed default wholesale prices and finished stock entries if absent."""
    if db.query(WholesalePriceRow).count() == 0:
        db.add(WholesalePriceRow(model=PRODUCT_NAME, price=DEFAULT_WHOLESALE_PRICE))
    if db.query(FinishedPrinterStockRow).filter(FinishedPrinterStockRow.model == PRODUCT_NAME).count() == 0:
        db.add(FinishedPrinterStockRow(model=PRODUCT_NAME, quantity=0))
    db.commit()


def create_sales_order(
    db: Session,
    retailer_name: str,
    model: str,
    quantity: int,
    placed_day: int,
) -> dict:
    price_row = db.query(WholesalePriceRow).filter(WholesalePriceRow.model == model).first()
    if price_row is None:
        raise ValueError(f"Model {model!r} not in wholesale catalog")

    order_id = str(uuid.uuid4())
    unit_price = float(price_row.price)
    order = SalesOrderRow(
        id=order_id,
        retailer_name=retailer_name,
        model=model,
        quantity=quantity,
        unit_price=unit_price,
        total_price=unit_price * quantity,
        status="pending",
        placed_day=placed_day,
        created_at=datetime.utcnow(),
    )
    db.add(order)
    db.add(EventRow(
        id=str(uuid.uuid4()),
        day=placed_day,
        event_type="sales_order_placed",
        entity_type="sales_order",
        entity_id=order_id,
        description=f"Day {placed_day}: Sales order from {retailer_name} — {quantity}x {model} @ ${unit_price:.2f}",
    ))
    db.commit()
    return _order_to_dict(order)


def list_sales_orders(db: Session, status: str | None = None) -> list[dict]:
    query = db.query(SalesOrderRow)
    if status:
        query = query.filter(SalesOrderRow.status == status)
    return [_order_to_dict(r) for r in query.order_by(SalesOrderRow.created_at).all()]


def get_sales_order(db: Session, order_id: str) -> dict | None:
    row = db.query(SalesOrderRow).filter(SalesOrderRow.id == order_id).first()
    return _order_to_dict(row) if row else None


def release_to_production(db: Session, order_id: str, current_day: int) -> tuple[bool, str]:
    order = db.query(SalesOrderRow).filter(SalesOrderRow.id == order_id).first()
    if order is None:
        return False, f"Sales order {order_id!r} not found"
    if order.status != "pending":
        return False, f"Order is not pending (status: {order.status!r})"
    order.status = "released"
    order.released_day = current_day
    db.add(EventRow(
        id=str(uuid.uuid4()),
        day=current_day,
        event_type="production_released",
        entity_type="sales_order",
        entity_id=order_id,
        description=f"Day {current_day}: Sales order {order_id} released to production",
    ))
    db.commit()
    return True, ""


def get_production_status(db: Session) -> dict:
    in_progress = db.query(SalesOrderRow).filter(SalesOrderRow.status == "released").all()
    finished = {r.model: r.quantity for r in db.query(FinishedPrinterStockRow).all()}
    cap_row = db.query(FactoryConfigRow).filter(FactoryConfigRow.key == "capacity_per_day").first()
    return {
        "released_orders": [_order_to_dict(o) for o in in_progress],
        "finished_printer_stock": finished,
        "daily_capacity": int(cap_row.value) if cap_row else 10,
    }


def get_capacity_info(db: Session) -> dict:
    cap_row = db.query(FactoryConfigRow).filter(FactoryConfigRow.key == "capacity_per_day").first()
    day_row = db.query(FactoryConfigRow).filter(FactoryConfigRow.key == "current_day").first()
    capacity = int(cap_row.value) if cap_row else 10
    current_day = int(day_row.value) if day_row else 0

    today_completed = (
        db.query(ManufacturingOrderRow)
        .filter(
            ManufacturingOrderRow.status == ManufacturingOrderStatus.completed.value,
            ManufacturingOrderRow.days_elapsed.isnot(None),
        )
        .count()
    )
    return {
        "capacity_per_day": capacity,
        "current_day": current_day,
        "utilization_estimate": today_completed,
    }


def advance_sales_orders(db: Session, day: int, newly_produced: int) -> list[dict]:
    """Called after advance_day(). Adds new printers to stock, then ships pending sales orders."""
    if newly_produced > 0:
        _add_to_finished_stock(db, PRODUCT_NAME, newly_produced, day)

    pending = (
        db.query(SalesOrderRow)
        .filter(SalesOrderRow.status.in_(["pending", "released"]))
        .order_by(SalesOrderRow.created_at)
        .all()
    )

    fulfilled = []
    for order in pending:
        stock = db.query(FinishedPrinterStockRow).filter(
            FinishedPrinterStockRow.model == order.model
        ).first()
        if stock is None or stock.quantity < order.quantity:
            continue
        stock.quantity -= order.quantity
        order.status = "delivered"
        order.shipped_day = day
        order.delivered_day = day
        db.add(EventRow(
            id=str(uuid.uuid4()),
            day=day,
            event_type="sales_order_delivered",
            entity_type="sales_order",
            entity_id=order.id,
            description=f"Day {day}: Delivered {order.quantity}x {order.model} to {order.retailer_name}",
        ))
        fulfilled.append(_order_to_dict(order))

    db.commit()
    return fulfilled


def get_wholesale_prices(db: Session) -> list[dict]:
    return [{"model": r.model, "price": r.price} for r in db.query(WholesalePriceRow).all()]


def set_wholesale_price(db: Session, model: str, price: float, current_day: int) -> dict:
    row = db.query(WholesalePriceRow).filter(WholesalePriceRow.model == model).first()
    if row is None:
        row = WholesalePriceRow(model=model, price=price)
        db.add(row)
    else:
        row.price = price
    db.add(EventRow(
        id=str(uuid.uuid4()),
        day=current_day,
        event_type="price_updated",
        entity_type="wholesale_price",
        entity_id=model,
        description=f"Day {current_day}: Wholesale price for {model} set to ${price:.2f}",
    ))
    db.commit()
    return {"model": model, "price": price}


def get_finished_stock(db: Session) -> list[dict]:
    return [{"model": r.model, "quantity": r.quantity} for r in db.query(FinishedPrinterStockRow).all()]


def _add_to_finished_stock(db: Session, model: str, quantity: int, day: int) -> None:
    row = db.query(FinishedPrinterStockRow).filter(FinishedPrinterStockRow.model == model).first()
    if row is None:
        row = FinishedPrinterStockRow(model=model, quantity=0)
        db.add(row)
    row.quantity += quantity
    db.add(EventRow(
        id=str(uuid.uuid4()),
        day=day,
        event_type="finished_stock_added",
        entity_type="finished_printer_stock",
        entity_id=model,
        description=f"Day {day}: +{quantity}x {model} added to finished stock (total: {row.quantity})",
    ))


def _order_to_dict(row: SalesOrderRow) -> dict:
    return {
        "id": row.id,
        "retailer_name": row.retailer_name,
        "model": row.model,
        "quantity": row.quantity,
        "unit_price": row.unit_price,
        "total_price": row.total_price,
        "status": row.status,
        "placed_day": row.placed_day,
        "released_day": row.released_day,
        "shipped_day": row.shipped_day,
        "delivered_day": row.delivered_day,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
