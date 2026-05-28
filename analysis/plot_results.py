#!/usr/bin/env python3
"""Render charts and a summary from a turn-engine ``metrics.jsonl`` file.

Usage:
    python analysis/plot_results.py <metrics.jsonl> <output_dir/> [--scenario <scenario.json>]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless: must come before pyplot import
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

_FIGSIZE = (12, 5)
_DPI = 150


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_metrics(path: Path) -> list[dict]:
    """Parse a JSONL file, skipping blank lines and unparseable rows."""
    rows: list[dict] = []
    with path.open() as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"[WARN] {path}:{lineno} skipped — {exc}", file=sys.stderr)
    rows.sort(key=lambda r: r.get("day", 0))
    return rows


def load_scenario(path: Path) -> dict:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Safe accessors
#
# Every metrics line can have nulls at any depth (an app may have been down
# when the snapshot was taken), so all helpers return a typed neutral value
# instead of propagating ``None``.
# ---------------------------------------------------------------------------

def _section(row: dict, name: str) -> dict:
    section = row.get(name)
    return section if isinstance(section, dict) else {}


def _dict_field(section: dict, key: str) -> dict:
    value = section.get(key)
    return value if isinstance(value, dict) else {}


def _sum_dict(section: dict, key: str) -> int:
    """Sum the numeric values of ``section[key]``; 0 if missing/None/non-dict."""
    d = _dict_field(section, key)
    return sum(v for v in d.values() if isinstance(v, (int, float)))


def _first_value(section: dict, key: str) -> float | None:
    """Return the first numeric value in ``section[key]`` (insertion order)."""
    d = _dict_field(section, key)
    for v in d.values():
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _scalar(section: dict, key: str) -> float:
    """Read a scalar numeric field, treating null/missing as 0."""
    v = section.get(key)
    return float(v) if isinstance(v, (int, float)) else 0.0


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def chart_inventory(rows: list[dict], out: Path) -> None:
    days = [r["day"] for r in rows]
    manu_parts = [_sum_dict(_section(r, "manufacturer"), "parts_stock") for r in rows]
    manu_fin = [_sum_dict(_section(r, "manufacturer"), "finished_stock") for r in rows]
    retail = [_sum_dict(_section(r, "retailer"), "stock") for r in rows]

    fig, ax = plt.subplots(figsize=_FIGSIZE)
    ax.plot(days, manu_parts, marker="o", label="Manufacturer parts")
    ax.plot(days, manu_fin, marker="s", label="Manufacturer finished")
    ax.plot(days, retail, marker="^", label="Retailer stock")
    ax.set_title("Inventory over time")
    ax.set_xlabel("Simulation Day")
    ax.set_ylabel("Units")
    ax.grid(True, alpha=0.4)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=_DPI)
    plt.close(fig)


def chart_prices(rows: list[dict], out: Path) -> None:
    days = [r["day"] for r in rows]

    def provider_pcb(r: dict) -> float | None:
        prices = _dict_field(_section(r, "provider"), "prices")
        if "PCB" in prices and isinstance(prices["PCB"], (int, float)):
            return float(prices["PCB"])
        return _first_value(_section(r, "provider"), "prices")

    provider_line = [provider_pcb(r) for r in rows]
    wholesale_line = [_first_value(_section(r, "manufacturer"), "wholesale_price") for r in rows]
    retail_line = [_first_value(_section(r, "retailer"), "retail_price") for r in rows]

    fig, ax = plt.subplots(figsize=_FIGSIZE)
    # Matplotlib drops None silently when it appears in the y-array, which is
    # exactly what we want for days with no data for a given series.
    ax.plot(days, provider_line, marker="o", label="Provider (PCB)")
    ax.plot(days, wholesale_line, marker="s", label="Manufacturer wholesale")
    ax.plot(days, retail_line, marker="^", label="Retailer retail")
    ax.set_title("Prices over time")
    ax.set_xlabel("Simulation Day")
    ax.set_ylabel("EUR")
    ax.grid(True, alpha=0.4)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=_DPI)
    plt.close(fig)


def chart_fulfillment(rows: list[dict], out: Path) -> None:
    days = [r["day"] for r in rows]
    cum_fulfilled = [_scalar(_section(r, "retailer"), "orders_fulfilled") for r in rows]
    cum_backordered = [_scalar(_section(r, "retailer"), "orders_backordered") for r in rows]

    # Convert cumulative totals to per-day deltas.
    def _deltas(series: list[float]) -> list[float]:
        return [series[0]] + [max(0.0, series[i] - series[i - 1]) for i in range(1, len(series))]

    fulfilled = _deltas(cum_fulfilled)
    backordered = _deltas(cum_backordered)

    width = 0.4
    x = [d - width / 2 for d in days]
    x2 = [d + width / 2 for d in days]

    fig, ax = plt.subplots(figsize=_FIGSIZE)
    ax.bar(x, fulfilled, width=width, color="#22c55e", label="Fulfilled")
    ax.bar(x2, backordered, width=width, color="#ef4444", label="Backordered")
    ax.set_title("Customer order fulfillment")
    ax.set_xlabel("Simulation Day")
    ax.set_ylabel("Orders")
    ax.grid(True, axis="y", alpha=0.4)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=_DPI)
    plt.close(fig)


def chart_events(rows: list[dict], scenario: dict, out: Path) -> None:
    events = scenario.get("events", []) or []
    if not events:
        print("[INFO] scenario has no events — skipping events chart")
        return

    # One y-row per unique event name, in the order they appear.
    names: list[str] = []
    for ev in events:
        n = ev.get("name", "event")
        if n not in names:
            names.append(n)
    y_for = {n: i for i, n in enumerate(names)}

    palette = plt.get_cmap("tab10").colors
    color_for = {n: palette[i % len(palette)] for i, n in enumerate(names)}

    day_min = min((r["day"] for r in rows), default=1)
    day_max = max((r["day"] for r in rows), default=1)

    fig, ax = plt.subplots(figsize=_FIGSIZE)
    bar_h = 0.7
    for ev in events:
        name = ev.get("name", "event")
        start = ev.get("start_day", day_min)
        end = ev.get("end_day", start)
        y = y_for[name]
        width = max(1, end - start + 1)
        ax.barh(
            y,
            width,
            left=start - 0.5,
            height=bar_h,
            color=color_for[name],
            edgecolor="black",
            alpha=0.7,
        )
        label = name
        desc = ev.get("description")
        if desc:
            label = f"{name} — {desc}"
        ax.text(
            start - 0.5 + width / 2,
            y,
            label,
            ha="center",
            va="center",
            fontsize=9,
            color="black",
        )

    ax.set_yticks(list(y_for.values()))
    ax.set_yticklabels(list(y_for.keys()))
    ax.set_xlim(day_min - 0.5, day_max + 0.5)
    ax.set_xlabel("Simulation Day")
    ax.set_title("Scenario events")
    ax.invert_yaxis()  # first event on top reads more naturally
    # Legend mirrors the colour bands so the chart is still readable if labels overlap.
    handles = [Patch(facecolor=color_for[n], edgecolor="black", label=n, alpha=0.7) for n in names]
    ax.legend(handles=handles, loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=_DPI)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def write_summary(rows: list[dict], out: Path) -> None:
    days = [r["day"] for r in rows]
    cum_fulfilled = [_scalar(_section(r, "retailer"), "orders_fulfilled") for r in rows]
    cum_backordered = [_scalar(_section(r, "retailer"), "orders_backordered") for r in rows]
    parts_totals = [_sum_dict(_section(r, "manufacturer"), "parts_stock") for r in rows]
    retail_totals = [_sum_dict(_section(r, "retailer"), "stock") for r in rows]

    # Cumulative totals at end of run give true total orders placed.
    total_fulfilled = int(cum_fulfilled[-1]) if cum_fulfilled else 0
    total_backordered = int(cum_backordered[-1]) if cum_backordered else 0
    fulfilled = cum_fulfilled
    backordered = cum_backordered
    total_orders = total_fulfilled + total_backordered
    backorder_rate = (total_backordered / total_orders * 100.0) if total_orders else 0.0

    if parts_totals:
        peak_parts = max(parts_totals)
        peak_parts_day = days[parts_totals.index(peak_parts)]
    else:
        peak_parts, peak_parts_day = 0, 0

    if retail_totals:
        low_retail = min(retail_totals)
        low_retail_day = days[retail_totals.index(low_retail)]
    else:
        low_retail, low_retail_day = 0, 0

    days_with_backorders = sum(1 for b in backordered if b > 0)

    lines = [
        f"Total days simulated: {len(rows)}",
        f"Total fulfilled orders: {total_fulfilled}",
        f"Total backordered orders: {total_backordered}",
        f"Backorder rate: {backorder_rate:.1f}%",
        f"Peak manufacturer parts stock: {int(peak_parts)} (day {peak_parts_day})",
        f"Lowest retailer stock: {int(low_retail)} (day {low_retail_day})",
        f"Days with backordered > 0: {days_with_backorders}",
    ]
    out.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metrics", type=Path, help="metrics.jsonl produced by turn_engine.py")
    parser.add_argument("output_dir", type=Path, help="directory to write PNGs and summary.txt into")
    parser.add_argument(
        "--scenario",
        type=Path,
        default=None,
        help="optional scenario.json — enables the events chart",
    )
    args = parser.parse_args(argv)

    if not args.metrics.exists():
        print(f"[ERROR] metrics file not found: {args.metrics}", file=sys.stderr)
        return 1

    rows = load_metrics(args.metrics)
    if len(rows) < 2:
        print(
            f"[WARN] {args.metrics} has only {len(rows)} valid row(s); need at least 2. Exiting cleanly.",
            file=sys.stderr,
        )
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)

    chart_inventory(rows, args.output_dir / "inventory.png")
    chart_prices(rows, args.output_dir / "prices.png")
    chart_fulfillment(rows, args.output_dir / "fulfillment.png")

    if args.scenario is not None:
        if not args.scenario.exists():
            print(f"[WARN] scenario file not found: {args.scenario} — skipping events chart", file=sys.stderr)
        else:
            chart_events(rows, load_scenario(args.scenario), args.output_dir / "events.png")

    write_summary(rows, args.output_dir / "summary.txt")
    print(f"[OK] wrote charts and summary to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
