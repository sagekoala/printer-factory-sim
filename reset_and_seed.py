#!/usr/bin/env python3
"""Reset all three simulation databases and re-seed them from scratch.

Usage:
    python reset_and_seed.py

Make sure all three servers are STOPPED before running this.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

DBS = [
    ROOT / "manufacturer" / "manufacturer.db",
    ROOT / "provider" / "provider.db",
    ROOT / "retailer" / "retailer.db",
]


def delete_dbs() -> None:
    for db in DBS:
        if db.exists():
            db.unlink()
            print(f"  deleted {db.name}")
        else:
            print(f"  (not found) {db.name}")


def seed_manufacturer() -> None:
    print("\n=== Seeding manufacturer ===")
    from manufacturer.seed import seed
    seed()


def seed_provider() -> None:
    print("\n=== Seeding provider ===")
    from provider.seed import seed
    seed()


def seed_retailer() -> None:
    print("\n=== Seeding retailer ===")
    from retailer.database import SessionLocal, Base, engine
    from retailer.seed import seed_if_empty
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        seed_if_empty(db)
        print("  Retailer seeded OK")
    finally:
        db.close()


if __name__ == "__main__":
    print("Deleting databases...")
    delete_dbs()
    seed_manufacturer()
    seed_provider()
    seed_retailer()
    print("\nDone. You can now start the three servers and run the turn engine.")
