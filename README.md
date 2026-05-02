# 3D Printer Production Simulator (Week 7)

Three independent FastAPI + SQLite apps forming a complete supply chain, orchestrated by a turn engine.

| App | Path | Port | DB file |
|-----|------|------|---------|
| **Provider** (parts supplier) | `provider/` | `8001` | `provider/provider.db` |
| **Manufacturer** (factory) | `manufacturer/` | `8002` | `manufacturer/manufacturer.db` |
| **Retailer** (sells to customers) | `retailer/` | `8003` | `retailer/retailer.db` |

---

## Prerequisites

- Python `3.10+`

Install all dependencies from repo root:

```bash
python -m venv .venv
# Windows
.venv\Scripts\pip install -e .
# macOS/Linux
.venv/bin/pip install -e .
```

This installs `provider-cli`, `manufacturer-cli`, and `retailer-cli` console scripts.

---

## First-time database setup

```bash
# Provider seeds itself on first startup, or explicitly:
cd provider && python seed.py && cd ..

# Manufacturer:
python -m manufacturer.seed

# Retailer seeds itself on first startup (no manual step needed)
```

To wipe and start over:

```bash
rm -f manufacturer/manufacturer.db provider/provider.db retailer/retailer.db
```

---

## Running all three apps

Open three terminals from the repo root:

### Terminal 1 — Provider `:8001`

```bash
uvicorn provider.api:app --host 0.0.0.0 --port 8001 --reload
# or:
provider-cli serve --port 8001
```

### Terminal 2 — Manufacturer `:8002`

```bash
uvicorn manufacturer.main:app --host 0.0.0.0 --port 8002 --reload
```

### Terminal 3 — Retailer `:8003`

```bash
uvicorn retailer.main:app --host 0.0.0.0 --port 8003 --reload
# or:
retailer-cli serve --port 8003
```

Swagger UIs:
- Provider: <http://localhost:8001/docs>
- Manufacturer: <http://localhost:8002/docs>
- Retailer: <http://localhost:8003/docs>

### Optional — Manufacturer dashboard

```bash
streamlit run manufacturer/dashboard.py
```

---

## Running the Turn Engine

With all three apps running, from the repo root:

```bash
python turn_engine.py config/sim.json scenarios/smoke-test.json <days>
```

Example — run 3 simulated days:

```bash
python turn_engine.py config/sim.json scenarios/smoke-test.json 3
```

What happens each day:
1. Reads today's market signal from `scenarios/smoke-test.json`
2. Injects customer orders at the retailer
3. Runs manufacturer agent (Claude Code via `claude --print`) or stub
4. Advances all three apps by one day
5. Saves agent output to `logs/day-NNN-manufacturer.log`

### Multiple retailer instances

```bash
retailer-cli serve --config retailer-1.json --port 8003
retailer-cli serve --config retailer-2.json --port 8005
```

---

## CLI Quick Reference

### Provider

```bash
provider-cli catalog
provider-cli stock
provider-cli orders list --status pending
provider-cli day current
provider-cli day advance
provider-cli serve --port 8001
```

### Manufacturer

```bash
manufacturer-cli stock
manufacturer-cli orders list --status pending
manufacturer-cli suppliers list
manufacturer-cli suppliers catalog "ChipSupply Co"
manufacturer-cli purchase create --supplier "ChipSupply Co" --product-id p-0001 --qty 5
manufacturer-cli purchase list
manufacturer-cli day current
manufacturer-cli day advance
# Week 7 additions:
manufacturer-cli sales orders
manufacturer-cli sales order <id>
manufacturer-cli production release <order_id>
manufacturer-cli production status
manufacturer-cli capacity
manufacturer-cli price list
manufacturer-cli price set "Pro 3D Printer" 1100
```

### Retailer

```bash
retailer-cli catalog
retailer-cli stock
retailer-cli customers orders
retailer-cli customers orders --status backordered
retailer-cli customers order <id>
retailer-cli fulfill <order_id>
retailer-cli backorder <order_id>
retailer-cli purchase list
retailer-cli purchase create "Pro 3D Printer" 5
retailer-cli price set "Pro 3D Printer" 1350.0
retailer-cli day current
retailer-cli day advance
retailer-cli export
retailer-cli import snapshot.json
retailer-cli serve --port 8003
```

---

## Typical 3-day manual scenario

```bash
# 1. Check state
manufacturer-cli stock
retailer-cli stock
retailer-cli catalog

# 2. Customer places an order
curl -X POST http://localhost:8003/api/orders \
  -H "Content-Type: application/json" \
  -d '{"customer": "Alice", "model": "Pro 3D Printer", "quantity": 1}'

# 3. Retailer orders from manufacturer if stock low
retailer-cli purchase create "Pro 3D Printer" 5

# 4. Advance all three apps
provider-cli day advance
curl -X POST http://localhost:8002/api/day/advance
retailer-cli day advance

# 5. Check deliveries
retailer-cli purchase list
manufacturer-cli sales orders
```

---

## Repository Layout

```
manufacturer/
  main.py                  # FastAPI app (port 8002)
  cli.py                   # Typer CLI
  simulation.py            # advance_day() and production logic
  database.py              # SQLAlchemy engine + ORM rows
  sales_orders.py          # Week 7: inbound sales order logic
  provider_integration.py  # outbound calls to provider:8001
  dashboard.py             # Streamlit UI
  models.py                # Pydantic models
  seed.py / seed.json      # initial data

provider/
  api.py                   # FastAPI app (port 8001)
  cli.py                   # Typer CLI
  db.py / models.py        # SQLAlchemy + Pydantic
  seed.py / seed-provider.json
  services/
    catalog.py | orders.py | simulation.py

retailer/
  main.py                  # FastAPI app (port 8003)
  cli.py                   # Typer CLI
  simulation.py            # advance_day(), poll manufacturer, auto-fulfill
  database.py              # SQLAlchemy engine + ORM rows
  manufacturer_integration.py  # HTTP calls to manufacturer:8002
  seed.py / seed-retailer.json
  retailer_config.json     # default config

turn_engine.py             # orchestration script
config/sim.json            # engine config (retailer/manufacturer/provider URLs)
scenarios/smoke-test.json  # minimal scenario
skills/
  manufacturer-manager.md  # Claude Code skill file
logs/                      # agent output per day (gitignored)
docs/
  PRD.md                   # orchestration design document
```

---

## Integration notes

- Manufacturer → Provider hosts configured in `manufacturer/provider_config.json`
- Retailer → Manufacturer URL configured in `retailer/retailer_config.json`
- DB locations are resolved relative to each app's source directory
- Override with env vars: `DATABASE_URL` (manufacturer), `PROVIDER_DATABASE_URL` (provider), `RETAILER_DATABASE_URL` (retailer)
