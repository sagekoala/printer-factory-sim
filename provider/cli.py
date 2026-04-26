"""Typer CLI for the provider app.

Every command is a thin wrapper around a function in
:mod:`provider.services` — no business logic lives in this module.

Layout
------
- ``catalog``                   — list products + tiers
- ``stock``                     — list per-product on-hand quantity
- ``orders list / show``        — read-side order queries
- ``price set``                 — upsert a pricing tier
- ``restock``                   — add stock for a product
- ``day advance / current``     — drive or inspect the simulation clock
- ``export / import``           — JSON state snapshots
- ``serve``                     — start the FastAPI server (uvicorn)
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Optional

import typer
import uvicorn

from provider.db import OrderStatus, SessionLocal, ensure_seeded
from provider.services.catalog import (
    get_catalog,
    get_stock,
    restock as restock_service,
    set_price as set_price_service,
)
from provider.services.exceptions import (
    InsufficientStockError,
    NotFoundError,
)
from provider.services.orders import get_order, get_orders
from provider.services.simulation import (
    advance_day,
    export_state,
    get_current_day,
    import_state,
)


app = typer.Typer(help="Provider simulation CLI", no_args_is_help=True)
orders_app = typer.Typer(help="Order commands")
price_app = typer.Typer(help="Pricing commands")
day_app = typer.Typer(help="Simulation day commands")

app.add_typer(orders_app, name="orders")
app.add_typer(price_app, name="price")
app.add_typer(day_app, name="day")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_STATUS_VALUES = [s.value for s in OrderStatus]


def _session():
    """Open a session, ensuring the DB is seeded on first use."""
    ensure_seeded(Path(__file__).parent / "seed-provider.json")
    return SessionLocal()


def _emit(payload) -> None:
    """Pretty-print a JSON payload to stdout."""
    typer.echo(json.dumps(payload, indent=2, default=str))


def _parse_status(raw: Optional[str]) -> Optional[OrderStatus]:
    """Coerce a CLI ``--status`` flag to :class:`OrderStatus`.

    Accepts the lowercase string values of the enum so that
    ``--status pending`` works regardless of typer's enum coercion.
    """
    if raw is None:
        return None
    try:
        return OrderStatus(raw)
    except ValueError as exc:
        raise typer.BadParameter(
            f"Invalid status {raw!r}. Choose from: {', '.join(_STATUS_VALUES)}"
        ) from exc


# ---------------------------------------------------------------------------
# Catalog / stock
# ---------------------------------------------------------------------------


@app.command("catalog", help="Print the provider catalog (products + tiers + stock).")
def catalog_command() -> None:
    db = _session()
    try:
        _emit(get_catalog(db))
    finally:
        db.close()


@app.command("stock", help="Print on-hand stock per product.")
def stock_command() -> None:
    db = _session()
    try:
        _emit(get_stock(db))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


@orders_app.command("list", help="List orders, optionally filtered by --status.")
def list_orders_command(
    status: Optional[str] = typer.Option(
        None,
        "--status",
        help=f"Filter by status: one of {', '.join(_STATUS_VALUES)}",
    ),
) -> None:
    parsed = _parse_status(status)
    db = _session()
    try:
        _emit(get_orders(db, status=parsed))
    finally:
        db.close()


@orders_app.command("show", help="Show a single order by id.")
def show_order_command(order_id: str) -> None:
    db = _session()
    try:
        order = get_order(db, order_id)
        if order is None:
            raise typer.BadParameter(f"Order not found: {order_id}")
        _emit(order)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Pricing & stock mutations
# ---------------------------------------------------------------------------


@price_app.command("set", help="Upsert a pricing tier for a product.")
def set_price_command(
    product_id: str = typer.Argument(..., help="Product id (e.g. p-0001) or name."),
    min_quantity: int = typer.Argument(..., help="Tier breakpoint (units)."),
    new_price: float = typer.Argument(..., help="New unit price for this tier."),
) -> None:
    db = _session()
    try:
        result = set_price_service(
            db,
            product_id=product_id,
            min_quantity=min_quantity,
            new_price=Decimal(str(new_price)),
        )
        _emit(result)
    except NotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        db.close()


@app.command("restock", help="Add stock for a product.")
def restock_command(
    product_id: str = typer.Argument(..., help="Product id (e.g. p-0001) or name."),
    quantity: int = typer.Argument(..., help="Units to add (must be > 0)."),
) -> None:
    db = _session()
    try:
        _emit(restock_service(db, product_id=product_id, quantity=quantity))
    except NotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Simulation clock
# ---------------------------------------------------------------------------


@day_app.command("advance", help="Advance the simulation by one day.")
def day_advance_command() -> None:
    db = _session()
    try:
        _emit(advance_day(db))
    finally:
        db.close()


@day_app.command("current", help="Print the current simulation day.")
def day_current_command() -> None:
    db = _session()
    try:
        _emit({"current_day": get_current_day(db)})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Snapshot / serve
# ---------------------------------------------------------------------------


@app.command("export", help="Print a JSON snapshot of the entire provider DB to stdout.")
def export_command() -> None:
    db = _session()
    try:
        _emit(export_state(db))
    finally:
        db.close()


@app.command("import", help="Restore provider DB contents from a JSON snapshot file.")
def import_command(file: Path = typer.Argument(..., exists=True, readable=True)) -> None:
    payload = json.loads(file.read_text())
    db = _session()
    try:
        try:
            import_state(db, payload)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(f"Imported provider state from {file}")
    finally:
        db.close()


@app.command("serve", help="Start the FastAPI server (uvicorn) on the given port.")
def serve_command(
    port: int = typer.Option(8001, "--port", help="TCP port to bind."),
    host: str = typer.Option("0.0.0.0", "--host", help="Network interface to bind."),
    reload: bool = typer.Option(False, "--reload", help="Enable uvicorn auto-reload."),
) -> None:
    ensure_seeded(Path(__file__).parent / "seed-provider.json")
    uvicorn.run("provider.api:app", host=host, port=port, reload=reload)


def main() -> None:  # entry point referenced from pyproject.toml
    app()


# Silence the unused-import warning while keeping the symbol importable.
__all__ = ["app", "main", "InsufficientStockError"]


if __name__ == "__main__":
    main()
