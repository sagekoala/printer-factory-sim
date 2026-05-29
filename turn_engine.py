#!/usr/bin/env python3
"""Turn engine: orchestrate one simulated day across all apps.

Per-day order of operations (``run_day``)
-----------------------------------------
1. Compute today's market signal from the scenario file.
2. Generate auto customer orders at each retailer.
3. Run the retailer agent(s), then manufacturer, then provider(s).
4. Snapshot all three apps and append a JSON line to the metrics file.
5. Print a single-line day summary (event names, customer orders placed,
   fulfilled / backordered deltas, stockouts).
6. Advance retailers + manufacturer, then advance each provider with the
   scenario's ``lead_time_modifier`` so chip-shortage-style events extend
   delivery windows.

Agents are pluggable via the ``TURN_ENGINE_AGENT`` environment variable
(``claude`` | ``cursor`` | ``github``); a model can be overridden with
``TURN_ENGINE_MODEL``.

Run::

    python turn_engine.py <config.json> <scenario.json> <days> [run_name]

A timestamped ``<run_name>_<YYYYMMDD_HHMMSS>_metrics.jsonl`` is written
in the current working directory. Pass ``TURN_ENGINE_PAUSE=1`` to pause
between days (handy for live demos).
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

_TIMEOUT = 30.0
_METRICS_TIMEOUT = 5.0
_AGENT_TIMEOUT = 300
_DEMAND_TIMEOUT = 8.0

_REPO_ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Config / scenario
# ---------------------------------------------------------------------------


def load_config(path: str) -> dict:
    return json.loads(Path(path).read_text())


def load_scenario(path: str) -> dict:
    return json.loads(Path(path).read_text())


def todays_signal(day: int, scenario: dict) -> dict:
    """Combine all currently-active scenario events into one signal.

    Overlapping events **multiply** their numeric modifiers (a chip
    shortage during a holiday rush compounds, it does not silently
    overwrite). ``price_sensitivity`` is categorical so we take the value
    of the last active event (defaulting to ``"normal"``).
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


# ---------------------------------------------------------------------------
# Customer demand injection
# ---------------------------------------------------------------------------


def generate_customer_orders(retailer_url: str, signal: dict) -> int:
    """POST auto-generated customer orders to ``retailer_url``.

    Returns the number of orders successfully placed (network errors are
    logged and skipped rather than aborting the turn).
    """
    try:
        catalog = httpx.get(f"{retailer_url}/api/catalog", timeout=_DEMAND_TIMEOUT).json()
    except Exception as exc:
        print(f"[WARN] Could not fetch retailer catalog from {retailer_url}: {exc}")
        return 0

    base = signal.get("base_demand", {"mean": 5, "variance": 2})
    modifier = signal.get("demand_modifier", 1.0)
    mean_orders = base["mean"] * modifier
    variance = base.get("variance", 2)

    placed = 0
    for item in catalog:
        model = item["model"]
        n = max(0, int(random.gauss(mean_orders, variance)))
        for _ in range(n):
            try:
                httpx.post(
                    f"{retailer_url}/api/orders",
                    json={"customer": "auto", "model": model, "quantity": 1},
                    timeout=_DEMAND_TIMEOUT,
                )
                placed += 1
            except Exception as exc:
                print(f"[WARN] Could not place customer order at {retailer_url}: {exc}")
    return placed


# ---------------------------------------------------------------------------
# Agent backends
# ---------------------------------------------------------------------------


def _claude_cmd() -> list[str]:
    """Return the correct invocation for the ``claude`` CLI on this platform.

    On Windows the CLI is installed as ``claude.CMD`` and ``subprocess``
    won't resolve the extension unless we wrap the call in ``cmd /c``.
    """
    full = shutil.which("claude")
    if full:
        if sys.platform == "win32" and full.lower().endswith((".cmd", ".bat")):
            return ["cmd", "/c", full]
        return [full]
    return ["claude"]


def _cursor_agent_cmd() -> list[str] | None:
    """Locate the Cursor CLI (binary is ``agent`` on some installs)."""
    for name in ("agent", "cursor-agent"):
        full = shutil.which(name)
        if full:
            return [full]
    return None


def _copilot_cmd() -> list[str] | None:
    full = shutil.which("copilot")
    return [full] if full else None


