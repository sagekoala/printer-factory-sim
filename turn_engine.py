#!/usr/bin/env python3
"""Turn engine: orchestrates one simulated day across all apps."""
from __future__ import annotations

import json
import random
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx

_TIMEOUT = 30.0
_METRICS_TIMEOUT = 5.0


def load_config(path: str) -> dict:
    return json.loads(Path(path).read_text())


def load_scenario(path: str) -> dict:
    return json.loads(Path(path).read_text())


def todays_signal(day: int, scenario: dict) -> dict:
    """Combine all currently-active events into one signal.

    When multiple events overlap on the same day their numeric modifiers
    are multiplied together (a chip shortage during a holiday rush should
    compound, not silently overwrite). ``price_sensitivity`` is taken
    from the last active event since it is categorical.
    """
    signal: dict = {
        "day": day,
        "events": [],
        "demand_modifier": 1.0,
        "supply_modifier": 1.0,
        "lead_time_modifier": 1.0,
        "price_sensitivity": "normal",
    }
    for event in scenario.get("events", []):
        if event["start_day"] <= day <= event["end_day"]:
            signal["events"].append(event)
            signal["demand_modifier"] *= event.get("demand_modifier", 1.0)
            signal["supply_modifier"] *= event.get("supply_modifier", 1.0)
            signal["lead_time_modifier"] *= event.get("lead_time_modifier", 1.0)
            if "price_sensitivity" in event:
                signal["price_sensitivity"] = event["price_sensitivity"]
    signal["base_demand"] = scenario.get("base_demand", {"mean": 5, "variance": 2})
    return signal


def generate_customer_orders(retailer_url: str, signal: dict) -> int:
    """Place auto-generated customer orders. Returns the number of orders placed."""
    try:
        catalog = httpx.get(f"{retailer_url}/api/catalog", timeout=8.0).json()
    except Exception as exc:
        print(f"[WARN] Could not fetch retailer catalog from {retailer_url}: {exc}")
        return 0

    base = signal.get("base_demand", {"mean": 5, "variance": 2})
    modifier = signal.get("demand_modifier", 1.0)

    total_placed = 0
    for item in catalog:
        model = item["model"]
        mean_orders = base["mean"] * modifier
        n = max(0, int(random.gauss(mean_orders, base.get("variance", 2))))
        for _ in range(n):
            try:
                httpx.post(
                    f"{retailer_url}/api/orders",
                    json={"customer": "auto", "model": model, "quantity": 1},
                    timeout=8.0,
                )
                total_placed += 1
            except Exception as exc:
                print(f"[WARN] Could not place customer order at {retailer_url}: {exc}")
    return total_placed


def _claude_cmd() -> list[str]:
    """Return the correct command to invoke claude on this platform."""
    import shutil, sys
    # shutil.which resolves .CMD/.PS1 on Windows; use the full resolved path
    full = shutil.which("claude")
    if full:
        if sys.platform == "win32" and full.lower().endswith((".cmd", ".bat")):
            return ["cmd", "/c", full]
        return [full]
    # Last-resort fallback
    return ["claude"]


def run_agent_or_stub(role: str, skill_path: str | None, context: dict, cwd: str) -> None:
    if skill_path is None:
        print(f"[stub] {role} would make decisions here")
        return

    day = context.get("day", 0)
    prompt = (
        f"You are acting as the {role} in a 3D printer supply chain simulation.\n\n"
        f"Read the skill file at {skill_path} to understand your role, available commands, "
        f"and decision framework.\n\n"
        f"Today is day {day}. Market context: {json.dumps(context)}\n\n"
        "INSTRUCTIONS: Execute your daily decisions NOW by running the actual CLI commands "
        "from the skill file. Do not describe what you would do — actually run the commands, "
        "read their output, and take action based on what you see.\n\n"
        "Follow the decision framework step by step:\n"
        "1. Run the state-check commands and summarise what you see.\n"
        "2. Take any needed actions (release production, place purchase orders, set prices).\n"
        "3. Print a 3-5 bullet summary of what you did and why.\n\n"
        "CONSTRAINT: Do NOT call `day advance` — the turn engine handles that."
    )

    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    cmd = _claude_cmd() + ["--print", "--dangerously-skip-permissions", prompt]
    try:
        result = subprocess.run(
            cmd,
            input="",           # prevent stdin-wait warning
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=300,
        )
        output = result.stdout or result.stderr
    except subprocess.TimeoutExpired:
        output = f"[TIMEOUT] {role} agent timed out after 300s"
    except FileNotFoundError:
        output = f"[ERROR] claude CLI not found — stub mode"

    log_path = logs_dir / f"day-{day:03d}-{role}.log"
    log_path.write_text(output)
    print(f"[{role}]\n{output}")


