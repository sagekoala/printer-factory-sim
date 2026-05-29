"""Tests for the manufacturer simulation + sales-order pipeline."""
from __future__ import annotations

import pytest


def _seed_basic_factory(db):
    """Plant a minimal but realistic state: 1 product, BOM, capacity."""
    from manufacturer.database import (
        BOMEntryRow,
        FactoryConfigRow,
        ProductRow,
    )

    db.add(ProductRow(id="p1", name="PCB", current_stock=100, storage_size=1))
    db.add(BOMEntryRow(id="b1", part_id="p1", quantity_per_unit=1))
    db.add(FactoryConfigRow(key="capacity_per_day", value=10))
    db.add(FactoryConfigRow(key="current_day", value=0))
    db.commit()


def test_release_to_production_queues_manufacturing_orders(tmp_manufacturer_db):
    """Repro for the bug noted in notes.md #18: releasing a sales order must
    queue MOs so ``_fulfill_manufacturing_orders`` has something to build."""
    db = tmp_manufacturer_db
    _seed_basic_factory(db)

    from manufacturer.database import ManufacturingOrderRow, SalesOrderRow
    from manufacturer.sales_orders import (
        create_sales_order,
        ensure_defaults,
        release_to_production,
    )

    ensure_defaults(db)
    order = create_sales_order(
        db, retailer_name="Shop", model="Pro 3D Printer", quantity=5, placed_day=1,
    )

    ok, msg = release_to_production(db, order["id"], current_day=1)
    assert ok, msg

    pending_mos = (
        db.query(ManufacturingOrderRow)
        .filter(ManufacturingOrderRow.status == "pending")
        .all()
    )
    assert len(pending_mos) == 5, "release_to_production must queue one MO per unit"

    sales_order = db.query(SalesOrderRow).filter(SalesOrderRow.id == order["id"]).first()
    assert sales_order.status == "released"
    assert sales_order.released_day == 1


def test_release_to_production_idempotent_on_non_pending(tmp_manufacturer_db):
    db = tmp_manufacturer_db
    _seed_basic_factory(db)

    from manufacturer.sales_orders import (
        create_sales_order,
        ensure_defaults,
        release_to_production,
    )

    ensure_defaults(db)
    order = create_sales_order(
        db, retailer_name="Shop", model="Pro 3D Printer", quantity=2, placed_day=1,
    )
    release_to_production(db, order["id"], current_day=1)

    ok, msg = release_to_production(db, order["id"], current_day=2)
    assert ok is False
    assert "not pending" in msg


def test_release_to_production_404_for_unknown_order(tmp_manufacturer_db):
    db = tmp_manufacturer_db
    _seed_basic_factory(db)

    from manufacturer.sales_orders import release_to_production

    ok, msg = release_to_production(db, "does-not-exist", current_day=1)
    assert ok is False
    assert "not found" in msg


def test_advance_day_fulfills_within_capacity_and_consumes_bom(tmp_manufacturer_db):
    """One full cycle: release → advance_day → finished stock incremented."""
    db = tmp_manufacturer_db
    _seed_basic_factory(db)

    from manufacturer.database import FinishedPrinterStockRow, ProductRow
    from manufacturer.sales_orders import (
        create_sales_order,
        ensure_defaults,
        release_to_production,
    )
    from manufacturer.simulation import advance_day

    ensure_defaults(db)
    order = create_sales_order(
        db, retailer_name="Shop", model="Pro 3D Printer", quantity=15, placed_day=0,
    )
    release_to_production(db, order["id"], current_day=0)

    new_day = advance_day(db)
    assert new_day == 1

    # Capacity is 10 per day → finished stock = 10, MOs done = 10, parts -= 10.
    finished = (
        db.query(FinishedPrinterStockRow)
        .filter(FinishedPrinterStockRow.model == "Pro 3D Printer")
        .first()
    )
    assert finished is not None
    assert finished.quantity == 10

    part = db.query(ProductRow).filter(ProductRow.id == "p1").first()
    assert part.current_stock == 90  # 100 - 10 * BOM(1)


def test_advance_day_does_not_double_count_finished_stock(tmp_manufacturer_db):
    """Regression for the double-call bug: even when ``/api/day/advance`` is
    exercised through the FastAPI route, finished stock should equal the
    number of MOs completed (not 2x)."""
    db = tmp_manufacturer_db
    _seed_basic_factory(db)

    from fastapi.testclient import TestClient

    from manufacturer.main import app
    from manufacturer.sales_orders import (
        create_sales_order,
        ensure_defaults,
        release_to_production,
    )

    ensure_defaults(db)
    order = create_sales_order(
        db, retailer_name="Shop", model="Pro 3D Printer", quantity=5, placed_day=0,
    )
    release_to_production(db, order["id"], current_day=0)

    with TestClient(app) as client:
        resp = client.post("/api/day/advance")
        assert resp.status_code == 200

        stock = client.get("/api/stock").json()
        # We expect 5 units of finished stock (one per released MO) — NOT 10.
        # The retailer ordered 5 units; capacity allows 10 so all 5 build.
        # If the bug returned, this would read 0 (everything shipped twice
        # against the order) or 10 (double-credit then shipped once).
        pcb_stock = next(item for item in stock if item["model"] == "Pro 3D Printer")
        assert pcb_stock["quantity"] == 0  # 5 produced, 5 shipped via advance_sales_orders


def test_create_sales_order_rejects_unknown_model(tmp_manufacturer_db):
    db = tmp_manufacturer_db
    _seed_basic_factory(db)

    from manufacturer.sales_orders import create_sales_order, ensure_defaults

    ensure_defaults(db)
    with pytest.raises(ValueError, match="wholesale catalog"):
        create_sales_order(
            db, retailer_name="Shop", model="UNKNOWN", quantity=1, placed_day=0,
        )


def test_bom_shortfall_detects_missing_parts(tmp_manufacturer_db):
    db = tmp_manufacturer_db

    from manufacturer.database import BOMEntryRow, ProductRow
    from manufacturer.simulation import _bom_shortfall

    db.add(ProductRow(id="p1", name="PCB", current_stock=3, storage_size=1))
    db.add(BOMEntryRow(id="b1", part_id="p1", quantity_per_unit=2))
    db.commit()

    # Need 2*5=10, have 3 → short by 7.
    bom = db.query(BOMEntryRow).all()
    short = _bom_shortfall(db, bom, quantity=5)
    assert short == {"PCB": 7}

    # Need 2*1=2, have 3 → no shortfall.
    assert _bom_shortfall(db, bom, quantity=1) == {}
