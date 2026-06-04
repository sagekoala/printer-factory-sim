"""REST API entry point for the 3D Printer Production Simulator.

Routes are intentionally thin: they validate the incoming Pydantic payload,
delegate to a service in :mod:`manufacturer.simulation`,
:mod:`manufacturer.sales_orders`, or :mod:`manufacturer.services.suppliers`,
and serialise the result. No business logic lives in this module.

Start the server::

    uvicorn manufacturer.main:app --reload --port 8002

Interactive docs::

    http://localhost:8002/docs
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from manufacturer.database import (
    BOMEntryRow,
    FactoryConfigRow,
    ManufacturingOrderRow,
    OutboundPurchaseOrderRow,
    ProductRow,
    PurchaseOrderRow,
    get_db,
    init_db,
)
from manufacturer.models import (
    ManufacturingOrder,
    ManufacturingOrderStatus,
    PurchaseOrder,
    PurchaseOrderStatus,
)
from manufacturer.sales_orders import (
    create_sales_order,
    ensure_defaults,
    get_capacity_info,
    get_finished_stock,
    get_production_status,
    get_sales_order,
    get_wholesale_prices,
    list_sales_orders,
    set_wholesale_price,
)
from manufacturer.services.suppliers import (
    ProviderHTTPError,
    ProviderUnreachableError,
    get_catalog as fetch_provider_catalog,
    list_providers,
    list_purchase_orders as list_outbound_purchase_orders,
    place_order as place_outbound_order,
)
from manufacturer.simulation import advance_day


# ---------------------------------------------------------------------------
# Lifespan — runs once at startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables on startup if they do not yet exist."""
    init_db()
    yield


