"""Catalog and stock services for provider app."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from provider.db import PricingTierRow, ProductRow, StockRow, get_current_day, log_event


def list_catalog(db: Session) -> list[dict]:
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


def list_stock(db: Session) -> list[dict]:
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


def set_tier_price(db: Session, product_ref: str, min_quantity: int, unit_price: Decimal) -> dict:
    product = _find_product(db, product_ref)
    if product is None:
        raise ValueError(f"Product not found: {product_ref}")
    if min_quantity <= 0:
        raise ValueError("min_quantity must be > 0")

    tier = (
        db.query(PricingTierRow)
        .filter(
            PricingTierRow.product_id == product.id,
            PricingTierRow.min_quantity == min_quantity,
        )
        .first()
    )
    if tier is None:
        tier = PricingTierRow(product_id=product.id, min_quantity=min_quantity, unit_price=unit_price)
        db.add(tier)
    else:
        tier.unit_price = unit_price

    log_event(
        db,
        sim_day=get_current_day(db),
        event_type="PRICE_TIER_SET",
        entity_type="pricing_tier",
        entity_id=tier.id,
        detail=(
            f"Set pricing tier for {product.name}: min_quantity={min_quantity}, "
            f"unit_price={unit_price}"
        ),
    )
    db.commit()
    return {
        "id": tier.id,
        "product_id": product.id,
        "min_quantity": tier.min_quantity,
        "unit_price": float(tier.unit_price),
    }


def restock(db: Session, product_ref: str, quantity: int) -> dict:
    if quantity <= 0:
        raise ValueError("quantity must be > 0")

    product = _find_product(db, product_ref)
    if product is None:
        raise ValueError(f"Product not found: {product_ref}")

    stock = db.query(StockRow).filter(StockRow.product_id == product.id).first()
    if stock is None:
        stock = StockRow(product_id=product.id, quantity=0)
        db.add(stock)
        db.flush()

    stock.quantity += quantity
    current_day = get_current_day(db)
    log_event(
        db,
        sim_day=current_day,
        event_type="RESTOCKED",
        entity_type="stock",
        entity_id=product.id,
        detail=f"Restocked {quantity} units of {product.name}. New stock: {stock.quantity}",
    )
    db.commit()

    return {
        "product_id": product.id,
        "product_name": product.name,
        "quantity": stock.quantity,
    }


def _find_product(db: Session, product_ref: str) -> ProductRow | None:
    by_id = db.query(ProductRow).filter(ProductRow.id == product_ref).first()
    if by_id is not None:
        return by_id
    return db.query(ProductRow).filter(ProductRow.name == product_ref).first()
