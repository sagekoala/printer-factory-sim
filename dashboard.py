"""Streamlit dashboard for the 3D Printer Production Simulator."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

from database import (
    BOMEntryRow,
    EventRow,
    FactoryConfigRow,
    ManufacturingOrderRow,
    ProductRow,
    PurchaseOrderRow,
    SessionLocal,
    SupplierCatalogRow,
    SupplierRow,
    init_db,
)
from models import EventType, ManufacturingOrderStatus
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
# Import / Export helpers
# ---------------------------------------------------------------------------

_SNAPSHOT_VERSION = "1.0"

_REQUIRED_TABLES = {
    "factory_config", "suppliers", "products", "supplier_catalog",
    "bom_entries", "manufacturing_orders", "purchase_orders", "events",
}


def _build_snapshot() -> str:
    """Serialise the entire database to a JSON string.

    The returned document includes a ``version`` tag and ``exported_at``
    timestamp so snapshots can be identified and validated on import.
    """
    def _dt(d: datetime | None) -> str | None:
        return d.isoformat() if d else None

    snapshot = {
        "version": _SNAPSHOT_VERSION,
        "exported_at": datetime.utcnow().isoformat(),
        "data": {
            "factory_config": [
                {"key": r.key, "value": r.value}
                for r in db.query(FactoryConfigRow).all()
            ],
            "suppliers": [
                {"id": r.id, "name": r.name, "contact_email": r.contact_email}
                for r in db.query(SupplierRow).all()
            ],
            "products": [
                {
                    "id": r.id, "name": r.name,
                    "current_stock": r.current_stock, "storage_size": r.storage_size,
                }
                for r in db.query(ProductRow).all()
            ],
            "supplier_catalog": [
                {
                    "id": r.id, "supplier_id": r.supplier_id, "part_id": r.part_id,
                    "unit_price": str(r.unit_price),
                    "min_order_qty": r.min_order_qty, "lead_time_days": r.lead_time_days,
                }
                for r in db.query(SupplierCatalogRow).all()
            ],
            "bom_entries": [
                {"id": r.id, "part_id": r.part_id, "quantity_per_unit": r.quantity_per_unit}
                for r in db.query(BOMEntryRow).all()
            ],
            "manufacturing_orders": [
                {
                    "id": r.id, "quantity": r.quantity, "status": r.status,
                    "created_at": _dt(r.created_at), "started_at": _dt(r.started_at),
                    "completed_at": _dt(r.completed_at), "days_elapsed": r.days_elapsed,
                }
                for r in db.query(ManufacturingOrderRow).all()
            ],
            "purchase_orders": [
                {
                    "id": r.id, "part_id": r.part_id, "supplier_id": r.supplier_id,
                    "quantity": r.quantity, "unit_price": str(r.unit_price),
                    "status": r.status,
                    "created_at": _dt(r.created_at), "ship_date": _dt(r.ship_date),
                    "delivered_at": _dt(r.delivered_at),
                    "lead_time_remaining": r.lead_time_remaining,
                }
                for r in db.query(PurchaseOrderRow).all()
            ],
            "events": [
                {
                    "id": r.id, "day": r.day, "event_type": r.event_type,
                    "entity_type": r.entity_type, "entity_id": r.entity_id,
                    "description": r.description, "event_metadata": r.event_metadata,
                }
                for r in db.query(EventRow).all()
            ],
        },
    }
    return json.dumps(snapshot, indent=2)


def _restore_snapshot(snapshot: dict) -> None:
    """Clear all tables and repopulate them from *snapshot*.

    Raises:
        ValueError: If the snapshot is structurally invalid (missing keys).
        Exception: Any SQLAlchemy error; the caller must rollback on failure.
    """
    data = snapshot.get("data", {})
    missing = _REQUIRED_TABLES - set(data.keys())
    if missing:
        raise ValueError(f"Snapshot is missing required table(s): {sorted(missing)}")

    def _dt(s: str | None) -> datetime | None:
        return datetime.fromisoformat(s) if s else None

    # Delete in reverse FK-dependency order so SQLite is happy even with FK
    # enforcement enabled.
    db.query(EventRow).delete()
    db.query(PurchaseOrderRow).delete()
    db.query(ManufacturingOrderRow).delete()
    db.query(SupplierCatalogRow).delete()
    db.query(BOMEntryRow).delete()
    db.query(ProductRow).delete()
    db.query(SupplierRow).delete()
    db.query(FactoryConfigRow).delete()
    db.flush()  # Send deletes before inserts

    for r in data["factory_config"]:
        db.add(FactoryConfigRow(key=r["key"], value=r["value"]))

    for r in data["suppliers"]:
        db.add(SupplierRow(id=r["id"], name=r["name"], contact_email=r.get("contact_email")))

    for r in data["products"]:
        db.add(ProductRow(
            id=r["id"], name=r["name"],
            current_stock=r["current_stock"], storage_size=r["storage_size"],
        ))

    for r in data["supplier_catalog"]:
        db.add(SupplierCatalogRow(
            id=r["id"], supplier_id=r["supplier_id"], part_id=r["part_id"],
            unit_price=Decimal(r["unit_price"]),
            min_order_qty=r["min_order_qty"], lead_time_days=r["lead_time_days"],
        ))

    for r in data["bom_entries"]:
        db.add(BOMEntryRow(id=r["id"], part_id=r["part_id"],
                           quantity_per_unit=r["quantity_per_unit"]))

    for r in data["manufacturing_orders"]:
        db.add(ManufacturingOrderRow(
            id=r["id"], quantity=r["quantity"], status=r["status"],
            created_at=_dt(r.get("created_at")), started_at=_dt(r.get("started_at")),
            completed_at=_dt(r.get("completed_at")), days_elapsed=r.get("days_elapsed"),
        ))

    for r in data["purchase_orders"]:
        db.add(PurchaseOrderRow(
            id=r["id"], part_id=r["part_id"], supplier_id=r["supplier_id"],
            quantity=r["quantity"], unit_price=Decimal(r["unit_price"]),
            status=r["status"],
            created_at=_dt(r.get("created_at")), ship_date=_dt(r.get("ship_date")),
            delivered_at=_dt(r.get("delivered_at")),
            lead_time_remaining=r.get("lead_time_remaining"),
        ))

    for r in data["events"]:
        db.add(EventRow(
            id=r["id"], day=r["day"], event_type=r["event_type"],
            entity_type=r["entity_type"], entity_id=r["entity_id"],
            description=r["description"], event_metadata=r.get("event_metadata"),
        ))

    db.commit()


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

overview_tab, orders_tab, analytics_tab, system_tab = st.tabs(["Overview", "Orders", "Analytics", "System"])

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

# ── Analytics tab ────────────────────────────────────────────────────────────

with analytics_tab:

    # --- Bar chart: current inventory levels ---
    st.subheader("Current Inventory Levels")

    chart_parts = db.query(ProductRow).order_by(ProductRow.name).all()

    if not chart_parts:
        st.info("No parts found. Run `python seed.py` to load initial data.")
    else:
        part_names = [p.name for p in chart_parts]
        stock_levels = [p.current_stock for p in chart_parts]

        fig_bar, ax_bar = plt.subplots(figsize=(8, 4))
        bars = ax_bar.bar(part_names, stock_levels, color="#4C9BE8", edgecolor="white")
        ax_bar.set_xlabel("Part", labelpad=8)
        ax_bar.set_ylabel("Units in Stock")
        ax_bar.set_title("Inventory by Part")
        ax_bar.yaxis.grid(True, alpha=0.35, linestyle="--")
        ax_bar.set_axisbelow(True)

        # Label each bar with its exact value
        for bar, val in zip(bars, stock_levels):
            ax_bar.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max(stock_levels, default=1) * 0.02,
                str(val),
                ha="center",
                va="bottom",
                fontsize=9,
            )

        fig_bar.tight_layout()
        st.pyplot(fig_bar)
        plt.close(fig_bar)

    st.divider()

    # --- Line chart: cumulative completed printers per day ---
    st.subheader("Completed Printers Over Time")

    # Reconstruct history from PRODUCTION_COMPLETED events on individual MOs.
    # Each such event carries {"quantity_built": N} in event_metadata.
    completion_events = (
        db.query(EventRow)
        .filter(
            EventRow.event_type == EventType.PRODUCTION_COMPLETED.value,
            EventRow.entity_type == "manufacturing_order",
        )
        .order_by(EventRow.day)
        .all()
    )

    if not completion_events:
        st.info("No completed printers yet. Advance a few days to generate data.")
    else:
        # Aggregate units built per simulation day
        day_totals: dict[int, int] = {}
        for e in completion_events:
            qty = (e.event_metadata or {}).get("quantity_built", 1)
            day_totals[e.day] = day_totals.get(e.day, 0) + qty

        days_sorted = sorted(day_totals.keys())

        # Fill any gaps so the x-axis is continuous (days with no completions → 0)
        all_days = list(range(min(days_sorted), max(days_sorted) + 1))
        daily_built = [day_totals.get(d, 0) for d in all_days]

        # Cumulative sum
        cumulative: list[int] = []
        running = 0
        for v in daily_built:
            running += v
            cumulative.append(running)

        fig_line, ax_line = plt.subplots(figsize=(8, 4))
        ax_line.plot(all_days, cumulative, marker="o", markersize=4,
                     color="#27AE60", linewidth=2, label="Cumulative")
        ax_line.fill_between(all_days, cumulative, alpha=0.12, color="#27AE60")
        ax_line.set_xlabel("Simulation Day", labelpad=8)
        ax_line.set_ylabel("Total Completed Printers")
        ax_line.set_title("Cumulative Printers Completed")
        ax_line.yaxis.grid(True, alpha=0.35, linestyle="--")
        ax_line.set_axisbelow(True)
        ax_line.legend()

        fig_line.tight_layout()
        st.pyplot(fig_line)
        plt.close(fig_line)

# ── System tab ───────────────────────────────────────────────────────────────

with system_tab:

    # --- Export ---
    st.subheader("Export")
    st.caption(
        "Download a complete snapshot of the current database state as JSON. "
        "The file includes a version tag and timestamp for traceability."
    )

    snapshot_json = _build_snapshot()
    export_filename = f"factory_snapshot_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    st.download_button(
        label="Download Snapshot",
        data=snapshot_json,
        file_name=export_filename,
        mime="application/json",
        type="primary",
    )

    st.divider()

    # --- Import ---
    st.subheader("Import")
    st.caption(
        "Upload a previously exported snapshot to restore the full database state. "
        "**This will erase all current data.**"
    )

    uploaded_file = st.file_uploader("Choose a snapshot file", type=["json"])

    if uploaded_file is not None:
        try:
            raw_bytes = uploaded_file.read()
            snapshot = json.loads(raw_bytes)
        except json.JSONDecodeError as exc:
            st.error(f"Could not parse the file — malformed JSON: {exc}")
            snapshot = None

        if snapshot is not None:
            # Show snapshot metadata before the user commits to restoring
            version = snapshot.get("version", "unknown")
            exported_at = snapshot.get("exported_at", "unknown")

            if "version" not in snapshot:
                st.error("Invalid snapshot: missing required 'version' field.")
                snapshot = None
            elif "data" not in snapshot:
                st.error("Invalid snapshot: missing required 'data' field.")
                snapshot = None
            else:
                st.info(
                    f"Snapshot version **{version}** — exported at `{exported_at}`"
                )

                # Summarise row counts from the file so the user knows what they are
                # restoring before clicking the confirm button.
                data = snapshot["data"]
                count_cols = st.columns(4)
                count_cols[0].metric("Products", len(data.get("products", [])))
                count_cols[1].metric("MO Records", len(data.get("manufacturing_orders", [])))
                count_cols[2].metric("PO Records", len(data.get("purchase_orders", [])))
                count_cols[3].metric("Events", len(data.get("events", [])))

                st.warning(
                    "Proceeding will **permanently delete** all current data and "
                    "replace it with the snapshot above."
                )

                if st.button("Restore Snapshot", type="primary"):
                    try:
                        _restore_snapshot(snapshot)
                        db.expire_all()
                        st.success("Database restored successfully from snapshot.")
                        st.rerun()
                    except ValueError as exc:
                        st.error(f"Snapshot validation failed: {exc}")
                    except Exception as exc:
                        db.rollback()
                        st.error(f"Restore failed — database unchanged: {exc}")
