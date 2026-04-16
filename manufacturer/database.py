"""SQLAlchemy database setup for the 3D Printer Production Simulator.

Defines the engine, session factory, ORM table rows, and helper functions.
The ORM rows mirror the Pydantic models in models.py but are kept separate
so that the API layer can use clean Pydantic objects throughout.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Generator

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./printer_factory.db")

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
    """Create all tables if they do not already exist."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Yield a database session; intended for use as a FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
