"""FastAPI app for provider simulation."""

from __future__ import annotations

from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from provider.db import OrderStatus, ensure_seeded, get_db
from provider.services.catalog import list_catalog, list_stock
from provider.services.orders import create_order, get_order, list_orders
from provider.services.simulation import advance_day, current_day


class CreateOrderRequest(BaseModel):
    buyer: str = Field(min_length=1)
    product_id: str
    quantity: int = Field(gt=0)


class DayAdvanceResponse(BaseModel):
    previous_day: int
    current_day: int


class CurrentDayResponse(BaseModel):
    current_day: int


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_seeded(Path(__file__).parent / "seed-provider.json")
    yield


app = FastAPI(
    title="Provider Parts Supplier API",
    description="Independent supplier simulation API serving parts to manufacturer clients.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/api/catalog", tags=["Catalog"])
def api_catalog(db: Session = Depends(get_db)) -> list[dict]:
    return list_catalog(db)


@app.get("/api/stock", tags=["Catalog"])
def api_stock(db: Session = Depends(get_db)) -> list[dict]:
    return list_stock(db)


@app.post("/api/orders", tags=["Orders"])
def api_create_order(payload: CreateOrderRequest, db: Session = Depends(get_db)) -> dict:
    try:
        return create_order(
            db,
            buyer=payload.buyer,
            product_id=payload.product_id,
            quantity=payload.quantity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/orders", tags=["Orders"])
def api_list_orders(
    status: OrderStatus | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[dict]:
    return list_orders(db, status=status)


@app.get("/api/orders/{order_id}", tags=["Orders"])
def api_get_order(order_id: str, db: Session = Depends(get_db)) -> dict:
    row = get_order(db, order_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return row


@app.post("/api/day/advance", response_model=DayAdvanceResponse, tags=["Simulation"])
def api_day_advance(db: Session = Depends(get_db)) -> DayAdvanceResponse:
    return DayAdvanceResponse(**advance_day(db))


@app.get("/api/day/current", response_model=CurrentDayResponse, tags=["Simulation"])
def api_day_current(db: Session = Depends(get_db)) -> CurrentDayResponse:
    return CurrentDayResponse(current_day=current_day(db))
