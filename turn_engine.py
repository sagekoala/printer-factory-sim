#!/usr/bin/env python3
"""Turn engine: orchestrates one simulated day across all apps."""
from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

import httpx

_TIMEOUT = 30.0


def load_config(path: str) -> dict:
    return json.loads(Path(path).read_text())


def load_scenario(path: str) -> dict:
    return json.loads(Path(path).read_text())


def todays_signal(day: int, scenario: dict) -> dict:
    signal: dict = {"day": day, "events": []}
    for event in scenario.get("events", []):
        if event["start_day"] <= day <= event["end_day"]:
            signal["events"].append(event)
            signal["demand_modifier"] = event.get("demand_modifier", 1.0)
    signal.setdefault("demand_modifier", 1.0)
    signal["base_demand"] = scenario.get("base_demand", {"mean": 5, "variance": 2})
    return signal


def generate_customer_orders(retailer_url: str, signal: dict) -> None:
    try:
        catalog = httpx.get(f"{retailer_url}/api/catalog", timeout=8.0).json()
    except Exception as exc:
        print(f"[WARN] Could not fetch retailer catalog from {retailer_url}: {exc}")
        return

    base = signal.get("base_demand", {"mean": 5, "variance": 2})
    modifier = signal.get("demand_modifier", 1.0)

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
            except Exception as exc:
                print(f"[WARN] Could not place customer order at {retailer_url}: {exc}")


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


def advance_all(urls: list[str]) -> None:
    for url in urls:
        try:
            httpx.post(f"{url}/api/day/advance", timeout=_TIMEOUT)
        except Exception as exc:
            print(f"[WARN] Could not advance {url}: {exc}")


def run_day(day: int, config: dict, scenario: dict) -> None:
    signal = todays_signal(day, scenario)
    print(f"\n{'='*60}\n DAY {day}   signal={signal}\n{'='*60}")

    for retailer in config["retailers"]:
        generate_customer_orders(retailer["url"], signal)

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

    all_urls = (
        [r["url"] for r in config["retailers"]]
        + [config["manufacturer"]["url"]]
        + [p["url"] for p in config["providers"]]
    )
    advance_all(all_urls)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python turn_engine.py <config.json> <scenario.json> <days>")
        sys.exit(1)

    cfg = load_config(sys.argv[1])
    scn = load_scenario(sys.argv[2])
    num_days = int(sys.argv[3])

    for day in range(1, num_days + 1):
        run_day(day, cfg, scn)
