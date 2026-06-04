"""Tests for the analysis/plot_results helpers."""
from __future__ import annotations

import json
from pathlib import Path


def test_load_metrics_handles_blank_lines_and_bad_json(tmp_path: Path):
    from analysis.plot_results import load_metrics

    path = tmp_path / "m.jsonl"
    path.write_text(
        '{"day": 2, "v": 2}\n'
        "\n"                                # blank
        '{"day": 1, "v": 1}\n'
        "not-json-at-all\n"                 # malformed
        '{"day": 3, "v": 3}\n'
    )

    rows = load_metrics(path)
    # All three valid rows survive, blank skipped, malformed reported.
    assert [r["day"] for r in rows] == [1, 2, 3]  # sorted by day


def test_daily_deltas_clamps_negative_drops_to_zero():
    from analysis.plot_results import _daily_deltas

    assert _daily_deltas([0.0, 5.0, 7.0, 7.0, 4.0]) == [0.0, 5.0, 2.0, 0.0, 0.0]


def test_section_handles_null_app():
    from analysis.plot_results import _section

    assert _section({"provider": None}, "provider") == {}
    assert _section({"provider": {"a": 1}}, "provider") == {"a": 1}
    assert _section({}, "missing") == {}


def test_sum_dict_ignores_non_numeric_values():
    from analysis.plot_results import _sum_dict

    section = {"stock": {"a": 10, "b": None, "c": 5, "d": "oops"}}
    assert _sum_dict(section, "stock") == 15


def test_write_summary_outputs_expected_lines(tmp_path: Path):
    from analysis.plot_results import write_summary

    rows = [
        {
            "day": 1,
            "manufacturer": {"parts_stock": {"PCB": 100}},
            "retailer": {
                "stock": {"Pro 3D Printer": 5},
                "orders_fulfilled": 3, "orders_backordered": 2,
            },
        },
        {
            "day": 2,
            "manufacturer": {"parts_stock": {"PCB": 80}},
            "retailer": {
                "stock": {"Pro 3D Printer": 0},
                "orders_fulfilled": 8, "orders_backordered": 4,
            },
        },
    ]

    out = tmp_path / "summary.txt"
    write_summary(rows, out)
    content = out.read_text()

    assert "Total days simulated: 2" in content
    assert "Total fulfilled orders: 8" in content
    assert "Total backordered orders: 4" in content
    assert "Peak manufacturer parts stock: 100" in content
    assert "Lowest retailer stock: 0" in content


def test_load_metrics_empty_file_returns_empty_list(tmp_path: Path):
    from analysis.plot_results import load_metrics

    path = tmp_path / "empty.jsonl"
    path.write_text("")
    assert load_metrics(path) == []


def test_load_metrics_sorts_by_day(tmp_path: Path):
    from analysis.plot_results import load_metrics

    path = tmp_path / "m.jsonl"
    path.write_text("\n".join([
        json.dumps({"day": 5}),
        json.dumps({"day": 1}),
        json.dumps({"day": 3}),
    ]))
    rows = load_metrics(path)
    assert [r["day"] for r in rows] == [1, 3, 5]
