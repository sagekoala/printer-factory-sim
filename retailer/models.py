"""Pydantic models for the Retailer Simulator."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
import uuid

from pydantic import BaseModel


class CustomerOrderStatus(str, Enum):
    pending = "pending"
    fulfilled = "fulfilled"
    backordered = "backordered"
    cancelled = "cancelled"


class PurchaseOrderStatus(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    in_progress = "in_progress"
    shipped = "shipped"
    delivered = "delivered"
    cancelled = "cancelled"


class CatalogItem(BaseModel):
    model: str
    retail_price: float


class CustomerOrder(BaseModel):
    id: uuid.UUID
    customer: str
    model: str
    quantity: int
    status: CustomerOrderStatus
    created_at: datetime
    fulfilled_at: Optional[datetime] = None


class RetailerPurchaseOrder(BaseModel):
    id: uuid.UUID
    model: str
    quantity: int
    unit_price: float
    total_price: float
    status: PurchaseOrderStatus
    placed_day: int
    manufacturer_order_id: Optional[str] = None
    delivered_day: Optional[int] = None
    created_at: datetime


class StockItem(BaseModel):
    model: str
    quantity: int
