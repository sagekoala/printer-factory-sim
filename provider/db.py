"""Database layer for the provider app."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Generator

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.getenv("PROVIDER_DATABASE_URL", "sqlite:///./provider/provider.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Provider declarative base."""


class OrderStatus(str, Enum):
    """Order status enum."""

    pending = "pending"
    confirmed = "confirmed"
    in_progress = "in_progress"
    shipped = "shipped"
    delivered = "delivered"
    rejected = "rejected"
    cancelled = "cancelled"


class ProductRow(Base):
    __tablename__ = "products"

    id = Column(String, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text, nullable=False)
    lead_time_days = Column(Integer, nullable=False)


class PricingTierRow(Base):
    __tablename__ = "pricing_tiers"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    product_id = Column(String, ForeignKey("products.id"), nullable=False)
    min_quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)


class StockRow(Base):
    __tablename__ = "stock"

    product_id = Column(String, ForeignKey("products.id"), primary_key=True)
    quantity = Column(Integer, nullable=False, default=0)


class OrderRow(Base):
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
    status = Column(String, nullable=False, default=OrderStatus.pending.value)


class EventRow(Base):
    __tablename__ = "events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    sim_day = Column(Integer, nullable=False)
    event_type = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=False)
    detail = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SimStateRow(Base):
    __tablename__ = "sim_state"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)


def init_db() -> None:
    """Create provider tables."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for DB sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_day(db: Session) -> int:
    row = db.query(SimStateRow).filter(SimStateRow.key == "current_day").first()
    return int(row.value) if row else 0


def set_current_day(db: Session, day: int) -> None:
    row = db.query(SimStateRow).filter(SimStateRow.key == "current_day").first()
    if row is None:
        db.add(SimStateRow(key="current_day", value=str(day)))
    else:
        row.value = str(day)


def log_event(
    db: Session,
    sim_day: int,
    event_type: str,
    entity_type: str,
    entity_id: str,
    detail: str,
) -> None:
    db.add(
        EventRow(
            sim_day=sim_day,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
            created_at=datetime.utcnow(),
        )
    )


def ensure_seeded(seed_file: Path | None = None) -> None:
    """Seed provider catalog if products table is empty."""
    init_db()
    db = SessionLocal()
    try:
        has_products = db.query(ProductRow).first() is not None
        if has_products:
            if db.query(SimStateRow).filter(SimStateRow.key == "current_day").first() is None:
                set_current_day(db, 0)
                db.commit()
            return

        source = seed_file or (Path(__file__).parent / "seed-provider.json")
        payload: dict[str, Any] = json.loads(source.read_text())

        for product in payload.get("products", []):
            db.add(
                ProductRow(
                    id=product["id"],
                    name=product["name"],
                    description=product["description"],
                    lead_time_days=int(product["lead_time_days"]),
                )
            )

        for tier in payload.get("pricing_tiers", []):
            db.add(
                PricingTierRow(
                    id=tier.get("id") or str(uuid.uuid4()),
                    product_id=tier["product_id"],
                    min_quantity=int(tier["min_quantity"]),
                    unit_price=tier["unit_price"],
                )
            )

        for item in payload.get("stock", []):
            db.add(
                StockRow(
                    product_id=item["product_id"],
                    quantity=int(item["quantity"]),
                )
            )

        state = payload.get("sim_state", {"current_day": 0})
        set_current_day(db, int(state.get("current_day", 0)))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
