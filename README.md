# 3D Printer Production Simulator (Week 6)

Two applications run together:

- **Provider** (`provider/`) on `:8001` — supplier catalog, stock, provider-side order lifecycle.
- **Manufacturer** (`manufacturer/`) on `:8002` — factory simulation, outbound purchases to provider, local inventory/production.

---

## Prerequisites

- Python `3.10+`
- `pip`

Install dependencies from repo root:

```bash
cd printer-factory-sim
pip install -e .
pip install -r manufacturer/requirements.txt
pip install -r provider/requirements.txt
```

Optional clean start (remove local SQLite DBs):

```bash
rm -f printer_factory.db provider/provider.db
```

---

## Start both apps

### Terminal 1 — Provider API (`:8001`)

```bash
uvicorn provider.api:app --host 0.0.0.0 --port 8001 --reload
```

- Swagger: `http://localhost:8001/docs`

### Terminal 2 — Manufacturer API (`:8002`)

```bash
uvicorn manufacturer.main:app --host 0.0.0.0 --port 8002 --reload
```

- Swagger: `http://localhost:8002/docs`

### Optional Terminal 3 — Manufacturer dashboard

```bash
streamlit run manufacturer/dashboard.py
```

---

## 5-day manual scenario (CLI, exact commands)

Run from repo root.

### 0) Baseline checks

```bash
provider-cli day current
python -m manufacturer.cli day current
python -m manufacturer.cli suppliers list
python -m manufacturer.cli suppliers catalog "ChipSupply Co"
```

### 1) Place outbound purchase from manufacturer to provider

```bash
python -m manufacturer.cli purchase create --supplier "ChipSupply Co" --product p-0001 --qty 12
python -m manufacturer.cli purchase list
```

### 2) Advance both simulations day-by-day for 5 days

```bash
provider-cli day advance
python -m manufacturer.cli day advance

provider-cli day advance
python -m manufacturer.cli day advance

provider-cli day advance
python -m manufacturer.cli day advance

provider-cli day advance
python -m manufacturer.cli day advance

provider-cli day advance
python -m manufacturer.cli day advance
```

### 3) Verify delivery sync into manufacturer

```bash
python -m manufacturer.cli purchase list
python -m manufacturer.cli stock
```

Expected outcome: outbound purchase status becomes `delivered` after provider lead time, and manufacturer inventory increases for the delivered part.

---

## Key CLI commands

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

## Integration notes

- Manufacturer provider config: `manufacturer/provider_config.json`
- Provider seed data: `provider/seed-provider.json`
- Issue coverage:
      - #14, #15: Provider app
      - #16: Manufacturer ↔ Provider wiring
      - #6: Week 6 docs/config cleanup
