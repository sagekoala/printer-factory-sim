"""REST API entry point for the 3D Printer Production Simulator.

This module wires FastAPI routes to the simulation and database layers.
All business logic lives in ``simulation.py``; all persistence lives in
``database.py``.  This file is intentionally thin: routes validate input,
call a service function, and serialise the result.

Start the server
----------------
    uvicorn manufacturer.main:app --reload

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

try:
    from manufacturer.database import (
        FactoryConfigRow,
        ManufacturingOrderRow,
        ProductRow,
        PurchaseOrderRow,
        get_db,
        init_db,
    )
    from manufacturer.models import (
        ManufacturingOrder,
        ManufacturingOrderStatus,
        Product,
        PurchaseOrder,
        PurchaseOrderStatus,
    )
    from manufacturer.simulation import advance_day
except ModuleNotFoundError:
    from database import (
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
        Product,
        PurchaseOrder,
        PurchaseOrderStatus,
    )
    from simulation import advance_day
from pydantic import BaseModel


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


# ---------------------------------------------------------------------------
# ORM-row → Pydantic mappers
# ---------------------------------------------------------------------------


def _map_product(row: ProductRow) -> Product:
    """Convert a :class:`~database.ProductRow` ORM object to a :class:`~models.Product`.

    String IDs stored in SQLite are cast back to ``uuid.UUID`` so the Pydantic
    model's type contract is satisfied.
    """
    return Product(
        id=uuid.UUID(row.id),
        name=row.name,
        current_stock=row.current_stock,
        storage_size=row.storage_size,
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
    response_model=list[Product],
    tags=["Inventory"],
)
def get_inventory(db: Session = Depends(get_db)) -> list[Product]:
    """Return all parts and their current on-hand stock levels.

    Each entry includes the part's unique ID, name, current stock quantity,
    and the storage-space size it consumes per unit in the warehouse.
    Results are sorted alphabetically by part name.
    """
    rows = db.query(ProductRow).order_by(ProductRow.name).all()
    return [_map_product(r) for r in rows]


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
