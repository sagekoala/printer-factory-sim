# 3D Printer Production Simulator

A discrete-event simulation of a 3D printer factory. The system models inventory management, supply chain logistics, and order fulfilment over time, creating realistic tension between daily customer demand, warehouse capacity constraints, and supplier lead times. Operators manage purchasing decisions through a live dashboard while a SimPy-inspired day-tick engine drives the simulation forward.

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| REST API | **FastAPI** + **Pydantic** | Async-ready, automatic OpenAPI docs, strict request/response validation |
| Dashboard UI | **Streamlit** | Rapid interactive UI with built-in widget state management |
| Database ORM | **SQLAlchemy** + SQLite | Lightweight persistence, no server required, full ORM query support |
| Data validation | **Pydantic v2** | Type-safe domain models shared between API and simulation layers |
| Charts | **matplotlib** | Bar and line charts rendered server-side via `st.pyplot` |
| Data export | Python **json** stdlib | Human-readable snapshots for import/export |

---

## Project Structure

```
printer-factory-sim/
├── main.py          # FastAPI app — REST API endpoints
├── dashboard.py     # Streamlit dashboard — interactive UI
├── simulation.py    # Simulation engine — advance_day() and user-action helpers
├── database.py      # SQLAlchemy setup — engine, session, ORM table rows
├── models.py        # Pydantic domain models and enums
├── seed.py          # One-time DB seeder — loads seed.json
├── seed.json        # Initial factory data (suppliers, parts, BOM, config)
└── requirements.txt
```

---

## Quick Start

### 1. Install dependencies

```bash
cd printer-factory-sim
pip install -r requirements.txt
```

### 2. Seed the database

Populates suppliers, parts, Bill of Materials, and factory configuration. Safe to re-run — skips rows that already exist.

```bash
python seed.py
```

### 3. Run the REST API

```bash
uvicorn main:app --reload
```

- API base URL: `http://localhost:8000`
- Interactive Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

### 4. Run the Dashboard

Open a **separate terminal** (the API and dashboard are independent processes):

```bash
streamlit run dashboard.py
```

- Dashboard URL: `http://localhost:8501`

---

## API Reference

| Method | Endpoint | Tag | Description |
|---|---|---|---|
| `GET` | `/health` | Health | Liveness check |
| `GET` | `/inventory` | Inventory | All parts with current stock levels |
| `GET` | `/orders/manufacturing` | Orders | Manufacturing orders; filter with `?status=pending` |
| `GET` | `/orders/purchase` | Orders | Purchase orders; filter with `?status=pending` |
| `GET` | `/factory/status` | Factory | Current simulation day + total completed printers |
| `POST` | `/simulation/advance` | Simulation | Advance simulation by one day |

All endpoints return JSON. Request/response schemas are documented in the Swagger UI at `/docs`.

---

## Provider App (Supplier)

The repo also includes an independent supplier service in `provider/`.

### Run Provider API (port 8001)

```bash
uvicorn provider.api:app --port 8001 --reload
```

- API base URL: `http://localhost:8001`
- Swagger UI: `http://localhost:8001/docs`

### Provider CLI

You can run commands either as a module:

```bash
python -m provider.cli --help
```

Or via script entrypoint (after editable install):

```bash
pip install -e .
provider-cli --help
```

Available commands:

```bash
provider-cli catalog
provider-cli stock
provider-cli orders list --status pending
provider-cli orders show <id>
provider-cli price set <product> <tier> <price>
provider-cli restock <product> <quantity>
provider-cli day current
provider-cli day advance
provider-cli export
provider-cli import <file>
provider-cli serve --port 8001
```

Seed data is loaded from `provider/seed-provider.json` and mirrors manufacturer BOM part names (PCB, Extruder, Cable, Frame, Stepper Motor).

This provider app work addresses issues #14 and #15.

---

## How the Simulation Works

The core of the system is the **Advance Day** cycle, implemented in `simulation.py:advance_day()`. Every time a day is advanced — either via the dashboard's "Next Day" button or the `POST /simulation/advance` API call — the following four phases execute in strict order:

### Phase 1 — Day Increment

The `current_day` counter stored in the `factory_config` table is incremented by one. This is the simulation clock. Day 0 is the un-started state; day 1 is the first simulated working day.

### Phase 2 — Purchase Order Delivery

Every `PurchaseOrder` with `status = pending` and a non-null `lead_time_remaining` has its countdown decremented by 1. Any order whose countdown reaches **zero** is marked `delivered`: its `quantity` is added directly to the corresponding part's `current_stock`, and a `PURCHASE_DELIVERED` event is written to the audit log.

This means if you place a PO for PCBs with a 3-day lead time on day 5, the parts will arrive at the start of day 8's delivery phase.

### Phase 3 — Demand Generation

A random number of new `ManufacturingOrder` rows are created — between `demand_min` (default 5) and `demand_max` (default 15) — each representing a single customer order for one **Pro 3D Printer**. All new orders start with `status = pending`. Both bounds are live-configurable via the `factory_config` table.

### Phase 4 — Order Fulfilment

The simulation attempts to fulfil as many pending `ManufacturingOrder` rows as possible, subject to two hard constraints:

1. **Daily production capacity** — the factory can assemble at most `capacity_per_day` (default 10) printers per day.
2. **Bill of Materials stock** — each printer requires specific quantities of every BOM part to be available in inventory. If any part is short, the entire order is skipped and the next one is attempted (FIFO queue, oldest orders first).

When an order is successfully built:
- BOM components are **immediately consumed** from `current_stock`.
- The `ManufacturingOrder` is marked `completed`.
- A `PRODUCTION_COMPLETED` event is logged.

Orders that cannot be fulfilled due to stockouts remain `pending` and will be retried on the next day — creating natural **backorder pressure** that drives purchasing decisions.

### Event Sourcing

Every significant action writes a row to the `events` table with the simulation day, event type, and a human-readable description. This provides a complete audit trail and is the data source for the Analytics tab's cumulative-completion chart.

### Key Simulation Parameters (in `factory_config`)

| Key | Default | Description |
|---|---|---|
| `current_day` | 0 | Current simulation day (auto-managed) |
| `capacity_per_day` | 10 | Max printers assembled per day |
| `demand_min` | 5 | Min new orders generated per day |
| `demand_max` | 15 | Max new orders generated per day |
| `warehouse_capacity` | 1000 | Max total storage units (informational) |

---

## Dashboard Guide

| Tab | Contents |
|---|---|
| **Overview** | Live inventory table, procurement form to place purchase orders |
| **Orders** | All pending manufacturing orders with one-click "Release for Production" |
| **Analytics** | Bar chart of current stock by part; line chart of cumulative completed printers |
| **System** | Full database export (JSON download) and import (snapshot restore) |

The **sidebar** shows the current simulation day, a "Next Day" button, and a live factory log of the 3 most recent events.

---

## Data Model

```
Supplier ──[1:N]── SupplierCatalog ──[N:1]── Product
                                              │
                                        BOMEntry (qty per printer)
                                              │
PurchaseOrder ──────────────────────── Product (restocks stock)
ManufacturingOrder ─── (consumes BOM) ─ Product (drains stock)
EventLog ── (audit trail for all of the above)
FactoryConfig ── (key/value runtime settings)
```
