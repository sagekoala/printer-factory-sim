# PRD: 3D Printer Supply Chain Simulator — Week 7 Orchestration Design

## Overview

Week 7 completes the three-node supply chain (Provider → Manufacturer → Retailer) and introduces the **Turn Engine**: a script that orchestrates one simulated day across all apps deterministically, with one LLM agent replacing the manufacturer's stub.

---

## Architecture

```
                    ┌─────────────────────────────────────┐
                    │           Turn Engine                │
                    │         turn_engine.py               │
                    │                                      │
                    │  1. Read scenario signal             │
                    │  2. Inject customer demand           │
                    │  3. Run agents (Claude / stub)       │
                    │  4. Advance all apps                 │
                    └──────┬──────────┬──────────┬─────────┘
                           │          │          │
              POST /api/orders  POST /api/day/advance (all)
                           │          │          │
               ┌───────────▼─┐  ┌─────▼──────┐  ┌▼────────────┐
               │  Retailer   │  │Manufacturer│  │  Provider   │
               │  :8003      │  │  :8002     │  │  :8001      │
               │             │  │            │  │             │
               │ customer    │  │ production │  │ parts       │
               │ orders      │  │ sales      │  │ catalog     │
               │ stock       │  │ inventory  │  │ stock       │
               └──────┬──────┘  └─────┬──────┘  └─────────────┘
                      │               │
           POST /api/purchases  POST /api/orders
           (buy printers)       (buy parts)
                      └───────────────┘
```

---

## Turn Engine Design

### Why a Turn Engine

When agents make decisions mid-turn and each app tracks its own clock, a human operator advancing apps one at a time does not scale. The turn engine acts as a **conductor**: it decides the order of operations and guarantees all apps advance in lock-step.

### Order of Operations

The ordering is downstream-first: actors closest to the end customer decide before upstream actors react.

1. **Signal** — read today's market signal from the scenario file
2. **Customer demand** — inject `N` customer orders at each retailer (`POST /api/orders`)
3. **Retailer turn** — agent/stub: fulfill from stock, place purchase orders if low
4. **Manufacturer turn** — Claude Code agent: review sales orders, release production, buy parts
5. **Provider turn** — stub: process shipments
6. **Advance all** — `POST /api/day/advance` on retailer, manufacturer, provider

### Agent Invocation

```python
result = subprocess.run(
    ["claude", "--print", "--prompt", prompt],
    capture_output=True, text=True, cwd=app_working_dir, timeout=180,
)
Path(f"logs/day-{day:03d}-{role}.log").write_text(result.stdout)
```

- `--print` runs Claude Code non-interactively
- Each turn is a fresh context (no state between days)
- Timeout 180s per role; on timeout, logs the error and continues
- Output stored to `logs/` (gitignored; next week's analysis will depend on it)

### Stub vs Agent

Phase 1 (this week): manufacturer uses Claude Code; retailer and provider use stubs.
Phase 2 (Week 8): all three roles upgraded to full agents.

---

## Retailer App Design

### Key Decisions

**Fulfill-or-backorder on receipt:** Customer orders are immediately evaluated against stock when they arrive via `POST /api/orders`. If stock is sufficient the order status is `fulfilled`; otherwise `backordered`. This avoids a separate manual fulfillment step for the common case.

**Polling pattern mirrors manufacturer→provider:** On each `advance_day`, the retailer polls the manufacturer for every open purchase order and updates its local status. When status becomes `delivered` it adds stock and auto-fulfills any backordered customer orders. This keeps the pattern consistent across the chain.

**Multi-instance via env vars:** The database URL and config path are read from `RETAILER_DATABASE_URL` and `RETAILER_CONFIG_PATH` at process startup. The `serve` command launches uvicorn as a subprocess with the correct env vars set, enabling multiple instances on different ports with different databases.

**Minimum price constraint:** Prices must stay above manufacturer wholesale + 15%. This is enforced by convention (skill file / agent constraint); the API does not reject violations to keep the system flexible for experimentation.

### Data Model

| Table | Purpose |
|-------|---------|
| `catalog` | Printer models with retail prices |
| `stock` | Current inventory (finished printers) |
| `customer_orders` | Orders from end customers |
| `purchase_orders` | Orders placed with manufacturer |
| `sales_history` | Fulfilled order audit log |
| `events` | Timestamped event log |
| `sim_state` | Key/value simulation state (current_day) |

---

## Manufacturer Additions (Week 7)

All changes are **purely additive** — no existing lines were modified.

### New tables
- `sales_orders` — inbound orders from retailers
- `finished_printer_stock` — output of manufacturing, input to sales
- `wholesale_prices` — price list for finished printer models

### New `/api/day/advance` endpoint

The existing `/simulation/advance` endpoint is preserved. The new endpoint adds sales-order processing on top:

```
POST /api/day/advance
  1. Count completed ManufacturingOrders (before)
  2. Call existing advance_day() → produces printers, increments day
  3. Count completed ManufacturingOrders (after)
  4. newly_produced = after − before
  5. advance_sales_orders(db, day, newly_produced)
     → adds newly produced printers to finished_printer_stock
     → ships pending sales orders from finished stock (oldest-first)
```

### Sales order lifecycle
```
pending  →  (released by agent)  →  shipped  →  delivered
```
Auto-fulfillment on day advance ships the oldest pending/released orders that have stock. The `production release` command lets the agent prioritize specific orders.

---

## Skill File Design: manufacturer-manager.md

The skill file is the **contract** between the designer and the LLM agent. Key design decisions:

1. **Explicit DO NOT section** — the most important constraint is "do not call `day advance`"; the turn engine owns that. Every skill must forbid it.

2. **Numbered decision framework** — the agent follows five steps in order: assess → fulfill → order → adjust prices → log reasoning. This prevents the agent from jumping straight to placing orders without checking state first.

3. **Named commands only** — the skill lists exact CLI commands with their exact syntax. Agents that can invent commands will, and will get them wrong.

4. **Forced summary** — the agent prints 3–5 bullet points before exiting. This makes logs readable and catches cases where the agent decided to do nothing.

5. **Market signal interpretation** — explicit rules for `demand_modifier > 1.5` (build ahead, raise prices) and `< 0.7` (avoid over-ordering, lower prices). Without this the agent ignores signals.

---

## Verification Checklist

- [x] All three apps start on their own ports and serve their APIs
- [x] Retailer CLI works for all core commands
- [x] Manufacturer accepts inbound retailer orders and processes them
- [x] Customer demand generator injects orders at retailers
- [x] Turn engine runs deterministic (stub) mode for 3 days without errors
- [x] One skill file exists (`skills/manufacturer-manager.md`)
- [ ] Turn engine runs with manufacturer-as-agent for at least 1 day
- [x] Agent output is captured and stored in `logs/`
- [x] JSON export/import works for all three apps
