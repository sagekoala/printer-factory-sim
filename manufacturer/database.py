"""SQLAlchemy database setup for the 3D Printer Production Simulator.

Defines the engine, session factory, ORM table rows, and helper functions.
The ORM rows mirror the Pydantic models in models.py but are kept separate
so that the API layer can use clean Pydantic objects throughout.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Generator

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# The default DB always lives next to this file (``manufacturer/manufacturer.db``)
# regardless of the caller's cwd, so ``cd manufacturer && uvicorn main:app``
# and ``uvicorn manufacturer.main:app`` from the repo root operate on the
# same file.  Override with the ``DATABASE_URL`` env var when needed.
_MANUFACTURER_DIR = Path(__file__).resolve().parent
_DEFAULT_DB_PATH = _MANUFACTURER_DIR / "manufacturer.db"

DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # required for SQLite + threading
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


# ---------------------------------------------------------------------------
# ORM table definitions
# ---------------------------------------------------------------------------


class ProductRow(Base):
    """Persistent representation of a :class:`models.Product`."""

    __tablename__ = "products"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, unique=True, nullable=False)
    current_stock = Column(Integer, default=0, nullable=False)
    storage_size = Column(Integer, default=1, nullable=False)


class SupplierRow(Base):
    """Persistent representation of a :class:`models.Supplier`."""

    __tablename__ = "suppliers"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, unique=True, nullable=False)
    contact_email = Column(String, nullable=True)


class SupplierCatalogRow(Base):
    """Persistent representation of a :class:`models.SupplierCatalog` entry."""

    __tablename__ = "supplier_catalog"
    __table_args__ = (UniqueConstraint("supplier_id", "part_id"),)

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    supplier_id = Column(String, ForeignKey("suppliers.id"), nullable=False)
    part_id = Column(String, ForeignKey("products.id"), nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    min_order_qty = Column(Integer, nullable=False)
    lead_time_days = Column(Integer, nullable=False)


class BOMEntryRow(Base):
    """Persistent representation of a :class:`models.BOMEntry`."""

    __tablename__ = "bom_entries"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    part_id = Column(String, ForeignKey("products.id"), nullable=False, unique=True)
    quantity_per_unit = Column(Integer, nullable=False)


class ManufacturingOrderRow(Base):
    """Persistent representation of a :class:`models.ManufacturingOrder`."""

    __tablename__ = "manufacturing_orders"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    quantity = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    days_elapsed = Column(Integer, nullable=True)


class PurchaseOrderRow(Base):
    """Persistent representation of a :class:`models.PurchaseOrder`."""

    __tablename__ = "purchase_orders"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    part_id = Column(String, ForeignKey("products.id"), nullable=False)
    supplier_id = Column(String, ForeignKey("suppliers.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    ship_date = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    lead_time_remaining = Column(Integer, nullable=True)


class OutboundPurchaseOrderRow(Base):
    """Purchase order placed from manufacturer to an external provider API.

    This is the *Week 6* "purchase_orders" table from the challenge spec —
    we keep the historical Python class name :class:`OutboundPurchaseOrderRow`
    (and physical table ``outbound_purchase_orders``) to avoid clashing
    with the Week 5 :class:`PurchaseOrderRow` table for *internal* POs.

    ``provider_order_id`` is stored as ``String`` rather than the spec's
    ``INTEGER`` because the running provider API issues UUID order ids
    (e.g. ``"78c4ead3-..."``) — coercing them to integers would lose
    information.
    """

    __tablename__ = "outbound_purchase_orders"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    provider_name = Column(String, nullable=False)
    provider_order_id = Column(String, nullable=False, unique=True)
    product_name = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False, default=0.0)
    total_price = Column(Float, nullable=False, default=0.0)
    placed_day = Column(Integer, nullable=False)
    expected_delivery_day = Column(Integer, nullable=False)
    delivered_day = Column(Integer, nullable=True)
    status = Column(String, nullable=False, default="pending")


class InventoryRow(Base):
    """Persistent representation of a :class:`models.Inventory` transaction."""

    __tablename__ = "inventory_transactions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    part_id = Column(String, ForeignKey("products.id"), nullable=False)
    transaction_type = Column(String, nullable=False)  # IN | OUT | ADJUST
    quantity = Column(Integer, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    reference_id = Column(String, nullable=True)  # PO or MO id


class EventRow(Base):
    """Persistent representation of a :class:`models.Event` audit entry."""

    __tablename__ = "events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    day = Column(Integer, nullable=False)
    event_type = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=False)
    description = Column(String, nullable=False)
    event_metadata = Column(JSON, nullable=True)


class FactoryConfigRow(Base):
    """Key/value store for runtime factory configuration."""

    __tablename__ = "factory_config"

    key = Column(String, primary_key=True)
    value = Column(JSON, nullable=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def init_db() -> None:
    """Create all tables if they do not already exist, then run lightweight
    column migrations for tables that gained fields after Week 5.

    SQLAlchemy's :func:`create_all` only creates *missing* tables — it
    never alters existing ones.  For SQLite this is fine when the DB is
    empty, but if a developer has a leftover ``manufacturer.db`` from
    Week 5/early-Week-6 it will be missing the new
    ``outbound_purchase_orders`` columns.  We patch them in here.
    """
    Base.metadata.create_all(bind=engine)
    _migrate_outbound_purchase_orders_columns()


def _migrate_outbound_purchase_orders_columns() -> None:
    """Add ``unit_price`` / ``total_price`` / ``delivered_day`` if missing.

    Idempotent: introspects the live schema with ``PRAGMA table_info``
    and only ALTERs columns that aren't already present.
    """
    needed: dict[str, str] = {
        "unit_price": "REAL NOT NULL DEFAULT 0.0",
        "total_price": "REAL NOT NULL DEFAULT 0.0",
        "delivered_day": "INTEGER",
    }
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "PRAGMA table_info(outbound_purchase_orders)"
        ).fetchall()
        if not rows:
            return  # table not created yet — create_all will handle it next start
        existing = {row[1] for row in rows}
        for col, ddl in needed.items():
            if col not in existing:
                conn.exec_driver_sql(
                    f"ALTER TABLE outbound_purchase_orders ADD COLUMN {col} {ddl}"
                )


def get_db() -> Generator[Session, None, None]:
    """Yield a database session; intended for use as a FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Week 7 additions — sales orders, finished printer stock, wholesale prices
# ---------------------------------------------------------------------------


class SalesOrderRow(Base):
    """Inbound order received from a retailer (Week 7)."""

    __tablename__ = "sales_orders"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    retailer_name = Column(String, nullable=False)
    model = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False, default=0.0)
    total_price = Column(Float, nullable=False, default=0.0)
    status = Column(String, nullable=False, default="pending")
    placed_day = Column(Integer, nullable=True)
    released_day = Column(Integer, nullable=True)
    shipped_day = Column(Integer, nullable=True)
    delivered_day = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FinishedPrinterStockRow(Base):
    """Finished printer inventory — output of manufacturing, input to sales (Week 7)."""

    __tablename__ = "finished_printer_stock"

    model = Column(String, primary_key=True)
    quantity = Column(Integer, default=0, nullable=False)


class WholesalePriceRow(Base):
    """Wholesale price for a finished printer model (Week 7)."""

    __tablename__ = "wholesale_prices"

    model = Column(String, primary_key=True)
    price = Column(Float, nullable=False)
