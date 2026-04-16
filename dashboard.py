"""Streamlit dashboard for the 3D Printer Production Simulator."""

from __future__ import annotations

import json
from datetime import datetime, UTC
from decimal import Decimal

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
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
from models import EventType, ManufacturingOrderStatus, PurchaseOrderStatus
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
        "exported_at": datetime.now(UTC).isoformat(),
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

    _confirm_next_day = st.checkbox("Confirm advance to next day")
    _next_day_btn = st.button(
        "Next Day ▶",
        width='stretch',
        disabled=not _confirm_next_day,
        help="Check the box above to enable this button and prevent accidental advances.",
    )
    if _next_day_btn and _confirm_next_day:
        advance_day(db)
        db.expire_all()
        st.rerun()

    st.divider()
    st.caption("Check the box to confirm before advancing. This cannot be undone.")

    # --- Snapshot reminder ---
    st.info(
        "💾 **Save point tip:** Use **Export JSON** in the System tab to create a "
        "snapshot you can restore later.",
        icon=None,
    )

    # --- Factory Log (last 3 events) ---
    st.divider()
    st.markdown("**Factory Log**")
    recent = (
        db.query(EventRow)
        .order_by(EventRow.day.desc(), EventRow.id.desc())
        .limit(3)
        .all()
    )
    if recent:
        for e in recent:
            st.caption(f"Day {e.day} · {e.event_type}")
            st.caption(f"↳ {e.description[:72]}{'…' if len(e.description) > 72 else ''}")
    else:
        st.caption("No events yet.")

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------

st.title("3D Printer Production Simulator")
db.expire_all()
current_day = _current_day()
st.subheader(f"Day {current_day}")

# ---------------------------------------------------------------------------
# Shared data used across multiple tabs
# ---------------------------------------------------------------------------

all_parts = db.query(ProductRow).order_by(ProductRow.name).all()
parts_by_id: dict[str, ProductRow] = {p.id: p for p in all_parts}

_bom_entries = db.query(BOMEntryRow).all()

# Active (non-delivered, non-cancelled) POs
_active_po_statuses = [PurchaseOrderStatus.pending.value, PurchaseOrderStatus.shipped.value]
_active_pos = (
    db.query(PurchaseOrderRow)
    .filter(PurchaseOrderRow.status.in_(_active_po_statuses))
    .all()
)

# Units committed to pending manufacturing orders (per part)
_pending_mos = (
    db.query(ManufacturingOrderRow)
    .filter(ManufacturingOrderRow.status == ManufacturingOrderStatus.pending.value)
    .all()
)
committed_by_part: dict[str, int] = {}
for _mo in _pending_mos:
    for _entry in _bom_entries:
        committed_by_part[_entry.part_id] = (
            committed_by_part.get(_entry.part_id, 0)
            + _entry.quantity_per_unit * _mo.quantity
        )

# ---------------------------------------------------------------------------
# Top-level metrics (always visible)
# ---------------------------------------------------------------------------

completed_printers = (
    db.query(ManufacturingOrderRow)
    .filter(ManufacturingOrderRow.status == ManufacturingOrderStatus.completed.value)
    .count()
)
pending_orders_count = len(_pending_mos)

# On Hand = physical stock; Committed = reserved for pending MOs
total_on_hand = sum(p.current_stock for p in all_parts)
total_committed = sum(committed_by_part.values())
total_available = total_on_hand - total_committed

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Completed Printers", completed_printers)
col2.metric("Pending Orders", pending_orders_count)
col3.metric(
    "Stock — On Hand",
    total_on_hand,
    help="Physical units currently in the warehouse across all parts.",
)
col4.metric(
    "Net Inventory Position",
    total_available,
    delta=f"-{total_committed} committed" if total_committed else None,
    delta_color="off",
    help=(
        "Physical stock minus total parts committed to all pending orders. "
        "A negative value indicates the total shortage across your entire backlog."
    ),
)

st.divider()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

overview_tab, procurement_tab, orders_tab, analytics_tab, system_tab = st.tabs(
    ["Overview", "Procurement Tracker", "Orders", "Analytics", "System"]
)

# ── Overview tab ────────────────────────────────────────────────────────────

