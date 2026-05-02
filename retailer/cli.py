"""Command-line interface for the Retailer Simulator."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

try:
    from retailer.database import (
        CatalogRow,
        CustomerOrderRow,
        PurchaseOrderRow,
        SessionLocal,
        SimStateRow,
        StockRow,
        init_db,
    )
    from retailer import simulation
    from retailer.seed import seed_if_empty
except ModuleNotFoundError:
    from database import (
        CatalogRow,
        CustomerOrderRow,
        PurchaseOrderRow,
        SessionLocal,
        SimStateRow,
        StockRow,
        init_db,
    )
    import simulation
    from seed import seed_if_empty

_RETAILER_DIR = Path(__file__).resolve().parent

app = typer.Typer(help="Retailer simulation CLI", no_args_is_help=True)
customers_app = typer.Typer(help="Customer order commands")
purchase_app = typer.Typer(help="Purchase order commands (manufacturer)")
day_app = typer.Typer(help="Simulation day commands")
price_app = typer.Typer(help="Pricing commands")

app.add_typer(customers_app, name="customers")
app.add_typer(purchase_app, name="purchase")
app.add_typer(day_app, name="day")
app.add_typer(price_app, name="price")


def _emit(payload) -> None:
    typer.echo(json.dumps(payload, indent=2, default=str))


def _get_current_day(db) -> int:
    row = db.query(SimStateRow).filter(SimStateRow.key == "current_day").first()
    return int(row.value) if row else 0


def _load_config(config_path: Path) -> dict:
    if config_path.exists():
        return json.loads(config_path.read_text())
    return {}


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
def customers_orders(
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status."),
) -> None:
    init_db()
    db = SessionLocal()
    try:
        query = db.query(CustomerOrderRow)
        if status:
            query = query.filter(CustomerOrderRow.status == status)
        rows = query.order_by(CustomerOrderRow.created_at).all()
        _emit([
            {
                "id": r.id, "customer": r.customer, "model": r.model,
                "quantity": r.quantity, "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ])
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
        stock = db.query(StockRow).filter(StockRow.model == order.model).first()
        if stock is None or stock.quantity < order.quantity:
            typer.echo(f"Insufficient stock for {order.quantity}x {order.model}", err=True)
            raise typer.Exit(1)
        stock.quantity -= order.quantity
        from datetime import datetime
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
# Purchase orders (from retailer to manufacturer)
# ---------------------------------------------------------------------------


@purchase_app.command("list", help="List purchase orders placed with manufacturer.")
def purchase_list(
    status: Optional[str] = typer.Option(None, "--status"),
) -> None:
    init_db()
    db = SessionLocal()
    try:
        query = db.query(PurchaseOrderRow)
        if status:
            query = query.filter(PurchaseOrderRow.status == status)
        rows = query.order_by(PurchaseOrderRow.created_at).all()
        _emit([
            {
                "id": r.id, "model": r.model, "quantity": r.quantity,
                "status": r.status, "placed_day": r.placed_day,
                "manufacturer_order_id": r.manufacturer_order_id,
                "delivered_day": r.delivered_day,
            }
            for r in rows
        ])
    finally:
        db.close()


@purchase_app.command("create", help="Order printers from the manufacturer.")
def purchase_create(
    model: str = typer.Argument(..., help="Printer model name."),
    qty: int = typer.Argument(..., help="Quantity to order."),
    config: Path = typer.Option(
        _RETAILER_DIR / "retailer_config.json",
        "--config",
        help="Config file path.",
    ),
) -> None:
    cfg = _load_config(config)
    manufacturer_url = cfg.get("retailer", {}).get("manufacturer", {}).get("url", "http://localhost:8002")
    retailer_name = cfg.get("retailer", {}).get("name", "PrinterWorld")

    import httpx
    from datetime import datetime
    import uuid

    init_db()
    db = SessionLocal()
    try:
        day = _get_current_day(db)
        try:
            resp = httpx.post(
                f"{manufacturer_url}/api/orders",
                json={"retailer_name": retailer_name, "model": model, "quantity": qty},
                timeout=8.0,
            )
            resp.raise_for_status()
            remote = resp.json()
        except Exception as exc:
            typer.echo(f"Manufacturer error: {exc}", err=True)
            raise typer.Exit(1)

        from retailer.database import PurchaseOrderRow, EventRow
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
        db.commit()
        _emit({"id": po.id, "model": model, "quantity": qty, "status": po.status, "manufacturer_order_id": po.manufacturer_order_id})
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
def day_advance(
    config: Path = typer.Option(
        _RETAILER_DIR / "retailer_config.json",
        "--config",
        help="Config file path.",
    ),
) -> None:
    cfg = _load_config(config)
    manufacturer_url = cfg.get("retailer", {}).get("manufacturer", {}).get("url", "http://localhost:8002")

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
def import_command(file: Path) -> None:
    if not file.exists():
        raise typer.BadParameter(f"File not found: {file}")
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
    port: int = typer.Option(8003, "--port", help="Port to listen on."),
    config: Path = typer.Option(
        _RETAILER_DIR / "retailer_config.json",
        "--config",
        help="Config file path.",
    ),
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
