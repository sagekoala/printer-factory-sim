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
from matplotlib.ticker import MaxNLocator

_DPI = 150


# ---------------------------------------------------------------------------
# Visual style
# ---------------------------------------------------------------------------
#
# We set rcParams once at import time so every figure shares the same look.
# Values picked to read cleanly on a projector AND in a Word/PDF report.
# Keep the colour palette here so individual chart functions don't have to
# hard-code hex values.

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 11,
    "axes.titlesize": 14,
    "axes.titleweight": "bold",
    "axes.titlepad": 14,
    "axes.labelsize": 11,
    "axes.labelweight": "regular",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.edgecolor": "#94a3b8",
    "axes.linewidth": 0.8,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.alpha": 0.35,
    "grid.linestyle": ":",
    "grid.color": "#cbd5e1",
    "legend.frameon": False,
    "legend.fontsize": 10,
    "xtick.color": "#475569",
    "ytick.color": "#475569",
    "figure.facecolor": "white",
    "axes.facecolor": "#fafbfc",
})

# Series colours — one per logical role, reused across charts so the audience
# learns "blue=parts, amber=manufacturer-finished, green=retailer" once.
COLORS = {
    "parts":         "#2563eb",  # blue 600
    "finished":      "#f59e0b",  # amber 500
    "retail":        "#10b981",  # emerald 500
    "fulfilled":     "#22c55e",  # green 500
    "backordered":   "#ef4444",  # red 500
    "provider":      "#0ea5e9",  # sky 500
    "wholesale":     "#f59e0b",  # amber 500
    "retail_price":  "#059669",  # emerald 600
}

# Background-shading colours for known scenario events. Anything not in the
# table falls back to a tab10 entry hashed from the name.
EVENT_PALETTE = {
    "normal":          "#cbd5e1",  # slate 300 — neutral
    "demand_spike":    "#fbbf24",  # amber 400
    "supply_shortage": "#f97316",  # orange 500
    "chip_shortage":   "#dc2626",  # red 600
    "christmas_rush":  "#a855f7",  # purple 500
    "recovery":        "#86efac",  # green 300
    "promotion":       "#38bdf8",  # sky 400
}


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


def _scalar_with_fallback(section: dict, primary_key: str, *fallbacks: str) -> float:
    """Read the first numeric scalar found in ``primary_key`` then ``*fallbacks``.

    Used to keep old metrics files (with ``orders_fulfilled`` /
    ``orders_backordered``) compatible after the schema rename to
    ``orders_fulfilled_total`` / ``orders_backordered_total``.
    """
    for key in (primary_key, *fallbacks):
        v = section.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    return 0.0


def _daily_deltas(cumulative: list[float]) -> list[float]:
    """Convert a monotonically-increasing cumulative series to daily diffs."""
    out: list[float] = []
    prev = 0.0
    for v in cumulative:
        out.append(max(0.0, v - prev))
        prev = v
    return out


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------

def _event_color(name: str) -> tuple:
    if name in EVENT_PALETTE:
        return EVENT_PALETTE[name]
    palette = plt.get_cmap("tab10").colors
    return palette[abs(hash(name)) % len(palette)]


def _shade_events(ax, events: list[dict] | None, alpha: float = 0.13) -> None:
    """Tint background spans on ``ax`` for each non-trivial scenario event.

    ``normal`` is intentionally skipped — shading the baseline adds noise
    without information. Overlapping events are stacked translucently so the
    reader still sees both.
    """
    if not events:
        return
    for ev in events:
        name = ev.get("name", "event")
        if name == "normal":
            continue
        start = ev.get("start_day", 1)
        end = ev.get("end_day", start)
        ax.axvspan(start - 0.5, end + 0.5, color=_event_color(name), alpha=alpha, zorder=0)


