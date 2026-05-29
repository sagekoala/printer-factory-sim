"""Shared pytest fixtures.

Most tests need a temporary SQLite DB so that they don't collide with the
checked-in default file. The fixtures below override the per-app
``*_DATABASE_URL`` environment variables **before** importing the app
modules, then return a fresh session per test.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def tmp_manufacturer_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the manufacturer at a tmp SQLite file and return its Session."""
    db_path = tmp_path / "manufacturer.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    # Reload the manufacturer modules so they pick up the new env var.
    import importlib

    import manufacturer.database as db_module
    importlib.reload(db_module)
    import manufacturer.sales_orders as sales_module
    importlib.reload(sales_module)
    import manufacturer.simulation as sim_module
    importlib.reload(sim_module)

    db_module.init_db()
    session = db_module.SessionLocal()
    try:
        yield session
    finally:
        session.close()
        db_module.engine.dispose()


@pytest.fixture()
def tmp_retailer_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "retailer.db"
    monkeypatch.setenv("RETAILER_DATABASE_URL", f"sqlite:///{db_path}")

    import importlib

    import retailer.database as db_module
    importlib.reload(db_module)
    import retailer.simulation as sim_module
    importlib.reload(sim_module)

    db_module.init_db()
    session = db_module.SessionLocal()
    try:
        yield session
    finally:
        session.close()
        db_module.engine.dispose()


@pytest.fixture()
def tmp_provider_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "provider.db"
    monkeypatch.setenv("PROVIDER_DATABASE_URL", f"sqlite:///{db_path}")

    import importlib

    import provider.db as db_module
    importlib.reload(db_module)
    import provider.services.simulation as sim_module
    importlib.reload(sim_module)
    import provider.services.orders as orders_module
    importlib.reload(orders_module)
    import provider.services.catalog as catalog_module
    importlib.reload(catalog_module)

    db_module.init_db()
    session = db_module.SessionLocal()
    try:
        yield session
    finally:
        session.close()
        db_module.engine.dispose()


@pytest.fixture(autouse=True)
def _seed_random():
    """Force a deterministic seed so tests using ``random`` are stable."""
    import random

    random.seed(42)
    yield
