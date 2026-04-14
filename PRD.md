# Product Requirements Document: 3D Printer Production Simulator

## Overview

A discrete event simulation system that models a factory manufacturing 3D printers from raw components. The system simulates inventory management, supply chain logistics, and order fulfillment over time, creating tension between daily demand generation, inventory limits, and supplier lead times.

## Goals

- Simulate factory production capacity constrained by inventory and supplier lead times
- Track Manufacturing Orders (printer assembly) and Purchase Orders (raw material procurement)
- Provide real-time dashboard visibility into factory status
- Enable scenario analysis through data export/import capabilities

---

## Tech Stack

| Component | Technology | Rationale |
|-----------|------------|-----------|
| Backend API | FastAPI + Pydantic | Async support, automatic OpenAPI docs, validation |
| Simulation Engine | SimPy | Proven discrete event simulation framework |
| Frontend Dashboard | Streamlit | Rapid UI development, built-in charting |
| Database | SQLite + SQLAlchemy | Lightweight, no deployment overhead, async-capable |
| Charts | matplotlib | Flexible, integrates with Streamlit |
| Data Export | JSON | Human-readable, easy to parse |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Streamlit Dashboard                      │
│  (Real-time monitoring, historical charts, import/export)    │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP/WebSocket
┌──────────────────────────▼──────────────────────────────────┐
│                    FastAPI Backend                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │   API Routes │  │  Services    │  │   Schemas    │     │
│  │  (/api/...)  │  │(Business Logic)│  │(Pydantic)   │     │
│  └──────┬───────┘  └──────┬───────┘  └──────────────┘     │
│         │                 │                                 │
│  ┌──────▼─────────────────▼───────┐                        │
│  │      SimPy Simulation Engine   │                        │
│  │  - Daily tick progression      │                        │
│  │  - Demand generation           │                        │
│  │  - Inventory consumption       │                        │
│  │  - Order state transitions     │                        │
│  └──────────────┬─────────────────┘                        │
└─────────────────┼──────────────────────────────────────────┘
                  │ ORM
┌─────────────────▼──────────────────────────────────────────┐
│                  SQLite Database                             │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐              │
│  │ Inventory  │ │   Orders   │ │ EventLogs  │              │
│  └────────────┘ └────────────┘ └────────────┘              │
└─────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

1. **Simulation runs as background process**: SimPy environment executes independently, state persisted to DB after each tick
2. **Day-based time unit**: All durations (lead times, production rates) expressed in days
3. **Deterministic demand**: Configurable daily demand generation (uniform or seeded random)
4. **Event sourcing**: All state changes recorded in EventLog for audit/history
5. **Manual purchasing only**: No auto-reorder; planner must issue POs via UI
6. **Global warehouse capacity**: Total inventory units across all parts cannot exceed warehouse_capacity

---

## Data Model

### Core Entities

#### 1. **Supplier**
Represents a vendor that sells parts.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| name | string | Unique supplier identifier |
| contact_email | string | Optional contact info |

#### 2. **Part**
Represents a component used in printer assembly.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| name | string | Unique part identifier (e.g., "PCB", "Extruder") |
| current_stock | int | Current inventory quantity |
| storage_size | int | Storage units per item (default: 1) |

#### 3. **SupplierCatalog**
Pricing and availability from suppliers for parts.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| supplier_id | UUID → Supplier | Which supplier |
| part_id | UUID → Part | Which part |
| unit_price | Decimal | Price per unit |
| min_order_qty | int | Minimum order quantity |
| lead_time_days | int | Days from order to delivery |

*Unique constraint: (supplier_id, part_id)*

#### 4. **BillOfMaterial (BOM)**
Defines parts required to build one printer.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| part_id | UUID → Part | Component part |
| quantity_per_unit | int | Units of part per printer |

#### 5. **ManufacturingOrder**
Orders to assemble printers.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| quantity | int | Number of printers to build |
| status | enum | pending, in_progress, completed |
| created_at | datetime | Order creation timestamp |
| started_at | datetime | When production began |
| completed_at | datetime | When finished |
| days_elapsed | int | Actual simulation days taken |

#### 6. **PurchaseOrder**
Orders to suppliers for restocking parts.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| part_id | UUID → Part | Which part to replenish |
| supplier_id | UUID → Supplier | Which supplier |
| quantity | int | Units ordered |
| unit_price | Decimal | Locked-in price at order time |
| status | enum | pending, shipped, delivered |
| created_at | datetime | Order placed |
| ship_date | datetime | Supplier shipped date |
| delivered_at | datetime | Received at warehouse |
| lead_time_remaining | int | Days until delivery |

