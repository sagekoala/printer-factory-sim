"""Provider database engine, session factory, and persistence helpers.

The ORM table classes themselves live in :mod:`provider.models` so that
schema and Pydantic contracts evolve together.  This module owns the
SQLAlchemy ``engine``, the session factory, the FastAPI session
dependency, and small persistence utilities that several services share
(``log_event``, ``get_current_day`` / ``set_current_day``).

DB file location
----------------
The default SQLite file is **always** placed inside the ``provider/``
directory regardless of the caller's current working directory.  This
guarantees that ``cd provider && python seed.py`` and
``uvicorn provider.api:app`` from the repo root operate on the same
``provider/provider.db`` file.

Override with the ``PROVIDER_DATABASE_URL`` environment variable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Re-export ORM classes & enum so existing call-sites
# ``from provider.db import ProductRow`` keep working without churn.
from provider.models import (  # noqa: F401  (re-exported)
    Base,
    EventRow,
    OrderRow,
    OrderStatus,
    PricingTierRow,
    ProductRow,
    SimStateRow,
    StockRow,
)


# ---------------------------------------------------------------------------
# Engine + session
# ---------------------------------------------------------------------------

_PROVIDER_DIR = Path(__file__).resolve().parent
_DEFAULT_DB_PATH = _PROVIDER_DIR / "provider.db"

DATABASE_URL: str = os.getenv(
    "PROVIDER_DATABASE_URL",
    f"sqlite:///{_DEFAULT_DB_PATH}",
)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------


def init_db() -> None:
    """Create every provider table if it does not already exist.

    Safe to call repeatedly (no-op when tables exist).
    """
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Yield a database session (intended for FastAPI ``Depends``)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Simulation-clock helpers
# ---------------------------------------------------------------------------


def get_current_day(db: Session) -> int:
    """Return the current simulation day (``0`` if never set)."""
    row = db.query(SimStateRow).filter(SimStateRow.key == "current_day").first()
    return int(row.value) if row else 0


def set_current_day(db: Session, day: int) -> None:
    """Persist the current simulation day in ``sim_state``."""
    row = db.query(SimStateRow).filter(SimStateRow.key == "current_day").first()
    if row is None:
        db.add(SimStateRow(key="current_day", value=str(day)))
    else:
        row.value = str(day)


# ---------------------------------------------------------------------------
# Audit-log helper
# ---------------------------------------------------------------------------


def log_event(
    db: Session,
    sim_day: int,
    event_type: str,
    entity_type: str,
    entity_id: str,
    detail: str,
) -> None:
    """Append a row to the ``events`` audit table.

    The caller is responsible for committing the surrounding transaction;
    ``log_event`` only stages the insert via ``db.add``.
    """
    db.add(
        EventRow(
            sim_day=sim_day,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
        )
    )


# ---------------------------------------------------------------------------
# Backwards-compatible seed shim
# ---------------------------------------------------------------------------


def ensure_seeded(seed_file: Path | None = None) -> None:
    """Idempotently load the seed file into the provider DB.

    Thin shim that delegates to :func:`provider.seed.seed` so that legacy
    callers (``api.py`` lifespan, ``cli.py`` command init) continue to
    work, while the canonical, runnable seeding logic lives in
    ``provider/seed.py``.
    """
    # Local import avoids a circular dependency at module import time.
    from provider.seed import seed as _seed

    _seed(seed_file=seed_file)
