"""Tests for the retailer day-clock + auto-fulfilment of backorders."""
from __future__ import annotations


def _seed(db):
    from retailer.database import CatalogRow, SimStateRow, StockRow

    db.add(CatalogRow(model="Pro 3D Printer", retail_price=1500.0))
    db.add(StockRow(model="Pro 3D Printer", quantity=0))
    db.add(SimStateRow(key="current_day", value="0"))
    db.commit()


def test_increment_day_starts_from_zero(tmp_retailer_db):
    db = tmp_retailer_db
    _seed(db)
    from retailer.simulation import _increment_day, get_current_day

    assert get_current_day(db) == 0
    new = _increment_day(db)
    db.commit()
    assert new == 1
    assert get_current_day(db) == 1


def test_auto_fulfill_backorders_when_stock_arrives(tmp_retailer_db):
    db = tmp_retailer_db
    _seed(db)

    from datetime import datetime

    from retailer.database import CustomerOrderRow, StockRow
    from retailer.simulation import _auto_fulfill_backorders

    db.add(CustomerOrderRow(
        id="o1", customer="alice", model="Pro 3D Printer",
        quantity=2, status="backordered", created_at=datetime.utcnow(),
    ))
    db.add(CustomerOrderRow(
        id="o2", customer="bob", model="Pro 3D Printer",
        quantity=5, status="backordered", created_at=datetime.utcnow(),
    ))
    stock_row = db.query(StockRow).filter(StockRow.model == "Pro 3D Printer").first()
    stock_row.quantity = 3  # only enough for o1, not o2
    db.commit()

    _auto_fulfill_backorders(db, day=1)
    db.commit()

    orders = {o.id: o.status for o in db.query(CustomerOrderRow).all()}
    assert orders["o1"] == "fulfilled"
    assert orders["o2"] == "backordered"  # not enough stock left

    assert db.query(StockRow).filter(StockRow.model == "Pro 3D Printer").first().quantity == 1


def test_create_customer_order_fulfills_from_stock(tmp_retailer_db):
    db = tmp_retailer_db
    _seed(db)

    from fastapi.testclient import TestClient

    from retailer.database import StockRow
    import retailer.main as retailer_main

    stock_row = db.query(StockRow).filter(StockRow.model == "Pro 3D Printer").first()
    stock_row.quantity = 3
    db.commit()

    with TestClient(retailer_main.app) as client:
        resp = client.post(
            "/api/orders",
            json={"customer": "alice", "model": "Pro 3D Printer", "quantity": 1},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "fulfilled"


def test_create_customer_order_backorders_when_no_stock(tmp_retailer_db):
    db = tmp_retailer_db
    _seed(db)

    from fastapi.testclient import TestClient

    import retailer.main as retailer_main
    with TestClient(retailer_main.app) as client:
        resp = client.post(
            "/api/orders",
            json={"customer": "alice", "model": "Pro 3D Printer", "quantity": 2},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "backordered"


def test_set_price_path_param(tmp_retailer_db):
    """Retailer's pricing endpoint must accept the model in the URL path
    (consistent with the manufacturer's POST /api/prices/{model})."""
    db = tmp_retailer_db
    _seed(db)

    from fastapi.testclient import TestClient

    import retailer.main as retailer_main
    with TestClient(retailer_main.app) as client:
        resp = client.post("/api/prices/Pro 3D Printer", json={"price": 1800.0})
        assert resp.status_code == 200
        assert resp.json() == {"model": "Pro 3D Printer", "retail_price": 1800.0}

        # 404 for unknown model
        resp = client.post("/api/prices/UNKNOWN", json={"price": 1.0})
        assert resp.status_code == 404
