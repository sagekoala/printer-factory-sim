"""Pydantic models for the 3D Printer Production Simulator.

These define the canonical shape of every domain entity and are used for
validation, serialisation, and as a reference for the SQLAlchemy ORM layer.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ManufacturingOrderStatus(str, Enum):
    """Lifecycle states for a manufacturing order."""

    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


class PurchaseOrderStatus(str, Enum):
    """Lifecycle states for a purchase order."""

    pending = "pending"
    shipped = "shipped"
    delivered = "delivered"
    cancelled = "cancelled"


class TransactionType(str, Enum):
    """Direction of an inventory stock movement."""

    IN = "IN"        # Parts received (PO delivery)
    OUT = "OUT"      # Parts consumed (production start)
    ADJUST = "ADJUST"  # Manual correction


class EventType(str, Enum):
    """Significant simulation events recorded in the audit log."""

    ORDER_CREATED = "ORDER_CREATED"
    PRODUCTION_STARTED = "PRODUCTION_STARTED"
    PRODUCTION_COMPLETED = "PRODUCTION_COMPLETED"
    PURCHASE_CREATED = "PURCHASE_CREATED"
    PURCHASE_SHIPPED = "PURCHASE_SHIPPED"
    PURCHASE_DELIVERED = "PURCHASE_DELIVERED"
    STOCK_ADJUSTED = "STOCK_ADJUSTED"
    SIMULATION_STARTED = "SIMULATION_STARTED"
    SIMULATION_PAUSED = "SIMULATION_PAUSED"
    SIMULATION_RESET = "SIMULATION_RESET"


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


class Product(BaseModel):
    """A component part used in printer assembly (e.g. PCB, Extruder).

    ``current_stock`` is the live on-hand quantity.
    ``storage_size`` is the warehouse space consumed per unit.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str
    current_stock: int = 0
    storage_size: int = 1


class BOMEntry(BaseModel):
    """One line in the Bill of Materials — quantity of a part per printer."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    part_id: uuid.UUID
    quantity_per_unit: int


class Supplier(BaseModel):
    """A vendor that sells parts to the factory."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str
    contact_email: Optional[str] = None


class SupplierCatalog(BaseModel):
    """Pricing and lead-time for a specific part from a specific supplier.

    The combination (supplier_id, part_id) must be unique.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    supplier_id: uuid.UUID
    part_id: uuid.UUID
    unit_price: Decimal
    min_order_qty: int
    lead_time_days: int


class ManufacturingOrder(BaseModel):
    """An instruction to assemble a batch of printers.

    Components are consumed from inventory when the order moves to
    ``in_progress``; the order completes after ``capacity_per_day`` days.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    quantity: int
    status: ManufacturingOrderStatus = ManufacturingOrderStatus.pending
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    days_elapsed: Optional[int] = None


class PurchaseOrder(BaseModel):
    """An order placed with a supplier to restock a part.

    The order arrives exactly ``lead_time_days`` simulation days after creation.
    Warehouse capacity is validated before the PO is accepted.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    part_id: uuid.UUID
    supplier_id: uuid.UUID
    quantity: int
    unit_price: Decimal
    status: PurchaseOrderStatus = PurchaseOrderStatus.pending
    created_at: datetime = Field(default_factory=datetime.utcnow)
    ship_date: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    lead_time_remaining: Optional[int] = None
    days_to_arrival: Optional[int] = None


class Inventory(BaseModel):
    """A single stock-movement record (IN, OUT, or ADJUST).

    ``reference_id`` links back to the PO or MO that caused the movement.
    Positive ``quantity`` = stock added; negative = stock consumed.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    part_id: uuid.UUID
    transaction_type: TransactionType
    quantity: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    reference_id: Optional[uuid.UUID] = None


class Event(BaseModel):
    """An audit-log entry for a significant simulation event."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    day: int
    event_type: EventType
    entity_type: str
    entity_id: uuid.UUID
    description: str
    event_metadata: dict[str, Any] = Field(default_factory=dict)
