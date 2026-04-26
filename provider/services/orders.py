"""Order lifecycle services for the provider app.

Public API
----------
- :func:`create_order` — validate, price, and persist a new order
- :func:`get_orders` — list orders, optionally filtered by status
- :func:`get_order` — fetch a single order by id

Pricing follows a *volume-break* model: the applicable tier is the one
with the largest ``min_quantity`` that is ``<=`` the requested quantity.

Lead-time floor (the *ironclad rule*)
-------------------------------------
Even if a product is configured with ``lead_time_days = 0`` or ``1``,
``expected_delivery_day`` is always at least ``current_day + 1``.  This
prevents same-day deliveries which would be physically implausible.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

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
from provider.services.exceptions import InsufficientStockError, NotFoundError

# Lead-time floor: orders never deliver same-day, regardless of how a
# product is configured.
MIN_LEAD_TIME_DAYS = 1


# ---------------------------------------------------------------------------
# Write-side
# ---------------------------------------------------------------------------


def create_order(
    db: Session,
    buyer: str,
    product_id: str,
    quantity: int,
) -> dict:
    """Create a new ``PENDING`` order and write an ``order_placed`` event.

    Steps:
        1. Validate ``buyer`` and ``quantity``.
        2. Look up the product (404 if missing).
        3. Verify on-hand stock covers ``quantity`` (409 if not).
        4. Pick the matching pricing tier (highest ``min_quantity``
           still ``<=`` ``quantity``).
        5. Compute ``total_price`` and ``expected_delivery_day``,
           clamped by :data:`MIN_LEAD_TIME_DAYS`.
        6. Insert the order, log ``order_placed``, commit, and return
           the serialised order.

    Raises:
        ValueError: ``buyer`` is empty or ``quantity`` is not positive.
        NotFoundError: ``product_id`` does not match any product.
        InsufficientStockError: requested quantity exceeds on-hand stock.
    """
    if not buyer or not buyer.strip():
        raise ValueError("buyer is required")
    if quantity <= 0:
        raise ValueError("quantity must be > 0")

    product = db.query(ProductRow).filter(ProductRow.id == product_id).first()
    if product is None:
        raise NotFoundError(f"Product not found: {product_id}")

    stock = db.query(StockRow).filter(StockRow.product_id == product.id).first()
    available = stock.quantity if stock else 0
    if available < quantity:
        raise InsufficientStockError(
            f"Insufficient stock for {product.name}: "
            f"requested={quantity}, available={available}"
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
        # No tier covers this quantity — typically means the smallest
        # configured tier has ``min_quantity > quantity``.
        raise ValueError(
            f"No pricing tier configured for {product.name} at quantity={quantity}"
        )

    sim_day = get_current_day(db)
    lead_time = max(MIN_LEAD_TIME_DAYS, int(product.lead_time_days))
    expected_delivery_day = sim_day + lead_time
    unit_price = Decimal(str(tier.unit_price))
    total_price = unit_price * Decimal(quantity)

    order = OrderRow(
        id=str(uuid.uuid4()),
        buyer=buyer.strip(),
        product_id=product.id,
        quantity=quantity,
        unit_price=unit_price,
        total_price=total_price,
        placed_day=sim_day,
        expected_delivery_day=expected_delivery_day,
        status=OrderStatus.PENDING.value,
    )
    db.add(order)

    log_event(
        db,
        sim_day=sim_day,
        event_type="order_placed",
        entity_type="order",
        entity_id=order.id,
        detail=(
            f"{buyer} placed order {order.id} for {quantity}x {product.name} "
            f"@ {unit_price}/unit (total {total_price}); "
            f"expected delivery day {expected_delivery_day}"
        ),
    )

    db.commit()
    db.refresh(order)
    return _serialize_order(order)


# ---------------------------------------------------------------------------
# Read-side
# ---------------------------------------------------------------------------


def get_orders(db: Session, status: Optional[OrderStatus] = None) -> list[dict]:
    """Return all orders sorted by placement, optionally filtered by status."""
    query = db.query(OrderRow)
    if status is not None:
        query = query.filter(OrderRow.status == status.value)
    rows = query.order_by(OrderRow.placed_day, OrderRow.id).all()
    return [_serialize_order(row) for row in rows]


def get_order(db: Session, order_id: str) -> Optional[dict]:
    """Return one order by id or ``None`` if it does not exist."""
    row = db.query(OrderRow).filter(OrderRow.id == order_id).first()
    if row is None:
        return None
    return _serialize_order(row)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _serialize_order(row: OrderRow) -> dict:
    """Convert an :class:`OrderRow` to the public dict representation."""
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


__all__ = ["create_order", "get_orders", "get_order", "MIN_LEAD_TIME_DAYS"]
