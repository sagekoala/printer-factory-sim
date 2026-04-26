"""FastAPI surface for the provider app.

This module is a *thin* wrapper around :mod:`provider.services` — every
endpoint validates input via Pydantic, dispatches to a service
function, and translates raised service exceptions into HTTP errors:

================================  =====================
Service exception                 HTTP status
================================  =====================
:class:`NotFoundError`            ``404 Not Found``
:class:`InsufficientStockError`   ``409 Conflict``
:class:`ValueError` (other)       ``400 Bad Request``
================================  =====================

All error responses share the FastAPI default shape ``{"detail": "..."}``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from provider.db import OrderStatus, ensure_seeded, get_db
from provider.services.catalog import get_catalog, get_stock
from provider.services.exceptions import (
    InsufficientStockError,
    NotFoundError,
)
from provider.services.orders import create_order, get_order, get_orders
from provider.services.simulation import advance_day, get_current_day


# ---------------------------------------------------------------------------
# Pydantic request / response schemas (transport-layer only)
# ---------------------------------------------------------------------------


class CreateOrderRequest(BaseModel):
    """Request body for ``POST /api/orders``."""

    buyer: str = Field(..., min_length=1, description="Identifier of the buyer.")
    product_id: str = Field(..., description="Provider product id, e.g. ``p-0001``.")
    quantity: int = Field(..., gt=0, description="Units requested (must be > 0).")


class PricingTierResponse(BaseModel):
    id: str
    min_quantity: int
    unit_price: float


class CatalogProductResponse(BaseModel):
    id: str
    name: str
    description: str
    lead_time_days: int
    stock_quantity: int
    pricing_tiers: list[PricingTierResponse]


class StockEntryResponse(BaseModel):
    product_id: str
    product_name: str
    quantity: int


class OrderResponse(BaseModel):
    id: str
    buyer: str
    product_id: str
    quantity: int
    unit_price: float
    total_price: float
    placed_day: int
    expected_delivery_day: int
    shipped_day: Optional[int] = None
    delivered_day: Optional[int] = None
    status: OrderStatus


class DayAdvanceResponse(BaseModel):
    day: int
    orders_shipped: int
    orders_delivered: int


class CurrentDayResponse(BaseModel):
    current_day: int


class ErrorResponse(BaseModel):
    detail: str


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure the DB is seeded before serving the first request."""
    ensure_seeded(Path(__file__).parent / "seed-provider.json")
    yield


app = FastAPI(
    title="Provider API",
    description=(
        "Independent supplier simulation. Exposes a catalog, on-hand stock, "
        "and a provider-side order lifecycle "
        "(``pending -> confirmed -> in_progress -> shipped -> delivered``)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Error normalisation
# ---------------------------------------------------------------------------
#
# Every error response across this API uses the shape ``{"detail": "..."}``.
# By default FastAPI returns ``422 Unprocessable Entity`` with a structured
# list of field errors when a request body fails Pydantic validation; we
# downgrade those to ``400 Bad Request`` with a flat string so the client
# contract stays consistent with the service-level errors below.


def _format_validation_errors(exc: RequestValidationError) -> str:
    """Flatten Pydantic validation errors into a single human-readable string."""
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()) if x != "body")
        msg = err.get("msg", "invalid value")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts) or "validation error"


@app.exception_handler(RequestValidationError)
async def _on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": _format_validation_errors(exc)},
    )


# ---------------------------------------------------------------------------
# Catalog & stock
# ---------------------------------------------------------------------------


@app.get(
    "/api/catalog",
    response_model=list[CatalogProductResponse],
    tags=["Catalog"],
    summary="List products with pricing tiers and stock.",
)
def api_catalog(db: Session = Depends(get_db)) -> list[dict]:
    return get_catalog(db)


@app.get(
    "/api/stock",
    response_model=list[StockEntryResponse],
    tags=["Catalog"],
    summary="List on-hand quantities per product.",
)
def api_stock(db: Session = Depends(get_db)) -> list[dict]:
    return get_stock(db)


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


@app.post(
    "/api/orders",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Orders"],
    summary="Place a new order.",
    responses={
        400: {"model": ErrorResponse, "description": "Validation error"},
        404: {"model": ErrorResponse, "description": "Product not found"},
        409: {"model": ErrorResponse, "description": "Insufficient stock"},
    },
)
def api_create_order(
    payload: CreateOrderRequest,
    db: Session = Depends(get_db),
) -> dict:
    try:
        return create_order(
            db,
            buyer=payload.buyer,
            product_id=payload.product_id,
            quantity=payload.quantity,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InsufficientStockError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get(
    "/api/orders",
    response_model=list[OrderResponse],
    tags=["Orders"],
    summary="List orders, optionally filtered by status.",
)
def api_list_orders(
    status: Optional[OrderStatus] = Query(
        default=None,
        description="Filter by order status.",
    ),
    db: Session = Depends(get_db),
) -> list[dict]:
    return get_orders(db, status=status)


@app.get(
    "/api/orders/{order_id}",
    response_model=OrderResponse,
    tags=["Orders"],
    summary="Fetch a single order by id.",
    responses={404: {"model": ErrorResponse, "description": "Order not found"}},
)
def api_get_order(order_id: str, db: Session = Depends(get_db)) -> dict:
    row = get_order(db, order_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Order not found: {order_id}")
    return row


# ---------------------------------------------------------------------------
# Simulation clock
# ---------------------------------------------------------------------------


@app.post(
    "/api/day/advance",
    response_model=DayAdvanceResponse,
    tags=["Simulation"],
    summary="Advance the simulation by one day.",
)
def api_day_advance(db: Session = Depends(get_db)) -> dict:
    return advance_day(db)


@app.get(
    "/api/day/current",
    response_model=CurrentDayResponse,
    tags=["Simulation"],
    summary="Return the current simulation day.",
)
def api_day_current(db: Session = Depends(get_db)) -> dict:
    return {"current_day": get_current_day(db)}


__all__ = ["app"]
