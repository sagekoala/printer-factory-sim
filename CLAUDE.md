# Project: 3D Printer Production Simulator

## Current State (Week 9 — post-refactor baseline)

Three independent FastAPI + SQLite applications that communicate over HTTP,
plus a turn engine that orchestrates them and per-role skill files.

- **Provider app** (`provider/`) on **port 8001**
	- Simulates external parts suppliers and order fulfilment.
	- Exposes supplier catalog, stock, order placement, simulation day controls.
	- CLI entrypoint: `provider-cli`

- **Manufacturer app** (`manufacturer/`) on **port 8002**
	- Simulates factory demand, inventory consumption, production, persistence.
	- Polls provider orders and reconciles delivered quantities into local inventory.
	- Accepts inbound sales orders from retailers, tracks finished printer stock,
	  exposes wholesale prices.
	- CLI entrypoint: `manufacturer-cli`

- **Retailer app** (`retailer/`) on **port 8003**
	- Sells finished printers to end customers.
	- Fulfills customer orders from stock; backordered orders are auto-fulfilled
	  on day advance.
	- Places purchase orders with the manufacturer and polls for delivery each day.
	- CLI entrypoint: `retailer-cli`

- **Turn engine** (`turn_engine.py`)
	- Orchestrates one simulated day across all three apps.
	- Generates deterministic customer demand from a scenario file.
	- Delegates per-role decisions to an LLM agent (Claude / Cursor /
	  GitHub Copilot) or falls back to stubs.
	- Logs agent output to `logs/day-NNN-role.log`.
	- Appends per-day metrics to `<run_name>_<timestamp>_metrics.jsonl`.

- **Skill files** (`skills/manufacturer-manager.md`,
  `skills/retailer-manager.md`, `skills/provider-manager.md`)
	- Teach the agent how to play each role.

## Tech Stack

- Python 3.10+
- FastAPI + Pydantic (REST APIs)
- SQLite + SQLAlchemy (persistence)
- Typer (CLI for all three apps)
- httpx (inter-app HTTP integration)
- React + Vite (`ui/`) — single dashboard for all three apps
- pytest (34 unit tests)

## Repository Structure

```
manufacturer/
	main.py
	cli.py
	simulation.py
	database.py
	services/
		suppliers.py        ← only provider integration layer
	sales_orders.py
	config.json             ← only config file (no legacy duplicate)
	models.py
	seed.py
	seed.json
	__init__.py

provider/
	api.py                  ← /health, /api/catalog, /api/orders, /api/day/*
	cli.py
	db.py
	seed-provider.json
	services/
		catalog.py
		orders.py
		simulation.py
	__init__.py

retailer/
	main.py                 ← /health, /api/catalog, /api/orders, /api/purchases,
	                          /api/prices/{model}, /api/day/*
	cli.py
	simulation.py
	database.py
	manufacturer_integration.py
	seed.py
	seed-retailer.json
	retailer_config.json
	models.py
	__init__.py

turn_engine.py              ← RunState-based orchestration (no globals)
config/
	sim.json                ← agent mode
	sim-stub.json           ← deterministic stub mode (skill: null everywhere)
scenarios/
	smoke-test.json
	calm-market.json
	holiday-rush.json
	demo.json
skills/
	manufacturer-manager.md
	retailer-manager.md
	provider-manager.md
analysis/
	plot_results.py
scripts/
	run-demo.sh
	run-holiday-15d.sh
tests/
	conftest.py
	test_turn_engine.py
	test_manufacturer_simulation.py
	test_retailer_simulation.py
	test_provider_services.py
	test_analysis.py
ui/                         ← React + Vite dashboard (single source of truth for UI)

README.md
CLAUDE.md
notes.md
pyproject.toml              ← packages = [provider, manufacturer, retailer];
                              optional [test]: pytest, httpx
.gitignore
```

## REST Contracts

### Retailer ↔ Manufacturer

Retailer calls manufacturer:
- `GET /api/catalog` — wholesale prices for finished printers
- `POST /api/orders` — place a purchase order (`retailer_name`, `model`, `qty`)
- `GET /api/orders/{id}` — poll order status

Manufacturer inbound endpoints:
- `GET /health`
- `POST /api/orders` — accept order from retailer
- `GET /api/orders` — list sales orders
- `GET /api/orders/{id}` — order details
- `POST /api/day/advance` — turn-engine-compatible day advance
- `GET /api/day/current` — current simulation day
- `GET /api/catalog` — wholesale catalog
- `GET /api/stock` — finished printer stock
- `GET /api/prices` — wholesale prices
- `POST /api/prices/{model}` — set wholesale price (body: `{"price": N}`)
- `GET /api/capacity` — daily capacity info
- `GET /api/production/status` — current production state

### Retailer REST endpoints

