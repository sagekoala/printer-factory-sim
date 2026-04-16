# Project: 3D Printer Production Simulator (Week 6)

## Current State

The repository now contains **two independent FastAPI + SQLite applications** that communicate over HTTP:

- **Provider app** (`provider/`) on **port 8001**
	- Simulates external parts suppliers and order fulfilment.
	- Exposes supplier catalog, stock, order placement, and simulation day controls.
	- CLI entrypoint: `provider-cli`

- **Manufacturer app** (`manufacturer/`) on **port 8002**
	- Simulates factory demand, inventory consumption, production, and local persistence.
	- Polls provider orders and reconciles delivered quantities into local inventory.
	- CLI entrypoint: `manufacturer-cli`

## Tech Stack

- Python 3.10+
- FastAPI + Pydantic (REST APIs)
- SQLite + SQLAlchemy (persistence)
- Typer (CLI for both apps)
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

README.md
CLAUDE.md
pyproject.toml
.gitignore
.env.example
```

## REST Contract (Manufacturer ↔ Provider)

Configured in `manufacturer/provider_config.json`:

```json
{
	"manufacturer": {
		"port": 8002,
		"providers": [
			{"name": "ChipSupply Co", "url": "http://localhost:8001"}
		]
	}
}
```

Manufacturer outbound calls to provider:

- `GET /api/catalog` (discover products/pricing tiers)
- `POST /api/orders` (place purchase)
- `GET /api/orders/{id}` (poll status on each day advance)

Manufacturer inbound provider-integration endpoints:

- `GET /api/suppliers`
- `GET /api/suppliers/{name}/catalog`
- `POST /api/purchase`
- `GET /api/purchase`

## Order Lifecycle State Machines

### Provider order status

`pending -> confirmed -> in_progress -> shipped -> delivered`

Also supported terminal states:

- `rejected`
- `cancelled`

Transitions are event-logged in provider `events`.

### Manufacturer outbound purchase lifecycle

- Created as local `outbound_purchase_orders` row when provider `POST /api/orders` succeeds.
- Polled each manufacturer simulation day (`advance_day`).
- When provider status becomes `delivered`:
	- local outbound row marked `delivered`
	- manufacturer product stock incremented
	- delivery event written to manufacturer `events`

Provider downtime or HTTP failures are logged as `PROVIDER_SYNC_ERROR` events and surfaced to CLI/API callers.

## CLI Surfaces

Provider:

- `provider-cli catalog`
- `provider-cli stock`
- `provider-cli orders list [--status X]`
- `provider-cli orders show <id>`
- `provider-cli price set <product> <tier> <price>`
- `provider-cli restock <product> <quantity>`
- `provider-cli day current`
- `provider-cli day advance`
- `provider-cli export`
- `provider-cli import <file>`
- `provider-cli serve --port 8001`

Manufacturer:

- `manufacturer-cli stock`
- `manufacturer-cli orders list [--status X]`
- `manufacturer-cli day current`
- `manufacturer-cli day advance`
- `manufacturer-cli suppliers list`
- `manufacturer-cli suppliers catalog <supplier_name>`
- `manufacturer-cli purchase create --supplier <name> --product <product_id> --qty <n>`
- `manufacturer-cli purchase list`