#### 7. **InventoryTransaction**
Records stock movements.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| part_id | UUID → Part | Affected part |
| transaction_type | enum | IN (purchase), OUT (production), ADJUST |
| quantity | int | Positive for IN, negative for OUT |
| timestamp | datetime | When occurred |
| reference_id | UUID | Link to PO/MO |

#### 8. **EventLog**
Audit trail of all significant events.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| day | int | Simulation day |
| event_type | enum | ORDER_CREATED, PRODUCTION_STARTED, PRODUCTION_COMPLETED, PURCHASE_CREATED, PURCHASE_DELIVERED, etc. |
| entity_type | string | manufacturing_order, purchase_order, inventory |
| entity_id | UUID | Related entity |
| description | string | Human-readable message |
| metadata | JSON | Additional context |

#### 9. **DailyStats**
Aggregated metrics per simulation day.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| day | int | Simulation day |
| total_inventory_units | int | Sum of all part quantities |
| total_inventory_value | float | Sum(stock * avg_cost) |
| pending_mo_count | int | Pending manufacturing orders |
| completed_mo_count | int | Cumulative completed orders |
| pending_po_count | int | Outstanding purchase orders |
| backorder_units | int | Total unfulfilled order units |

### Configuration Entity

#### **FactoryConfig**
Runtime configuration stored in DB.

| Field | Type | Description |
|-------|------|-------------|
| key | string | Primary key (config name) |
| value | JSON | Configuration value |

*Keys include:*
- `warehouse_capacity`: int - Maximum total storage units
- `capacity_per_day`: int - Printers factory can assemble per day
- `simulation_speed`: float - Real seconds per simulation day

### Relationships

```
Supplier ──[1:N]── SupplierCatalog
Part ──[1:N]── SupplierCatalog
Part ──[1:N]── BillOfMaterial
Part ──[1:N]── InventoryTransaction
Part ──[1:N]── PurchaseOrder
ManufacturingOrder ──[1:0..1]── EventLog
PurchaseOrder ──[1:0..1]── EventLog
```

### Business Rules

1. **Warehouse Capacity**: `SUM(part.current_stock * part.storage_size) <= warehouse_capacity`
   - Purchase orders that would exceed capacity must be rejected or truncated
2. **Production Start**: Requires sufficient BOM components in stock
   - Components consumed immediately when MO moves to `in_progress`
3. **Lead Time**: PO arrives exactly `lead_time_days` after creation
4. **Demand Generation**: Each simulation day generates new random demand (MOs)
5. **No Auto-Reorder**: Purchasing is purely manual via UI

---

## API Endpoints

### Simulation Control

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/simulation/start` | Start/resume simulation |
| POST | `/api/simulation/pause` | Pause simulation |
| POST | `/api/simulation/reset` | Reset to initial state |
| GET | `/api/simulation/status` | Current status, current day |

### Factory Configuration

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/config` | Get all configuration |
| PUT | `/api/config` | Update configuration |
| GET | `/api/config/{key}` | Get single config value |

### Inventory

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/inventory` | List all parts with stock levels |
| GET | `/api/inventory/{part_id}` | Single part details |
| POST | `/api/inventory/{part_id}/adjust` | Manual stock adjustment |
| GET | `/api/inventory/capacity` | Current vs. max warehouse capacity |
| GET | `/api/bom` | Get bill of materials |
| PUT | `/api/bom` | Update BOM configuration |

### Suppliers & Catalog

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/suppliers` | List all suppliers |
| POST | `/api/suppliers` | Create new supplier |
| GET | `/api/suppliers/{id}/catalog` | Get supplier's product catalog |
| GET | `/api/parts/{id}/suppliers` | Get all suppliers for a part |

### Manufacturing Orders

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/manufacturing-orders` | List all MOs with filters |
| POST | `/api/manufacturing-orders` | Create new production order |
| GET | `/api/manufacturing-orders/{id}` | Order details |
| POST | `/api/manufacturing-orders/{id}/cancel` | Cancel pending order |

### Purchase Orders

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/purchase-orders` | List all POs with filters |
| POST | `/api/purchase-orders` | Create purchase order |
| GET | `/api/purchase-orders/{id}` | PO details |
| POST | `/api/purchase-orders/{id}/cancel` | Cancel pending PO |

### Reports & Events

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/events` | Event log with pagination |
| GET | `/api/stats/daily` | Daily stats for charts |
| GET | `/api/reports/completed-orders` | Completed orders summary |

### Import/Export

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/export/full-state` | Export complete system state as JSON |
| GET | `/api/export/inventory` | Export inventory snapshot |
| GET | `/api/export/events` | Export event history |
| POST | `/api/import/full-state` | Import saved state |
| POST | `/api/import/inventory` | Import inventory snapshot |