def _build_agent_invocation(prompt: str) -> tuple[list[str], str]:
    """Build the subprocess argv for the selected agent backend.

    Backends (selected via ``TURN_ENGINE_AGENT``):

    - ``"claude"`` (default) → ``claude --print --dangerously-skip-permissions <prompt>``
    - ``"cursor"`` → ``agent -p --force --trust <prompt>``
    - ``"github"`` (or ``"copilot"``) → ``copilot -p <prompt> --allow-all
      --add-dir <repo_root> --no-color`` (with optional ``--model`` from
      ``TURN_ENGINE_MODEL``)

    Raises :class:`FileNotFoundError` for a non-default backend whose CLI is
    missing — we never silently fall back to Claude (that would burn the
    wrong account).
    """
    backend = os.environ.get("TURN_ENGINE_AGENT", "claude").lower()

    if backend == "cursor":
        cur = _cursor_agent_cmd()
        if cur is None:
            raise FileNotFoundError(
                "TURN_ENGINE_AGENT=cursor but neither `agent` nor `cursor-agent` "
                "is on PATH. Install with: curl https://cursor.com/install -fsS | bash"
            )
        return cur + ["-p", "--force", "--trust", prompt], "cursor"

    if backend in ("github", "copilot", "github-copilot"):
        cop = _copilot_cmd()
        if cop is None:
            raise FileNotFoundError(
                "TURN_ENGINE_AGENT=github but `copilot` is not on PATH. "
                "Install with: npm install -g @github/copilot"
            )
        cmd = cop + [
            "-p", prompt,
            "--allow-all",
            "--add-dir", str(_REPO_ROOT),
            "--no-color",
        ]
        model = os.environ.get("TURN_ENGINE_MODEL")
        if model:
            cmd += ["--model", model]
        return cmd, "github"

    return _claude_cmd() + ["--print", "--dangerously-skip-permissions", prompt], "claude"


def _agent_env() -> dict[str, str]:
    """Build the subprocess env with ``.venv/bin`` prepended to PATH.

    The agent is run from inside ``<role>/`` so the parent ``manufacturer-cli``
    / ``retailer-cli`` / ``provider-cli`` entry points must be reachable; they
    live in ``<repo>/.venv/bin`` when the package is installed with ``pip
    install -e .``.
    """
    env = os.environ.copy()
    venv_bin = _REPO_ROOT / ".venv" / "bin"
    if venv_bin.exists():
        env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"
    return env


