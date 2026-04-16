# Project: 3D Printer Production Simulator

## What This Is

A discrete event simulation system that models a factory manufacturing 3D printers from raw components. The system simulates inventory management, supply chain logistics, and order fulfillment over time, creating tension between daily demand generation, inventory limits (warehouse capacity), and supplier lead times. Users manage purchasing decisions via a dashboard while SimPy drives day-by-day simulation progression.

## Tech Stack

- Python 3.11+
- FastAPI + Pydantic for the REST API
- Streamlit for the dashboard UI
- SQLite + SQLAlchemy for persistence
- SimPy for discrete event simulation
- matplotlib for charts

## Architecture

- **Simulation runs as background process**: SimPy environment executes independently, state persisted to DB after each tick
- **Day-based time unit**: All durations (lead times, production rates) expressed in simulation days
- **Manual purchasing only**: No auto-reorder; planner must issue POs via UI
- **Global warehouse capacity**: Total inventory units across all parts cannot exceed `warehouse_capacity`
- **Event sourcing**: All state changes recorded in EventLog for audit/history

### Repository Structure

```
manufacturer/
	main.py            # FastAPI app entry point
	simulation.py      # Simulation engine / business logic
	database.py        # SQLAlchemy engine, sessions, ORM rows
	models.py          # Pydantic domain models
	dashboard.py       # Streamlit dashboard
	seed.py            # Database seeding script
	seed.json          # Seed data
	requirements.txt   # Python dependencies

api/                 # Reserved for future router decomposition
tests/               # Test suite
ui/                  # UI workspace

CLAUDE.md            # Project guidance
README.md            # Setup and usage docs
.gitignore           # Git ignore rules
.env.example         # Example environment variables
```

## Data Model

### Core Entities

| Entity | Purpose |
|--------|---------|
| **Supplier** | Vendor that sells parts |
| **Part** | Component used in printer assembly |
| **SupplierCatalog** | Pricing/availability per supplier per part (unit_price, min_order_qty, lead_time_days) |
| **BillOfMaterial (BOM)** | Parts required to build one printer |
| **ManufacturingOrder** | Order to assemble printers (status: pending, in_progress, completed) |
| **PurchaseOrder** | Order to suppliers for restocking (status: pending, shipped, delivered) |
| **InventoryTransaction** | Record of stock movements |
| **EventLog** | Audit trail of all significant events |
| **DailyStats** | Aggregated metrics per simulation day |
| **FactoryConfig** | Runtime config (warehouse_capacity, capacity_per_day) |

### Key Constraints

- **Warehouse Capacity**: `SUM(part.current_stock * part.storage_size) <= warehouse_capacity`
- **Production Start**: Requires sufficient BOM components; consumed immediately when MO starts
- **Lead Time**: PO arrives exactly `lead_time_days` after creation

## Coding Conventions

- Use type hints everywhere
- Pydantic models for all API request/response schemas
- Keep API routes in separate files from business logic
- Write docstrings for public functions
- All configuration via environment variables or config files
- Use UUIDs for all entity IDs
- Separate services module for business logic (not in route handlers)

## Current State

- Core FastAPI + SQLite simulation app lives under `manufacturer/`
- Root-level documentation and environment scaffolding remain in place
- Repository is prepared for future multi-app architecture expansion
