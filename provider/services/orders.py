"""Order services for provider app."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from provider.db import (
    OrderRow,
    OrderStatus,
    PricingTierRow,
    ProductRow,
    StockRow,
    get_current_day,
    log_event,
)


def create_order(db: Session, buyer: str, product_id: str, quantity: int) -> dict:
    if quantity <= 0:
        raise ValueError("quantity must be > 0")
    if not buyer.strip():
        raise ValueError("buyer is required")

    product = db.query(ProductRow).filter(ProductRow.id == product_id).first()
    if product is None:
        raise ValueError(f"Product not found: {product_id}")

    stock = db.query(StockRow).filter(StockRow.product_id == product.id).first()
    available = stock.quantity if stock else 0
    if available < quantity:
        raise ValueError(
            f"Insufficient stock for {product.name}. Requested={quantity}, available={available}"
        )

    tier = (
        db.query(PricingTierRow)
        .filter(
            PricingTierRow.product_id == product.id,
            PricingTierRow.min_quantity <= quantity,
        )
        .order_by(PricingTierRow.min_quantity.desc())
        .first()
    )
    if tier is None:
        raise ValueError(f"No pricing tier configured for product: {product.name}")

    current_day = get_current_day(db)
    lead_time = max(1, int(product.lead_time_days))
    expected_delivery_day = current_day + lead_time
    unit_price = Decimal(str(tier.unit_price))
    total_price = unit_price * Decimal(quantity)

    order = OrderRow(
        id=str(uuid.uuid4()),
        buyer=buyer,
        product_id=product.id,
        quantity=quantity,
        unit_price=unit_price,
        total_price=total_price,
        placed_day=current_day,
        expected_delivery_day=expected_delivery_day,
        status=OrderStatus.pending.value,
    )
    db.add(order)

    log_event(
        db,
        sim_day=current_day,
        event_type="ORDER_CREATED",
        entity_type="order",
        entity_id=order.id,
        detail=(
            f"Order placed by {buyer} for {quantity}x {product.name} at {unit_price}/unit; "
            f"expected delivery day {expected_delivery_day}"
        ),
    )

    db.commit()
    db.refresh(order)
    return _serialize_order(order)


def list_orders(db: Session, status: OrderStatus | None = None) -> list[dict]:
    query = db.query(OrderRow)
    if status is not None:
        query = query.filter(OrderRow.status == status.value)
    rows = query.order_by(OrderRow.placed_day, OrderRow.id).all()
    return [_serialize_order(row) for row in rows]


def get_order(db: Session, order_id: str) -> dict | None:
    row = db.query(OrderRow).filter(OrderRow.id == order_id).first()
    if row is None:
        return None
    return _serialize_order(row)


def _serialize_order(row: OrderRow) -> dict:
    return {
        "id": row.id,
        "buyer": row.buyer,
        "product_id": row.product_id,
        "quantity": row.quantity,
        "unit_price": float(row.unit_price),
        "total_price": float(row.total_price),
        "placed_day": row.placed_day,
        "expected_delivery_day": row.expected_delivery_day,
        "shipped_day": row.shipped_day,
        "delivered_day": row.delivered_day,
        "status": row.status,
    }
