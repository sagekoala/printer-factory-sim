"""SQLAlchemy database setup for the Retailer Simulator."""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Generator

from sqlalchemy import Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

_RETAILER_DIR = Path(__file__).resolve().parent
_DEFAULT_DB_PATH = _RETAILER_DIR / "retailer.db"

DATABASE_URL: str = os.getenv("RETAILER_DATABASE_URL", f"sqlite:///{_DEFAULT_DB_PATH}")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class CatalogRow(Base):
    __tablename__ = "catalog"

    model = Column(String, primary_key=True)
    retail_price = Column(Float, nullable=False)


class StockRow(Base):
    __tablename__ = "stock"

    model = Column(String, primary_key=True)
    quantity = Column(Integer, default=0, nullable=False)


class CustomerOrderRow(Base):
    __tablename__ = "customer_orders"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    customer = Column(String, nullable=False)
    model = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    fulfilled_at = Column(DateTime, nullable=True)


class PurchaseOrderRow(Base):
    __tablename__ = "purchase_orders"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    model = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False, default=0.0)
    total_price = Column(Float, nullable=False, default=0.0)
    status = Column(String, nullable=False, default="pending")
    placed_day = Column(Integer, nullable=False, default=0)
    manufacturer_order_id = Column(String, nullable=True)
    delivered_day = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SalesHistoryRow(Base):
    __tablename__ = "sales_history"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    model = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    customer_order_id = Column(String, nullable=False)
    day = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class EventRow(Base):
    __tablename__ = "events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    day = Column(Integer, nullable=False)
    event_type = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=False)
    description = Column(String, nullable=False)


class SimStateRow(Base):
    __tablename__ = "sim_state"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