def _integer_day_axis(ax, days: list[int]) -> None:
    """Force integer-only ticks on the x-axis bounded to the simulation range."""
    if not days:
        return
    ax.set_xlim(min(days) - 0.5, max(days) + 0.5)
    if len(days) <= 30:
        ax.set_xticks(days)
    else:
        ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=15))


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def chart_inventory(rows: list[dict], out: Path, events: list[dict] | None = None) -> None:
    """Two-panel inventory chart.

    Manufacturer raw-parts stock typically sits two orders of magnitude
    above finished-printer stock (parts = hundreds, printers = tens). Sharing
    a single y-axis crushes both finished series flat against zero, so we
    split into stacked panels with shared x. Parts on top, finished + retail
    on bottom.
    """
    days = [r["day"] for r in rows]
    manu_parts = [_sum_dict(_section(r, "manufacturer"), "parts_stock") for r in rows]
    manu_fin = [_sum_dict(_section(r, "manufacturer"), "finished_stock") for r in rows]
    retail = [_sum_dict(_section(r, "retailer"), "stock") for r in rows]

    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(12, 7.5), sharex=True,
        gridspec_kw={"height_ratios": [1, 1], "hspace": 0.18},
    )

    ax_top.plot(days, manu_parts, marker="o", color=COLORS["parts"],
                linewidth=2.2, markersize=6, label="Manufacturer parts")
    ax_top.fill_between(days, 0, manu_parts, color=COLORS["parts"], alpha=0.12)
    ax_top.set_ylabel("Raw parts (units)")
    ax_top.set_title("Inventory over time")
    ax_top.set_ylim(bottom=0)

    ax_bot.plot(days, manu_fin, marker="s", color=COLORS["finished"],
                linewidth=2.2, markersize=6, label="Manufacturer finished")
    ax_bot.plot(days, retail, marker="^", color=COLORS["retail"],
                linewidth=2.2, markersize=6, label="Retailer stock")
    ax_bot.set_ylabel("Finished printers (units)")
    ax_bot.set_xlabel("Simulation day")
    # Avoid the 0–0.05 nonsense matplotlib picks when every value is zero.
    finished_max = max(max(manu_fin, default=0), max(retail, default=0))
    ax_bot.set_ylim(0, max(10, finished_max * 1.15))

    _shade_events(ax_top, events)
    _shade_events(ax_bot, events)

    ax_top.legend(loc="upper right")
    ax_bot.legend(loc="upper right")

    _integer_day_axis(ax_bot, days)

    fig.savefig(out, dpi=_DPI, bbox_inches="tight")
    plt.close(fig)


def chart_prices(rows: list[dict], out: Path, events: list[dict] | None = None) -> None:
    """Two-panel price chart.

    Provider component prices live in tens of euros while wholesale/retail
    printer prices live in thousands. A single axis collapses the provider
    line onto the x-axis. Stacked panels keep both series readable.
    """
    days = [r["day"] for r in rows]

    def provider_pcb(r: dict) -> float | None:
        prices = _dict_field(_section(r, "provider"), "prices")
        if "PCB" in prices and isinstance(prices["PCB"], (int, float)):
            return float(prices["PCB"])
        return _first_value(_section(r, "provider"), "prices")

    provider_line = [provider_pcb(r) for r in rows]
    wholesale_line = [_first_value(_section(r, "manufacturer"), "wholesale_price") for r in rows]
    retail_line = [_first_value(_section(r, "retailer"), "retail_price") for r in rows]

    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(12, 7.5), sharex=True,
        gridspec_kw={"height_ratios": [1, 1.4], "hspace": 0.18},
    )

    ax_top.plot(days, provider_line, marker="o", color=COLORS["provider"],
                linewidth=2.2, markersize=6, label="Provider component (PCB)")
    ax_top.set_ylabel("EUR / part")
    ax_top.set_title("Prices over time")

    ax_bot.plot(days, wholesale_line, marker="s", color=COLORS["wholesale"],
                linewidth=2.2, markersize=6, label="Manufacturer wholesale")
    ax_bot.plot(days, retail_line, marker="^", color=COLORS["retail_price"],
                linewidth=2.2, markersize=6, label="Retailer retail")
    ax_bot.set_ylabel("EUR / printer")
    ax_bot.set_xlabel("Simulation day")

    _shade_events(ax_top, events)
    _shade_events(ax_bot, events)

    ax_top.legend(loc="upper right")
    ax_bot.legend(loc="upper right")

    _integer_day_axis(ax_bot, days)

    fig.savefig(out, dpi=_DPI, bbox_inches="tight")
    plt.close(fig)


