"""REST API entry point for the 3D Printer Production Simulator.

This module wires FastAPI routes to the simulation and database layers.
All business logic lives in ``simulation.py``; all persistence lives in
``database.py``.  This file is intentionally thin: routes validate input,
call a service function, and serialise the result.

Start the server
----------------
    uvicorn main:app --reload

Interactive docs
----------------
    http://localhost:8000/docs    (Swagger UI)
    http://localhost:8000/redoc   (ReDoc)
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy.orm import Session

from database import (
    BOMEntryRow,
    FactoryConfigRow,
    ManufacturingOrderRow,
    ProductRow,
    PurchaseOrderRow,
    get_db,
    init_db,
)
from models import (
    ManufacturingOrder,
    ManufacturingOrderStatus,
    PurchaseOrder,
    PurchaseOrderStatus,
)
from pydantic import BaseModel
from simulation import advance_day


# ---------------------------------------------------------------------------
# Lifespan — runs once at startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables on startup if they do not yet exist."""
    init_db()
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


app = FastAPI(
    title="3D Printer Production Simulator",
    description=(
        "REST API for managing and observing a discrete-event simulation of a "
        "3D printer factory.  Use the `/simulation/advance` endpoint to step "
        "through time, and the inventory / order endpoints to inspect state."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Response-only models (not in models.py because they are API-layer concerns)
# ---------------------------------------------------------------------------


class FactoryStatus(BaseModel):
    """Summary of the factory's current operational state."""

    current_day: int
    total_completed_printers: int


class AdvanceDayResponse(BaseModel):
    """Returned after successfully advancing the simulation by one day."""

    previous_day: int
    current_day: int


class InventoryItem(BaseModel):
    """Per-part inventory snapshot with derived planning fields.

    - **committed**: units reserved by pending manufacturing orders (not yet
      consumed from stock).
    - **in_transit**: units on open (pending/shipped) purchase orders not yet
      delivered to the warehouse.
    - **deficit**: units short relative to committed demand
      (``max(0, committed - current_stock)``).
    """

    id: uuid.UUID
    name: str
    current_stock: int
    storage_size: int
    committed: int
    in_transit: int
    deficit: int


# ---------------------------------------------------------------------------
# ORM-row → Pydantic mappers
# ---------------------------------------------------------------------------


def _build_inventory_item(
    row: ProductRow,
    committed_by_part: dict[str, int],
    in_transit_by_part: dict[str, int],
) -> InventoryItem:
    """Build an :class:`InventoryItem` from a product row and pre-computed dicts.

    ``committed_by_part`` maps part ID → units required by pending MOs.
    ``in_transit_by_part`` maps part ID → units on open (pending/shipped) POs.
    ``deficit`` is the shortfall of on-hand stock against committed demand.
    """
    committed = committed_by_part.get(row.id, 0)
    in_transit = in_transit_by_part.get(row.id, 0)
    deficit = max(0, committed - row.current_stock)
    return InventoryItem(
        id=uuid.UUID(row.id),
        name=row.name,
        current_stock=row.current_stock,
        storage_size=row.storage_size,
        committed=committed,
        in_transit=in_transit,
        deficit=deficit,
    )


def _map_manufacturing_order(row: ManufacturingOrderRow) -> ManufacturingOrder:
    """Convert a :class:`~database.ManufacturingOrderRow` to a :class:`~models.ManufacturingOrder`.

    The ``status`` string is coerced to the :class:`~models.ManufacturingOrderStatus`
    enum so FastAPI can serialise it consistently.
    """
    return ManufacturingOrder(
        id=uuid.UUID(row.id),
        quantity=row.quantity,
        status=ManufacturingOrderStatus(row.status),
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        days_elapsed=row.days_elapsed,
    )


def _map_purchase_order(row: PurchaseOrderRow) -> PurchaseOrder:
    """Convert a :class:`~database.PurchaseOrderRow` to a :class:`~models.PurchaseOrder`.

    ``unit_price`` is stored as ``Numeric(10, 2)`` in SQLite and returned as a
    string by some drivers; wrapping it in ``Decimal(str(...))`` normalises this
    before Pydantic validates the field.
    """
    active_statuses = {PurchaseOrderStatus.pending.value, PurchaseOrderStatus.shipped.value}
    days_to_arrival = row.lead_time_remaining if row.status in active_statuses else None
    return PurchaseOrder(
        id=uuid.UUID(row.id),
        part_id=uuid.UUID(row.part_id),
        supplier_id=uuid.UUID(row.supplier_id),
        quantity=row.quantity,
        unit_price=Decimal(str(row.unit_price)),
        status=PurchaseOrderStatus(row.status),
        created_at=row.created_at,
        ship_date=row.ship_date,
        delivered_at=row.delivered_at,
        lead_time_remaining=row.lead_time_remaining,
        days_to_arrival=days_to_arrival,
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health", tags=["Health"])
def health() -> dict[str, str]:
    """Return a simple liveness check.

    Use this to verify the API process is running before making other calls.
    """
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Inventory endpoints
# ---------------------------------------------------------------------------


@app.get(
    "/inventory",
    response_model=list[InventoryItem],
    tags=["Inventory"],
)
def get_inventory(db: Session = Depends(get_db)) -> list[InventoryItem]:
    """Return all parts with on-hand stock and derived planning fields.

    Each entry includes:

    - **current_stock**: units physically in the warehouse right now.
    - **committed**: units required by *pending* manufacturing orders (BOM
      quantity × order quantity, summed across all pending MOs).
    - **in_transit**: units on open purchase orders (``pending`` or
      ``shipped``) not yet delivered.
    - **deficit**: units short of committed demand (``max(0, committed −
      current_stock)``).

    Results are sorted alphabetically by part name.
    """
    rows = db.query(ProductRow).order_by(ProductRow.name).all()

    # BOM lookup: part_id → units required per printer
    bom_entries = db.query(BOMEntryRow).all()
    bom_by_part: dict[str, int] = {e.part_id: e.quantity_per_unit for e in bom_entries}

    # Committed: sum BOM requirements across all pending MOs
    pending_mos = (
        db.query(ManufacturingOrderRow)
        .filter(ManufacturingOrderRow.status == ManufacturingOrderStatus.pending.value)
        .all()
    )
    committed_by_part: dict[str, int] = {}
    for mo in pending_mos:
        for part_id, qty_per_unit in bom_by_part.items():
            committed_by_part[part_id] = (
                committed_by_part.get(part_id, 0) + qty_per_unit * mo.quantity
            )

    # In-transit: sum quantities on open POs not yet delivered
    active_pos = (
        db.query(PurchaseOrderRow)
        .filter(
            PurchaseOrderRow.status.in_(
                [PurchaseOrderStatus.pending.value, PurchaseOrderStatus.shipped.value]
            )
        )
        .all()
    )
    in_transit_by_part: dict[str, int] = {}
    for po in active_pos:
        in_transit_by_part[po.part_id] = (
            in_transit_by_part.get(po.part_id, 0) + po.quantity
        )

    return [_build_inventory_item(r, committed_by_part, in_transit_by_part) for r in rows]


# ---------------------------------------------------------------------------
# Order endpoints
# ---------------------------------------------------------------------------


@app.get(
    "/orders/manufacturing",
    response_model=list[ManufacturingOrder],
    tags=["Orders"],
)
def get_manufacturing_orders(
    status: Optional[ManufacturingOrderStatus] = Query(
        default=None,
        description="Filter by order status. Omit to return all orders.",
    ),
    db: Session = Depends(get_db),
) -> list[ManufacturingOrder]:
    """Return manufacturing orders, optionally filtered by status.

    **Status values:** `pending` | `in_progress` | `completed` | `cancelled`

    Orders are returned oldest-first so the queue priority is immediately
    visible.  Omit the `status` parameter to retrieve all orders regardless
    of their state.
    """
    query = db.query(ManufacturingOrderRow)
    if status is not None:
        query = query.filter(ManufacturingOrderRow.status == status.value)
    rows = query.order_by(ManufacturingOrderRow.created_at).all()
    return [_map_manufacturing_order(r) for r in rows]


@app.get(
    "/orders/purchase",
    response_model=list[PurchaseOrder],
    tags=["Orders"],
)
def get_purchase_orders(
    status: Optional[PurchaseOrderStatus] = Query(
        default=None,
        description="Filter by order status. Omit to return all orders.",
    ),
    db: Session = Depends(get_db),
) -> list[PurchaseOrder]:
    """Return purchase orders placed with suppliers, optionally filtered by status.

    **Status values:** `pending` | `shipped` | `delivered` | `cancelled`

    Each purchase order includes the locked-in unit price, the supplier and
    part IDs, and the remaining lead-time days until the next simulated
    delivery.  Omit `status` to return all orders.
    """
    query = db.query(PurchaseOrderRow)
    if status is not None:
        query = query.filter(PurchaseOrderRow.status == status.value)
    rows = query.order_by(PurchaseOrderRow.created_at).all()
    return [_map_purchase_order(r) for r in rows]


# ---------------------------------------------------------------------------
# Factory status endpoint
# ---------------------------------------------------------------------------


@app.get(
    "/factory/status",
    response_model=FactoryStatus,
    tags=["Factory"],
)
def get_factory_status(db: Session = Depends(get_db)) -> FactoryStatus:
    """Return a high-level snapshot of the factory's current state.

    - **current_day**: The simulation day that was last fully processed.
      Day 0 means the simulation has not been started yet.
    - **total_completed_printers**: Cumulative count of manufacturing orders
      that have reached `completed` status since the simulation began.
    """
    day_row = db.query(FactoryConfigRow).filter(FactoryConfigRow.key == "current_day").first()
    current_day = int(day_row.value) if day_row else 0

    completed = (
        db.query(ManufacturingOrderRow)
        .filter(ManufacturingOrderRow.status == ManufacturingOrderStatus.completed.value)
        .count()
    )

    return FactoryStatus(current_day=current_day, total_completed_printers=completed)


# ---------------------------------------------------------------------------
# Simulation control endpoint
# ---------------------------------------------------------------------------


@app.post(
    "/simulation/advance",
    response_model=AdvanceDayResponse,
    tags=["Simulation"],
)
def simulation_advance(db: Session = Depends(get_db)) -> AdvanceDayResponse:
    """Advance the simulation by one day and return the updated day numbers.

    Each call triggers the full daily simulation cycle:

    1. **Day increment** — the factory clock moves forward by one day.
    2. **PO delivery** — purchase orders whose lead time has elapsed are
       delivered and stock levels are updated.
    3. **Demand generation** — between 5 and 15 new manufacturing orders are
       created to represent incoming customer demand.
    4. **Order fulfilment** — pending manufacturing orders are fulfilled in
       FIFO order up to the factory's daily production capacity (10 printers),
       consuming BOM components from inventory.

    All changes are persisted to the SQLite database before this endpoint
    returns.
    """
    day_row = db.query(FactoryConfigRow).filter(FactoryConfigRow.key == "current_day").first()
    previous_day = int(day_row.value) if day_row else 0

    new_day = advance_day(db)

    return AdvanceDayResponse(previous_day=previous_day, current_day=new_day)
