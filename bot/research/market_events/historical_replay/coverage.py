"""Task A — data coverage audit for historical replay."""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from bot.research.market_events.historical_replay.constants import (
    THRESHOLD_GRID_PCT,
    THRESHOLD_WINDOWS_SEC,
)


def _human_span(seconds: float) -> str:
    if seconds < 3600:
        return f"{seconds / 60:.1f} minutes"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} hours"
    return f"{seconds / 86400:.1f} days"


def _interval_stats(deltas: list[int]) -> dict[str, float | int]:
    if not deltas:
        return {"count": 0}
    return {
        "count": len(deltas),
        "median_sec": statistics.median(deltas),
        "p90_sec": sorted(deltas)[int(len(deltas) * 0.9)] if len(deltas) > 1 else deltas[0],
        "min_sec": min(deltas),
        "max_sec": max(deltas),
    }


def _count_impulses(series: list[tuple[int, float]], *, window: int, threshold: float) -> int:
    if len(series) < 5:
        return 0
    hits = 0
    last = -999999
    for i in range(1, len(series)):
        ts, px = series[i]
        if ts - last < 300:
            continue
        target = ts - window
        ref = None
        for j in range(i, -1, -1):
            if series[j][0] <= target:
                ref = series[j][1]
                break
        if not ref or ref <= 0:
            continue
        ret = abs((px / ref - 1.0) * 100.0)
        if ret >= threshold:
            hits += 1
            last = ts
    return hits


def _audit_asset_class(
    conn: Any,
    *,
    asset_class: str,
    label: str,
) -> list[str]:
    rows = conn.execute(
        """
        SELECT i.canonical_asset, i.asset_class, o.obs_ts, o.trade_price
        FROM market_events_price_observations o
        JOIN market_events_instruments i ON i.id = o.instrument_id
        WHERE i.asset_class = ? AND o.trade_price IS NOT NULL AND o.trade_price > 0
        ORDER BY i.canonical_asset, o.obs_ts
        """,
        (asset_class,),
    ).fetchall()

    lines = [f"=== {label} ({asset_class}) ==="]
    if not rows:
        lines.append("No observation ticks.")
        return lines

    by_sym: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for r in rows:
        by_sym[r["canonical_asset"]].append((int(r["obs_ts"]), float(r["trade_price"])))

    total_ticks = len(rows)
    all_ts = [int(r["obs_ts"]) for r in rows]
    span = max(all_ts) - min(all_ts)
    lines.append(f"symbols: {len(by_sym)}  ticks: {total_ticks}  span: {_human_span(span)}")
    lines.append(f"first_ts: {min(all_ts)}  last_ts: {max(all_ts)}")

    all_deltas: list[int] = []
    gap_count = 0
    for sym, series in by_sym.items():
        for i in range(1, len(series)):
            d = series[i][0] - series[i - 1][0]
            all_deltas.append(d)
            if d > 120:
                gap_count += 1
    istats = _interval_stats(all_deltas)
    lines.append(
        f"sampling: median={istats.get('median_sec', 'n/a')}s "
        f"p90={istats.get('p90_sec', 'n/a')}s gaps>120s={gap_count}",
    )

    lines.append("per-symbol (top 15 by ticks):")
    ranked = sorted(by_sym.items(), key=lambda x: len(x[1]), reverse=True)[:15]
    for sym, series in ranked:
        sym_span = series[-1][0] - series[0][0]
        sym_deltas = [series[i][0] - series[i - 1][0] for i in range(1, len(series))]
        med = statistics.median(sym_deltas) if sym_deltas else 0
        lines.append(
            f"  {sym}: ticks={len(series)} span={_human_span(sym_span)} median_interval={med:.0f}s",
        )

    lines.append("candidate impulses (fixed grid, not optimized):")
    grid_total = 0
    for window in THRESHOLD_WINDOWS_SEC:
        for thr in THRESHOLD_GRID_PCT:
            n = sum(_count_impulses(s, window=window, threshold=thr) for s in by_sym.values())
            grid_total += n
            lines.append(f"  window={window}s threshold={thr}% → {n}")
    lines.append(f"grid_total_hits: {grid_total}")

    if span < 7 * 86400:
        lines.append(
            "VERDICT: observation span < 7 days — NOT a large historical sample for inference.",
        )
    elif span < 30 * 86400:
        lines.append("VERDICT: observation span < 30 days — limited for robust OOS evaluation.")
    else:
        lines.append("VERDICT: observation span may support train/val/OOS splits (check per-symbol).")
    return lines


def historical_replay_coverage(conn: Any) -> str:
    """Report crypto + TradFi observation coverage without mutating data."""
    crypto_events = conn.execute(
        "SELECT COUNT(*) AS n, MIN(event_ts) AS t0, MAX(event_ts) AS t1 FROM market_events",
    ).fetchone()
    obs_total = conn.execute("SELECT COUNT(*) AS n FROM market_events_price_observations").fetchone()

    lines = [
        "HISTORICAL REPLAY COVERAGE AUDIT",
        "mode: HISTORICAL REPLAY (read-only — does not mutate market_events)",
        "",
        "PRODUCTION ONLINE market_events (reference only):",
        f"  live_shock_events: {int(crypto_events['n'] if crypto_events else 0)}",
        f"  live_event_span: {crypto_events['t0']} → {crypto_events['t1']}",
        f"  price_observation_ticks_total: {int(obs_total['n'] if obs_total else 0)}",
        "",
    ]
    lines.extend(_audit_asset_class(conn, asset_class="CRYPTO", label="Crypto observation ticks"))
    lines.append("")
    lines.extend(_audit_asset_class(conn, asset_class="EQUITY", label="TradFi equity observations"))
    lines.append("")
    lines.extend(_audit_asset_class(conn, asset_class="COMMODITY", label="TradFi commodity observations"))
    lines.append("")
    lines.extend(_audit_asset_class(conn, asset_class="ETF_INDEX", label="TradFi ETF/index observations"))

    candle_n = conn.execute("SELECT COUNT(*) AS n FROM market_events_historical_candles").fetchone()
    lines.append("")
    lines.append(f"historical_candle_rows: {int(candle_n['n'] if candle_n else 0)}")
    lines.append("Run historical-candle-backfill to extend depth beyond live observations.")
    return "\n".join(lines)
