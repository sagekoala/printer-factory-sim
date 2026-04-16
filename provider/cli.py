"""Typer CLI for provider simulation app."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import typer
import uvicorn

from provider.db import OrderStatus, SessionLocal, ensure_seeded
from provider.services.catalog import list_catalog, list_stock, restock, set_tier_price
from provider.services.orders import get_order, list_orders
from provider.services.simulation import advance_day, current_day, export_state, import_state

app = typer.Typer(help="Provider simulation CLI")
orders_app = typer.Typer(help="Order commands")
price_app = typer.Typer(help="Pricing commands")
day_app = typer.Typer(help="Simulation day commands")

app.add_typer(orders_app, name="orders")
app.add_typer(price_app, name="price")
app.add_typer(day_app, name="day")


def _session():
    ensure_seeded(Path(__file__).parent / "seed-provider.json")
    return SessionLocal()


@app.command("catalog")
def catalog_command() -> None:
    db = _session()
    try:
        typer.echo(json.dumps(list_catalog(db), indent=2))
    finally:
        db.close()


@app.command("stock")
def stock_command() -> None:
    db = _session()
    try:
        typer.echo(json.dumps(list_stock(db), indent=2))
    finally:
        db.close()


@orders_app.command("list")
def list_orders_command(status: OrderStatus | None = typer.Option(None, "--status")) -> None:
    db = _session()
    try:
        typer.echo(json.dumps(list_orders(db, status=status), indent=2))
    finally:
        db.close()


@orders_app.command("show")
def show_order_command(order_id: str) -> None:
    db = _session()
    try:
        order = get_order(db, order_id)
        if order is None:
            raise typer.BadParameter(f"Order not found: {order_id}")
        typer.echo(json.dumps(order, indent=2))
    finally:
        db.close()


@price_app.command("set")
def set_price_command(product: str, tier: int, price: float) -> None:
    db = _session()
    try:
        result = set_tier_price(
            db,
            product_ref=product,
            min_quantity=tier,
            unit_price=Decimal(str(price)),
        )
        typer.echo(json.dumps(result, indent=2))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        db.close()


@app.command("restock")
def restock_command(product: str, quantity: int) -> None:
    db = _session()
    try:
        result = restock(db, product_ref=product, quantity=quantity)
        typer.echo(json.dumps(result, indent=2))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        db.close()


@day_app.command("advance")
def day_advance_command() -> None:
    db = _session()
    try:
        typer.echo(json.dumps(advance_day(db), indent=2))
    finally:
        db.close()


@day_app.command("current")
def day_current_command() -> None:
    db = _session()
    try:
        typer.echo(json.dumps({"current_day": current_day(db)}, indent=2))
    finally:
        db.close()


@app.command("export")
def export_command() -> None:
    db = _session()
    try:
        typer.echo(json.dumps(export_state(db), indent=2))
    finally:
        db.close()


@app.command("import")
def import_command(file: Path) -> None:
    if not file.exists():
        raise typer.BadParameter(f"File not found: {file}")

    payload = json.loads(file.read_text())
    db = _session()
    try:
        import_state(db, payload)
        typer.echo(f"Imported provider state from {file}")
    finally:
        db.close()


@app.command("serve")
def serve_command(port: int = typer.Option(8001, "--port")) -> None:
    ensure_seeded(Path(__file__).parent / "seed-provider.json")
    uvicorn.run("provider.api:app", host="0.0.0.0", port=port, reload=False)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
