"""Near-miss shadow audit — research only, no market_events or paper runs."""

from __future__ import annotations

import json
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.config import SHOCK_THRESHOLDS
from bot.research.market_events.db import execute_with_retry, retry_on_db_locked
from bot.research.market_events.event_report import _days_ago_ts
from bot.research.market_events.event_types import SHOCK_DIRECTION_DOWN, SHOCK_DIRECTION_UP
from bot.research.market_events.price_feed import PriceTick, SymbolPriceState
from bot.research.market_events.shock_profiles import profile_name_for_symbol

NEAR_MISS_WINDOWS = (30, 60, 180)
THRESHOLD_FRACTIONS = (0.25, 0.50, 0.75, 0.90)

_NEAR_MISS_INSERT_SQL = """
INSERT INTO market_events_near_miss_summaries (
  symbol, profile_name, window_sec, direction, session_regime,
  max_abs_return_pct, current_abs_return_pct, p95_abs_return_pct, p99_abs_return_pct,
  threshold_pct, max_threshold_reached_pct, max_volume_zscore, max_relative_return_pct,
  episodes_25pct, episodes_50pct, episodes_75pct, episodes_90pct,
  period_start, period_end, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


@dataclass
class NearMissSnapshot:
    symbol: str
    profile_name: str
    window_sec: int
    direction: str
    session_regime: str | None = None
    max_abs_return_pct: float = 0.0
    current_abs_return_pct: float = 0.0
    max_volume_zscore: float | None = None
    max_relative_return_pct: float | None = None
    threshold_pct: float = 0.0
    max_threshold_reached_pct: float = 0.0
    abs_returns: list[float] = field(default_factory=list)
    episodes_25: int = 0
    episodes_50: int = 0
    episodes_75: int = 0
    episodes_90: int = 0


def _threshold_for_window(window_sec: int) -> float | None:
    for det_id in ("SHOCK_A", "SHOCK_B", "SHOCK_C"):
        cfg = SHOCK_THRESHOLDS[det_id]
        if int(cfg["window_sec"]) == window_sec:
            return float(cfg["min_abs_return_pct"])
    return None


def _direction(ret: float) -> str:
    return SHOCK_DIRECTION_UP if ret >= 0 else SHOCK_DIRECTION_DOWN


def update_near_miss_from_state(
    tracker: dict[tuple[str, int, str], NearMissSnapshot],
    state: SymbolPriceState,
    *,
    now_ts: int,
    btc_state: SymbolPriceState | None = None,
    session_regime: str | None = None,
) -> None:
    """Update in-memory near-miss tracker from live feed state (no DB writes)."""
    sym = state.symbol
    profile = profile_name_for_symbol(sym)
    for window in NEAR_MISS_WINDOWS:
        ret = state.return_over(window, now_ts)
        if ret is None:
            continue
        threshold = _threshold_for_window(window)
        if threshold is None or threshold <= 0:
            continue
        direction = _direction(ret)
        key = (sym, window, direction)
        snap = tracker.get(key)
        if snap is None:
            snap = NearMissSnapshot(
                symbol=sym, profile_name=profile, window_sec=window,
                direction=direction, session_regime=session_regime,
                threshold_pct=threshold,
            )
            tracker[key] = snap
        abs_ret = abs(ret)
        snap.current_abs_return_pct = abs_ret
        snap.max_abs_return_pct = max(snap.max_abs_return_pct, abs_ret)
        snap.abs_returns.append(abs_ret)
        if len(snap.abs_returns) > 5000:
            snap.abs_returns = snap.abs_returns[-5000:]
        reached = abs_ret / threshold * 100.0
        snap.max_threshold_reached_pct = max(snap.max_threshold_reached_pct, reached)
        for frac, attr in zip(THRESHOLD_FRACTIONS, ("episodes_25", "episodes_50", "episodes_75", "episodes_90")):
            if abs_ret >= threshold * frac:
                setattr(snap, attr, getattr(snap, attr) + 1)
        vol_z = state.volume_zscore(window, now_ts)
        if vol_z is not None:
            snap.max_volume_zscore = max(snap.max_volume_zscore or vol_z, vol_z)
        if btc_state:
            btc_ret = btc_state.return_over(window, now_ts)
            if btc_ret is not None and ret is not None:
                rel = abs(ret - btc_ret)
                snap.max_relative_return_pct = max(snap.max_relative_return_pct or rel, rel)


def persist_near_miss_snapshots(
    conn: Any,
    tracker: dict[tuple[str, int, str], NearMissSnapshot],
    *,
    period_start: int,
    period_end: int,
) -> int:
    if not tracker:
        return 0
    now = int(time.time())
    params: list[tuple[Any, ...]] = []
    for snap in tracker.values():
        p95 = p99 = None
        if len(snap.abs_returns) >= 5:
            ordered = sorted(snap.abs_returns)
            p95 = ordered[int(len(ordered) * 0.95)]
            p99 = ordered[int(len(ordered) * 0.99)]
        params.append((
            snap.symbol, snap.profile_name, snap.window_sec, snap.direction,
            snap.session_regime, snap.max_abs_return_pct, snap.current_abs_return_pct,
            p95, p99, snap.threshold_pct, snap.max_threshold_reached_pct,
            snap.max_volume_zscore, snap.max_relative_return_pct,
            snap.episodes_25, snap.episodes_50, snap.episodes_75, snap.episodes_90,
            period_start, period_end, now,
        ))

    def _batch() -> int:
        executemany = getattr(conn, "executemany", None)
        if callable(executemany):
            executemany(_NEAR_MISS_INSERT_SQL, params)
        else:
            for p in params:
                execute_with_retry(conn, _NEAR_MISS_INSERT_SQL, p)
        return len(params)

    return int(retry_on_db_locked(_batch))


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


def scan_near_miss_from_observations(conn: Any, *, days: int = 1) -> dict[tuple[str, int, str], NearMissSnapshot]:
    since = _days_ago_ts(days)
    rows = conn.execute(
        """
        SELECT i.canonical_asset, o.obs_ts, o.trade_price, o.session_regime
        FROM market_events_price_observations o
        JOIN market_events_instruments i ON i.id = o.instrument_id
        WHERE o.obs_ts >= ? AND o.trade_price IS NOT NULL AND o.trade_price > 0
        ORDER BY i.canonical_asset, o.obs_ts
        """,
        (since,),
    ).fetchall()
    by_sym: dict[str, list[tuple[int, float, str | None]]] = defaultdict(list)
    for r in rows:
        by_sym[r["canonical_asset"]].append((
            int(r["obs_ts"]), float(r["trade_price"]), r["session_regime"],
        ))

    tracker: dict[tuple[str, int, str], NearMissSnapshot] = {}
    for sym, obs in by_sym.items():
        series = [(ts, px) for ts, px, _ in obs]
        btc_series = by_sym.get("BTC", [])
        for i in range(5, len(series)):
            ts, _, session = obs[i]
            st = SymbolPriceState(symbol=sym, pair=sym)
            for j in range(max(0, i - 300), i + 1):
                st.append(PriceTick(ts=series[j][0], price=series[j][1], volume=1000.0), max_age_sec=900)
            btc_st = None
            if btc_series and sym != "BTC":
                btc_st = SymbolPriceState(symbol="BTC", pair="BTCUSDT")
                btc_idx = 0
                for j in range(max(0, i - 300), i + 1):
                    bts = series[j][0]
                    while btc_idx < len(btc_series) - 1 and btc_series[btc_idx + 1][0] <= bts:
                        btc_idx += 1
                    bpx = btc_series[btc_idx][1] if btc_series[btc_idx][0] <= bts else None
                    if bpx:
                        btc_st.append(PriceTick(ts=bts, price=bpx, volume=1000.0), max_age_sec=900)
            update_near_miss_from_state(
                tracker, st, now_ts=ts, btc_state=btc_st, session_regime=session,
            )
    return tracker


def shock_near_miss_report(conn: Any, *, days: int = 1, persist: bool = True) -> str:
    since = _days_ago_ts(days)
    if persist:
        tracker = scan_near_miss_from_observations(conn, days=days)
        persist_near_miss_snapshots(conn, tracker, period_start=since, period_end=int(time.time()))

    rows = conn.execute(
        """
        SELECT profile_name, symbol, direction, window_sec, session_regime,
               MAX(max_abs_return_pct) AS max_move,
               MAX(p95_abs_return_pct) AS p95,
               MAX(p99_abs_return_pct) AS p99,
               MAX(threshold_pct) AS threshold,
               MAX(max_threshold_reached_pct) AS max_reached,
               SUM(episodes_25pct) AS e25, SUM(episodes_50pct) AS e50,
               SUM(episodes_75pct) AS e75, SUM(episodes_90pct) AS e90
        FROM market_events_near_miss_summaries
        WHERE created_at >= ?
        GROUP BY profile_name, symbol, direction, window_sec, session_regime
        ORDER BY profile_name, symbol, window_sec
        """,
        (since,),
    ).fetchall()

    lines = [
        "SHOCK NEAR-MISS REPORT (research only — no market_events, no paper runs)",
        f"days: {days}",
        "thresholds: production SHOCK_A/B/C (unchanged)",
        "",
    ]
    if not rows:
        lines.append("No near-miss summaries in window.")
        return "\n".join(lines)

    by_profile: dict[str, list] = defaultdict(list)
    for r in rows:
        by_profile[r["profile_name"]].append(r)

    for prof, prof_rows in sorted(by_profile.items()):
        lines.append(f"=== {prof} ===")
        for r in prof_rows:
            dist = (r["threshold"] or 0) - (r["max_move"] or 0)
            lines.append(
                f"  {r['symbol']} {r['direction']} window={r['window_sec']}s "
                f"max={r['max_move']:.3f}% p95={r['p95'] or 0:.3f}% p99={r['p99'] or 0:.3f}% "
                f"threshold={r['threshold']:.2f}% reached={r['max_reached']:.0f}% "
                f"distance={dist:.3f}% "
                f"episodes@25/50/75/90%={r['e25']}/{r['e50']}/{r['e75']}/{r['e90']} "
                f"session={r['session_regime'] or 'unknown'}",
            )
        lines.append("")
    return "\n".join(lines)