with overview_tab:

    # --- Inventory Status ---
    st.subheader("Inventory Status")

    # Units in transit per part
    _pending_arrival: dict[str, int] = {}
    for _po in _active_pos:
        _pending_arrival[_po.part_id] = _pending_arrival.get(_po.part_id, 0) + _po.quantity

    if all_parts:
        _table_header = (
            "<table style='width:100%;border-collapse:collapse;font-size:0.9rem'>"
            "<thead><tr style='border-bottom:2px solid #ccc'>"
            "<th style='text-align:left;padding:6px 10px'>Part Name</th>"
            "<th style='text-align:right;padding:6px 10px'"
            " title='Physical units currently in the warehouse'>On Hand</th>"
            "<th style='text-align:right;padding:6px 10px'"
            " title='Units reserved for pending manufacturing orders'>Committed</th>"
            "<th style='text-align:right;padding:6px 10px'"
            " title='Units on active purchase orders not yet delivered'>In Transit</th>"
            "<th style='text-align:right;padding:6px 10px'"
            " title='Committed − On Hand − In Transit. Order this quantity to cover your backlog.'>"
            "Deficit (Need to Order)</th>"
            "</tr></thead><tbody>"
        )
        _table_rows = ""
        for p in all_parts:
            _on_hand = p.current_stock
            _committed = committed_by_part.get(p.id, 0)
            _in_transit = _pending_arrival.get(p.id, 0)
            _deficit = _committed - _on_hand - _in_transit
            if _deficit > 0:
                _deficit_cell = (
                    f'<td style="text-align:right;padding:6px 10px;'
                    f'color:#c0392b;font-weight:bold">{_deficit}</td>'
                )
            else:
                _deficit_cell = '<td style="text-align:right;padding:6px 10px">✅ OK</td>'
            _table_rows += (
                f"<tr style='border-bottom:1px solid #eee'>"
                f"<td style='padding:6px 10px'>{p.name}</td>"
                f"<td style='text-align:right;padding:6px 10px'>{_on_hand}</td>"
                f"<td style='text-align:right;padding:6px 10px'>{_committed}</td>"
                f"<td style='text-align:right;padding:6px 10px'>{_in_transit}</td>"
                f"{_deficit_cell}"
                f"</tr>"
            )
        st.markdown(
            _table_header + _table_rows + "</tbody></table>",
            unsafe_allow_html=True,
        )
    else:
        st.info("No parts found. Run `python seed.py` to load initial data.")

    st.divider()

    # --- Procurement ---
    st.subheader("Procurement")
    st.caption("Place a purchase order to restock a part from a supplier.")

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

# ── Procurement Tracker tab ──────────────────────────────────────────────────

with procurement_tab:
    st.subheader("Procurement Tracker")
    st.caption("Live view of all purchase orders that have not yet been delivered.")

    # Build supplier lookup
    _suppliers_by_id: dict[str, SupplierRow] = {
        s.id: s for s in db.query(SupplierRow).all()
    }

    if not _active_pos:
        st.info("No active purchase orders. Place a PO from the Overview tab.")
    else:
        # ── Summary: total units in transit per part ─────────────────────────
        st.markdown("#### Parts in Transit")

        # Group: part_id → list of (qty, lead_time_remaining)
        _transit_by_part: dict[str, list[tuple[int, int | None]]] = {}
        for _po in _active_pos:
            _transit_by_part.setdefault(_po.part_id, []).append(
                (_po.quantity, _po.lead_time_remaining)
            )

        _summary_cols = st.columns(min(len(_transit_by_part), 4))
        for _col, (_pid, _entries) in zip(_summary_cols, _transit_by_part.items()):
            _total_qty = sum(q for q, _ in _entries)
            _remaining_days = [d for _, d in _entries if d is not None]
            _earliest = min(_remaining_days) if _remaining_days else None
            _part_name = parts_by_id[_pid].name if _pid in parts_by_id else _pid[:8]
            _arrival_str = f"earliest in {_earliest}d" if _earliest is not None else "arrival unknown"
            _col.metric(
                label=_part_name,
                value=f"{_total_qty} units",
                delta=_arrival_str,
                delta_color="off",
            )

        st.divider()

        # ── Shipment Timeline table ──────────────────────────────────────────
        st.markdown("#### Shipment Timeline")

        _timeline_rows = []
        for _po in sorted(_active_pos, key=lambda p: p.lead_time_remaining or 9999):
            _part = parts_by_id.get(_po.part_id)
            _supplier = _suppliers_by_id.get(_po.supplier_id)
            _days_left = _po.lead_time_remaining

            # Arrival urgency label
            if _days_left is None:
                _urgency = "—"
            elif _days_left <= 1:
                _urgency = "🔴 Tomorrow"
            elif _days_left <= 4:
                _urgency = f"🟠 {_days_left}d"
            else:
                _urgency = f"🟢 {_days_left}d"

            _timeline_rows.append({
                "PO ID": _po.id[:8] + "…",
                "Part": _part.name if _part else "Unknown",
                "Supplier": _supplier.name if _supplier else "Unknown",
                "Status": _po.status.capitalize(),
                "Qty": _po.quantity,
                "Days to Arrival": _days_left if _days_left is not None else "—",
                "Urgency": _urgency,
            })

        _df_timeline = pd.DataFrame(_timeline_rows)
        st.dataframe(
            _df_timeline,
            width='stretch',
            hide_index=True,
            column_config={
                "Qty": st.column_config.NumberColumn(width="small"),
                "Days to Arrival": st.column_config.NumberColumn(
                    help="Simulation days until this shipment lands in the warehouse (R4)",
                    width="medium",
                ),
                "Urgency": st.column_config.TextColumn(
                    help="🔴 = arrives tomorrow, 🟠 = 2–4 days, 🟢 = 5+ days",
                    width="medium",
                ),
            },
        )

