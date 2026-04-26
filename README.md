# 3D Printer Production Simulator (Week 6)

Two independent FastAPI + SQLite apps that talk to each other over HTTP.

| App | Path | Port | DB file |
|-----|------|------|---------|
| **Provider** (parts supplier) | `provider/` | `8001` | `provider/provider.db` |
| **Manufacturer** (factory) | `manufacturer/` | `8002` | `manufacturer/manufacturer.db` |

The provider exposes a catalog, stock, and an order lifecycle. The manufacturer
runs a discrete-event factory simulation, places outbound purchases against
the provider, and reconciles deliveries into local inventory.

---

## Prerequisites

- Python `3.10+`
- `pip`

Install dependencies (from repo root):

```bash
pip install -e .
pip install -r manufacturer/requirements.txt
pip install -r provider/requirements.txt
```

This installs both packages plus the `provider-cli` and `manufacturer-cli`
console scripts defined in `pyproject.toml`.

---

## First-time database setup

The provider seeds itself automatically on app startup, but you can also
populate it explicitly:

```bash
cd provider && python seed.py
```

This creates `provider/provider.db` (if missing) and loads catalog,
pricing tiers, and stock from `seed-provider.json`. The script is
idempotent — running it again is a no-op once products are present.

To seed the manufacturer:

```bash
python -m manufacturer.seed
```

This creates `manufacturer/manufacturer.db` and loads suppliers,
products, BOM, and factory config from `manufacturer/seed.json`.

To wipe and start over:

```bash
rm -f manufacturer/manufacturer.db provider/provider.db
```

---

## Running both apps

### Terminal 1 — Provider API on `:8001`

From the repo root:

```bash
uvicorn provider.api:app --host 0.0.0.0 --port 8001 --reload
```

Or via the CLI:

```bash
provider-cli serve --port 8001
```

Swagger UI: <http://localhost:8001/docs>

### Terminal 2 — Manufacturer API on `:8002`

From the repo root:

```bash
uvicorn manufacturer.main:app --host 0.0.0.0 --port 8002 --reload
```

Or, equivalently, from the manufacturer directory (matches the Week 6 spec):

```bash
cd manufacturer && uvicorn main:app --reload --port 8002
```

Swagger UI: <http://localhost:8002/docs>

### Optional — Manufacturer dashboard

```bash
streamlit run manufacturer/dashboard.py
```

---

## 5-day manual scenario (CLI)

Run from repo root.

### 0) Baseline checks

```bash
provider-cli day current
python -m manufacturer.cli day current
python -m manufacturer.cli suppliers list
python -m manufacturer.cli suppliers catalog "ChipSupply Co"
```

### 1) Place an outbound purchase

```bash
python -m manufacturer.cli purchase create --supplier "ChipSupply Co" --product p-0001 --qty 12
python -m manufacturer.cli purchase list
```

### 2) Advance both simulations 5 days

```bash
for _ in 1 2 3 4 5; do
  provider-cli day advance
  python -m manufacturer.cli day advance
done
```

### 3) Verify the delivery synced into manufacturer inventory

```bash
python -m manufacturer.cli purchase list
python -m manufacturer.cli stock
```

The outbound purchase should reach `delivered` after the provider lead
time elapses, and manufacturer stock for the delivered part should
increase.

---

## Useful CLI reference

### Provider

```bash
provider-cli catalog
provider-cli stock
provider-cli orders list --status pending
provider-cli orders show <id>
provider-cli day current
provider-cli day advance
provider-cli serve --port 8001
```

### Manufacturer

```bash
python -m manufacturer.cli stock
python -m manufacturer.cli orders list --status pending
python -m manufacturer.cli suppliers list
python -m manufacturer.cli suppliers catalog "ChipSupply Co"
python -m manufacturer.cli purchase create --supplier "ChipSupply Co" --product p-0001 --qty 5
python -m manufacturer.cli purchase list
python -m manufacturer.cli day current
python -m manufacturer.cli day advance
```

---

## Layout

```
manufacturer/
  main.py                  # FastAPI app (port 8002)
  cli.py                   # Typer CLI
  simulation.py            # advance_day() and demand/production logic
  database.py              # SQLAlchemy engine + ORM rows
  provider_integration.py  # outbound calls to provider:8001
  provider_config.json     # provider hosts/ports
  dashboard.py             # Streamlit UI
  models.py                # Pydantic models
  seed.py                  # `python -m manufacturer.seed`
  seed.json
  requirements.txt

provider/
  api.py                   # FastAPI app (port 8001)
  cli.py                   # Typer CLI
  db.py                    # SQLAlchemy engine + helpers
  models.py                # ORM rows + Pydantic schemas
  seed.py                  # `cd provider && python seed.py`
  seed-provider.json
  services/
    catalog.py             # get_catalog / set_price / get_stock / restock
    orders.py              # create_order / get_orders / get_order
    simulation.py          # advance_day / get_current_day
  requirements.txt
```

---

## Integration notes

- Manufacturer → Provider hosts: `manufacturer/provider_config.json`
- Provider seed data: `provider/seed-provider.json`
- DB locations are resolved relative to each app's source directory, so
  `cd manufacturer && uvicorn main:app` and `uvicorn manufacturer.main:app`
  from the repo root operate on the same `manufacturer/manufacturer.db`
  file. The same applies to provider.
- Override DB locations with `DATABASE_URL` (manufacturer) or
  `PROVIDER_DATABASE_URL` (provider) when needed — see
  `manufacturer/.env.example` and `provider/.env.example`.
