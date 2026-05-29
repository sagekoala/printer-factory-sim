# 3D Printer Production Simulator

Three independent FastAPI + SQLite apps forming a complete supply chain,
orchestrated by a turn engine that can either run deterministic stubs or
delegate per-role decisions to an LLM agent (Claude Code, Cursor CLI, or
GitHub Copilot CLI).

| App | Path | Port | DB file |
|-----|------|------|---------|
| **Provider** (parts supplier) | `provider/` | `8001` | `provider/provider.db` |
| **Manufacturer** (factory) | `manufacturer/` | `8002` | `manufacturer/manufacturer.db` |
| **Retailer** (sells to customers) | `retailer/` | `8003` | `retailer/retailer.db` |

## Quick start

```bash
python -m venv .venv
.venv/bin/pip install -e ".[test]"

# Manufacturer needs an explicit seed; provider + retailer self-seed.
python -m manufacturer.seed

# Open three terminals and run one server each:
uvicorn provider.api:app --port 8001
uvicorn manufacturer.main:app --port 8002
uvicorn retailer.main:app --port 8003

# In a fourth terminal, drive the simulation:
python turn_engine.py config/sim-stub.json scenarios/smoke-test.json 5 smoke
```

Each app exposes its Swagger UI at `http://localhost:<port>/docs`.

## Running tests

```bash
.venv/bin/pip install -e ".[test]"
.venv/bin/pytest
```

## Turn engine

`turn_engine.py` orchestrates one simulated day across all three apps:

1. Compute today's market signal from a scenario file.
2. Inject auto-generated customer orders at each retailer.
3. Run the retailer, manufacturer, and provider agents (in that order).
4. Snapshot every app and append a JSON line to
   `<run_name>_<timestamp>_metrics.jsonl`.
5. Advance retailers + manufacturer + provider clocks (the provider
   receives the scenario's `lead_time_modifier`).

Agent backend (`TURN_ENGINE_AGENT`):

| Value | Command run | Notes |
|-------|-------------|-------|
| `claude` (default) | `claude --print --dangerously-skip-permissions` | Anthropic Claude Code CLI |
| `cursor` | `agent -p --force --trust` | Cursor CLI |
| `github` / `copilot` | `copilot -p --allow-all --add-dir <repo_root> --no-color` | GitHub Copilot CLI; optional `TURN_ENGINE_MODEL` |

Use `config/sim-stub.json` (`"skill": null` for every role) to run a
deterministic, zero-cost cycle without any LLM calls.

### Scenario events (overlap = MULTIPLY)

When multiple `events` are active on the same day,
`turn_engine.todays_signal` **multiplies** their numeric modifiers
(`demand_modifier`, `supply_modifier`, `lead_time_modifier`).
`price_sensitivity` is categorical so it takes the value of the last
active event.

Example (`scenarios/holiday-rush.json`, days 18–20):

| Day | Active events | Demand | Supply | Lead time |
|----:|---|---:|---:|---:|
| 14 | chip_shortage | 1.50 | 0.40 | 2.00 |
| 18 | chip_shortage × christmas_season | **3.75** | **0.24** | 2.00 |
| 21 | christmas_season | 2.50 | 0.60 | 1.00 |

## Analysis

```bash
python analysis/plot_results.py <run_metrics.jsonl> charts/<name>/ \
    --scenario scenarios/holiday-rush.json
```

Generates `inventory.png`, `prices.png`, `fulfillment.png`,
`events.png`, and `summary.txt`. Robust to null rows and short runs
(exits cleanly with a warning when there are fewer than two valid lines).

## CLI cheat-sheet

```bash
# Provider
provider-cli catalog | stock | orders list [--status X]
provider-cli day current | day advance
provider-cli price set <product_id> <min_qty> <price>
provider-cli restock <product_id> <qty>

# Manufacturer
manufacturer-cli stock | orders list [--status X]
manufacturer-cli suppliers list | suppliers catalog "<supplier_name>"
manufacturer-cli purchase create --supplier "<name>" --product-id <id> --qty <n>
manufacturer-cli purchase list [--status X]
manufacturer-cli sales orders [--status X] | sales order <id>
manufacturer-cli production release <sales_order_id> | production status
manufacturer-cli capacity
manufacturer-cli price list | price set "<model>" <price>
manufacturer-cli day current | day advance
manufacturer-cli export | import <file>

# Retailer
retailer-cli catalog | stock
retailer-cli customers orders [--status X] | customers order <id>
retailer-cli fulfill <id> | backorder <id>
retailer-cli purchase list | purchase create "<model>" <qty>
retailer-cli price set "<model>" <price>
retailer-cli day current | day advance
retailer-cli export | import <file>
retailer-cli serve --port 8003 [--config retailer_config.json]
```

## Configuration files

`config/sim.json` — turn-engine wiring (URLs + per-role skill files).
`config/sim-stub.json` — same shape but every `skill` is `null`.
`manufacturer/config.json` — known provider URLs (currently one).
`retailer/retailer_config.json` — retailer name + manufacturer URL.

DB locations are resolved relative to each app's source directory and
overridable via env vars: `DATABASE_URL` (manufacturer),
`PROVIDER_DATABASE_URL`, `RETAILER_DATABASE_URL`.

## Repository layout

```
manufacturer/    FastAPI app on :8002 + Typer CLI
provider/        FastAPI app on :8001 + Typer CLI
retailer/        FastAPI app on :8003 + Typer CLI
turn_engine.py   Orchestration script
config/          Turn engine wiring (sim.json / sim-stub.json)
scenarios/       Market scenarios (calm-market, holiday-rush, demo, smoke-test)
skills/          Per-role markdown skills consumed by the agent backends
analysis/        plot_results.py — metrics → charts + summary
scripts/         End-to-end run scripts (run-demo.sh, run-holiday-15d.sh)
tests/           Pytest suite (34 tests)
ui/              React + Vite dashboard (proxied through /p, /m, /r)
notes.md         Issue log (problems hit during development)
CLAUDE.md        Cursor / Claude project guide
```
