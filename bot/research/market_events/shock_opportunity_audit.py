"""Read-only shock opportunity grid audit — no inserts, no paper trades."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.config import CORE_SYMBOLS, SHOCK_DEDUP_WINDOW_SEC
from bot.research.market_events.event_report import _days_ago_ts
from bot.research.market_events.event_types import SHOCK_DIRECTION_DOWN, SHOCK_DIRECTION_UP

RESEARCH_GRID: dict[int, list[float]] = {
    30: [0.5, 0.75, 1.0, 1.25, 1.5],
    60: [0.75, 1.0, 1.5, 2.0],
    180: [1.0, 1.5, 2.0, 3.0],
}

BTC_ETH = frozenset({"BTC", "ETH"})
LIQUID_ALTS = frozenset({"SOL", "XRP", "BNB", "LINK", "AVAX"})
HIGH_BETA_ALTS = frozenset({"DOGE", "ADA", "SUI"})


def _symbol_group(sym: str) -> str:
    if sym in BTC_ETH:
        return "btc_eth"
    if sym in LIQUID_ALTS:
        return "liquid_alts"
    if sym in HIGH_BETA_ALTS:
        return "high_beta_alts"
    if sym in CORE_SYMBOLS:
        return "other_crypto"
    return "tradfi"


@dataclass
class GridCell:
    window_sec: int
    threshold_pct: float
    direction: str
    candidate_count: int = 0
    episode_count: int = 0


def _load_price_series(conn: Any, since: int) -> dict[str, list[tuple[int, float]]]:
    out: dict[str, list[tuple[int, float]]] = defaultdict(list)
    rows = conn.execute(
        """
        SELECT i.canonical_asset, o.obs_ts, o.trade_price
        FROM market_events_price_observations o
        JOIN market_events_instruments i ON i.id = o.instrument_id
        WHERE o.obs_ts >= ? AND o.trade_price IS NOT NULL AND o.trade_price > 0
        ORDER BY i.canonical_asset, o.obs_ts
        """,
        (since,),
    ).fetchall()
    for r in rows:
        out[r["canonical_asset"]].append((int(r["obs_ts"]), float(r["trade_price"])))
    return out


def _return_at(series: list[tuple[int, float]], idx: int, window_sec: int) -> float | None:
    ts, px = series[idx]
    target = ts - window_sec
    ref_px = None
    for j in range(idx, -1, -1):
        if series[j][0] <= target:
            ref_px = series[j][1]
            break
    if ref_px is None or ref_px <= 0:
        return None
    return (px / ref_px - 1.0) * 100.0


def _dedupe_episodes(hits: list[int], dedup_sec: int = SHOCK_DEDUP_WINDOW_SEC) -> int:
    if not hits:
        return 0
    episodes = 1
    last = hits[0]
    for t in hits[1:]:
        if t - last >= dedup_sec:
            episodes += 1
            last = t
    return episodes


def _audit_symbol(series: list[tuple[int, float]]) -> dict[tuple, GridCell]:
    cells: dict[tuple, GridCell] = {}
    if len(series) < 5:
        return cells
    for window, thresholds in RESEARCH_GRID.items():
        for thr in thresholds:
            for direction in (SHOCK_DIRECTION_UP, SHOCK_DIRECTION_DOWN):
                key = (window, thr, direction)
                cell = GridCell(window, thr, direction)
                hit_ts: list[int] = []
                for i in range(len(series)):
                    ret = _return_at(series, i, window)
                    if ret is None:
                        continue
                    if direction == SHOCK_DIRECTION_UP and ret >= thr:
                        cell.candidate_count += 1
                        hit_ts.append(series[i][0])
                    elif direction == SHOCK_DIRECTION_DOWN and ret <= -thr:
                        cell.candidate_count += 1
                        hit_ts.append(series[i][0])
                cell.episode_count = _dedupe_episodes(hit_ts)
                cells[key] = cell
    return cells


def shock_opportunity_audit(conn: Any, *, days: int = 1) -> str:
    since = _days_ago_ts(days)
    series_by_sym = _load_price_series(conn, since)
    lines = [
        "SHOCK OPPORTUNITY AUDIT (read-only research grid)",
        f"window_days: {days}",
        "data_source: market_events_price_observations (no market_events inserts)",
        "",
        "Grid (fixed, not optimized):",
    ]
    for w, thrs in RESEARCH_GRID.items():
        lines.append(f"  {w}s: {thrs}")
    lines.append("")
    if not series_by_sym:
        lines.append("No stored price observations in window.")
        lines.append("Crypto core collector does not persist ticks — run observe-run or use TradFi data.")
        return "\n".join(lines)

    group_totals: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for sym, series in sorted(series_by_sym.items()):
        grp = _symbol_group(sym)
        lines.append(f"=== {sym} (group={grp}) obs_points={len(series)} ===")
        cells = _audit_symbol(series)
        if not cells:
            lines.append("  insufficient history")
            continue
        for (window, thr, direction), cell in sorted(cells.items()):
            lines.append(
                f"  window={window}s thr={thr}% {direction}: "
                f"candidates={cell.candidate_count} episodes={cell.episode_count}",
            )
            gk = f"{window}s_{thr}%_{direction}"
            group_totals[grp][gk] += cell.candidate_count

    lines.extend(["", "=== GROUP SUMMARY (candidate counts) ==="])
    for grp in ("btc_eth", "liquid_alts", "high_beta_alts", "tradfi", "other_crypto"):
        if grp in group_totals:
            lines.append(f"  {grp}: {dict(group_totals[grp])}")

    missing_crypto = [s for s in CORE_SYMBOLS if s not in series_by_sym]
    if missing_crypto:
        lines.extend(["", f"Crypto symbols without stored observations: {', '.join(missing_crypto)}"])
    if series_by_sym:
        intervals = []
        for s, ser in series_by_sym.items():
            if len(ser) >= 2:
                gaps = [ser[i][0] - ser[i - 1][0] for i in range(1, min(100, len(ser)))]
                if gaps:
                    intervals.append(statistics.median(gaps))
        if intervals:
            lines.append(f"Median sampling interval (TradFi sample): {statistics.median(intervals):.1f}s")

    lines.extend([
        "",
        "DESCRIPTIVE ONLY — do not auto-select thresholds or change SHOCK_A-E defaults.",
    ])
    return "\n".join(lines)
