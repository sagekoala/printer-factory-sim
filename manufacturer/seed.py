"""Seed script — loads initial factory data from seed.json into the database.

Running this script multiple times is safe: existing rows (matched by primary
key) are skipped so data is never duplicated.

Usage:
    python -m manufacturer.seed
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

try:
    from manufacturer.database import (
        BOMEntryRow,
        FactoryConfigRow,
        ProductRow,
        SessionLocal,
        SupplierCatalogRow,
        SupplierRow,
        init_db,
    )
except ModuleNotFoundError:
    from database import (
        BOMEntryRow,
        FactoryConfigRow,
        ProductRow,
        SessionLocal,
        SupplierCatalogRow,
        SupplierRow,
        init_db,
    )

SEED_FILE = Path(__file__).parent / "seed.json"


def _exists(db, model, pk_field: str, pk_value: str) -> bool:
    """Return True if a row with the given primary key already exists."""
    return db.query(model).filter(getattr(model, pk_field) == pk_value).first() is not None


def seed() -> None:
    """Load seed.json into the database, skipping any rows that already exist."""
    init_db()

    data: dict = json.loads(SEED_FILE.read_text())
    db = SessionLocal()

    try:
        # --- Suppliers ---
        for s in data["suppliers"]:
            if not _exists(db, SupplierRow, "id", s["id"]):
                db.add(SupplierRow(
                    id=s["id"],
                    name=s["name"],
                    contact_email=s.get("contact_email"),
                ))
                print(f"  + Supplier: {s['name']}")
            else:
                print(f"  ~ Supplier already exists: {s['name']}")

        # --- Products ---
        for p in data["products"]:
            if not _exists(db, ProductRow, "id", p["id"]):
                db.add(ProductRow(
                    id=p["id"],
                    name=p["name"],
                    current_stock=p.get("current_stock", 0),
                    storage_size=p.get("storage_size", 1),
                ))
                print(f"  + Product: {p['name']} (stock={p.get('current_stock', 0)})")
            else:
                print(f"  ~ Product already exists: {p['name']}")

        # --- Supplier Catalog ---
        for c in data["supplier_catalog"]:
            if not _exists(db, SupplierCatalogRow, "id", c["id"]):
                db.add(SupplierCatalogRow(
                    id=c["id"],
                    supplier_id=c["supplier_id"],
                    part_id=c["part_id"],
                    unit_price=Decimal(c["unit_price"]),
                    min_order_qty=c["min_order_qty"],
                    lead_time_days=c["lead_time_days"],
                ))
                print(f"  + Catalog entry: supplier={c['supplier_id'][:8]}… part={c['part_id'][:8]}…")
            else:
                print(f"  ~ Catalog entry already exists: {c['id'][:8]}…")

        # --- BOM Entries ---
        for b in data["bom_entries"]:
            if not _exists(db, BOMEntryRow, "id", b["id"]):
                db.add(BOMEntryRow(
                    id=b["id"],
                    part_id=b["part_id"],
                    quantity_per_unit=b["quantity_per_unit"],
                ))
                print(f"  + BOM entry: part={b['part_id'][:8]}… qty={b['quantity_per_unit']}")
            else:
                print(f"  ~ BOM entry already exists: {b['id'][:8]}…")

        # --- Factory Config ---
        for key, value in data["factory_config"].items():
            existing = db.query(FactoryConfigRow).filter(FactoryConfigRow.key == key).first()
            if existing is None:
                db.add(FactoryConfigRow(key=key, value=value))
                print(f"  + Config: {key}={value}")
            else:
                print(f"  ~ Config already exists: {key}")

        db.commit()
        print("\nSeed complete.")

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print(f"Seeding database from {SEED_FILE} …\n")
    seed()