app = FastAPI(
    title="3D Printer Production Simulator",
    description=(
        "REST API for managing and observing a discrete-event simulation of a "
        "3D printer factory. Step time with ``POST /api/day/advance`` and "
        "inspect state through the inventory / orders / sales endpoints."
    ),
    version="0.2.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request / Response models (API-layer only)
# ---------------------------------------------------------------------------


class FactoryStatus(BaseModel):
    current_day: int
    total_completed_printers: int


class AdvanceDayResponse(BaseModel):
    previous_day: int
    current_day: int


class InventoryItem(BaseModel):
    """Per-part inventory snapshot with derived planning fields.

    - **committed**: units reserved by pending manufacturing orders.
    - **in_transit**: units on open (pending/shipped) purchase orders.
    - **deficit**: ``max(0, committed − current_stock)``.
    """

    id: uuid.UUID
    name: str
    current_stock: int
    storage_size: int
    committed: int
    in_transit: int
    deficit: int


class ConfiguredSupplier(BaseModel):
    name: str
    url: str


class CreateOutboundPurchaseRequest(BaseModel):
    supplier_name: str = Field(..., min_length=1)
    product_id: str = Field(..., min_length=1)
    quantity: int = Field(..., gt=0)


class OutboundPurchaseOrderResponse(BaseModel):
    id: str
    provider_name: str
    provider_order_id: str
    product_name: str
    quantity: int
    placed_day: int
    expected_delivery_day: int
    status: str
    unit_price: float | None = None
    total_price: float | None = None
    delivered_day: int | None = None


class CreateSalesOrderRequest(BaseModel):
    retailer_name: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    quantity: int = Field(..., gt=0)


class SetWholesalePriceRequest(BaseModel):
    price: float = Field(..., ge=0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _current_day(db: Session) -> int:
    row = db.query(FactoryConfigRow).filter(FactoryConfigRow.key == "current_day").first()
    return int(row.value) if row else 0


def _provider_url_or_404(name: str) -> str:
    for entry in list_providers():
        if entry["name"] == name:
            return entry["url"]
    raise HTTPException(status_code=404, detail=f"Unknown supplier: {name!r}")


# ---------------------------------------------------------------------------
# ORM → Pydantic mappers
# ---------------------------------------------------------------------------


def _build_inventory_item(
    row: ProductRow,
    committed_by_part: dict[str, int],
    in_transit_by_part: dict[str, int],
) -> InventoryItem:
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
    """Simple liveness probe."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


@app.get("/inventory", response_model=list[InventoryItem], tags=["Inventory"])
def get_inventory(db: Session = Depends(get_db)) -> list[InventoryItem]:
    """Return parts with on-hand stock and derived planning fields."""
    rows = db.query(ProductRow).order_by(ProductRow.name).all()

    bom_entries = db.query(BOMEntryRow).all()
    bom_by_part = {e.part_id: e.quantity_per_unit for e in bom_entries}

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
# Orders (legacy Week 5 internal orders)
# ---------------------------------------------------------------------------


@app.get("/orders/manufacturing", response_model=list[ManufacturingOrder], tags=["Orders"])
def get_manufacturing_orders(
    status: Optional[ManufacturingOrderStatus] = Query(default=None),
    db: Session = Depends(get_db),
) -> list[ManufacturingOrder]:
    query = db.query(ManufacturingOrderRow)
    if status is not None:
        query = query.filter(ManufacturingOrderRow.status == status.value)
    rows = query.order_by(ManufacturingOrderRow.created_at).all()
    return [_map_manufacturing_order(r) for r in rows]


@app.get("/orders/purchase", response_model=list[PurchaseOrder], tags=["Orders"])
def get_purchase_orders(
    status: Optional[PurchaseOrderStatus] = Query(default=None),
    db: Session = Depends(get_db),
) -> list[PurchaseOrder]:
    query = db.query(PurchaseOrderRow)
    if status is not None:
        query = query.filter(PurchaseOrderRow.status == status.value)
    rows = query.order_by(PurchaseOrderRow.created_at).all()
    return [_map_purchase_order(r) for r in rows]


# ---------------------------------------------------------------------------
# Factory status
# ---------------------------------------------------------------------------


@app.get("/factory/status", response_model=FactoryStatus, tags=["Factory"])
def get_factory_status(db: Session = Depends(get_db)) -> FactoryStatus:
    current_day = _current_day(db)
    completed = (
        db.query(ManufacturingOrderRow)
        .filter(ManufacturingOrderRow.status == ManufacturingOrderStatus.completed.value)
        .count()
    )
    return FactoryStatus(current_day=current_day, total_completed_printers=completed)


@app.post("/simulation/advance", response_model=AdvanceDayResponse, tags=["Simulation"])
def simulation_advance(db: Session = Depends(get_db)) -> AdvanceDayResponse:
    """Advance the simulation by one day (Week 5 endpoint)."""
    previous_day = _current_day(db)
    new_day = advance_day(db)
    return AdvanceDayResponse(previous_day=previous_day, current_day=new_day)


# ---------------------------------------------------------------------------
# Provider integration (outbound purchases)
# ---------------------------------------------------------------------------


@app.get("/api/suppliers", response_model=list[ConfiguredSupplier], tags=["Provider Integration"])
def api_list_suppliers() -> list[ConfiguredSupplier]:
    return [ConfiguredSupplier(**row) for row in list_providers()]


@app.get("/api/suppliers/{name}/catalog", tags=["Provider Integration"])
def api_supplier_catalog(name: str) -> list[dict]:
    url = _provider_url_or_404(name)
    try:
        return fetch_provider_catalog(url)
    except ProviderHTTPError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except ProviderUnreachableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post(
    "/api/purchase",
    response_model=OutboundPurchaseOrderResponse,
    tags=["Provider Integration"],
)
def api_create_outbound_purchase(
    payload: CreateOutboundPurchaseRequest,
    db: Session = Depends(get_db),
) -> OutboundPurchaseOrderResponse:
    url = _provider_url_or_404(payload.supplier_name)
    try:
        row = place_outbound_order(
            db,
            provider_url=url,
            supplier_name=payload.supplier_name,
            product_id=payload.product_id,
            quantity=payload.quantity,
            current_day=_current_day(db),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProviderHTTPError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except ProviderUnreachableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Translate the services.suppliers ``supplier_name`` field back to
    # ``provider_name`` to preserve the public API contract.
    row["provider_name"] = row.pop("supplier_name")
    return OutboundPurchaseOrderResponse(**row)


@app.get(
    "/api/purchase",
    response_model=list[OutboundPurchaseOrderResponse],
    tags=["Provider Integration"],
)
def api_list_outbound_purchases(db: Session = Depends(get_db)) -> list[OutboundPurchaseOrderResponse]:
    rows: list[dict] = []
    for row in list_outbound_purchase_orders(db):
        row["provider_name"] = row.pop("supplier_name")
        rows.append(row)
    return [OutboundPurchaseOrderResponse(**row) for row in rows]


# ---------------------------------------------------------------------------
# Sales (inbound from retailers — Week 7 additions)
# ---------------------------------------------------------------------------


@app.get("/api/catalog", tags=["Sales"])
def api_get_catalog(db: Session = Depends(get_db)) -> list[dict]:
    ensure_defaults(db)
    return get_wholesale_prices(db)


@app.get("/api/stock", tags=["Sales"])
def api_get_finished_stock(db: Session = Depends(get_db)) -> list[dict]:
    ensure_defaults(db)
    return get_finished_stock(db)


@app.post("/api/orders", tags=["Sales"])
def api_create_sales_order(
    payload: CreateSalesOrderRequest,
    db: Session = Depends(get_db),
) -> dict:
    ensure_defaults(db)
    try:
        return create_sales_order(
            db,
            retailer_name=payload.retailer_name,
            model=payload.model,
            quantity=payload.quantity,
            placed_day=_current_day(db),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/orders", tags=["Sales"])
def api_list_sales_orders(
    status: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> list[dict]:
    return list_sales_orders(db, status=status)


@app.get("/api/orders/{order_id}", tags=["Sales"])
def api_get_sales_order(order_id: str, db: Session = Depends(get_db)) -> dict:
    order = get_sales_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Sales order {order_id!r} not found")
    return order


@app.get("/api/capacity", tags=["Sales"])
def api_get_capacity(db: Session = Depends(get_db)) -> dict:
    return get_capacity_info(db)


@app.get("/api/prices", tags=["Sales"])
def api_get_prices(db: Session = Depends(get_db)) -> list[dict]:
    ensure_defaults(db)
    return get_wholesale_prices(db)


@app.post("/api/prices/{model}", tags=["Sales"])
def api_set_price(
    model: str,
    payload: SetWholesalePriceRequest,
    db: Session = Depends(get_db),
) -> dict:
    return set_wholesale_price(db, model, payload.price, _current_day(db))


@app.get("/api/production/status", tags=["Sales"])
def api_production_status(db: Session = Depends(get_db)) -> dict:
    return get_production_status(db)


# ---------------------------------------------------------------------------
# Turn-engine endpoints
# ---------------------------------------------------------------------------


@app.post("/api/day/advance", tags=["Turn Engine"])
def api_day_advance(db: Session = Depends(get_db)) -> dict:
    """Advance the manufacturer simulation by one day.

    ``simulation.advance_day`` already runs ``advance_sales_orders`` with the
    correct ``printers_built`` count, so this endpoint only seeds defaults
    and returns the new day. Calling ``advance_sales_orders`` again would
    double-count today's production into finished stock.
    """
    ensure_defaults(db)
    previous_day = _current_day(db)
    new_day = advance_day(db)
    return {"previous_day": previous_day, "current_day": new_day}


@app.get("/api/day/current", tags=["Turn Engine"])
def api_day_current(db: Session = Depends(get_db)) -> dict:
    return {"current_day": _current_day(db)}
