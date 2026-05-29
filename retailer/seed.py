"""Seed initial data for the Retailer database."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from retailer.database import CatalogRow, SimStateRow, StockRow

_RETAILER_DIR = Path(__file__).resolve().parent


def seed_if_empty(db: Session, config: dict | None = None) -> None:
    """Seed the retailer DB from ``seed-retailer.json`` on first start.

    ``config`` is accepted for forward-compatibility (some deployments may
    want a per-retailer seed file later) but is currently unused — every
    retailer ships the same baseline catalogue.
    """
    del config  # reserved for future per-config customisation
    if db.query(CatalogRow).count() > 0:
        return

    seed_data = json.loads((_RETAILER_DIR / "seed-retailer.json").read_text())

    for item in seed_data["catalog"]:
        db.add(CatalogRow(model=item["model"], retail_price=item["retail_price"]))

    for item in seed_data.get("stock", []):
        existing = db.query(StockRow).filter(StockRow.model == item["model"]).first()
        if existing is None:
            db.add(StockRow(model=item["model"], quantity=item["quantity"]))

    if db.query(SimStateRow).filter(SimStateRow.key == "current_day").count() == 0:
        db.add(SimStateRow(key="current_day", value="0"))

    db.commit()
