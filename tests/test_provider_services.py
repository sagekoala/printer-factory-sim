"""Tests for the provider order/catalog services."""
from __future__ import annotations

from decimal import Decimal

import pytest


def _seed_provider(db, *, stock_quantity: int = 50):
    from provider.db import PricingTierRow, ProductRow, SimStateRow, StockRow

    db.add(ProductRow(
        id="p-0001", name="PCB", description="Printed Circuit Board", lead_time_days=3,
    ))
    db.add(PricingTierRow(
        id="t-0001", product_id="p-0001", min_quantity=1, unit_price=Decimal("45.00"),
    ))
    db.add(PricingTierRow(
        id="t-0002", product_id="p-0001", min_quantity=10, unit_price=Decimal("40.00"),
    ))
    db.add(StockRow(product_id="p-0001", quantity=stock_quantity))
    db.add(SimStateRow(key="current_day", value="3"))
    db.commit()


def test_create_order_picks_correct_pricing_tier(tmp_provider_db):
    db = tmp_provider_db
    _seed_provider(db)

    from provider.services.orders import create_order

    # qty=15 hits the 10-unit tier (40.00 ea), not the 1-unit tier (45.00).
    order = create_order(db, buyer="manufacturer", product_id="p-0001", quantity=15)

    assert order["unit_price"] == 40.0
    assert order["total_price"] == 600.0
    assert order["quantity"] == 15
    assert order["status"] == "pending"
    # Lead-time floor: placed_day=3 + lead_time=3 → delivery=6.
    assert order["placed_day"] == 3
    assert order["expected_delivery_day"] == 6


def test_create_order_falls_back_to_lowest_tier(tmp_provider_db):
    db = tmp_provider_db
    _seed_provider(db)

    from provider.services.orders import create_order

    # qty=5 is between tiers → picks min_qty=1 (45.00).
    order = create_order(db, buyer="manufacturer", product_id="p-0001", quantity=5)
    assert order["unit_price"] == 45.0
    assert order["total_price"] == 225.0


def test_create_order_rejects_insufficient_stock(tmp_provider_db):
    db = tmp_provider_db
    _seed_provider(db, stock_quantity=2)

    from provider.services.exceptions import InsufficientStockError
    from provider.services.orders import create_order

    with pytest.raises(InsufficientStockError):
        create_order(db, buyer="m", product_id="p-0001", quantity=5)


def test_create_order_rejects_unknown_product(tmp_provider_db):
    db = tmp_provider_db
    _seed_provider(db)

    from provider.services.exceptions import NotFoundError
    from provider.services.orders import create_order

    with pytest.raises(NotFoundError):
        create_order(db, buyer="m", product_id="bogus", quantity=1)


def test_create_order_rejects_zero_quantity(tmp_provider_db):
    db = tmp_provider_db
    _seed_provider(db)

    from provider.services.orders import create_order

    with pytest.raises(ValueError, match="quantity"):
        create_order(db, buyer="m", product_id="p-0001", quantity=0)


def test_create_order_rejects_empty_buyer(tmp_provider_db):
    db = tmp_provider_db
    _seed_provider(db)

    from provider.services.orders import create_order

    with pytest.raises(ValueError, match="buyer"):
        create_order(db, buyer="   ", product_id="p-0001", quantity=1)


def test_advance_day_ships_then_delivers(tmp_provider_db):
    """Full lifecycle: pending → confirmed → in_progress → shipped → delivered."""
    db = tmp_provider_db
    _seed_provider(db)

    from provider.db import OrderRow
    from provider.services.orders import create_order
    from provider.services.simulation import advance_day

    order = create_order(db, buyer="m", product_id="p-0001", quantity=1)
    order_id = order["id"]

    summary = advance_day(db)
    row = db.query(OrderRow).filter(OrderRow.id == order_id).first()
    assert row.status == "shipped"
    assert summary["orders_shipped"] == 1
    assert summary["day"] == 4

    # Walk forward until expected_delivery_day arrives (3-day lead time
    # was rebased to day 4 + 3 = 7).
    for _ in range(3):
        advance_day(db)
    row = db.query(OrderRow).filter(OrderRow.id == order_id).first()
    assert row.status == "delivered"


def test_advance_day_lead_time_modifier_extends_window(tmp_provider_db):
    """A modifier > 1 must extend the delivery date."""
    db = tmp_provider_db
    _seed_provider(db)

    from provider.db import OrderRow
    from provider.services.orders import create_order
    from provider.services.simulation import advance_day

    order = create_order(db, buyer="m", product_id="p-0001", quantity=1)
    order_id = order["id"]
    base_delivery = order["expected_delivery_day"]

    advance_day(db, lead_time_modifier=2.0)
    row = db.query(OrderRow).filter(OrderRow.id == order_id).first()
    # New delivery = new_day(4) + ceil(lead_time(3)*2.0) = 4 + 6 = 10
    assert row.expected_delivery_day == 10
    assert row.expected_delivery_day > base_delivery
