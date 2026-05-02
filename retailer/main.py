"""FastAPI REST API for the Retailer Simulator."""
from __future__ import annotations

import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

try:
    from retailer.database import (
        CatalogRow,
        CustomerOrderRow,
        EventRow,
        PurchaseOrderRow,
        SessionLocal,
        SimStateRow,
        StockRow,
        get_db,
        init_db,
    )
    from retailer.manufacturer_integration import place_manufacturer_order
    from retailer.seed import seed_if_empty
    from retailer import simulation
except ModuleNotFoundError:
    from database import (
        CatalogRow,
        CustomerOrderRow,
        EventRow,
        PurchaseOrderRow,
        SessionLocal,
        SimStateRow,
        StockRow,
        get_db,
        init_db,
    )
    from manufacturer_integration import place_manufacturer_order
    from seed import seed_if_empty
    import simulation

_RETAILER_DIR = Path(__file__).resolve().parent
_config: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    config_path = Path(os.getenv("RETAILER_CONFIG_PATH", str(_RETAILER_DIR / "retailer_config.json")))
    if config_path.exists():
        _config.update(json.loads(config_path.read_text()))
    init_db()
    db = SessionLocal()
    try:
        seed_if_empty(db, _config)
    finally:
        db.close()
    yield


app = FastAPI(
    title="Retailer Simulator",
    description="REST API for the 3D Printer Retailer simulation.",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class CreateCustomerOrderRequest(BaseModel):
    customer: str
    model: str
    quantity: int = 1


class CreatePurchaseRequest(BaseModel):
    model: str
    quantity: int


class SetPriceRequest(BaseModel):
    price: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_manufacturer_url() -> str:
    return _config.get("retailer", {}).get("manufacturer", {}).get("url", "http://localhost:8002")


def _get_retailer_name() -> str:
    return _config.get("retailer", {}).get("name", "PrinterWorld")


def _get_current_day(db: Session) -> int:
    row = db.query(SimStateRow).filter(SimStateRow.key == "current_day").first()
    return int(row.value) if row else 0


def _order_to_dict(r: CustomerOrderRow) -> dict:
    return {
        "id": r.id, "customer": r.customer, "model": r.model,
        "quantity": r.quantity, "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "fulfilled_at": r.fulfilled_at.isoformat() if r.fulfilled_at else None,
    }


def _po_to_dict(r: PurchaseOrderRow) -> dict:
    return {
        "id": r.id, "model": r.model, "quantity": r.quantity,
        "unit_price": r.unit_price, "total_price": r.total_price,
        "status": r.status, "placed_day": r.placed_day,
        "manufacturer_order_id": r.manufacturer_order_id,
        "delivered_day": r.delivered_day,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/catalog")
def get_catalog(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.query(CatalogRow).order_by(CatalogRow.model).all()
    return [{"model": r.model, "retail_price": r.retail_price} for r in rows]


@app.get("/api/stock")
def get_stock(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.query(StockRow).order_by(StockRow.model).all()
    return [{"model": r.model, "quantity": r.quantity} for r in rows]


@app.post("/api/orders")
def create_customer_order(
    payload: CreateCustomerOrderRequest,
    db: Session = Depends(get_db),
) -> dict:
    day = _get_current_day(db)
    stock = db.query(StockRow).filter(StockRow.model == payload.model).first()

    if stock and stock.quantity >= payload.quantity:
        stock.quantity -= payload.quantity
        status = "fulfilled"
        fulfilled_at = datetime.utcnow()
    else:
        status = "backordered"
        fulfilled_at = None

    order_id = str(uuid.uuid4())
    order = CustomerOrderRow(
        id=order_id,
        customer=payload.customer,
        model=payload.model,
        quantity=payload.quantity,
        status=status,
        created_at=datetime.utcnow(),
        fulfilled_at=fulfilled_at,
    )
    db.add(order)
    db.add(EventRow(
        id=str(uuid.uuid4()),
        day=day,
        event_type="ORDER_PLACED",
        entity_type="customer_order",
        entity_id=order_id,
        description=f"Day {day}: Customer order from {payload.customer} — {payload.quantity}x {payload.model} ({status})",
    ))
    db.commit()
    return _order_to_dict(order)


@app.get("/api/orders")
def list_customer_orders(
    status: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = db.query(CustomerOrderRow)
    if status:
        query = query.filter(CustomerOrderRow.status == status)
    rows = query.order_by(CustomerOrderRow.created_at).all()
    return [_order_to_dict(r) for r in rows]


@app.get("/api/orders/{order_id}")
def get_customer_order(order_id: str, db: Session = Depends(get_db)) -> dict:
    row = db.query(CustomerOrderRow).filter(CustomerOrderRow.id == order_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Order {order_id!r} not found")
    return _order_to_dict(row)


@app.post("/api/orders/{order_id}/fulfill")
def fulfill_order(order_id: str, db: Session = Depends(get_db)) -> dict:
    order = db.query(CustomerOrderRow).filter(CustomerOrderRow.id == order_id).first()
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order {order_id!r} not found")
    if order.status not in ("pending", "backordered"):
        raise HTTPException(status_code=409, detail=f"Order status is {order.status!r}")

    stock = db.query(StockRow).filter(StockRow.model == order.model).first()
    if stock is None or stock.quantity < order.quantity:
        raise HTTPException(status_code=409, detail="Insufficient stock")

    stock.quantity -= order.quantity
    order.status = "fulfilled"
    order.fulfilled_at = datetime.utcnow()
    db.commit()
    return _order_to_dict(order)


@app.post("/api/orders/{order_id}/backorder")
def backorder_order(order_id: str, db: Session = Depends(get_db)) -> dict:
    order = db.query(CustomerOrderRow).filter(CustomerOrderRow.id == order_id).first()
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order {order_id!r} not found")
    if order.status != "pending":
        raise HTTPException(status_code=409, detail=f"Order status is {order.status!r}")
    order.status = "backordered"
    db.commit()
    return _order_to_dict(order)


@app.post("/api/purchases")
def create_purchase_order(
    payload: CreatePurchaseRequest,
    db: Session = Depends(get_db),
) -> dict:
    manufacturer_url = _get_manufacturer_url()
    retailer_name = _get_retailer_name()
    day = _get_current_day(db)

    try:
        remote = place_manufacturer_order(manufacturer_url, retailer_name, payload.model, payload.quantity)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"Manufacturer unreachable: {exc}") from exc

    po = PurchaseOrderRow(
        id=str(uuid.uuid4()),
        model=payload.model,
        quantity=payload.quantity,
        unit_price=remote.get("unit_price", 0.0),
        total_price=remote.get("total_price", 0.0),
        status=remote.get("status", "pending"),
        placed_day=day,
        manufacturer_order_id=remote.get("id"),
        created_at=datetime.utcnow(),
    )
    db.add(po)
    db.add(EventRow(
        id=str(uuid.uuid4()),
        day=day,
        event_type="PURCHASE_PLACED",
        entity_type="purchase_order",
        entity_id=po.id,
        description=f"Day {day}: Purchase order placed with manufacturer — {payload.quantity}x {payload.model}",
    ))
    db.commit()
    return _po_to_dict(po)


@app.get("/api/purchases")
def list_purchase_orders(
    status: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = db.query(PurchaseOrderRow)
    if status:
        query = query.filter(PurchaseOrderRow.status == status)
    rows = query.order_by(PurchaseOrderRow.created_at).all()
    return [_po_to_dict(r) for r in rows]


@app.post("/api/price")
def set_price(payload: SetPriceRequest, model: str = Query(...), db: Session = Depends(get_db)) -> dict:
    row = db.query(CatalogRow).filter(CatalogRow.model == model).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Model {model!r} not in catalog")
    row.retail_price = payload.price
    db.commit()
    return {"model": row.model, "retail_price": row.retail_price}


@app.post("/api/day/advance")
def advance_day(db: Session = Depends(get_db)) -> dict:
    manufacturer_url = _get_manufacturer_url()
    previous = simulation.get_current_day(db)
    new_day = simulation.advance_day(db, manufacturer_url)
    return {"previous_day": previous, "current_day": new_day}


@app.get("/api/day/current")
def day_current(db: Session = Depends(get_db)) -> dict:
    return {"current_day": _get_current_day(db)}


@app.get("/api/export")
def export_state(db: Session = Depends(get_db)) -> dict:
    return simulation.export_state(db)
