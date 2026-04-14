"""Streamlit dashboard for the 3D Printer Production Simulator."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from database import (
    EventRow,
    FactoryConfigRow,
    ManufacturingOrderRow,
    ProductRow,
    SessionLocal,
    init_db,
)
from models import ManufacturingOrderStatus
from simulation import advance_day

# ---------------------------------------------------------------------------
# One-time setup
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="3D Printer Production Simulator",
    layout="wide",
)

init_db()  # No-op if tables already exist


# ---------------------------------------------------------------------------
# DB helper — a fresh session per Streamlit script run, closed at the end
# ---------------------------------------------------------------------------

@st.cache_resource
def _get_session():
    """Return a module-level SQLAlchemy session reused across reruns."""
    return SessionLocal()


db = _get_session()


def _current_day() -> int:
    row = db.query(FactoryConfigRow).filter(FactoryConfigRow.key == "current_day").first()
    return int(row.value) if row else 0


# ---------------------------------------------------------------------------
# Sidebar — simulation controls
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Simulation Controls")

    # Refresh session state before reading so the displayed day is always current
    db.expire_all()
    day = _current_day()
    st.metric("Current Day", day)

    if st.button("Next Day ▶", use_container_width=True):
        advance_day(db)
        db.expire_all()
        st.rerun()

    st.divider()
    st.caption("Press 'Next Day' to advance the simulation by one day.")

# ---------------------------------------------------------------------------
# Page title
# ---------------------------------------------------------------------------

st.title("3D Printer Production Simulator")
st.subheader(f"Day {_current_day()}")

# ---------------------------------------------------------------------------
# Section 1 — Key metrics (three columns)
# ---------------------------------------------------------------------------

db.expire_all()

completed_printers = (
    db.query(ManufacturingOrderRow)
    .filter(ManufacturingOrderRow.status == ManufacturingOrderStatus.completed.value)
    .count()
)

pending_orders = (
    db.query(ManufacturingOrderRow)
    .filter(ManufacturingOrderRow.status == ManufacturingOrderStatus.pending.value)
    .count()
)

total_stock = db.query(ProductRow).all()
current_stock_level = sum(p.current_stock for p in total_stock)

col1, col2, col3 = st.columns(3)
col1.metric("Total Completed Printers", completed_printers)
col2.metric("Pending Orders", pending_orders)
col3.metric("Current Stock Level", current_stock_level, help="Total units across all parts")

st.divider()

# ---------------------------------------------------------------------------
# Section 2 — Inventory status table
# ---------------------------------------------------------------------------

st.subheader("Inventory Status")

parts = db.query(ProductRow).order_by(ProductRow.name).all()

if parts:
    inventory_df = pd.DataFrame(
        [{"Part Name": p.name, "Current Stock": p.current_stock} for p in parts]
    )
    st.table(inventory_df)
else:
    st.info("No parts found. Run `python seed.py` to load initial data.")

st.divider()

# ---------------------------------------------------------------------------
# Section 3 — Recent events log
# ---------------------------------------------------------------------------

st.subheader("Recent Events")

recent_events = (
    db.query(EventRow)
    .order_by(EventRow.day.desc(), EventRow.id.desc())
    .limit(15)
    .all()
)

if recent_events:
    events_df = pd.DataFrame(
        [
            {
                "Day": e.day,
                "Type": e.event_type,
                "Entity": e.entity_type,
                "Description": e.description,
            }
            for e in recent_events
        ]
    )
    st.table(events_df)
else:
    st.info("No events yet. Click 'Next Day' to start the simulation.")