- `GET /health`
- `GET /api/catalog` — models with retail prices
- `GET /api/stock` — current inventory
- `POST /api/orders` — customer places an order
- `GET /api/orders` — list customer orders (optional `?status=`)
- `GET /api/orders/{id}` — order details
- `POST /api/purchases` — order printers from manufacturer
- `GET /api/purchases` — list purchase orders
- `POST /api/prices/{model}` — set retail price (body: `{"price": N}`)
- `POST /api/day/advance` — advance one day
- `GET /api/day/current` — current day

### Provider REST endpoints

- `GET /health`
- `GET /api/catalog`
- `POST /api/orders` — place purchase
- `GET /api/orders/{id}` — poll status each day advance
- `GET /api/day/current`
- `POST /api/day/advance?lead_time_modifier=X` — driven by the turn engine

## Order Lifecycle State Machines

### Provider order status
`pending → confirmed → in_progress → shipped → delivered`

### Manufacturer outbound purchase lifecycle
- Created as `outbound_purchase_orders` row
- Polled each `advance_day`; delivered → parts stock incremented

### Manufacturer sales order lifecycle
`pending → released → shipped → delivered`

`release_to_production()` flips status from `pending` to `released` **and**
enqueues one `manufacturing_order` row per unit (capped per day by
`capacity_per_day`). Skipping the enqueue step was bug #18 — see `notes.md`.

### Retailer customer order lifecycle
`pending → fulfilled | backordered`

Backordered orders auto-retry on each `advance_day`.

### Retailer purchase order lifecycle
`pending → confirmed → in_progress → shipped → delivered`

## Turn Engine

Run one simulated day across all apps:

```bash
python turn_engine.py config/sim-stub.json scenarios/smoke-test.json 5 smoke
python turn_engine.py config/sim.json scenarios/holiday-rush.json 25 holiday
```

Order of operations per turn:
1. Compute today's signal from the scenario file.
2. Generate customer demand → `POST /api/orders` at each retailer.
3. Run retailer agent/stub.
4. Run manufacturer agent.
5. Run provider agent/stub.
6. Snapshot all three apps → append one JSON line to the run metrics file.
7. `advance_all(signal, config)` → advance retailers + manufacturer + provider
   (provider receives `lead_time_modifier`).

### Overlapping-modifier rule: MULTIPLY

When multiple scenario events are active on the same day,
`todays_signal()` **multiplies** their numeric modifiers
(`demand_modifier`, `supply_modifier`, `lead_time_modifier`).
`price_sensitivity` is categorical so the last active event wins.

Test coverage: `tests/test_turn_engine.py::test_todays_signal_overlapping_events_multiply_modifiers`.

### Metrics capture

Schema per line:

```json
{
 "day": 1,
 "scenario_event": "normal",
 "demand_modifier": 1.0,
 "supply_modifier": 1.0,
 "lead_time_modifier": 1.0,
 "price_sensitivity": "normal",
 "provider": { "stock": {...}, "prices": {...}, "orders_pending": N, "orders_shipped": N },
 "manufacturer": { "parts_stock": {...}, "finished_stock": {...}, "wholesale_price": {...},
                   "sales_orders_pending": N, "capacity_per_day": N },
 "retailer": { "stock": {...}, "retail_price": {...},
               "orders_fulfilled": N, "orders_backordered": N }
}
```

Every HTTP call inside `collect_metrics` is wrapped in its own
try/except; a failed fetch yields `null` instead of aborting the run.

## CLI Surfaces

Provider:
- `provider-cli catalog | stock | restock <product> <qty> | export | import <file>`
- `provider-cli orders list [--status X] | orders show <id>`
- `provider-cli day current | day advance`
- `provider-cli price list | price set <product_id> <min_qty> <price>`
- `provider-cli serve --port 8001`

Manufacturer:
- `manufacturer-cli stock | export | import <file> | capacity`
- `manufacturer-cli orders list [--status X] | orders show <id>`
- `manufacturer-cli purchase create --supplier "<name>" --product-id <id> --qty <n>`
- `manufacturer-cli purchase list [--status X] | purchase show <id>`
- `manufacturer-cli sales orders [--status X] | sales order <id>`
- `manufacturer-cli production release <sales_order_id> | production status`
- `manufacturer-cli suppliers list | suppliers catalog "<supplier>"`
- `manufacturer-cli price list | price set "<model>" <price>`
- `manufacturer-cli day current | day advance`

Retailer:
- `retailer-cli catalog | stock | export | import <file>`
- `retailer-cli customers orders [--status X] | customers order <id>`
- `retailer-cli fulfill <order_id> | backorder <order_id>`
- `retailer-cli purchase list | purchase create "<model>" <qty>`
- `retailer-cli price set "<model>" <price>`
- `retailer-cli day current | day advance`
- `retailer-cli serve --port 8003 [--config retailer_config.json]`

