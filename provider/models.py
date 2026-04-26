"""Provider data models.

This module is the single source of truth for both the persistence layer
(SQLAlchemy ORM rows) and the wire/serialisation layer (Pydantic schemas)
of the provider app.

Layout
------
- Status enums (``OrderStatus``)
- ``Base``: declarative base shared by every ORM table
- ORM row classes (``*Row``) — mapped to SQLite tables
- Pydantic schemas — used by the API layer and service responses

Keeping ORM and Pydantic side by side here means the database schema and
the public contract evolve together.  The ``provider.db`` module imports
the ORM rows from here so existing call-sites (``from provider.db import
ProductRow``) continue to work.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase


# ---------------------------------------------------------------------------
# Status enums
# ---------------------------------------------------------------------------


class OrderStatus(str, Enum):
    """Lifecycle states for a provider-side order.

    Transitions are linear during normal operation
    (``PENDING -> CONFIRMED -> IN_PROGRESS -> SHIPPED -> DELIVERED``);
    ``CANCELLED`` and ``REJECTED`` are terminal failure states.

    The string ``value`` of each member is the lowercase form persisted
    in the database and surfaced over the API.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# ORM declarative base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Shared declarative base for every provider ORM table."""


# ---------------------------------------------------------------------------
# ORM row definitions
# ---------------------------------------------------------------------------


class ProductRow(Base):
    """A part the provider sells.  Mirrors :class:`Product`."""

    __tablename__ = "products"

    id = Column(String, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text, nullable=False)
    lead_time_days = Column(Integer, nullable=False)


class PricingTierRow(Base):
    """A volume-break price for a product.  Mirrors :class:`PricingTier`.

    The applicable tier for an order is the one with the largest
    ``min_quantity`` that is still ``<=`` the order quantity.
    """

    __tablename__ = "pricing_tiers"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    min_quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)


class StockRow(Base):
    """On-hand inventory of a single product.  Mirrors :class:`Stock`."""

    __tablename__ = "stock"

    product_id = Column(String, ForeignKey("products.id"), primary_key=True)
    quantity = Column(Integer, nullable=False, default=0)


class OrderRow(Base):
    """A purchase placed by a buyer (typically the manufacturer app).

    Mirrors :class:`Order`.  ``placed_day`` / ``shipped_day`` /
    ``delivered_day`` are simulation days, not wall-clock dates.
    """

    __tablename__ = "orders"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    buyer = Column(String, nullable=False)
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    total_price = Column(Numeric(12, 2), nullable=False)
    placed_day = Column(Integer, nullable=False)
    expected_delivery_day = Column(Integer, nullable=False)
    shipped_day = Column(Integer, nullable=True)
    delivered_day = Column(Integer, nullable=True)
    status = Column(String, nullable=False, default=OrderStatus.PENDING.value)


class EventRow(Base):
    """An audit-log entry for any significant provider state change."""

    __tablename__ = "events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    sim_day = Column(Integer, nullable=False)
    event_type = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=False)
    detail = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SimStateRow(Base):
    """Free-form key/value store for simulation state (e.g. ``current_day``)."""

    __tablename__ = "sim_state"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)


# ---------------------------------------------------------------------------
# Pydantic schemas — public/API surface
# ---------------------------------------------------------------------------


class Product(BaseModel):
    """Public representation of a provider catalog product."""

    id: str
    name: str
    description: str
    lead_time_days: int


class PricingTier(BaseModel):
    """Public representation of a pricing tier."""

    id: str
    product_id: str
    min_quantity: int
    unit_price: Decimal


class Stock(BaseModel):
    """Public representation of a stock row."""

    product_id: str
    quantity: int


class Order(BaseModel):
    """Public representation of a provider order."""

    id: str
    buyer: str
    product_id: str
    quantity: int
    unit_price: Decimal
    total_price: Decimal
    placed_day: int
    expected_delivery_day: int
    shipped_day: Optional[int] = None
    delivered_day: Optional[int] = None
    status: OrderStatus


class Event(BaseModel):
    """Public representation of an audit event."""

    id: str
    sim_day: int
    event_type: str
    entity_type: str
    entity_id: str
    detail: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


__all__ = [
    "Base",
    "OrderStatus",
    "ProductRow",
    "PricingTierRow",
    "StockRow",
    "OrderRow",
    "EventRow",
    "SimStateRow",
    "Product",
    "PricingTier",
    "Stock",
    "Order",
    "Event",
]
