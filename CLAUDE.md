# Project: 3D Printer Production Simulator (Week 7)

## Current State

The repository now contains **three independent FastAPI + SQLite applications** that communicate over HTTP, plus a **turn engine** that orchestrates them and a **skill file** for the manufacturer agent.

- **Provider app** (`provider/`) on **port 8001**
	- Simulates external parts suppliers and order fulfilment.
	- Exposes supplier catalog, stock, order placement, and simulation day controls.
	- CLI entrypoint: `provider-cli`

- **Manufacturer app** (`manufacturer/`) on **port 8002**
	- Simulates factory demand, inventory consumption, production, and local persistence.
	- Polls provider orders and reconciles delivered quantities into local inventory.
	- **Week 7 additions (additive only):** accepts inbound sales orders from retailers,
	  tracks finished printer stock, exposes wholesale prices, and adds turn-engine-compatible
	  `/api/day/advance` and `/api/day/current` endpoints.
	- CLI entrypoint: `manufacturer-cli`

- **Retailer app** (`retailer/`) on **port 8003**
	- Sells finished printers to end customers.
	- Fulfills customer orders from stock; backordered orders are auto-fulfilled on day advance.
	- Places purchase orders with the manufacturer and polls for delivery each day.
	- CLI entrypoint: `retailer-cli`

- **Turn engine** (`turn_engine.py`)
	- Orchestrates one simulated day across all three apps.
	- Generates deterministic customer demand from a scenario file.
	- Calls Claude Code (`claude --print`) for agent roles; falls back to stubs.
	- Logs agent output to `logs/day-NNN-role.log`.

- **Skill file** (`skills/manufacturer-manager.md`)
	- Teaches Claude Code how to play the manufacturer manager role.

## Tech Stack

- Python 3.10+
- FastAPI + Pydantic (REST APIs)
- SQLite + SQLAlchemy (persistence)
- Typer (CLI for all three apps)
- httpx (inter-app HTTP integration)
- Streamlit (manufacturer dashboard)

## Repository Structure

```
manufacturer/
	main.py
	cli.py
	simulation.py
	database.py
	provider_integration.py
	sales_orders.py          ← Week 7: inbound sales order logic
	provider_config.json
	dashboard.py
	models.py
	seed.py
	seed.json
	requirements.txt

provider/
	api.py
	cli.py
	db.py
	seed-provider.json
	requirements.txt
	services/
		catalog.py
		orders.py
		simulation.py

retailer/                    ← Week 7: new app
	main.py
	cli.py
	simulation.py
	database.py
	manufacturer_integration.py
	seed.py
	seed-retailer.json
	retailer_config.json
	models.py
	requirements.txt

turn_engine.py               ← Week 7: orchestration script
config/
	sim.json                 ← turn engine configuration
scenarios/
	smoke-test.json          ← minimal smoke-test scenario
skills/
	manufacturer-manager.md  ← Claude Code skill file
logs/                        ← agent output (gitignored)

README.md
CLAUDE.md
pyproject.toml
.gitignore
.env.example
```

## REST Contracts

### Retailer ↔ Manufacturer

Retailer calls manufacturer:
- `GET /api/catalog` — wholesale prices for finished printers
- `POST /api/orders` — place a purchase order (payload: retailer_name, model, qty)
- `GET /api/orders/{id}` — poll order status

Manufacturer inbound endpoints (Week 7 additions):
- `POST /api/orders` — accept order from retailer
- `GET /api/orders` — list sales orders
- `GET /api/orders/{id}` — order details
- `POST /api/day/advance` — turn-engine-compatible day advance
- `GET /api/day/current` — current simulation day
- `GET /api/catalog` — wholesale catalog
- `GET /api/stock` — finished printer stock
- `GET /api/prices` — wholesale prices
- `POST /api/prices/{model}` — set wholesale price
- `GET /api/capacity` — daily capacity info
- `GET /api/production/status` — current production state

### Retailer REST endpoints

- `GET /api/catalog` — models with retail prices
- `GET /api/stock` — current inventory
- `POST /api/orders` — customer places an order
- `GET /api/orders` — list customer orders (optional `?status=`)
- `GET /api/orders/{id}` — order details
- `POST /api/purchases` — order printers from manufacturer
- `GET /api/purchases` — list purchase orders
- `POST /api/day/advance` — advance one day
- `GET /api/day/current` — current day

### Manufacturer ↔ Provider (unchanged from Week 6)

- `GET /api/catalog` — discover products/pricing tiers
- `POST /api/orders` — place purchase
- `GET /api/orders/{id}` — poll status each day advance

## Order Lifecycle State Machines

### Provider order status
`pending -> confirmed -> in_progress -> shipped -> delivered`

### Manufacturer outbound purchase lifecycle (Week 6, unchanged)
- Created as `outbound_purchase_orders` row
- Polled each `advance_day`; delivered → stock incremented

### Manufacturer sales order lifecycle (Week 7)
`pending -> (released) -> shipped -> delivered`

### Retailer customer order lifecycle
`pending -> fulfilled | backordered`

### Retailer purchase order lifecycle
`pending -> confirmed -> in_progress -> shipped -> delivered`

## Turn Engine

Run one simulated day across all apps:

```bash
python turn_engine.py config/sim.json scenarios/smoke-test.json <days>
```

Order of operations per turn:
1. Read today's signal from scenario file
2. Generate customer demand → `POST /api/orders` at each retailer
3. Run retailer agent/stub
4. Run manufacturer agent (Claude Code via `claude --print`)
5. Run provider agent/stub
6. Advance all apps: `POST /api/day/advance`

## CLI Surfaces

Provider (unchanged):
- `provider-cli catalog | stock | orders list | day advance | serve`

Manufacturer (Week 6 + Week 7 additions):
- `manufacturer-cli stock | orders list | day advance | suppliers list`
- `manufacturer-cli sales orders [--status X]` ← Week 7
- `manufacturer-cli sales order <id>` ← Week 7
- `manufacturer-cli production release <order_id>` ← Week 7
- `manufacturer-cli production status` ← Week 7
- `manufacturer-cli capacity` ← Week 7
- `manufacturer-cli price list | set <model> <price>` ← Week 7

Retailer (Week 7):
- `retailer-cli catalog | stock`
- `retailer-cli customers orders [--status X] | order <id>`
- `retailer-cli fulfill <order_id> | backorder <order_id>`
- `retailer-cli purchase list | create <model> <qty>`
- `retailer-cli price set <model> <price>`
- `retailer-cli day advance | current`
- `retailer-cli export | import <file>`
- `retailer-cli serve --port 8003 [--config retailer_config.json]`

## Configuration

Retailer config (`retailer/retailer_config.json`):

```json
{
  "retailer": {
    "name": "PrinterWorld",
    "port": 8003,
    "manufacturer": {"name": "Factory", "url": "http://localhost:8002"},
    "markup_pct": 30
  }
}
```

Turn engine config (`config/sim.json`):

```json
{
  "retailers": [{"name": "PrinterWorld", "url": "http://localhost:8003", "path": "retailer", "skill": null}],
  "manufacturer": {"name": "Factory", "url": "http://localhost:8002", "path": "manufacturer", "skill": "skills/manufacturer-manager.md"},
  "providers": [{"name": "ChipSupply Co", "url": "http://localhost:8001", "path": "provider", "skill": null}]
}
```