---

## Configuration

All via environment variables:

```env
# Database
DATABASE_URL=sqlite:///./printer_factory.db

# Simulation Parameters
SIMULATION_CAPACITY_PER_DAY=10          # Printers per day
SIMULATION_DEMAND_MIN=5                 # Min daily manufacturing orders
SIMULATION_DEMAND_MAX=15                # Max daily manufacturing orders
SIMULATION_TICK_INTERVAL_SEC=1.0        # Seconds per sim day (real-time speed)
SIMULATION_WAREHOUSE_CAPACITY=1000      # Max total storage units

# Initial Parts Configuration (JSON)
INITIAL_INVENTORY='{"PCB": 100, "Extruder": 50, "Cable": 200}'

# Server
HOST=0.0.0.0
PORT=8000
```

---

## Development Plan

### Milestone 1: Foundation (Week 1)
- [ ] Project setup: FastAPI scaffold, SQLAlchemy models, Pydantic schemas
- [ ] Database migrations (Alembic)
- [ ] Basic CRUD API endpoints for Parts, Suppliers, SupplierCatalog
- [ ] Unit tests for data layer

### Milestone 2: Orders & BOM (Week 2)
- [ ] BOM model and API endpoints
- [ ] Manufacturing Order model, API, business logic
- [ ] Purchase Order model, API, business logic
- [ ] Warehouse capacity validation on PO creation
- [ ] Order state transition validation

### Milestone 3: Simulation Engine (Week 3)
- [ ] Integrate SimPy environment
- [ ] Daily tick implementation
- [ ] Demand generation logic (new MOs each day)
- [ ] Inventory consumption on production start
- [ ] Inventory restock on PO delivery
- [ ] Event logging throughout
- [ ] FactoryConfig persistence

### Milestone 4: Dashboard UI (Week 4)
- [ ] Streamlit app scaffold
- [ ] Real-time inventory display with capacity indicator
- [ ] Pending orders panel (MOs and POs)
- [ ] BOM breakdown view
- [ ] Supplier catalog browser
- [ ] Purchasing panel with PO creation

### Milestone 5: Reporting & Polish (Week 5)
- [ ] matplotlib charts integration (stock trends, completion rates)
- [ ] Historical data views
- [ ] JSON import/export functionality
- [ ] Error handling, input validation
- [ ] Documentation (OpenAPI + user guide)

### Milestone 6: Testing & Review (Week 6)
- [ ] Integration tests for simulation scenarios
- [ ] Load testing (concurrent dashboard/API users)
- [ ] Edge case handling (stockouts, capacity saturation, full warehouse)
- [ ] Performance optimization if needed
- [ ] Final review against requirements

---

## Non-Functional Requirements

- **Performance**: Handle 1000+ events/day simulation without lag
- **Reliability**: Simulation state recoverable after crash
- **Usability**: Dashboard usable without training
- **Maintainability**: Code coverage >70%, clear separation of concerns

---

## Appendix: Example Initial Setup

### Sample Suppliers
```json
{
  "suppliers": [
    {"id": "supp_001", "name": "TechComponents Inc"},
    {"id": "supp_002", "name": "GlobalParts Ltd"}
  ]
}
```

### Sample Parts
```json
{
  "parts": [
    {"id": "part_001", "name": "PCB", "storage_size": 1},
    {"id": "part_002", "name": "Extruder", "storage_size": 1},
    {"id": "part_003", "name": "Cable", "storage_size": 1},
    {"id": "part_004", "name": "Frame", "storage_size": 1},
    {"id": "part_005", "name": "Stepper Motor", "storage_size": 1}
  ]
}
```

### Sample BOM (per printer)
```json
{
  "bom": [
    {"part_id": "part_001", "quantity_per_unit": 1},   // 1 PCB
    {"part_id": "part_002", "quantity_per_unit": 2},   // 2 Extruders
    {"part_id": "part_003", "quantity_per_unit": 4},   // 4m Cable
    {"part_id": "part_004", "quantity_per_unit": 1},   // 1 Frame
    {"part_id": "part_005", "quantity_per_unit": 3}    // 3 Stepper Motors
  ]
}
```

### Sample Supplier Catalog
```json
{
  "catalog": [
    {"supplier_id": "supp_001", "part_id": "part_001", "unit_price": 45.00, "min_order_qty": 10, "lead_time_days": 3},
    {"supplier_id": "supp_001", "part_id": "part_002", "unit_price": 120.00, "min_order_qty": 5, "lead_time_days": 7},
    {"supplier_id": "supp_002", "part_id": "part_003", "unit_price": 2.50, "min_order_qty": 50, "lead_time_days": 2}
  ]
}
```
