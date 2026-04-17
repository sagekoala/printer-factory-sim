"""Command-line interface for the manufacturer simulation app.

Run as:
    python -m manufacturer.cli <command>

After installing with ``pip install -e .``:
    manufacturer-cli <command>
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

try:
    from manufacturer.database import (
        FactoryConfigRow,
        ManufacturingOrderRow,
        ProductRow,
        SessionLocal,
        init_db,
    )
    from manufacturer.models import ManufacturingOrderStatus
    from manufacturer.provider_integration import (
        create_outbound_purchase,
        fetch_supplier_catalog,
        list_configured_suppliers,
        list_outbound_purchase_orders,
    )
    from manufacturer.simulation import advance_day, export_state, import_state
except ModuleNotFoundError:
    from database import (
        FactoryConfigRow,
        ManufacturingOrderRow,
        ProductRow,
        SessionLocal,
        init_db,
    )
    from models import ManufacturingOrderStatus
    from provider_integration import (
        create_outbound_purchase,
        fetch_supplier_catalog,
        list_configured_suppliers,
        list_outbound_purchase_orders,
    )
    from simulation import advance_day, export_state, import_state

app = typer.Typer(help="Manufacturer simulation CLI")
orders_app = typer.Typer(help="Manufacturing order commands")
purchase_app = typer.Typer(help="Purchase order commands")
day_app = typer.Typer(help="Simulation day commands")
suppliers_app = typer.Typer(help="External supplier commands")

app.add_typer(orders_app, name="orders")
app.add_typer(purchase_app, name="purchase")
app.add_typer(day_app, name="day")
app.add_typer(suppliers_app, name="suppliers")


def _get_current_day(db) -> int:
    row = db.query(FactoryConfigRow).filter(FactoryConfigRow.key == "current_day").first()
    return int(row.value) if row else 0


@app.command("stock")
def stock() -> None:
    """Show current inventory."""
    init_db()
    db = SessionLocal()
    try:
        rows = db.query(ProductRow).order_by(ProductRow.name).all()
        typer.echo(json.dumps([
            {
                "id": r.id,
                "name": r.name,
                "current_stock": r.current_stock,
                "storage_size": r.storage_size,
            }
            for r in rows
        ], indent=2))
    finally:
        db.close()


@orders_app.command("list")
def orders_list(
    status: Optional[ManufacturingOrderStatus] = typer.Option(
        None,
        "--status",
        help="Filter by order status.",
    ),
) -> None:
    """List manufacturing orders."""
    init_db()
    db = SessionLocal()
    try:
        query = db.query(ManufacturingOrderRow)
        if status is not None:
            query = query.filter(ManufacturingOrderRow.status == status.value)
        rows = query.order_by(ManufacturingOrderRow.created_at).all()
        typer.echo(json.dumps([
            {
                "id": r.id,
                "quantity": r.quantity,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "days_elapsed": r.days_elapsed,
            }
            for r in rows
        ], indent=2))
    finally:
        db.close()


@suppliers_app.command("list")
def suppliers_list() -> None:
    """List configured external providers."""
    init_db()
    typer.echo(json.dumps(list_configured_suppliers(), indent=2))


@suppliers_app.command("catalog")
def suppliers_catalog(supplier_name: str) -> None:
    """Show catalog for a configured supplier."""
    init_db()
    try:
        typer.echo(json.dumps(fetch_supplier_catalog(supplier_name), indent=2))
    except (ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@purchase_app.command("create")
def purchase_create(
    supplier: str = typer.Option(..., "--supplier"),
    product: str = typer.Option(..., "--product"),
    qty: int = typer.Option(..., "--qty"),
) -> None:
    """Create outbound purchase order against provider API."""
    init_db()
    db = SessionLocal()
    try:
        row = create_outbound_purchase(
            db,
            supplier_name=supplier,
            product_id=product,
            quantity=qty,
        )
        typer.echo(json.dumps(row, indent=2))
    except (ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        db.close()


@purchase_app.command("list")
def purchase_list() -> None:
    """List outbound purchase orders recorded locally."""
    init_db()
    db = SessionLocal()
    try:
        typer.echo(json.dumps(list_outbound_purchase_orders(db), indent=2))
    finally:
        db.close()


@day_app.command("advance")
def day_advance() -> None:
    """Advance simulation one day."""
    init_db()
    db = SessionLocal()
    try:
        previous_day = _get_current_day(db)
        current_day = advance_day(db)
        typer.echo(json.dumps({"previous_day": previous_day, "current_day": current_day}, indent=2))
    finally:
        db.close()


@day_app.command("current")
def day_current() -> None:
    """Show current simulation day."""
    init_db()
    db = SessionLocal()
    try:
        typer.echo(json.dumps({"current_day": _get_current_day(db)}, indent=2))
    finally:
        db.close()


@app.command("export")
def export_command() -> None:
    """Dump full simulation state to JSON (stdout)."""
    init_db()
    db = SessionLocal()
    try:
        typer.echo(json.dumps(export_state(db), indent=2))
    finally:
        db.close()


@app.command("import")
def import_command(file: Path) -> None:
    """Load full simulation state from JSON file."""
    if not file.exists():
        raise typer.BadParameter(f"File not found: {file}")

    snapshot = json.loads(file.read_text())

    init_db()
    db = SessionLocal()
    try:
        import_state(db, snapshot)
        typer.echo(f"Imported state from {file}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
