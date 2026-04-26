"""Seed the provider database from ``seed-provider.json``.

This script is idempotent: running it multiple times will not duplicate
rows or overwrite existing data.  If the ``products`` table already
contains rows the seed step is skipped (only the ``current_day`` is
ensured).

Usage
-----
From repo root::

    python -m provider.seed

Or from inside the provider directory (matches Week 6 spec)::

    cd provider && python seed.py
"""

from __future__ import annotations

import json
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

# Support both ``python -m provider.seed`` (package import) and the spec's
# ``cd provider && python seed.py`` invocation (flat import).
try:
    from provider.db import SessionLocal, init_db, set_current_day
    from provider.models import (
        PricingTierRow,
        ProductRow,
        SimStateRow,
        StockRow,
    )
except ModuleNotFoundError:  # pragma: no cover — flat-import fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from provider.db import SessionLocal, init_db, set_current_day  # type: ignore  # noqa: E402
    from provider.models import (  # type: ignore  # noqa: E402
        PricingTierRow,
        ProductRow,
        SimStateRow,
        StockRow,
    )


SEED_FILE = Path(__file__).resolve().parent / "seed-provider.json"


def _has_products(db) -> bool:
    return db.query(ProductRow).first() is not None


def _ensure_current_day(db, default_day: int = 0) -> None:
    if db.query(SimStateRow).filter(SimStateRow.key == "current_day").first() is None:
        set_current_day(db, default_day)


def seed(seed_file: Path | None = None) -> None:
    """Idempotently populate the provider DB from ``seed-provider.json``.

    Steps:
        1. Create tables if missing (``init_db``).
        2. If the catalog is already seeded, only ensure ``current_day``
           is initialised, then return.
        3. Otherwise insert products, pricing tiers, stock rows, and the
           starting simulation day from the JSON file.
    """
    init_db()

    source = seed_file or SEED_FILE
    if not source.exists():
        raise FileNotFoundError(f"Seed file not found: {source}")

    payload: dict[str, Any] = json.loads(source.read_text())

    db = SessionLocal()
    try:
        if _has_products(db):
            _ensure_current_day(db)
            db.commit()
            return

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
                    unit_price=Decimal(str(tier["unit_price"])),
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


def main() -> None:
    """CLI entry point for ``python seed.py`` / ``python -m provider.seed``."""
    print(f"Seeding provider DB from {SEED_FILE.name} …")
    seed()
    db = SessionLocal()
    try:
        product_count = db.query(ProductRow).count()
        tier_count = db.query(PricingTierRow).count()
        stock_count = db.query(StockRow).count()
    finally:
        db.close()
    print(
        f"Done. provider.db contains "
        f"{product_count} products, {tier_count} pricing tiers, {stock_count} stock rows."
    )


if __name__ == "__main__":
    main()