def chart_fulfillment(rows: list[dict], out: Path, events: list[dict] | None = None) -> None:
    """Daily fulfilled vs. backordered grouped bars.

    The retailer metric exposes ``orders_fulfilled_total`` /
    ``orders_backordered_total`` as cumulative counters since the start of
    the run. Plotting them raw produces an uninformative staircase, so we
    diff them to recover the daily increment.
    """
    days = [r["day"] for r in rows]
    fulfilled_cum = [
        _scalar_with_fallback(_section(r, "retailer"), "orders_fulfilled_total", "orders_fulfilled")
        for r in rows
    ]
    backordered_cum = [
        _scalar_with_fallback(_section(r, "retailer"), "orders_backordered_total", "orders_backordered")
        for r in rows
    ]

    fulfilled = _daily_deltas(fulfilled_cum)
    backordered = _daily_deltas(backordered_cum)

    width = 0.4
    x_left = [d - width / 2 for d in days]
    x_right = [d + width / 2 for d in days]

    fig, ax = plt.subplots(figsize=(12, 5.5))

    _shade_events(ax, events)

    bars_f = ax.bar(x_left, fulfilled, width=width, color=COLORS["fulfilled"],
                    label="Fulfilled", edgecolor="white", linewidth=1, zorder=3)
    bars_b = ax.bar(x_right, backordered, width=width, color=COLORS["backordered"],
                    label="Backordered", edgecolor="white", linewidth=1, zorder=3)

    # Value labels on top of each bar (skip zeros to avoid noise).
    y_max = max(max(fulfilled, default=0), max(backordered, default=0), 1)
    label_offset = y_max * 0.02
    for bars in (bars_f, bars_b):
        for bar in bars:
            h = bar.get_height()
            if h > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    h + label_offset,
                    f"{int(h)}",
                    ha="center", va="bottom", fontsize=9, color="#334155",
                )

    ax.set_title("Customer order fulfillment (daily)")
    ax.set_xlabel("Simulation day")
    ax.set_ylabel("Orders")
    ax.set_ylim(0, y_max * 1.18)
    ax.grid(True, axis="y", alpha=0.35, linestyle=":")
    ax.grid(False, axis="x")
    ax.legend(loc="upper left")

    _integer_day_axis(ax, days)

    fig.savefig(out, dpi=_DPI, bbox_inches="tight")
    plt.close(fig)


