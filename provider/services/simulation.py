"""Simulation services for the provider app.

Public API
----------
- :func:`get_current_day` — current simulation day (``int``)
- :func:`advance_day` — move one day forward and run the daily cycle
- :func:`export_state` / :func:`import_state` — JSON snapshot helpers

Daily cycle (matches Week 6 spec exactly)
-----------------------------------------
For each call to :func:`advance_day`:

1. **Deliver due shipments** — every ``SHIPPED`` order whose
   ``expected_delivery_day <= current_day + 1`` transitions to
   ``DELIVERED``; an ``order_delivered`` event is logged.
2. **Process pending orders** — every ``PENDING`` order whose product
   has enough stock walks the full chain
   ``PENDING -> CONFIRMED -> IN_PROGRESS -> SHIPPED`` in a single
   advance.  Stock is decremented, and an event row is written for
   each transition.
3. **Increment** ``current_day`` in ``sim_state`` and log a
   ``day_advanced`` event.

The function returns a summary dict ``{day, orders_shipped,
orders_delivered}`` that the CLI/API can render directly.
"""

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
    get_current_day as _read_current_day,
    log_event,
    set_current_day,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_current_day(db: Session) -> int:
    """Return the current simulation day (``0`` if never set)."""
    return _read_current_day(db)


def advance_day(db: Session) -> dict:
    """Advance the simulation by one day.

    See module docstring for the full step ordering.  Returns::

        {"day": <new_day>, "orders_shipped": int, "orders_delivered": int}

    The transaction commits exactly once at the end so events and state
    changes are atomic.
    """
    current_day = _read_current_day(db)
    new_day = current_day + 1

    # Step 1: deliver shipped orders that are due.
    orders_delivered = _deliver_due_orders(db, new_day=new_day)

    # Step 2: ship pending orders that have stock.
    orders_shipped = _process_pending_orders(db, new_day=new_day)

    # Step 3: advance the clock and audit.
    set_current_day(db, new_day)
    log_event(
        db,
        sim_day=new_day,
        event_type="day_advanced",
        entity_type="sim_state",
        entity_id="current_day",
        detail=(
            f"Day advanced from {current_day} to {new_day} "
            f"(shipped={orders_shipped}, delivered={orders_delivered})"
        ),
    )
    db.commit()

    return {
        "day": new_day,
        "orders_shipped": orders_shipped,
        "orders_delivered": orders_delivered,
    }


# ---------------------------------------------------------------------------
# Snapshot helpers (used by `provider-cli export` / `import`)
# ---------------------------------------------------------------------------


def export_state(db: Session) -> dict:
    """Serialise the entire provider DB as a JSON-compatible snapshot."""
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
                {"product_id": row.product_id, "quantity": row.quantity}
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
                {"key": row.key, "value": row.value}
                for row in db.query(SimStateRow).all()
            ],
        },
    }


def import_state(db: Session, snapshot: dict) -> None:
    """Replace the provider DB contents with ``snapshot`` (destructive)."""
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
        created_at = (
            datetime.fromisoformat(row["created_at"])
            if row.get("created_at")
            else datetime.utcnow()
        )
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


# ---------------------------------------------------------------------------
# Internal step helpers
# ---------------------------------------------------------------------------


def _deliver_due_orders(db: Session, new_day: int) -> int:
    """Mark every shipped order whose due day has arrived as DELIVERED.

    The condition ``expected_delivery_day <= new_day`` matches the
    spec wording ``expected_delivery_day <= current_day + 1`` since
    ``new_day == current_day + 1`` at this point in the cycle.

    Returns the number of orders that transitioned.
    """
    orders = (
        db.query(OrderRow)
        .filter(
            OrderRow.status == OrderStatus.SHIPPED.value,
            OrderRow.expected_delivery_day <= new_day,
        )
        .order_by(OrderRow.expected_delivery_day, OrderRow.id)
        .all()
    )

    for order in orders:
        _transition(db, order, OrderStatus.DELIVERED, sim_day=new_day)
        order.delivered_day = new_day

    return len(orders)


def _process_pending_orders(db: Session, new_day: int) -> int:
    """Walk every fulfillable PENDING order through the full chain.

    For each pending order whose product has enough stock, the status
    moves ``PENDING -> CONFIRMED -> IN_PROGRESS -> SHIPPED`` in this
    single advance, decrementing stock and writing one event per
    transition.  Returns the count of orders shipped.
    """
    pending_orders = (
        db.query(OrderRow)
        .filter(OrderRow.status == OrderStatus.PENDING.value)
        .order_by(OrderRow.placed_day, OrderRow.id)
        .all()
    )

    shipped_count = 0
    for order in pending_orders:
        stock = db.query(StockRow).filter(StockRow.product_id == order.product_id).first()
        available = stock.quantity if stock else 0
        if available < order.quantity:
            # Not enough stock — leave PENDING and try again next day.
            continue

        _transition(db, order, OrderStatus.CONFIRMED, sim_day=new_day)
        _transition(db, order, OrderStatus.IN_PROGRESS, sim_day=new_day)

        # Decrement stock once production starts.
        if stock is not None:
            stock.quantity -= order.quantity

        _transition(db, order, OrderStatus.SHIPPED, sim_day=new_day)
        order.shipped_day = new_day
        shipped_count += 1

    return shipped_count


def _transition(
    db: Session,
    order: OrderRow,
    new_status: OrderStatus,
    sim_day: int,
) -> None:
    """Mutate ``order.status`` and log a transition-specific event.

    The event type is derived from ``new_status`` so each transition
    has a distinct, queryable type (``order_confirmed``,
    ``order_in_progress``, ``order_shipped``, ``order_delivered``).
    """
    old_status = order.status
    order.status = new_status.value
    log_event(
        db,
        sim_day=sim_day,
        event_type=f"order_{new_status.value}",
        entity_type="order",
        entity_id=order.id,
        detail=f"Order {order.id}: {old_status} -> {new_status.value}",
    )


__all__ = [
    "advance_day",
    "get_current_day",
    "export_state",
    "import_state",
]
