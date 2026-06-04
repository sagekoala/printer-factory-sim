"""Command-line interface for the Retailer Simulator."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

from retailer import simulation
from retailer.database import (
    CatalogRow,
    CustomerOrderRow,
    EventRow,
    PurchaseOrderRow,
    SessionLocal,
    SimStateRow,
    StockRow,
    init_db,
)
from retailer.manufacturer_integration import place_manufacturer_order

_RETAILER_DIR = Path(__file__).resolve().parent
_DEFAULT_CONFIG = _RETAILER_DIR / "retailer_config.json"
_DEFAULT_MANUFACTURER_URL = "http://localhost:8002"
_DEFAULT_RETAILER_NAME = "PrinterWorld"

app = typer.Typer(help="Retailer simulation CLI", no_args_is_help=True)
customers_app = typer.Typer(help="Customer order commands")
purchase_app = typer.Typer(help="Purchase order commands (manufacturer)")
day_app = typer.Typer(help="Simulation day commands")
price_app = typer.Typer(help="Pricing commands")

app.add_typer(customers_app, name="customers")
app.add_typer(purchase_app, name="purchase")
app.add_typer(day_app, name="day")
app.add_typer(price_app, name="price")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit(payload) -> None:
    typer.echo(json.dumps(payload, indent=2, default=str))


def _get_current_day(db) -> int:
    row = db.query(SimStateRow).filter(SimStateRow.key == "current_day").first()
    return int(row.value) if row else 0


def _load_config(config_path: Path) -> dict:
    if config_path.exists():
        return json.loads(config_path.read_text())
    return {}


def _manufacturer_url(cfg: dict) -> str:
    return cfg.get("retailer", {}).get("manufacturer", {}).get("url", _DEFAULT_MANUFACTURER_URL)


def _retailer_name(cfg: dict) -> str:
    return cfg.get("retailer", {}).get("name", _DEFAULT_RETAILER_NAME)


# ---------------------------------------------------------------------------
# Catalog & Stock
# ---------------------------------------------------------------------------


@app.command("catalog", help="Show models and retail prices.")
def catalog() -> None:
    init_db()
    db = SessionLocal()
    try:
        rows = db.query(CatalogRow).order_by(CatalogRow.model).all()
        _emit([{"model": r.model, "retail_price": r.retail_price} for r in rows])
    finally:
        db.close()


@app.command("stock", help="Show current finished-printer inventory.")
def stock() -> None:
    init_db()
    db = SessionLocal()
    try:
        rows = db.query(StockRow).order_by(StockRow.model).all()
        _emit([{"model": r.model, "quantity": r.quantity} for r in rows])
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Customer orders
# ---------------------------------------------------------------------------


@customers_app.command("orders", help="List customer orders (optional --status).")
def customers_orders(status: Optional[str] = typer.Option(None, "--status")) -> None:
    init_db()
    db = SessionLocal()
    try:
        query = db.query(CustomerOrderRow)
        if status:
            query = query.filter(CustomerOrderRow.status == status)
        rows = query.order_by(CustomerOrderRow.created_at).all()
        _emit(
            [
                {
                    "id": r.id, "customer": r.customer, "model": r.model,
                    "quantity": r.quantity, "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        )
    finally:
        db.close()


@customers_app.command("order", help="Show details of a customer order.")
def customers_order(order_id: str) -> None:
    init_db()
    db = SessionLocal()
    try:
        row = db.query(CustomerOrderRow).filter(CustomerOrderRow.id == order_id).first()
        if row is None:
            typer.echo(f"Order {order_id!r} not found", err=True)
            raise typer.Exit(1)
        _emit({
            "id": row.id, "customer": row.customer, "model": row.model,
            "quantity": row.quantity, "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "fulfilled_at": row.fulfilled_at.isoformat() if row.fulfilled_at else None,
        })
    finally:
        db.close()


@app.command("fulfill", help="Ship a customer order from stock.")
def fulfill(order_id: str) -> None:
    init_db()
    db = SessionLocal()
    try:
        order = db.query(CustomerOrderRow).filter(CustomerOrderRow.id == order_id).first()
        if order is None:
            typer.echo(f"Order {order_id!r} not found", err=True)
            raise typer.Exit(1)
        stock_row = db.query(StockRow).filter(StockRow.model == order.model).first()
        if stock_row is None or stock_row.quantity < order.quantity:
            typer.echo(f"Insufficient stock for {order.quantity}x {order.model}", err=True)
            raise typer.Exit(1)
        stock_row.quantity -= order.quantity
        order.status = "fulfilled"
        order.fulfilled_at = datetime.utcnow()
        db.commit()
        _emit({"id": order.id, "status": order.status})
    finally:
        db.close()


@app.command("backorder", help="Mark a customer order as backordered.")
def backorder(order_id: str) -> None:
    init_db()
    db = SessionLocal()
    try:
        order = db.query(CustomerOrderRow).filter(CustomerOrderRow.id == order_id).first()
        if order is None:
            typer.echo(f"Order {order_id!r} not found", err=True)
            raise typer.Exit(1)
        order.status = "backordered"
        db.commit()
        _emit({"id": order.id, "status": order.status})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Purchase orders (retailer → manufacturer)
# ---------------------------------------------------------------------------


@purchase_app.command("list", help="List purchase orders placed with manufacturer.")
def purchase_list(status: Optional[str] = typer.Option(None, "--status")) -> None:
    init_db()
    db = SessionLocal()
    try:
        query = db.query(PurchaseOrderRow)
        if status:
            query = query.filter(PurchaseOrderRow.status == status)
        rows = query.order_by(PurchaseOrderRow.created_at).all()
        _emit(
            [
                {
                    "id": r.id, "model": r.model, "quantity": r.quantity,
                    "status": r.status, "placed_day": r.placed_day,
                    "manufacturer_order_id": r.manufacturer_order_id,
                    "delivered_day": r.delivered_day,
                }
                for r in rows
            ]
        )
    finally:
        db.close()


@purchase_app.command("create", help="Order printers from the manufacturer.")
def purchase_create(
    model: str = typer.Argument(...),
    qty: int = typer.Argument(...),
    config: Path = typer.Option(_DEFAULT_CONFIG, "--config"),
) -> None:
    cfg = _load_config(config)
    manufacturer_url = _manufacturer_url(cfg)
    retailer_name = _retailer_name(cfg)

    init_db()
    db = SessionLocal()
    try:
        day = _get_current_day(db)
        try:
            remote = place_manufacturer_order(manufacturer_url, retailer_name, model, qty)
        except Exception as exc:  # httpx covers both transport and HTTP errors
            typer.echo(f"Manufacturer error: {exc}", err=True)
            raise typer.Exit(1) from exc

        po = PurchaseOrderRow(
            id=str(uuid.uuid4()),
            model=model,
            quantity=qty,
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
            description=f"Day {day}: PO placed with manufacturer — {qty}x {model}",
        ))
        db.commit()
        _emit({
            "id": po.id, "model": model, "quantity": qty,
            "status": po.status, "manufacturer_order_id": po.manufacturer_order_id,
        })
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


@price_app.command("set", help="Set retail price for a model.")
def price_set(model: str, price: float) -> None:
    init_db()
    db = SessionLocal()
    try:
        row = db.query(CatalogRow).filter(CatalogRow.model == model).first()
        if row is None:
            typer.echo(f"Model {model!r} not in catalog", err=True)
            raise typer.Exit(1)
        row.retail_price = price
        db.commit()
        _emit({"model": model, "retail_price": price})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Simulation day
# ---------------------------------------------------------------------------


@day_app.command("advance", help="Advance the retailer simulation by one day.")
def day_advance(config: Path = typer.Option(_DEFAULT_CONFIG, "--config")) -> None:
    cfg = _load_config(config)
    manufacturer_url = _manufacturer_url(cfg)

    init_db()
    db = SessionLocal()
    try:
        previous = _get_current_day(db)
        new_day = simulation.advance_day(db, manufacturer_url)
        _emit({"previous_day": previous, "current_day": new_day})
    finally:
        db.close()


@day_app.command("current", help="Show current simulation day.")
def day_current() -> None:
    init_db()
    db = SessionLocal()
    try:
        _emit({"current_day": _get_current_day(db)})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Export / Import
# ---------------------------------------------------------------------------


@app.command("export", help="Dump simulation state as JSON to stdout.")
def export_command() -> None:
    init_db()
    db = SessionLocal()
    try:
        _emit(simulation.export_state(db))
    finally:
        db.close()


@app.command("import", help="Restore simulation state from a JSON snapshot file.")
def import_command(file: Path = typer.Argument(..., exists=True, readable=True)) -> None:
    snapshot = json.loads(file.read_text())
    init_db()
    db = SessionLocal()
    try:
        simulation.import_state(db, snapshot)
        typer.echo(f"Imported state from {file}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Serve
# ---------------------------------------------------------------------------


@app.command("serve", help="Start the Retailer REST API server.")
def serve(
    port: int = typer.Option(8003, "--port"),
    config: Path = typer.Option(_DEFAULT_CONFIG, "--config"),
) -> None:
    cfg = _load_config(config)
    name = cfg.get("retailer", {}).get("name", "retailer")
    db_name = name.lower().replace(" ", "_")
    db_path = _RETAILER_DIR / f"{db_name}.db"

    env = os.environ.copy()
    env["RETAILER_CONFIG_PATH"] = str(config.resolve())
    env["RETAILER_DATABASE_URL"] = f"sqlite:///{db_path}"

    subprocess.run(
        [sys.executable, "-m", "uvicorn", "retailer.main:app",
         "--host", "0.0.0.0", "--port", str(port)],
        env=env,
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
