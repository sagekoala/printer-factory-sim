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
    SupplierCatalogRow,
    SupplierRow,
    init_db,
)
from models import ManufacturingOrderStatus
from simulation import advance_day, create_purchase_order, release_manufacturing_order

# ---------------------------------------------------------------------------
# One-time setup
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="3D Printer Production Simulator",
    layout="wide",
)

init_db()  # No-op if tables already exist


# ---------------------------------------------------------------------------
# Shared DB session (one per server process, reused across reruns)
# ---------------------------------------------------------------------------

@st.cache_resource
def _get_session():
    """Return a long-lived SQLAlchemy session reused across Streamlit reruns."""
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
    db.expire_all()
    st.metric("Current Day", _current_day())

    if st.button("Next Day ▶", use_container_width=True):
        advance_day(db)
        db.expire_all()
        st.rerun()

    st.divider()
    st.caption("Press 'Next Day' to advance the simulation by one day.")

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------

st.title("3D Printer Production Simulator")
db.expire_all()
st.subheader(f"Day {_current_day()}")

# ---------------------------------------------------------------------------
# Top-level metrics (always visible)
# ---------------------------------------------------------------------------

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
all_parts = db.query(ProductRow).all()
current_stock_level = sum(p.current_stock for p in all_parts)

col1, col2, col3 = st.columns(3)
col1.metric("Total Completed Printers", completed_printers)
col2.metric("Pending Orders", pending_orders)
col3.metric("Current Stock Level", current_stock_level, help="Total units across all parts")

st.divider()

# ---------------------------------------------------------------------------
# Tabs — Overview | Orders
# ---------------------------------------------------------------------------

overview_tab, orders_tab = st.tabs(["Overview", "Orders"])

# ── Overview tab ────────────────────────────────────────────────────────────

with overview_tab:

    # --- Inventory Status ---
    st.subheader("Inventory Status")
    parts = db.query(ProductRow).order_by(ProductRow.name).all()
    if parts:
        st.table(pd.DataFrame(
            [{"Part Name": p.name, "Current Stock": p.current_stock} for p in parts]
        ))
    else:
        st.info("No parts found. Run `python seed.py` to load initial data.")

    st.divider()

    # --- Procurement ---
    st.subheader("Procurement")
    st.caption("Place a purchase order to restock a part from a supplier.")

    # Parts that appear in at least one catalog entry
    parts_with_suppliers = (
        db.query(ProductRow)
        .join(SupplierCatalogRow, SupplierCatalogRow.part_id == ProductRow.id)
        .distinct()
        .order_by(ProductRow.name)
        .all()
    )

    if not parts_with_suppliers:
        st.warning("No supplier catalog found. Run `python seed.py` first.")
    else:
        selected_part = st.selectbox(
            "Part",
            options=parts_with_suppliers,
            format_func=lambda p: p.name,
        )

        # Catalog entries for the chosen part (with supplier name)
        catalog_entries = (
            db.query(SupplierCatalogRow, SupplierRow)
            .join(SupplierRow, SupplierCatalogRow.supplier_id == SupplierRow.id)
            .filter(SupplierCatalogRow.part_id == selected_part.id)
            .all()
        )

        catalog_options = {
            f"{supplier.name}  —  ${catalog.unit_price}/unit  |  "
            f"min {catalog.min_order_qty}  |  lead {catalog.lead_time_days}d": catalog
            for catalog, supplier in catalog_entries
        }

        selected_label = st.selectbox("Supplier", options=list(catalog_options.keys()))
        selected_catalog = catalog_options[selected_label]

        quantity = st.number_input(
            "Quantity",
            min_value=selected_catalog.min_order_qty,
            value=selected_catalog.min_order_qty,
            step=1,
        )

        total_cost = float(selected_catalog.unit_price) * quantity
        st.caption(
            f"Estimated cost: **${total_cost:,.2f}**  |  "
            f"Arrives in **{selected_catalog.lead_time_days} day(s)**"
        )

        if st.button("Place Purchase Order", type="primary"):
            po, err = create_purchase_order(
                db,
                part_id=selected_part.id,
                supplier_id=selected_catalog.supplier_id,
                quantity=int(quantity),
            )
            if err:
                st.error(err)
            else:
                db.expire_all()
                st.success(
                    f"Purchase order created for {quantity}x **{selected_part.name}**. "
                    f"Delivery in {selected_catalog.lead_time_days} day(s)."
                )
                st.rerun()

    st.divider()

    # --- Recent Events ---
    st.subheader("Recent Events")
    recent_events = (
        db.query(EventRow)
        .order_by(EventRow.day.desc(), EventRow.id.desc())
        .limit(15)
        .all()
    )
    if recent_events:
        st.table(pd.DataFrame([
            {
                "Day": e.day,
                "Type": e.event_type,
                "Entity": e.entity_type,
                "Description": e.description,
            }
            for e in recent_events
        ]))
    else:
        st.info("No events yet. Click 'Next Day' to start the simulation.")

# ── Orders tab ──────────────────────────────────────────────────────────────

with orders_tab:
    st.subheader("Pending Manufacturing Orders")
    st.caption(
        "Click **Release for Production** to immediately build the order if "
        "sufficient parts are in stock."
    )

    pending_mos = (
        db.query(ManufacturingOrderRow)
        .filter(ManufacturingOrderRow.status == ManufacturingOrderStatus.pending.value)
        .order_by(ManufacturingOrderRow.created_at)
        .all()
    )

    if not pending_mos:
        st.info("No pending manufacturing orders.")
    else:
        # Header row
        hcols = st.columns([2, 1, 3, 2])
        hcols[0].markdown("**Order ID**")
        hcols[1].markdown("**Qty**")
        hcols[2].markdown("**Created**")
        hcols[3].markdown("**Action**")
        st.divider()

        for mo in pending_mos:
            row = st.columns([2, 1, 3, 2])
            row[0].text(mo.id[:8] + "…")
            row[1].text(mo.quantity)
            row[2].text(str(mo.created_at)[:19] if mo.created_at else "—")

            if row[3].button("Release for Production", key=f"release_{mo.id}"):
                ok, err = release_manufacturing_order(db, mo.id)
                db.expire_all()
                if ok:
                    st.success(f"Order {mo.id[:8]}… completed successfully.")
                else:
                    st.error(f"Could not release order: {err}")
                st.rerun()
