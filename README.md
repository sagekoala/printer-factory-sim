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

The turn engine orchestrates one simulated day across all three apps, generates customer demand, runs agent decisions (via Claude Code), and advances all apps in lock-step.

### Prerequisites

1. **Claude Code CLI installed**
   ```bash
   # Install from: https://claude.com/claude-code
   # Verify:
   claude --version
   ```

2. **All three apps running on separate ports**
   
   Open three terminals from the repo root and run:

   ```bash
   # Terminal 1 — Provider (port 8001)
   uvicorn provider.api:app --reload --port 8001
   
   # Terminal 2 — Manufacturer (port 8002)
   uvicorn manufacturer.main:app --reload --port 8002
   
   # Terminal 3 — Retailer (port 8003)
   uvicorn retailer.main:app --reload --port 8003
   ```

   Verify all three are healthy before proceeding.

### Running the Engine

From the repo root (in a 4th terminal):

```bash
python turn_engine.py config/sim.json scenarios/smoke-test.json <days>
```

Example — run 3 simulated days:

```bash
python turn_engine.py config/sim.json scenarios/smoke-test.json 3
```

### Order of Operations per Day

The turn engine executes the following steps in order:

1. **Read market signal** from scenario file (`scenarios/smoke-test.json`)
   - Demand modifier for today
   - Any market events
   
2. **Generate customer demand** at each retailer
   - Based on base demand + modifier
   - Injected via `POST /api/orders`

3. **Run retailer agents** (currently stubs, extensible to Claude Code)
   - Fulfill customer orders from stock
   - Place purchase orders with manufacturer if inventory low
   - Adjust prices based on demand
   
4. **Run manufacturer agent** (executes Claude Code skill file)
   - Review incoming sales orders from retailers
   - Release orders to production if parts available
   - Order parts from provider if stock running low
   - Adjust wholesale prices based on demand vs capacity
   - Output captured to `logs/day-NNN-manufacturer.log`

5. **Run provider agents** (currently stubs)
   - Process pending orders
   - Manage stock levels

6. **Advance all three apps** by one day
   - Calls `POST /api/day/advance` on each app

### Configuration Files

**`config/sim.json`** — Turn engine configuration

```json
{
  "retailers": [
    {
      "name": "PrinterWorld",
      "url": "http://localhost:8003",
      "path": "retailer",
      "skill": "skills/retailer-manager.md"
    }
  ],
  "manufacturer": {
    "name": "Factory",
    "url": "http://localhost:8002",
    "path": "manufacturer",
    "skill": "skills/manufacturer-manager.md"
  },
  "providers": [
    {
      "name": "ChipSupply Co",
      "url": "http://localhost:8001",
      "path": "provider",
      "skill": "skills/provider-manager.md"
    }
  ]
}
```

- **`skill: null`** → agent uses stub (prints "[stub] X would make decisions here")
- **`skill: "path/to/file.md"`** → agent executes Claude Code via `claude --print`

**`scenarios/smoke-test.json`** — Market scenario definition

```json
{
  "scenario_name": "smoke-test",
  "base_demand": {"mean": 4, "variance": 1},
  "events": [
    {
      "name": "normal",
      "start_day": 1,
      "end_day": 10,
      "demand_modifier": 1.0,
      "description": "Steady state"
    }
  ]
}
```

- `demand_modifier = 1.0` → normal demand
- `demand_modifier > 1.5` → high demand period
- `demand_modifier < 0.7` → low demand period

### Skill Files

Skill files teach Claude Code how to play a role in the simulation. Located in `skills/`:

- **`manufacturer-manager.md`** — Manufacturer agent instructions
  - How to assess state (CLI commands to run)
  - Decision framework (5-step process)
  - Constraints (what NOT to do)
  - Market signal interpretation

- **`retailer-manager.md`** — Retailer agent instructions
- **`provider-manager.md`** — Provider agent instructions

To add a role as an agent, point its `skill` in `config/sim.json` to the markdown file. Claude Code will execute the skill file instructions for that role each turn.

### Agent Output and Logging

Every time an agent runs, its output is captured and saved to:

```
logs/day-001-retailer.log
logs/day-001-manufacturer.log
logs/day-001-provider.log
logs/day-002-retailer.log
...
```

Each log file contains:
- State assessment (what the agent saw)
- Actions taken (what commands were run)
- Reasoning (why each decision was made)
- Summary (3-5 bullet points)

View the logs to understand agent behavior:

```bash
cat logs/day-001-manufacturer.log
cat logs/day-002-retailer.log
```

### Multiple Retailer Instances

To run multiple retailers (for market experiments):

```bash
retailer-cli serve --config retailer-1.json --port 8003 &
retailer-cli serve --config retailer-2.json --port 8005 &

# Update config/sim.json to include both retailers
# Then run the engine as normal
python turn_engine.py config/sim.json scenarios/smoke-test.json 3
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