def run_agent_or_stub(role: str, skill_path: str | None, context: dict, cwd: str) -> None:
    """Execute the configured agent for ``role`` (or print a stub line)."""
    if skill_path is None:
        print(f"[stub] {role} would make decisions here")
        return

    day = context.get("day", 0)
    prompt = (
        f"You are acting as the {role} in a 3D printer supply chain simulation.\n\n"
        f"Read the skill file at {skill_path} to understand your role, available commands, "
        f"and decision framework.\n\n"
        f"Today is day {day}. Market context: {json.dumps(context)}\n\n"
        f"DAY-COUNTER NOTE: The turn engine advances each app's DB day counter at the END "
        f"of every turn. While you run, `<role>-cli day current` will report day {day - 1} "
        f"(yesterday's value), not {day}. This is expected — trust the day number above "
        f"({day}), not the CLI output. You can SKIP `day current` entirely; it adds no "
        f"useful information for today's decisions.\n\n"
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
    log_path = logs_dir / f"day-{day:03d}-{role}.log"

    try:
        cmd, backend = _build_agent_invocation(prompt)
    except FileNotFoundError as exc:
        message = f"[ERROR] {exc}"
        log_path.write_text(message)
        print(f"[{role}]\n{message}")
        return

    try:
        result = subprocess.run(
            cmd,
            input="",  # prevent stdin-wait warning from the claude CLI
            capture_output=True,
            text=True,
            cwd=cwd,
            env=_agent_env(),
            timeout=_AGENT_TIMEOUT,
        )
        output = result.stdout or result.stderr or ""
    except subprocess.TimeoutExpired:
        output = f"[TIMEOUT] {role} agent ({backend}) timed out after {_AGENT_TIMEOUT}s"
    except FileNotFoundError:
        output = f"[ERROR] {backend} CLI not found — stub mode"

    log_path.write_text(output)
    print(f"[{role} via {backend}]\n{output}")


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _safe_get(url: str) -> object | None:
    """GET ``url`` and return parsed JSON, or ``None`` on any error.

    Metric fetches must be best-effort: a downed app must not abort the
    day. The caller decides what to substitute when ``None`` comes back.
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

    stock = (
        {item["product_name"]: item["quantity"] for item in stock_resp}
        if isinstance(stock_resp, list) else None
    )

    prices: dict[str, float] | None
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

    parts_stock = (
        {item["name"]: item["current_stock"] for item in parts_resp}
        if isinstance(parts_resp, list) else None
    )
    finished_stock = (
        {item["model"]: item["quantity"] for item in stock_resp}
        if isinstance(stock_resp, list) else None
    )
    wholesale_price = (
        {item["model"]: item["price"] for item in prices_resp}
        if isinstance(prices_resp, list) else None
    )
    sales_pending = (
        sum(1 for o in orders_resp if o.get("status") in ("pending", "released"))
        if isinstance(orders_resp, list) else None
    )
    capacity_per_day = (
        capacity_resp.get("capacity_per_day", 10)
        if isinstance(capacity_resp, dict) else None
    )

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

    stock = (
        {item["model"]: item["quantity"] for item in stock_resp}
        if isinstance(stock_resp, list) else None
    )
    retail_price = (
        {item["model"]: item["retail_price"] for item in catalog_resp}
        if isinstance(catalog_resp, list) else None
    )
    orders_fulfilled = len(fulfilled_resp) if isinstance(fulfilled_resp, list) else None
    orders_backordered = len(backordered_resp) if isinstance(backordered_resp, list) else None
    stockouts = (
        sum(1 for item in stock_resp if item.get("quantity", 0) == 0)
        if isinstance(stock_resp, list) else None
    )

    return {
        "stock": stock,
        "retail_price": retail_price,
        "orders_fulfilled": orders_fulfilled,
        "orders_backordered": orders_backordered,
        "stockouts": stockouts,
    }


def collect_metrics(day: int, signal: dict, config: dict) -> dict:
    """Snapshot the state of every app at the end of a turn."""
    events = signal.get("events") or []
    scenario_event = events[0]["name"] if events else "normal"

    provider_url = config["providers"][0]["url"] if config.get("providers") else None
    manufacturer_url = config.get("manufacturer", {}).get("url")
    retailer_url = config["retailers"][0]["url"] if config.get("retailers") else None

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


# ---------------------------------------------------------------------------
# Day clock
# ---------------------------------------------------------------------------


@dataclass
class RunState:
    """Mutable per-run state that survives across days (without a global)."""

    prev_fulfilled: int = 0
    prev_backordered: int = 0
    metrics_path: str | None = None
    events: list[dict] = field(default_factory=list)


def _advance_one(url: str, body: dict | None = None) -> None:
    try:
        httpx.post(f"{url}/api/day/advance", json=body, timeout=_TIMEOUT)
    except Exception as exc:
        print(f"[WARN] Could not advance {url}: {exc}")


def advance_all(config: dict, lead_time_modifier: float = 1.0) -> None:
    """Advance retailers + manufacturer (no body) then providers (with body)."""
    for retailer in config["retailers"]:
        _advance_one(retailer["url"])
    _advance_one(config["manufacturer"]["url"])
    for provider in config["providers"]:
        _advance_one(provider["url"], body={"lead_time_modifier": lead_time_modifier})


def run_day(day: int, config: dict, scenario: dict, state: RunState) -> None:
    signal = todays_signal(day, scenario)
    print(f"\n{'=' * 60}\n DAY {day}   signal={signal}\n{'=' * 60}")

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
    if state.metrics_path is not None:
        append_metrics(metrics, state.metrics_path)

    retailer_m = metrics.get("retailer") or {}
    cum_fulfilled = int(retailer_m.get("orders_fulfilled") or 0)
    cum_backordered = int(retailer_m.get("orders_backordered") or 0)
    daily_fulfilled = cum_fulfilled - state.prev_fulfilled
    daily_backordered = cum_backordered - state.prev_backordered
    state.prev_fulfilled = cum_fulfilled
    state.prev_backordered = cum_backordered

    event_names = ", ".join(e["name"] for e in signal.get("events", [])) or "normal"
    stockouts = int(retailer_m.get("stockouts") or 0)
    print(
        f"\n--- Day {day} summary [{event_names}]: "
        f"{orders_placed} customer orders / "
        f"{daily_fulfilled} fulfilled / "
        f"{daily_backordered} backordered / "
        f"{stockouts} stockout(s) ---"
    )

    advance_all(config, signal.get("lead_time_modifier", 1.0))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) not in (3, 4):
        print(
            "Usage: python turn_engine.py <config.json> <scenario.json> <days> [run_name]"
        )
        return 1

    cfg = load_config(argv[0])
    scn = load_scenario(argv[1])
    num_days = int(argv[2])
    run_name = argv[3] if len(argv) >= 4 else "run"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    metrics_path = f"{run_name}_{ts}_metrics.jsonl"
    print(f"[turn_engine] writing metrics to {metrics_path}")

    pause_between_days = os.environ.get("TURN_ENGINE_PAUSE", "0") == "1"

    state = RunState(metrics_path=metrics_path)
    for day in range(1, num_days + 1):
        run_day(day, cfg, scn, state)
        if pause_between_days and day < num_days:
            try:
                input(
                    f"\n[pause] Día {day} completo. "
                    f"Pulsa Enter para continuar al día {day + 1}… "
                )
            except EOFError:
                pause_between_days = False
    return 0


if __name__ == "__main__":
    sys.exit(main())