# ── Orders tab ──────────────────────────────────────────────────────────────

with orders_tab:
    st.subheader("Manufacturing Orders")

    # ── In-Progress ──────────────────────────────────────────────────────────
    in_progress_mos = (
        db.query(ManufacturingOrderRow)
        .filter(ManufacturingOrderRow.status == ManufacturingOrderStatus.in_progress.value)
        .order_by(ManufacturingOrderRow.started_at)
        .all()
    )
    if in_progress_mos:
        st.markdown("**In Progress**")
        total_in_progress = sum(mo.quantity for mo in in_progress_mos)
        st.info(f"{len(in_progress_mos)} order(s) · {total_in_progress} printer(s) currently being built.")

    # ── Pending — summary grouped by quantity ────────────────────────────────
    st.markdown("**Pending Orders**")
    st.caption(
        "Click **Release for Production** inside each order to build it immediately "
        "if sufficient parts are available."
    )

    if not _pending_mos:
        st.info("No pending manufacturing orders.")
    else:
        # Group pending MOs by quantity (proxy for 'model' since the simulator
        # has a single product type; expander shows BOM detail per order).
        _qty_groups: dict[int, list[ManufacturingOrderRow]] = {}
        for _mo in _pending_mos:
            _qty_groups.setdefault(_mo.quantity, []).append(_mo)

        for _qty, _group in sorted(_qty_groups.items()):
            _group_total = sum(m.quantity for m in _group)
            with st.expander(
                f"{len(_group)}x order(s) for **{_qty} printer(s)** each "
                f"— {_group_total} printers total",
                expanded=False,
            ):
                for _mo in _group:
                    st.markdown(
                        f"**Order** `{_mo.id[:8]}…`  |  "
                        f"Created: {str(_mo.created_at)[:10] if _mo.created_at else '—'}"
                    )

                    # BOM breakdown
                    _bom_rows = []
                    _all_satisfied = True
                    for _entry in _bom_entries:
                        _part = parts_by_id.get(_entry.part_id)
                        if _part is None:
                            continue
                        _needed = _entry.quantity_per_unit * _mo.quantity
                        _on_hand = _part.current_stock
                        _ok = _on_hand >= _needed
                        if not _ok:
                            _all_satisfied = False
                        _bom_rows.append({
                            "": "✅" if _ok else "❌",
                            "Part": _part.name,
                            "Required": _needed,
                            "On Hand": _on_hand,
                            "Shortage": max(0, _needed - _on_hand),
                        })

                    if _bom_rows:
                        st.dataframe(
                            pd.DataFrame(_bom_rows),
                            width='stretch',
                            hide_index=True,
                            column_config={
                                "": st.column_config.TextColumn(width="small"),
                                "Shortage": st.column_config.NumberColumn(
                                    help="Units still needed to fulfil this order"
                                ),
                            },
                        )
                        if not _all_satisfied:
                            st.warning(
                                "Insufficient stock — place purchase orders or wait for arrivals.",
                                icon=None,
                            )

                    if st.button(
                        "Release for Production",
                        key=f"release_{_mo.id}",
                        type="primary",
                        disabled=not _all_satisfied,
                    ):
                        ok, err = release_manufacturing_order(db, _mo.id)
                        db.expire_all()
                        if ok:
                            st.success(f"Order {_mo.id[:8]}… completed successfully.")
                        else:
                            st.error(f"Could not release order: {err}")
                        st.rerun()

                    st.divider()

# ── Analytics tab ────────────────────────────────────────────────────────────

