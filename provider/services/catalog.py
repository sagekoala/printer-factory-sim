"""Catalog and stock services for the provider app.

Public API
----------
- :func:`get_catalog` — list products with their pricing tiers and stock
- :func:`get_stock` — list every product's on-hand quantity
- :func:`set_price` — upsert a pricing tier (writes ``price_set`` event)
- :func:`restock` — increment stock for a product (writes ``restocked``)

All mutating functions log to the ``events`` table and commit in the
same transaction so the audit log is never out of sync with the data.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from provider.db import (
    PricingTierRow,
    ProductRow,
    StockRow,
    get_current_day,
    log_event,
)
from provider.services.exceptions import NotFoundError


# ---------------------------------------------------------------------------
# Read-side helpers
# ---------------------------------------------------------------------------


def get_catalog(db: Session) -> list[dict]:
    """Return every product with its pricing tiers and on-hand stock.

    Each list element has shape::

        {
            "id": str,
            "name": str,
            "description": str,
            "lead_time_days": int,
            "stock_quantity": int,
            "pricing_tiers": [
                {"id": str, "min_quantity": int, "unit_price": float},
                ...
            ],
        }

    Pricing tiers are sorted ascending by ``min_quantity``.
    """
    products = db.query(ProductRow).order_by(ProductRow.name).all()
    response: list[dict] = []
    for product in products:
        tiers = (
            db.query(PricingTierRow)
            .filter(PricingTierRow.product_id == product.id)
            .order_by(PricingTierRow.min_quantity)
            .all()
        )
        stock = db.query(StockRow).filter(StockRow.product_id == product.id).first()
        response.append(
            {
                "id": product.id,
                "name": product.name,
                "description": product.description,
                "lead_time_days": product.lead_time_days,
                "stock_quantity": stock.quantity if stock else 0,
                "pricing_tiers": [
                    {
                        "id": tier.id,
                        "min_quantity": tier.min_quantity,
                        "unit_price": float(tier.unit_price),
                    }
                    for tier in tiers
                ],
            }
        )
    return response


def get_stock(db: Session) -> list[dict]:
    """Return ``[{product_id, product_name, quantity}, ...]`` sorted by name."""
    rows = (
        db.query(ProductRow, StockRow)
        .join(StockRow, StockRow.product_id == ProductRow.id)
        .order_by(ProductRow.name)
        .all()
    )
    return [
        {
            "product_id": product.id,
            "product_name": product.name,
            "quantity": stock.quantity,
        }
        for product, stock in rows
    ]


# ---------------------------------------------------------------------------
# Write-side helpers
# ---------------------------------------------------------------------------


def set_price(
    db: Session,
    product_id: str,
    min_quantity: int,
    new_price: Decimal | float | str,
) -> dict:
    """Upsert a pricing tier and log a ``price_set`` event.

    ``product_id`` accepts either a product UUID/slug or a product name —
    this matches what the CLI passes through.  Raises:

    - :class:`NotFoundError` if the product cannot be located.
    - :class:`ValueError` if ``min_quantity`` is not positive.
    """
    if min_quantity <= 0:
        raise ValueError("min_quantity must be > 0")

    product = _find_product(db, product_id)
    if product is None:
        raise NotFoundError(f"Product not found: {product_id}")

    unit_price = Decimal(str(new_price))

    tier = (
        db.query(PricingTierRow)
        .filter(
            PricingTierRow.product_id == product.id,
            PricingTierRow.min_quantity == min_quantity,
        )
        .first()
    )
    if tier is None:
        tier = PricingTierRow(
            product_id=product.id,
            min_quantity=min_quantity,
            unit_price=unit_price,
        )
        db.add(tier)
        db.flush()
        action = "created"
    else:
        tier.unit_price = unit_price
        action = "updated"

    log_event(
        db,
        sim_day=get_current_day(db),
        event_type="price_set",
        entity_type="pricing_tier",
        entity_id=tier.id,
        detail=(
            f"{action} pricing tier for {product.name}: "
            f"min_quantity={min_quantity}, unit_price={unit_price}"
        ),
    )
    db.commit()

    return {
        "id": tier.id,
        "product_id": product.id,
        "min_quantity": tier.min_quantity,
        "unit_price": float(tier.unit_price),
    }


def restock(db: Session, product_id: str, quantity: int) -> dict:
    """Add ``quantity`` units to a product's stock and log ``restocked``.

    Raises :class:`NotFoundError` if the product is unknown and
    :class:`ValueError` if ``quantity`` is not positive.
    """
    if quantity <= 0:
        raise ValueError("quantity must be > 0")

    product = _find_product(db, product_id)
    if product is None:
        raise NotFoundError(f"Product not found: {product_id}")

    stock = db.query(StockRow).filter(StockRow.product_id == product.id).first()
    if stock is None:
        stock = StockRow(product_id=product.id, quantity=0)
        db.add(stock)
        db.flush()

    stock.quantity += quantity

    log_event(
        db,
        sim_day=get_current_day(db),
        event_type="restocked",
        entity_type="stock",
        entity_id=product.id,
        detail=(
            f"Restocked {quantity} units of {product.name}. "
            f"New stock: {stock.quantity}"
        ),
    )
    db.commit()

    return {
        "product_id": product.id,
        "product_name": product.name,
        "quantity": stock.quantity,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_product(db: Session, product_ref: str) -> Optional[ProductRow]:
    """Look up a product by id (preferred) or by name (fallback)."""
    by_id = db.query(ProductRow).filter(ProductRow.id == product_ref).first()
    if by_id is not None:
        return by_id
    return db.query(ProductRow).filter(ProductRow.name == product_ref).first()


__all__ = ["get_catalog", "get_stock", "set_price", "restock"]
