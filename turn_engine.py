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


def run_agent_or_stub(role: str, skill_path: str | None, context: dict, cwd: str) -> None:
    if skill_path is None:
        print(f"[stub] {role} would make decisions here")
        return

    day = context.get("day", 0)
    prompt = (
        f"Read the skill file at {skill_path}.\n"
        f"Today's context: {json.dumps(context)}\n"
        "Execute your daily decisions following the skill's decision framework.\n"
        "Do NOT advance the day - the turn engine does that.\n"
    )

    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    try:
        result = subprocess.run(
            ["claude", "--print", "--prompt", prompt],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=180,
        )
        output = result.stdout
    except subprocess.TimeoutExpired:
        output = f"[TIMEOUT] {role} agent timed out after 180s"
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
