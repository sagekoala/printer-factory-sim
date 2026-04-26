"""Command-line interface for the manufacturer simulation app.

Run as::

    python -m manufacturer.cli <command>

After ``pip install -e .``::

    manufacturer-cli <command>

Every command is a thin wrapper around a function in
:mod:`manufacturer.services` (provider integration) or
:mod:`manufacturer.simulation` (factory clock + state).
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
    from manufacturer.services.suppliers import (
        ProviderError,
        get_catalog,
        list_providers,
        list_purchase_orders,
        place_order,
    )
    from manufacturer.simulation import advance_day, export_state, import_state
except ModuleNotFoundError:
    from database import (  # type: ignore[no-redef]
        FactoryConfigRow,
        ManufacturingOrderRow,
        ProductRow,
        SessionLocal,
        init_db,
    )
    from models import ManufacturingOrderStatus  # type: ignore[no-redef]
    from services.suppliers import (  # type: ignore[no-redef]
        ProviderError,
        get_catalog,
        list_providers,
        list_purchase_orders,
        place_order,
    )
    from simulation import advance_day, export_state, import_state  # type: ignore[no-redef]


app = typer.Typer(help="Manufacturer simulation CLI", no_args_is_help=True)
orders_app = typer.Typer(help="Manufacturing order commands")
purchase_app = typer.Typer(help="Outbound provider purchase order commands")
day_app = typer.Typer(help="Simulation day commands")
suppliers_app = typer.Typer(help="External supplier commands")

app.add_typer(orders_app, name="orders")
app.add_typer(purchase_app, name="purchase")
app.add_typer(day_app, name="day")
app.add_typer(suppliers_app, name="suppliers")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_PURCHASE_STATUS_VALUES = ["pending", "confirmed", "in_progress", "shipped", "delivered"]


def _get_current_day(db) -> int:
    row = db.query(FactoryConfigRow).filter(FactoryConfigRow.key == "current_day").first()
    return int(row.value) if row else 0


def _emit(payload) -> None:
    typer.echo(json.dumps(payload, indent=2, default=str))


def _provider_or_raise(name: str) -> dict[str, str]:
    for entry in list_providers():
        if entry["name"] == name:
            return entry
    raise typer.BadParameter(f"Unknown supplier: {name!r}")


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


@app.command("stock", help="Show current local inventory.")
def stock() -> None:
    init_db()
    db = SessionLocal()
    try:
        rows = db.query(ProductRow).order_by(ProductRow.name).all()
        _emit(
            [
                {
                    "id": r.id,
                    "name": r.name,
                    "current_stock": r.current_stock,
                    "storage_size": r.storage_size,
                }
                for r in rows
            ]
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Manufacturing orders
# ---------------------------------------------------------------------------


@orders_app.command("list", help="List manufacturing orders.")
def orders_list(
    status: Optional[ManufacturingOrderStatus] = typer.Option(
        None,
        "--status",
        help="Filter by manufacturing order status.",
    ),
) -> None:
    init_db()
    db = SessionLocal()
    try:
        query = db.query(ManufacturingOrderRow)
        if status is not None:
            query = query.filter(ManufacturingOrderRow.status == status.value)
        rows = query.order_by(ManufacturingOrderRow.created_at).all()
        _emit(
            [
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
            ]
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Suppliers (external providers)
# ---------------------------------------------------------------------------


@suppliers_app.command("list", help="List configured external providers.")
def suppliers_list() -> None:
    init_db()
    _emit(list_providers())


@suppliers_app.command(
    "catalog",
    help="Show the catalog (products + tiers + stock) for a configured supplier.",
)
def suppliers_catalog(supplier_name: str) -> None:
    init_db()
    supplier = _provider_or_raise(supplier_name)
    try:
        _emit(get_catalog(supplier["url"]))
    except ProviderError as exc:
        raise typer.BadParameter(str(exc)) from exc


# ---------------------------------------------------------------------------
# Outbound purchase orders
# ---------------------------------------------------------------------------


@purchase_app.command(
    "create",
    help="Place a new purchase order with a configured external provider.",
)
def purchase_create(
    supplier: str = typer.Option(..., "--supplier", help="Supplier name from config.json."),
    product_id: str = typer.Option(
        ...,
        "--product-id",
        help="Provider product id (e.g. p-0001).",
    ),
    qty: int = typer.Option(..., "--qty", min=1, help="Units to order."),
) -> None:
    init_db()
    supplier_entry = _provider_or_raise(supplier)
    db = SessionLocal()
    try:
        try:
            row = place_order(
                db,
                provider_url=supplier_entry["url"],
                supplier_name=supplier,
                product_id=product_id,
                quantity=qty,
                current_day=_get_current_day(db),
            )
        except ProviderError as exc:
            raise typer.BadParameter(str(exc)) from exc
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        _emit(row)
    finally:
        db.close()


@purchase_app.command("list", help="List local outbound purchase orders.")
def purchase_list(
    status: Optional[str] = typer.Option(
        None,
        "--status",
        help=(
            "Filter by status. Accepts any provider status, "
            f"commonly: {', '.join(_PURCHASE_STATUS_VALUES)}"
        ),
    ),
) -> None:
    init_db()
    db = SessionLocal()
    try:
        _emit(list_purchase_orders(db, status=status))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Simulation clock
# ---------------------------------------------------------------------------


@day_app.command("advance", help="Advance the manufacturer simulation by one day.")
def day_advance() -> None:
    init_db()
    db = SessionLocal()
    try:
        previous_day = _get_current_day(db)
        current_day = advance_day(db)
        _emit({"previous_day": previous_day, "current_day": current_day})
    finally:
        db.close()


@day_app.command("current", help="Show the current simulation day.")
def day_current() -> None:
    init_db()
    db = SessionLocal()
    try:
        _emit({"current_day": _get_current_day(db)})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


@app.command("export", help="Dump full simulation state as JSON to stdout.")
def export_command() -> None:
    init_db()
    db = SessionLocal()
    try:
        _emit(export_state(db))
    finally:
        db.close()


@app.command("import", help="Restore full simulation state from a JSON snapshot file.")
def import_command(file: Path) -> None:
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