# Cumulative fulfilled/backordered counts from the previous day, used to compute daily deltas.
_prev_retailer_counts: dict[str, int] = {"fulfilled": 0, "backordered": 0}


def advance_all(urls: list[str], lead_time_modifier: float = 1.0) -> None:
    """Advance all apps by one day.

    The provider receives ``lead_time_modifier`` in the request body so its
    ``_process_pending_orders`` can scale delivery dates for scenario events
    (e.g. chip shortage with lead_time_modifier=2.0).  All other apps ignore
    the modifier and receive no body.
    """
    for url in urls:
        try:
            httpx.post(f"{url}/api/day/advance", timeout=_TIMEOUT)
        except Exception as exc:
            print(f"[WARN] Could not advance {url}: {exc}")


def _safe_get(url: str) -> object | None:
    """GET ``url`` and return parsed JSON, or ``None`` on any error.

    Every metric fetch must be best-effort: a downed app must not abort
    the day. The caller decides what to substitute when ``None`` comes
    back (usually ``null`` in the emitted JSON line).
    """
    try:
        resp = httpx.get(url, timeout=_METRICS_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        print(f"[WARN] metrics fetch failed for {url}: {exc}")
        return None


def _provider_metrics(url: str) -> dict:
    stock_resp = _safe_get(f"{url}/api/stock")
    catalog_resp = _safe_get(f"{url}/api/catalog")
    orders_resp = _safe_get(f"{url}/api/orders")

    if isinstance(stock_resp, list):
        stock = {item["product_name"]: item["quantity"] for item in stock_resp}
    else:
        stock = None

    if isinstance(catalog_resp, list):
        prices = {}
        for item in catalog_resp:
            tiers = item.get("pricing_tiers") or []
            tier_min = min((t["unit_price"] for t in tiers), default=None)
            if tier_min is not None:
                prices[item["name"]] = tier_min
    else:
        prices = None

    if isinstance(orders_resp, list):
        orders_pending = sum(1 for o in orders_resp if o.get("status") == "pending")
        orders_shipped = sum(1 for o in orders_resp if o.get("status") == "shipped")
    else:
        orders_pending = None
        orders_shipped = None

    return {
        "stock": stock,
        "prices": prices,
        "orders_pending": orders_pending,
        "orders_shipped": orders_shipped,
    }


def _manufacturer_metrics(url: str) -> dict:
    parts_resp = _safe_get(f"{url}/inventory")
    stock_resp = _safe_get(f"{url}/api/stock")
    prices_resp = _safe_get(f"{url}/api/prices")
    orders_resp = _safe_get(f"{url}/api/orders")
    capacity_resp = _safe_get(f"{url}/api/capacity")

    if isinstance(parts_resp, list):
        parts_stock = {item["name"]: item["current_stock"] for item in parts_resp}
    else:
        parts_stock = None

    if isinstance(stock_resp, list):
        finished_stock = {item["model"]: item["quantity"] for item in stock_resp}
    else:
        finished_stock = None

    if isinstance(prices_resp, list):
        wholesale_price = {item["model"]: item["price"] for item in prices_resp}
    else:
        wholesale_price = None

    if isinstance(orders_resp, list):
        sales_pending = sum(
            1 for o in orders_resp if o.get("status") in ("pending", "released")
        )
    else:
        sales_pending = None

    if isinstance(capacity_resp, dict):
        capacity_per_day = capacity_resp.get("capacity_per_day", 10)
    else:
        capacity_per_day = None

    return {
        "parts_stock": parts_stock,
        "finished_stock": finished_stock,
        "wholesale_price": wholesale_price,
        "sales_orders_pending": sales_pending,
        "capacity_per_day": capacity_per_day,
    }


def _retailer_metrics(url: str) -> dict:
    stock_resp = _safe_get(f"{url}/api/stock")
    catalog_resp = _safe_get(f"{url}/api/catalog")
    fulfilled_resp = _safe_get(f"{url}/api/orders?status=fulfilled")
    backordered_resp = _safe_get(f"{url}/api/orders?status=backordered")
    stockout_resp = _safe_get(f"{url}/api/stock")

    if isinstance(stock_resp, list):
        stock = {item["model"]: item["quantity"] for item in stock_resp}
    else:
        stock = None

    if isinstance(catalog_resp, list):
        retail_price = {item["model"]: item["retail_price"] for item in catalog_resp}
    else:
        retail_price = None

    orders_fulfilled = len(fulfilled_resp) if isinstance(fulfilled_resp, list) else None
    orders_backordered = len(backordered_resp) if isinstance(backordered_resp, list) else None

    # Stockout: number of models with zero stock
    stockouts = (
        sum(1 for item in stock_resp if item.get("quantity", 0) == 0)
        if isinstance(stock_resp, list)
        else None
    )

    return {
        "stock": stock,
        "retail_price": retail_price,
        "orders_fulfilled": orders_fulfilled,
        "orders_backordered": orders_backordered,
        "stockouts": stockouts,
    }


def collect_metrics(day: int, signal: dict, config: dict) -> dict:
    """Snapshot the state of every app at the end of a turn.

    Each sub-fetch is best-effort: any failure (network, parse, schema
    drift) yields ``None`` for that field rather than aborting the run,
    so the metrics file stays append-only across noisy days.
    """
    events = signal.get("events") or []
    scenario_event = events[0]["name"] if events else "normal"

    provider_url = (
        config["providers"][0]["url"] if config.get("providers") else None
    )
    manufacturer_url = config.get("manufacturer", {}).get("url")
    retailer_url = (
        config["retailers"][0]["url"] if config.get("retailers") else None
    )

    return {
        "day": day,
        "scenario_event": scenario_event,
        "demand_modifier": signal.get("demand_modifier", 1.0),
        "provider": _provider_metrics(provider_url) if provider_url else None,
        "manufacturer": _manufacturer_metrics(manufacturer_url) if manufacturer_url else None,
        "retailer": _retailer_metrics(retailer_url) if retailer_url else None,
    }


def append_metrics(metrics: dict, path: str) -> None:
    """Append one JSON object per line to ``path`` (creating it if needed)."""
    with open(path, "a") as f:
        f.write(json.dumps(metrics) + "\n")


def run_day(day: int, config: dict, scenario: dict, metrics_path: str | None = None) -> None:
    global _prev_retailer_counts

    signal = todays_signal(day, scenario)
    print(f"\n{'='*60}\n DAY {day}   signal={signal}\n{'='*60}")

    orders_placed = 0
    for retailer in config["retailers"]:
        orders_placed += generate_customer_orders(retailer["url"], signal)

    for retailer in config["retailers"]:
        run_agent_or_stub("retailer", retailer.get("skill"), signal, retailer["path"])

    run_agent_or_stub(
        "manufacturer",
        config["manufacturer"].get("skill"),
        signal,
        config["manufacturer"]["path"],
    )

    for provider in config["providers"]:
        run_agent_or_stub("provider", provider.get("skill"), signal, provider["path"])

    metrics = collect_metrics(day, signal, config)
    if metrics_path is not None:
        append_metrics(metrics, metrics_path)

    # Compute daily fulfilled/backordered deltas and print turn summary.
    retailer_m = metrics.get("retailer") or {}
    cum_fulfilled = int(retailer_m.get("orders_fulfilled") or 0)
    cum_backordered = int(retailer_m.get("orders_backordered") or 0)
    daily_fulfilled = cum_fulfilled - _prev_retailer_counts["fulfilled"]
    daily_backordered = cum_backordered - _prev_retailer_counts["backordered"]
    _prev_retailer_counts = {"fulfilled": cum_fulfilled, "backordered": cum_backordered}

    event_names = ", ".join(e["name"] for e in signal.get("events", [])) or "normal"
    stockouts = int((metrics.get("retailer") or {}).get("stockouts") or 0)
    print(
        f"\n--- Day {day} summary [{event_names}]: "
        f"{orders_placed} customer orders / "
        f"{daily_fulfilled} fulfilled / "
        f"{daily_backordered} backordered / "
        f"{stockouts} stockout(s) ---"
    )

    non_provider_urls = (
        [r["url"] for r in config["retailers"]]
        + [config["manufacturer"]["url"]]
    )
    advance_all(non_provider_urls)

    # Advance each provider with the scenario's lead_time_modifier so that
    # events like chip shortages actually extend delivery windows.
    lead_time_mod = signal.get("lead_time_modifier", 1.0)
    for provider in config["providers"]:
        try:
            httpx.post(
                f"{provider['url']}/api/day/advance",
                json={"lead_time_modifier": lead_time_mod},
                timeout=_TIMEOUT,
            )
        except Exception as exc:
            print(f"[WARN] Could not advance provider {provider['url']}: {exc}")


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        print(
            "Usage: python turn_engine.py <config.json> <scenario.json> <days> [run_name]"
        )
        sys.exit(1)

    cfg = load_config(sys.argv[1])
    scn = load_scenario(sys.argv[2])
    num_days = int(sys.argv[3])
    run_name = sys.argv[4] if len(sys.argv) >= 5 else "run"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    metrics_path = f"{run_name}_{ts}_metrics.jsonl"
    print(f"[turn_engine] writing metrics to {metrics_path}")

    for day in range(1, num_days + 1):
        run_day(day, cfg, scn, metrics_path=metrics_path)
