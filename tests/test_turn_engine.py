"""Unit tests for the turn-engine pure functions."""
from __future__ import annotations

import json
from pathlib import Path

import turn_engine as te


def test_todays_signal_no_event_defaults_to_normal():
    scenario = {"events": [], "base_demand": {"mean": 7, "variance": 1}}
    sig = te.todays_signal(day=3, scenario=scenario)

    assert sig["day"] == 3
    assert sig["events"] == []
    assert sig["demand_modifier"] == 1.0
    assert sig["supply_modifier"] == 1.0
    assert sig["lead_time_modifier"] == 1.0
    assert sig["price_sensitivity"] == "normal"
    assert sig["base_demand"] == {"mean": 7, "variance": 1}


def test_todays_signal_single_active_event():
    scenario = {
        "events": [
            {
                "name": "spike",
                "start_day": 1, "end_day": 5,
                "demand_modifier": 2.0, "supply_modifier": 0.8,
                "lead_time_modifier": 1.5, "price_sensitivity": "high",
            }
        ]
    }
    sig = te.todays_signal(day=3, scenario=scenario)

    assert [e["name"] for e in sig["events"]] == ["spike"]
    assert sig["demand_modifier"] == 2.0
    assert sig["supply_modifier"] == 0.8
    assert sig["lead_time_modifier"] == 1.5
    assert sig["price_sensitivity"] == "high"


def test_todays_signal_overlapping_events_multiply_modifiers():
    """Two overlapping events compound (multiply), they do not overwrite."""
    scenario = {
        "events": [
            {
                "name": "chip_shortage",
                "start_day": 1, "end_day": 10,
                "demand_modifier": 1.5, "supply_modifier": 0.4,
                "lead_time_modifier": 2.0,
            },
            {
                "name": "christmas_rush",
                "start_day": 5, "end_day": 15,
                "demand_modifier": 2.5, "supply_modifier": 0.6,
                "price_sensitivity": "high",
            },
        ]
    }
    sig = te.todays_signal(day=7, scenario=scenario)

    names = [e["name"] for e in sig["events"]]
    assert names == ["chip_shortage", "christmas_rush"]
    # 1.5 * 2.5 = 3.75 demand; 0.4 * 0.6 = 0.24 supply; lead time still 2.0
    assert sig["demand_modifier"] == 3.75
    assert sig["supply_modifier"] == 0.24
    assert sig["lead_time_modifier"] == 2.0
    # Last event wins for categorical fields.
    assert sig["price_sensitivity"] == "high"


def test_todays_signal_event_outside_range_is_skipped():
    scenario = {
        "events": [
            {"name": "ended", "start_day": 1, "end_day": 2, "demand_modifier": 5.0},
        ]
    }
    sig = te.todays_signal(day=10, scenario=scenario)

    assert sig["events"] == []
    assert sig["demand_modifier"] == 1.0


def test_append_metrics_creates_valid_jsonl(tmp_path: Path):
    path = tmp_path / "out.jsonl"
    te.append_metrics({"day": 1, "x": 10}, str(path))
    te.append_metrics({"day": 2, "x": 11}, str(path))

    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"day": 1, "x": 10}
    assert json.loads(lines[1]) == {"day": 2, "x": 11}


def test_collect_metrics_missing_apps_returns_nulls():
    """When config has no providers/retailers, the sections are None."""
    metrics = te.collect_metrics(
        day=1, signal={"events": [], "demand_modifier": 1.0},
        config={"providers": [], "manufacturer": {}, "retailers": []},
    )
    assert metrics["day"] == 1
    assert metrics["provider"] is None
    assert metrics["retailer"] is None


def test_collect_metrics_persists_all_three_modifiers():
    """All three scenario modifiers must land in the JSONL so charts can
    later attribute stockouts to supply or lead-time events, not only to
    demand spikes."""
    signal = {
        "events": [{"name": "chip_shortage", "start_day": 1, "end_day": 5}],
        "demand_modifier": 1.5,
        "supply_modifier": 0.4,
        "lead_time_modifier": 2.0,
    }
    metrics = te.collect_metrics(
        day=3, signal=signal,
        config={"providers": [], "manufacturer": {}, "retailers": []},
    )

    assert metrics["scenario_event"] == "chip_shortage"
    assert metrics["demand_modifier"] == 1.5
    assert metrics["supply_modifier"] == 0.4
    assert metrics["lead_time_modifier"] == 2.0


def test_abs_cwd_resolves_relative_role_path_against_repo_root(tmp_path):
    """The role.path in sim.json is relative; the subprocess must still get
    an absolute cwd so the turn engine can be invoked from anywhere."""
    abs_path = te._abs_cwd("retailer")
    assert Path(abs_path).is_absolute()
    assert abs_path.endswith("retailer")

    # An absolute path passes through unchanged.
    already_abs = str(tmp_path)
    assert te._abs_cwd(already_abs) == already_abs


def test_run_state_tracks_per_run_counters():
    state = te.RunState()
    assert state.prev_fulfilled == 0
    assert state.prev_backordered == 0
    state.prev_fulfilled = 10
    state.prev_backordered = 3

    fresh = te.RunState()  # a separate run starts clean — no global leakage
    assert fresh.prev_fulfilled == 0