with analytics_tab:

    # --- Chart 1: Current Inventory — On Hand vs Committed ──────────────────
    st.subheader("Inventory: On Hand vs Committed")

    if not all_parts:
        st.info("No parts found. Run `python seed.py` to load initial data.")
    else:
        _chart_data = pd.DataFrame([
            {
                "Part": p.name,
                "On Hand": p.current_stock,
                "Committed": committed_by_part.get(p.id, 0),
                "Available": p.current_stock - committed_by_part.get(p.id, 0),
            }
            for p in all_parts
        ])

        _fig_inv = go.Figure()
        _fig_inv.add_bar(
            name="Available",
            x=_chart_data["Part"],
            y=_chart_data["Available"],
            marker_color="#27AE60",
            hovertemplate="<b>%{x}</b><br>Available: %{y}<extra></extra>",
        )
        _fig_inv.add_bar(
            name="Committed",
            x=_chart_data["Part"],
            y=_chart_data["Committed"],
            marker_color="#E67E22",
            hovertemplate="<b>%{x}</b><br>Committed: %{y}<extra></extra>",
        )
        _fig_inv.update_layout(
            barmode="stack",
            xaxis_title="Part",
            yaxis_title="Units",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(t=40, b=40),
            hovermode="x unified",
        )
        st.plotly_chart(_fig_inv, width='stretch')

    st.divider()

    # --- Chart 2: Supplier Lead Time comparison ──────────────────────────────
    st.subheader("Average Lead Time by Supplier")
    st.caption("Use this to compare suppliers and choose faster restocking options.")

    _catalog_rows = (
        db.query(SupplierCatalogRow, SupplierRow, ProductRow)
        .join(SupplierRow, SupplierCatalogRow.supplier_id == SupplierRow.id)
        .join(ProductRow, SupplierCatalogRow.part_id == ProductRow.id)
        .all()
    )

    if not _catalog_rows:
        st.info("No supplier catalog found. Run `python seed.py` first.")
    else:
        _lt_rows = [
            {
                "Supplier": s.name,
                "Part": p.name,
                "Lead Time (days)": c.lead_time_days,
                "Unit Price ($)": float(c.unit_price),
            }
            for c, s, p in _catalog_rows
        ]
        _df_lt = pd.DataFrame(_lt_rows)

        # Average lead time per supplier across all parts they carry
        _avg_lt = (
            _df_lt.groupby("Supplier")["Lead Time (days)"]
            .mean()
            .reset_index()
            .sort_values("Lead Time (days)")
        )

        _fig_lt = px.bar(
            _avg_lt,
            x="Supplier",
            y="Lead Time (days)",
            color="Lead Time (days)",
            color_continuous_scale=["#27AE60", "#F39C12", "#E74C3C"],
            text="Lead Time (days)",
            title="Average Lead Time per Supplier (lower is faster)",
        )
        _fig_lt.update_traces(
            texttemplate="%{text:.1f}d",
            textposition="outside",
            hovertemplate="<b>%{x}</b><br>Avg lead time: %{y:.1f} days<extra></extra>",
        )
        _fig_lt.update_layout(
            showlegend=False,
            coloraxis_showscale=False,
            yaxis_title="Avg Lead Time (days)",
            margin=dict(t=60, b=40),
        )
        st.plotly_chart(_fig_lt, width='stretch')

        # Per-part detail table as supporting data
        with st.expander("Per-part supplier detail"):
            st.dataframe(
                _df_lt.sort_values(["Part", "Lead Time (days)"]),
                width='stretch',
                hide_index=True,
            )

    st.divider()

    # --- Chart 3: Cumulative Completed Printers Over Time ────────────────────
    st.subheader("Completed Printers Over Time")

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
        day_totals: dict[int, int] = {}
        for e in completion_events:
            qty = (e.event_metadata or {}).get("quantity_built", 1)
            day_totals[e.day] = day_totals.get(e.day, 0) + qty

        days_sorted = sorted(day_totals.keys())
        all_days = list(range(min(days_sorted), max(days_sorted) + 1))
        daily_built = [day_totals.get(d, 0) for d in all_days]

        cumulative: list[int] = []
        running = 0
        for v in daily_built:
            running += v
            cumulative.append(running)

        _df_prod = pd.DataFrame({"Day": all_days, "Daily": daily_built, "Cumulative": cumulative})

        _fig_prod = go.Figure()
        _fig_prod.add_scatter(
            x=_df_prod["Day"],
            y=_df_prod["Cumulative"],
            mode="lines+markers",
            name="Cumulative",
            line=dict(color="#4C9BE8", width=2),
            marker=dict(size=5),
            fill="tozeroy",
            fillcolor="rgba(76, 155, 232, 0.12)",
            hovertemplate="Day %{x}<br>Total built: %{y}<extra></extra>",
        )
        _fig_prod.add_bar(
            x=_df_prod["Day"],
            y=_df_prod["Daily"],
            name="Built this day",
            marker_color="rgba(39, 174, 96, 0.6)",
            hovertemplate="Day %{x}<br>Built: %{y}<extra></extra>",
        )
        _fig_prod.update_layout(
            xaxis_title="Simulation Day",
            yaxis_title="Printers",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            hovermode="x unified",
            margin=dict(t=40, b=40),
        )
        st.plotly_chart(_fig_prod, width='stretch')

# ── System tab ───────────────────────────────────────────────────────────────

with system_tab:

    # --- Export ---
    st.subheader("Export")
    st.caption(
        "Download a complete snapshot of the current database state as JSON. "
        "The file includes a version tag and timestamp for traceability."
    )

    snapshot_json = _build_snapshot()
    export_filename = f"factory_snapshot_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
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
