"""Unicode heatmaps for parameter sweeps (Report v3 §23)."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.report.advanced import (
    _simulate_stop_pnl,
    _simulate_trailing_pnl,
    build_parameter_optimizer,
)
from bot.report.analytics import (
    ALT_STOP_PCTS,
    ENTRY_PRICES,
    HOLDING_BUCKETS_SEC,
    TRAIL_ACTIVATION_OPTS,
    _metrics,
    trade_pnl,
)


def _bar(value: float, max_value: float, width: int = 13) -> str:
    if max_value <= 0:
        return ""
    filled = max(0, min(width, int(round(value / max_value * width))))
    return "█" * filled


def _heatmap_rows(
    items: list[dict[str, Any]],
    *,
    value_key: str,
    label_key: str,
    metric_key: str = "profit_factor",
    min_trades: int = 5,
) -> list[dict[str, Any]]:
    valid = [i for i in items if i.get("trades", 0) >= min_trades]
    if not valid:
        return []
    max_pf = max(i[metric_key] for i in valid if i[metric_key] != float("inf"))
    rows = []
    for item in valid:
        pf = item[metric_key]
        pf_display = pf if pf != float("inf") else 99.9
        rows.append(
            {
                "label": item[label_key],
                "profit_factor": pf,
                "win_rate": item.get("win_rate", 0),
                "trades": item.get("trades", 0),
                "bar": _bar(pf_display, max_pf),
                "line": (
                    f"{item[label_key]} { _bar(pf_display, max_pf)} "
                    f"PF {pf:.2f}" if pf != float("inf") else f"{item[label_key]} PF inf"
                ),
            }
        )
    return sorted(rows, key=lambda r: r["label"])


def build_heatmaps(
    conn: sqlite3.Connection,
    closed: list,
    optimizer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if optimizer is None:
        optimizer = build_parameter_optimizer(conn, closed)

    entry_items = []
    for price in ENTRY_PRICES:
        subset = [t for t in closed if abs(float(t["entry_price"]) - price) <= 0.005]
        pnls = [trade_pnl(t) for t in subset]
        entry_items.append({"entry": f"{price:.2f}", "entry_price": price, **_metrics(pnls)})

    stop_items = []
    for stop_pct in ALT_STOP_PCTS:
        pnls = [_simulate_stop_pnl(conn, t, stop_pct) for t in closed]
        stop_items.append(
            {"stop": f"{abs(stop_pct):.0f}%", "stop_pct": stop_pct, **_metrics(pnls)}
        )

    trail_items = []
    for activation in TRAIL_ACTIVATION_OPTS:
        pnls = [_simulate_trailing_pnl(conn, t, activation, 0.01) for t in closed]
        trail_items.append(
            {
                "trailing": f"{activation:.3f}",
                "activation": activation,
                **_metrics(pnls),
            }
        )

    holding_items = []
    for max_sec in HOLDING_BUCKETS_SEC:
        subset = [
            t
            for t in closed
            if float(t["holding_time_seconds"] or 0) <= max_sec
        ]
        pnls = [trade_pnl(t) for t in subset]
        holding_items.append(
            {"holding": f"<={max_sec}s", "max_sec": max_sec, **_metrics(pnls)}
        )

    return {
        "entry_threshold": _heatmap_rows(entry_items, value_key="entry_price", label_key="entry"),
        "stop_loss": _heatmap_rows(stop_items, value_key="stop_pct", label_key="stop"),
        "trailing": _heatmap_rows(trail_items, value_key="activation", label_key="trailing"),
        "holding_time": _heatmap_rows(holding_items, value_key="max_sec", label_key="holding"),
        "btc_filter": [],
    }


def enrich_heatmaps_from_report(heatmaps: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
  bf_rows = report.get("btc_filter_analysis", {}).get("rows", [])
  if bf_rows:
      items = [
          {
              "bucket": r["bucket"],
              "trades": r["trades"],
              "profit_factor": r["profit_factor"],
              "win_rate": r["win_rate"],
          }
          for r in bf_rows
          if r.get("trades", 0) >= 5
      ]
      heatmaps["btc_filter"] = _heatmap_rows(items, value_key="bucket", label_key="bucket")
  return heatmaps
