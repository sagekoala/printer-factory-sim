"""Simulation engine for the 3D Printer Production Simulator.

Public functions
----------------
:func:`advance_day`
    The main simulation clock tick.  Call once per simulated day to run all
    four production phases and persist the results.

:func:`create_purchase_order`
    Operator action: place a restocking order with a supplier.  Looks up the
    ``SupplierCatalog`` for pricing and lead time, then creates a
    ``PurchaseOrder`` row that ``advance_day`` will deliver automatically.

:func:`release_manufacturing_order`
    Operator action: immediately attempt to build a specific pending order,
    bypassing the daily capacity ceiling.  Used by the dashboard's "Release
    for Production" button.

Advance Day — four phases
--------------------------
1. **Day increment** — ``current_day`` in ``FactoryConfig`` is bumped by 1.
2. **PO delivery** — every pending ``PurchaseOrder`` with ``lead_time_remaining``
   set has its counter decremented; those reaching zero are marked ``delivered``
   and their parts are added to ``current_stock``.
3. **Demand generation** — between ``demand_min`` and ``demand_max`` new
   single-printer ``ManufacturingOrder`` rows are created.
4. **Order fulfilment** — pending ``ManufacturingOrder`` rows are processed
   FIFO; each is fulfilled if stock satisfies the BOM *and* the day's capacity
   ceiling has not been reached.  Under-stocked orders are skipped (not
   cancelled) so they will be retried on subsequent days.

Every significant action writes a row to the ``events`` table for auditing
and chart history.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

try:
    from manufacturer.database import (
        BOMEntryRow,
        EventRow,
        FactoryConfigRow,
        ManufacturingOrderRow,
        OutboundPurchaseOrderRow,
        ProductRow,
        PurchaseOrderRow,
        SupplierCatalogRow,
        SupplierRow,
    )
    from manufacturer.models import EventType, ManufacturingOrderStatus, PurchaseOrderStatus
    from manufacturer.provider_integration import sync_outbound_purchase_orders
except ModuleNotFoundError:
    from database import (
        BOMEntryRow,
        EventRow,
        FactoryConfigRow,
        ManufacturingOrderRow,
        OutboundPurchaseOrderRow,
        ProductRow,
        PurchaseOrderRow,
        SupplierCatalogRow,
        SupplierRow,
    )
    from models import EventType, ManufacturingOrderStatus, PurchaseOrderStatus
    from provider_integration import sync_outbound_purchase_orders

# ---------------------------------------------------------------------------
# Constants (all overridable via factory_config table)
# ---------------------------------------------------------------------------

PRODUCT_NAME = "Pro 3D Printer"

_DEFAULT_DEMAND_MIN: int = 5
_DEFAULT_DEMAND_MAX: int = 15
_DEFAULT_CAPACITY_PER_DAY: int = 10

# Stable sentinel UUID used as entity_id for factory-level events
_FACTORY_ENTITY_ID = "00000000-0000-0000-0000-000000000001"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def advance_day(db: Session) -> int:
    """Advance the simulation by one day.

    Runs all four simulation phases in order, commits the session, and
    returns the new simulation day number.

    Args:
        db: An active SQLAlchemy :class:`~sqlalchemy.orm.Session`.

    Returns:
        The new current simulation day (1-based).
    """
    day = _increment_day(db)
    sync_outbound_purchase_orders(db, day)
    _deliver_purchase_orders(db, day)
    _generate_demand(db, day)
    _fulfill_manufacturing_orders(db, day)
    db.commit()
    return day


def create_purchase_order(
    db: Session,
    part_id: str,
    supplier_id: str,
    quantity: int,
) -> tuple[PurchaseOrderRow | None, str]:
    """Create and persist a :class:`~database.PurchaseOrderRow`.

    Looks up the ``SupplierCatalog`` to obtain the locked-in unit price and
    lead time, then initialises ``lead_time_remaining`` so the simulation's
    daily delivery phase can count it down automatically.

    Args:
        db: Active SQLAlchemy session.
        part_id: ID of the part to order.
        supplier_id: ID of the chosen supplier.
        quantity: Units to order (must meet the supplier's minimum).

    Returns:
        ``(po, "")`` on success, or ``(None, error_message)`` on failure.
        The caller is responsible for nothing further — the session is
        committed inside this function.
    """
    catalog = (
        db.query(SupplierCatalogRow)
        .filter(
            SupplierCatalogRow.supplier_id == supplier_id,
            SupplierCatalogRow.part_id == part_id,
        )
        .first()
    )
    if catalog is None:
        return None, "No catalog entry found for this supplier/part combination."
    if quantity < catalog.min_order_qty:
        return None, (
            f"Quantity {quantity} is below the supplier minimum of {catalog.min_order_qty}."
        )

    po_id = str(uuid.uuid4())
    po = PurchaseOrderRow(
        id=po_id,
        part_id=part_id,
        supplier_id=supplier_id,
        quantity=quantity,
        unit_price=catalog.unit_price,
        status=PurchaseOrderStatus.pending.value,
        created_at=datetime.utcnow(),
        lead_time_remaining=catalog.lead_time_days,
    )
    db.add(po)

    day = int(_get_config(db, "current_day", 0))
    _log(
        db, day,
        EventType.PURCHASE_CREATED,
        entity_type="purchase_order",
        entity_id=po_id,
        description=(
            f"Day {day}: PO created — {quantity}x part ordered from supplier "
            f"(lead time: {catalog.lead_time_days} days, "
            f"${catalog.unit_price}/unit)"
        ),
        extra={
            "part_id": part_id,
            "supplier_id": supplier_id,
            "quantity": quantity,
            "unit_price": str(catalog.unit_price),
            "lead_time_days": catalog.lead_time_days,
        },
    )
    db.commit()
    return po, ""


def release_manufacturing_order(
    db: Session,
    mo_id: str,
) -> tuple[bool, str]:
    """Immediately attempt to build a specific pending MO.

    Checks BOM stock, consumes components, and marks the order ``completed``
    in a single operation.  Unlike :func:`advance_day`, this does not consume
    daily production capacity — it is an explicit operator override.

    Args:
        db: Active SQLAlchemy session.
        mo_id: Primary key of the :class:`~database.ManufacturingOrderRow`.

    Returns:
        ``(True, "")`` on success, ``(False, reason)`` if the MO cannot be
        fulfilled.  The session is committed on success only.
    """
    mo = db.query(ManufacturingOrderRow).filter(ManufacturingOrderRow.id == mo_id).first()
    if mo is None:
        return False, f"Manufacturing order {mo_id!r} not found."
    if mo.status != ManufacturingOrderStatus.pending.value:
        return False, f"Order is not pending (current status: {mo.status!r})."

    bom = db.query(BOMEntryRow).all()
    if not bom:
        return False, "No Bill of Materials is defined — cannot build printers."

    shortfall = _bom_shortfall(db, bom, mo.quantity)
    if shortfall:
        parts_list = ", ".join(f"{name}: need {n} more" for name, n in shortfall.items())
        return False, f"Insufficient stock — {parts_list}."

    # Consume BOM components
    for entry in bom:
        part = db.query(ProductRow).filter(ProductRow.id == entry.part_id).first()
        part.current_stock -= entry.quantity_per_unit * mo.quantity

    mo.status = ManufacturingOrderStatus.completed.value
    mo.started_at = datetime.utcnow()
    mo.completed_at = datetime.utcnow()
    mo.days_elapsed = 0  # Manually released; not driven by the daily tick

    day = int(_get_config(db, "current_day", 0))
    _log(
        db, day,
        EventType.PRODUCTION_COMPLETED,
        entity_type="manufacturing_order",
        entity_id=mo_id,
        description=(
            f"Day {day}: MO manually released — "
            f"completed {mo.quantity}x {PRODUCT_NAME}"
        ),
        extra={"product": PRODUCT_NAME, "quantity_built": mo.quantity, "manual": True},
    )
    db.commit()
    return True, ""


def export_state(db: Session) -> dict:
    """Export the full simulation state as a serializable snapshot dict."""
    def _dt(d: datetime | None) -> str | None:
        return d.isoformat() if d else None

    return {
        "version": "1.0",
        "exported_at": datetime.utcnow().isoformat(),
        "data": {
            "factory_config": [
                {"key": r.key, "value": r.value}
                for r in db.query(FactoryConfigRow).all()
            ],
            "suppliers": [
                {"id": r.id, "name": r.name, "contact_email": r.contact_email}
                for r in db.query(SupplierRow).all()
            ],
            "products": [
                {
                    "id": r.id,
                    "name": r.name,
                    "current_stock": r.current_stock,
                    "storage_size": r.storage_size,
                }
                for r in db.query(ProductRow).all()
            ],
            "supplier_catalog": [
                {
                    "id": r.id,
                    "supplier_id": r.supplier_id,
                    "part_id": r.part_id,
                    "unit_price": str(r.unit_price),
                    "min_order_qty": r.min_order_qty,
                    "lead_time_days": r.lead_time_days,
                }
                for r in db.query(SupplierCatalogRow).all()
            ],
            "bom_entries": [
                {"id": r.id, "part_id": r.part_id, "quantity_per_unit": r.quantity_per_unit}
                for r in db.query(BOMEntryRow).all()
            ],
            "manufacturing_orders": [
                {
                    "id": r.id,
                    "quantity": r.quantity,
                    "status": r.status,
                    "created_at": _dt(r.created_at),
                    "started_at": _dt(r.started_at),
                    "completed_at": _dt(r.completed_at),
                    "days_elapsed": r.days_elapsed,
                }
                for r in db.query(ManufacturingOrderRow).all()
            ],
            "purchase_orders": [
                {
                    "id": r.id,
                    "part_id": r.part_id,
                    "supplier_id": r.supplier_id,
                    "quantity": r.quantity,
                    "unit_price": str(r.unit_price),
                    "status": r.status,
                    "created_at": _dt(r.created_at),
                    "ship_date": _dt(r.ship_date),
                    "delivered_at": _dt(r.delivered_at),
                    "lead_time_remaining": r.lead_time_remaining,
                }
                for r in db.query(PurchaseOrderRow).all()
            ],
            "events": [
                {
                    "id": r.id,
                    "day": r.day,
                    "event_type": r.event_type,
                    "entity_type": r.entity_type,
                    "entity_id": r.entity_id,
                    "description": r.description,
                    "event_metadata": r.event_metadata,
                }
                for r in db.query(EventRow).all()
            ],
            "outbound_purchase_orders": [
                {
                    "id": r.id,
                    "provider_name": r.provider_name,
                    "provider_order_id": r.provider_order_id,
                    "product_name": r.product_name,
                    "quantity": r.quantity,
                    "placed_day": r.placed_day,
                    "expected_delivery_day": r.expected_delivery_day,
                    "status": r.status,
                }
                for r in db.query(OutboundPurchaseOrderRow).all()
            ],
        },
    }


def import_state(db: Session, snapshot: dict) -> None:
    """Replace the full simulation state from a previously exported snapshot."""
    required_tables = {
        "factory_config",
        "suppliers",
        "products",
        "supplier_catalog",
        "bom_entries",
        "manufacturing_orders",
        "purchase_orders",
        "events",
    }
    data = snapshot.get("data", {})
    missing = required_tables - set(data.keys())
    if missing:
        raise ValueError(f"Snapshot is missing required table(s): {sorted(missing)}")

    def _dt(s: str | None) -> datetime | None:
        return datetime.fromisoformat(s) if s else None

    db.query(EventRow).delete()
    db.query(PurchaseOrderRow).delete()
    db.query(OutboundPurchaseOrderRow).delete()
    db.query(ManufacturingOrderRow).delete()
    db.query(SupplierCatalogRow).delete()
    db.query(BOMEntryRow).delete()
    db.query(ProductRow).delete()
    db.query(SupplierRow).delete()
    db.query(FactoryConfigRow).delete()
    db.flush()

    for r in data["factory_config"]:
        db.add(FactoryConfigRow(key=r["key"], value=r["value"]))

    for r in data["suppliers"]:
        db.add(SupplierRow(id=r["id"], name=r["name"], contact_email=r.get("contact_email")))

    for r in data["products"]:
        db.add(ProductRow(
            id=r["id"],
            name=r["name"],
            current_stock=r["current_stock"],
            storage_size=r["storage_size"],
        ))

    for r in data["supplier_catalog"]:
        db.add(SupplierCatalogRow(
            id=r["id"],
            supplier_id=r["supplier_id"],
            part_id=r["part_id"],
            unit_price=Decimal(r["unit_price"]),
            min_order_qty=r["min_order_qty"],
            lead_time_days=r["lead_time_days"],
        ))

    for r in data["bom_entries"]:
        db.add(BOMEntryRow(
            id=r["id"],
            part_id=r["part_id"],
            quantity_per_unit=r["quantity_per_unit"],
        ))

    for r in data["manufacturing_orders"]:
        db.add(ManufacturingOrderRow(
            id=r["id"],
            quantity=r["quantity"],
            status=r["status"],
            created_at=_dt(r.get("created_at")),
            started_at=_dt(r.get("started_at")),
            completed_at=_dt(r.get("completed_at")),
            days_elapsed=r.get("days_elapsed"),
        ))

    for r in data["purchase_orders"]:
        db.add(PurchaseOrderRow(
            id=r["id"],
            part_id=r["part_id"],
            supplier_id=r["supplier_id"],
            quantity=r["quantity"],
            unit_price=Decimal(r["unit_price"]),
            status=r["status"],
            created_at=_dt(r.get("created_at")),
            ship_date=_dt(r.get("ship_date")),
            delivered_at=_dt(r.get("delivered_at")),
            lead_time_remaining=r.get("lead_time_remaining"),
        ))

    for r in data["events"]:
        db.add(EventRow(
            id=r["id"],
            day=r["day"],
            event_type=r["event_type"],
            entity_type=r["entity_type"],
            entity_id=r["entity_id"],
            description=r["description"],
            event_metadata=r.get("event_metadata"),
        ))

    for r in data.get("outbound_purchase_orders", []):
        db.add(OutboundPurchaseOrderRow(
            id=r["id"],
            provider_name=r["provider_name"],
            provider_order_id=r["provider_order_id"],
            product_name=r["product_name"],
            quantity=r["quantity"],
            placed_day=r["placed_day"],
            expected_delivery_day=r["expected_delivery_day"],
            status=r["status"],
        ))

    db.commit()


# ---------------------------------------------------------------------------
# Phase 1 — Day increment
# ---------------------------------------------------------------------------


def _increment_day(db: Session) -> int:
    """Bump *current_day* in FactoryConfig and return the new value."""
    current = int(_get_config(db, "current_day", 0))
    new_day = current + 1
    _set_config(db, "current_day", new_day)
    return new_day


# ---------------------------------------------------------------------------
# Phase 2 — Purchase order delivery
# ---------------------------------------------------------------------------


def _deliver_purchase_orders(db: Session, day: int) -> None:
    """Decrement lead-time counters on pending POs; deliver those at zero.

    Only processes POs that have ``lead_time_remaining`` set.  POs created
    outside the simulation (e.g. manual API calls) must initialise this field
    to the supplier's ``lead_time_days`` value when the PO is created.
    """
    pending_pos = (
        db.query(PurchaseOrderRow)
        .filter(
            PurchaseOrderRow.status == PurchaseOrderStatus.pending.value,
            PurchaseOrderRow.lead_time_remaining.isnot(None),
        )
        .all()
    )

    for po in pending_pos:
        po.lead_time_remaining -= 1

        if po.lead_time_remaining > 0:
            continue

        part = db.query(ProductRow).filter(ProductRow.id == po.part_id).first()
        if part is None:
            continue

        part.current_stock += po.quantity
        po.status = PurchaseOrderStatus.delivered.value
        po.delivered_at = datetime.utcnow()

        _log(
            db, day,
            EventType.PURCHASE_DELIVERED,
            entity_type="purchase_order",
            entity_id=po.id,
            description=(
                f"Day {day}: PO delivered — +{po.quantity}x {part.name} "
                f"(stock now {part.current_stock})"
            ),
            extra={
                "part_id": po.part_id,
                "part_name": part.name,
                "quantity_received": po.quantity,
                "new_stock": part.current_stock,
            },
        )


# ---------------------------------------------------------------------------
# Phase 3 — Demand generation
# ---------------------------------------------------------------------------


def _generate_demand(db: Session, day: int) -> None:
    """Create between *demand_min* and *demand_max* new single-printer MOs.

    Each generated ``ManufacturingOrder`` represents a customer order for
    one ``Pro 3D Printer`` and starts in the ``pending`` state.
    """
    demand_min = int(_get_config(db, "demand_min", _DEFAULT_DEMAND_MIN))
    demand_max = int(_get_config(db, "demand_max", _DEFAULT_DEMAND_MAX))
    count = random.randint(demand_min, demand_max)

    for _ in range(count):
        mo_id = str(uuid.uuid4())
        db.add(ManufacturingOrderRow(
            id=mo_id,
            quantity=1,
            status=ManufacturingOrderStatus.pending.value,
            created_at=datetime.utcnow(),
        ))
        _log(
            db, day,
            EventType.ORDER_CREATED,
            entity_type="manufacturing_order",
            entity_id=mo_id,
            description=f"Day {day}: New customer demand — 1x {PRODUCT_NAME} queued",
            extra={"product": PRODUCT_NAME, "quantity": 1},
        )


# ---------------------------------------------------------------------------
# Phase 4 — Manufacturing order fulfilment
# ---------------------------------------------------------------------------


def _fulfill_manufacturing_orders(db: Session, day: int) -> None:
    """Attempt to fulfil pending MOs within today's production capacity.

    Processes MOs oldest-first (FIFO).  For each MO:

    * Checks available capacity (printers built so far this day vs ceiling).
    * Checks BOM stock for the required number of printers.
    * If both checks pass: consumes parts, marks the MO completed, logs it.
    * If stock is insufficient: skips the MO and moves to the next.

    The loop exits early once the daily capacity ceiling is reached.
    """
    capacity = int(_get_config(db, "capacity_per_day", _DEFAULT_CAPACITY_PER_DAY))
    bom: list[BOMEntryRow] = db.query(BOMEntryRow).all()

    if not bom:
        return  # Cannot build anything without a Bill of Materials

    pending_mos = (
        db.query(ManufacturingOrderRow)
        .filter(ManufacturingOrderRow.status == ManufacturingOrderStatus.pending.value)
        .order_by(ManufacturingOrderRow.created_at)
        .all()
    )

    printers_built = 0

    for mo in pending_mos:
        remaining_capacity = capacity - printers_built
        if remaining_capacity <= 0:
            break

        # Build as many units from this MO as capacity and quantity allow
        to_build = min(mo.quantity, remaining_capacity)

        shortfall = _bom_shortfall(db, bom, to_build)
        if shortfall:
            # Log the skip so the operator can see why the MO was not started
            _log(
                db, day,
                EventType.STOCK_ADJUSTED,
                entity_type="manufacturing_order",
                entity_id=mo.id,
                description=(
                    f"Day {day}: MO skipped — insufficient stock for "
                    f"{to_build}x {PRODUCT_NAME}: {shortfall}"
                ),
                extra={"shortfall": shortfall, "to_build": to_build},
            )
            continue

        # Consume BOM components from inventory
        for entry in bom:
            part = db.query(ProductRow).filter(ProductRow.id == entry.part_id).first()
            part.current_stock -= entry.quantity_per_unit * to_build

        mo.status = ManufacturingOrderStatus.completed.value
        mo.started_at = datetime.utcnow()
        mo.completed_at = datetime.utcnow()
        mo.days_elapsed = 1
        printers_built += to_build

        _log(
            db, day,
            EventType.PRODUCTION_COMPLETED,
            entity_type="manufacturing_order",
            entity_id=mo.id,
            description=f"Day {day}: Completed {to_build}x {PRODUCT_NAME}",
            extra={"product": PRODUCT_NAME, "quantity_built": to_build},
        )

    if printers_built > 0:
        _log(
            db, day,
            EventType.PRODUCTION_COMPLETED,
            entity_type="factory",
            entity_id=_FACTORY_ENTITY_ID,
            description=(
                f"Day {day}: Factory produced {printers_built} printer(s). "
                f"Capacity utilisation: {printers_built}/{capacity}."
            ),
            extra={
                "day": day,
                "printers_built": printers_built,
                "capacity": capacity,
            },
        )


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------


def _bom_shortfall(
    db: Session,
    bom: list[BOMEntryRow],
    quantity: int,
) -> dict[str, int]:
    """Return ``{part_name: units_short}`` for any under-stocked BOM parts.

    An empty dict means all parts are sufficiently stocked for *quantity*
    printers.
    """
    shortfall: dict[str, int] = {}
    for entry in bom:
        part = db.query(ProductRow).filter(ProductRow.id == entry.part_id).first()
        needed = entry.quantity_per_unit * quantity
        available = part.current_stock if part else 0
        if available < needed:
            name = part.name if part else entry.part_id
            shortfall[name] = needed - available
    return shortfall


def _get_config(db: Session, key: str, default: int | float) -> int | float:
    """Read a value from the ``factory_config`` table; return *default* if absent."""
    row = db.query(FactoryConfigRow).filter(FactoryConfigRow.key == key).first()
    return row.value if row else default


def _set_config(db: Session, key: str, value: int | float) -> None:
    """Upsert a single key in the ``factory_config`` table."""
    row = db.query(FactoryConfigRow).filter(FactoryConfigRow.key == key).first()
    if row is None:
        db.add(FactoryConfigRow(key=key, value=value))
    else:
        row.value = value


def _log(
    db: Session,
    day: int,
    event_type: EventType,
    entity_type: str,
    entity_id: str,
    description: str,
    extra: dict | None = None,
) -> None:
    """Append one row to the ``events`` table.

    Args:
        db: Active SQLAlchemy session.  The row is added but not committed;
            the caller is responsible for committing when the full operation
            is complete.
        day: Current simulation day number.
        event_type: Categorises the event for filtering and charting.
        entity_type: Human-readable table name of the affected entity
            (e.g. ``"manufacturing_order"``, ``"purchase_order"``).
        entity_id: Primary key of the affected row (stored as a string).
        description: Human-readable summary shown in the dashboard log.
        extra: Optional dict of structured metadata serialised to JSON.
    """
    db.add(EventRow(
        id=str(uuid.uuid4()),
        day=day,
        event_type=event_type.value,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
        event_metadata=extra or {},
    ))