def chart_events(rows: list[dict], scenario: dict, out: Path) -> None:
    """Gantt-style scenario timeline.

    Earlier versions printed the full description **inside** each bar, which
    overflowed for short events (1-day spikes) and collided with the legend.
    Now the bar shows just the event name (large, white, bold) and the full
    description goes into a footer-style legend below the axes — never
    overlapping the data.
    """
    events = scenario.get("events", []) or []
    if not events:
        print("[INFO] scenario has no events — skipping events chart")
        return

    names: list[str] = []
    descriptions: dict[str, str] = {}
    for ev in events:
        n = ev.get("name", "event")
        if n not in names:
            names.append(n)
            descriptions[n] = ev.get("description", "") or ""

    y_for = {n: i for i, n in enumerate(names)}
    color_for = {n: _event_color(n) for n in names}

    # X range: span the full scenario, not just the days present in the
    # metrics file. Otherwise an aborted run produces orphaned event labels
    # outside the visible axes.
    metric_days = [r.get("day", 0) for r in rows if isinstance(r.get("day"), (int, float))]
    event_days = [
        d for ev in events for d in (ev.get("start_day", 1), ev.get("end_day", 1))
        if isinstance(d, (int, float))
    ]
    day_min = min(metric_days + event_days) if (metric_days or event_days) else 1
    day_max = max(metric_days + event_days) if (metric_days or event_days) else 1

    height = max(2.6, 0.55 * len(names) + 1.6)
    fig, ax = plt.subplots(figsize=(12, height))

    bar_h = 0.65
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
            edgecolor="white",
            linewidth=1.5,
            alpha=0.92,
        )
        # Inside the bar: just the name (always fits because it's short).
        ax.text(
            start - 0.5 + width / 2,
            y,
            name,
            ha="center", va="center",
            fontsize=10, fontweight="bold", color="white",
        )
        # Day-range tag floating above the bar — useful when several bars
        # share a row.
        ax.text(
            start - 0.5 + width / 2,
            y - bar_h / 2 - 0.12,
            f"d{start}" + (f"–d{end}" if end != start else ""),
            ha="center", va="top",
            fontsize=8, color="#475569",
        )

    ax.set_yticks(list(y_for.values()))
    ax.set_yticklabels(list(y_for.keys()))
    ax.set_xlim(day_min - 0.5, day_max + 0.5)
    ax.set_ylim(-0.8, len(names) - 0.2)
    ax.set_xlabel("Simulation day")
    ax.set_title("Scenario events")
    ax.invert_yaxis()  # first event on top reads more naturally
    ax.grid(True, axis="x", linestyle=":", alpha=0.35)
    ax.grid(False, axis="y")
    if max(2, day_max - day_min + 1) <= 30:
        ax.set_xticks(range(day_min, day_max + 1))
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", which="both", left=False)

    # Legend: only when at least one event ships a non-empty description.
    legend_handles = [
        Patch(facecolor=color_for[n], edgecolor="white",
              label=f"{n} — {descriptions[n]}" if descriptions[n] else n)
        for n in names
    ]
    if any(descriptions[n] for n in names):
        ax.legend(
            handles=legend_handles,
            loc="upper center",
            bbox_to_anchor=(0.5, -0.22),
            ncol=1,
            fontsize=9,
            frameon=False,
        )

    fig.savefig(out, dpi=_DPI, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def write_summary(rows: list[dict], out: Path) -> None:
    days = [r["day"] for r in rows]
    fulfilled = [
        _scalar_with_fallback(_section(r, "retailer"), "orders_fulfilled_total", "orders_fulfilled")
        for r in rows
    ]
    backordered = [
        _scalar_with_fallback(_section(r, "retailer"), "orders_backordered_total", "orders_backordered")
        for r in rows
    ]
    parts_totals = [_sum_dict(_section(r, "manufacturer"), "parts_stock") for r in rows]
    retail_totals = [_sum_dict(_section(r, "retailer"), "stock") for r in rows]

    total_fulfilled = int(max(fulfilled) if fulfilled else 0)
    total_backordered = int(max(backordered) if backordered else 0)
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

    daily_backorders = _daily_deltas(backordered)
    days_with_backorders = sum(1 for b in daily_backorders if b > 0)

    lines = [
        f"Total days simulated: {len(rows)}",
        f"Total fulfilled orders: {total_fulfilled}",
        f"Total backordered orders: {total_backordered}",
        f"Backorder rate: {backorder_rate:.1f}%",
        f"Peak manufacturer parts stock: {int(peak_parts)} (day {peak_parts_day})",
        f"Lowest retailer stock: {int(low_retail)} (day {low_retail_day})",
        f"Days with new backorders: {days_with_backorders}",
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
        help="optional scenario.json — enables event background shading and the events chart",
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

    scenario: dict | None = None
    events: list[dict] | None = None
    if args.scenario is not None:
        if not args.scenario.exists():
            print(f"[WARN] scenario file not found: {args.scenario} — skipping event shading", file=sys.stderr)
        else:
            scenario = load_scenario(args.scenario)
            events = scenario.get("events") or []

    chart_inventory(rows, args.output_dir / "inventory.png", events=events)
    chart_prices(rows, args.output_dir / "prices.png", events=events)
    chart_fulfillment(rows, args.output_dir / "fulfillment.png", events=events)

    if scenario is not None:
        chart_events(rows, scenario, args.output_dir / "events.png")

    write_summary(rows, args.output_dir / "summary.txt")
    print(f"[OK] wrote charts and summary to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