## Configuration

`manufacturer/config.json` — known provider URLs:

```json
{ "providers": [{"name": "ChipSupply Co", "url": "http://localhost:8001"}] }
```

`retailer/retailer_config.json`:

```json
{ "retailer": { "name": "PrinterWorld", "port": 8003,
                "manufacturer": {"name": "Factory", "url": "http://localhost:8002"},
                "markup_pct": 30 } }
```

`config/sim.json` — turn engine wiring:

```json
{ "retailers": [{"name": "PrinterWorld", "url": "http://localhost:8003",
                 "path": "retailer", "skill": "skills/retailer-manager.md"}],
  "manufacturer": {"name": "Factory", "url": "http://localhost:8002",
                   "path": "manufacturer", "skill": "skills/manufacturer-manager.md"},
  "providers": [{"name": "ChipSupply Co", "url": "http://localhost:8001",
                 "path": "provider", "skill": "skills/provider-manager.md"}] }
```

For deterministic stub runs, use `config/sim-stub.json`
(`"skill": null` everywhere — no LLM CLI calls).

## How to run a full simulation end-to-end

1. **Reset all DBs** (only for a clean run):

	```bash
	rm -f provider/provider.db manufacturer/manufacturer.db retailer/retailer.db
	```

2. **Start the three apps** (each in its own terminal):

	```bash
	uvicorn provider.api:app --port 8001
	uvicorn manufacturer.main:app --port 8002
	uvicorn retailer.main:app --port 8003
	```

3. **Seed the manufacturer** (one-time, after first start — see issue #7 in
	`notes.md`). The provider and retailer self-seed on startup.

	```bash
	python -m manufacturer.seed
	```

4. **Run the turn engine.** Fourth argument is an optional run-name that
	becomes the metrics-file prefix.

	```bash
	# stub mode
	python turn_engine.py config/sim-stub.json scenarios/calm-market.json 25 calm-market

	# agent mode (default backend: claude)
	python turn_engine.py config/sim.json scenarios/holiday-rush.json 25 holiday-rush

	# agent backend selection
	TURN_ENGINE_AGENT=cursor python turn_engine.py config/sim.json ...
	TURN_ENGINE_AGENT=github python turn_engine.py config/sim.json ...
	```

	Each run writes `logs/day-NNN-<role>.log` and one
	`<run_name>_<timestamp>_metrics.jsonl` in the repo root.

## How to generate charts

```bash
python analysis/plot_results.py <run>_metrics.jsonl charts/<name>/ \
    --scenario scenarios/holiday-rush.json
```

`analysis/plot_results.py` requires only matplotlib + stdlib and produces:

- `inventory.png` — manufacturer parts, manufacturer finished, retailer stock
- `prices.png` — provider PCB, manufacturer wholesale, retailer retail
- `fulfillment.png` — fulfilled vs. backordered orders per day
- `events.png` — horizontal strip per event band (when `--scenario` is passed)
- `summary.txt` — totals, backorder rate, peak/low stock, days with backorders

Exits cleanly (code 0, stderr warning) if there are fewer than 2 valid rows.

## Tests

```bash
.venv/bin/pip install -e ".[test]"
.venv/bin/pytest                    # 34 tests
```

Test layout:
- `tests/test_turn_engine.py` — signal computation, metrics, RunState
- `tests/test_manufacturer_simulation.py` — release/produce/ship + bug regressions
- `tests/test_retailer_simulation.py` — fulfillment, backorders, price set
- `tests/test_provider_services.py` — tier pricing, advance day, validations
- `tests/test_analysis.py` — plot_results robustness

`conftest.py` adds the repo root to `sys.path` so `turn_engine` is importable
without installing it as a package.

## Known issues to remember (from `notes.md`)

- **#2** Claude CLI: prompt is positional, not `--prompt`.
- **#4** On Windows, `claude` resolves to `claude.CMD`; handled in
  `_claude_cmd()`.
- **#5** Agent timeout is **300 s**, not 180 s.
- **#7** Manufacturer **does not self-seed** — run `python -m manufacturer.seed`
  after the first server start.
- **#10** Day 1 of a fresh manufacturer DB shows `newly_produced = 0`; expected.
- **#11** Claude rate limits look like normal output — check log content.
- **#18** `release_to_production` MUST enqueue manufacturing orders, not just
  flip the sales order status. Covered by
  `test_release_to_production_queues_manufacturing_orders`.
- **#19** `/api/day/advance` was double-counting finished stock. Fixed.
  Covered by `test_advance_day_does_not_double_count_finished_stock`.

## Demo

A short scenario lives at `scenarios/demo.json` (3 days: normal → 3× spike →
recovery) for live demonstrations.
