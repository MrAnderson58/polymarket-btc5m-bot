"""Phase G.1 — Liquidity & Trend Engine orchestrator."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.adaptive_shock_g1 import compute_adaptive_threshold
from bot.research.market_events.signal_intelligence.candles import load_recent_candles
from bot.research.market_events.signal_intelligence.liquidity_engine_g1 import (
    SIGNAL_CAPITULATION,
    SIGNAL_LIQUIDITY_ACCUM,
    SIGNAL_SLOW_TREND,
    compute_sweep_probability,
    detect_capitulation,
    detect_liquidity_accumulation,
    detect_slow_trend_shock,
)
from bot.research.market_events.signal_intelligence.liquidation_intelligence_f7 import analyze_liquidations
from bot.research.market_events.signal_intelligence.reversal_learning_g1 import lookup_historical_reversal_rate
from bot.research.market_events.signal_intelligence.telegram_g1 import build_telegram_from_g1
from bot.research.market_events.signal_intelligence.trend_windows_g1 import analyze_all_windows


@dataclass(frozen=True)
class LiquidityTrendG1:
    event_id: int
    symbol: str
    signal_type: str
    continuation_probability: float
    reversal_probability: float
    historical_reversal_rate: float | None
    adaptive_threshold_pct: float
    telegram_rendered: str


def _dataclass_to_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if hasattr(obj, "__dataclass_fields__"):
        return {k: getattr(obj, k) for k in obj.__dataclass_fields__}
    return dict(obj)


def _pick_signal_type(
    *,
    slow_trend: Any,
    liquidity: Any,
    capitulation: Any,
) -> str:
    if capitulation:
        return SIGNAL_CAPITULATION
    if liquidity and liquidity.score >= 70:
        return SIGNAL_LIQUIDITY_ACCUM
    if slow_trend:
        return SIGNAL_SLOW_TREND
    if liquidity:
        return SIGNAL_LIQUIDITY_ACCUM
    return SIGNAL_SLOW_TREND


def run_liquidity_trend_g1(conn: Any, event_id: int) -> LiquidityTrendG1 | None:
    from bot.research.market_events.signal_intelligence.config import G1_ENABLED

    if not G1_ENABLED:
        return None

    existing = conn.execute(
        "SELECT 1 FROM market_events_liquidity_trend_g1 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if existing:
        return load_liquidity_trend_g1(conn, event_id)

    row = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return None

    symbol = str(row["symbol"])
    shock_ret = float(row["return_pct"] or 0)
    direction = str(row["direction"] or "DOWN")
    event_ts = int(row["event_ts"])

    windows, patterns = analyze_all_windows(conn, symbol=symbol)
    bars = load_recent_candles(conn, symbol=symbol, timeframe="5m", limit=300)

    vol_mult = 1.0
    if windows:
        w15 = next((w for w in windows if w.window_minutes == 15), windows[0])
        vol_mult = max(1.0, abs(w15.cumulative_return_pct) / 2.0)

    slow = detect_slow_trend_shock(bars) if bars else None
    liquidity = detect_liquidity_accumulation(
        conn, event_id=event_id, symbol=symbol,
        price_return_pct=shock_ret, volume_multiple=vol_mult,
    )

    funding = None
    exch = conn.execute(
        "SELECT funding FROM market_event_exchange_context WHERE event_id = ? LIMIT 1",
        (event_id,),
    ).fetchone()
    if exch and exch["funding"] is not None:
        funding = float(exch["funding"])

    trend_row = conn.execute(
        "SELECT stage FROM market_events_trend_shock_v2 WHERE event_id = ? LIMIT 1",
        (event_id,),
    ).fetchone()
    trend = {"stage": trend_row["stage"]} if trend_row else None
    liq_intel = analyze_liquidations(conn, symbol=symbol, trend=trend, shock_return=shock_ret)
    liq_intensity = max(
        (ex.get("intensity", 0) for ex in liq_intel.exchanges.values()),
        default=0.0,
    )

    capitulation = detect_capitulation(
        bars, funding=funding, liquidation_intensity=liq_intensity,
    ) if bars else None

    adaptive = compute_adaptive_threshold(conn, symbol=symbol)

    pattern_key = f"{symbol}|{_pick_signal_type(slow_trend=slow, liquidity=liquidity, capitulation=capitulation)}"
    if patterns:
        p = max(patterns, key=lambda x: x.max_streak)
        pattern_key = f"{symbol}|{p.max_streak}_{p.max_streak_color}|{p.window_minutes}m"

    hist_rate = lookup_historical_reversal_rate(conn, pattern_key) or 0.55
    sweep = compute_sweep_probability(
        slow_trend=slow,
        liquidity=liquidity,
        capitulation=capitulation,
        windows=windows,
        historical_reversal_rate=hist_rate,
    )

    signal_type = _pick_signal_type(slow_trend=slow, liquidity=liquidity, capitulation=capitulation)

    mtf_json = json.dumps([_dataclass_to_dict(w) for w in windows], ensure_ascii=False)
    pat_json = json.dumps([_dataclass_to_dict(p) for p in patterns], ensure_ascii=False)
    slow_json = json.dumps(_dataclass_to_dict(slow), ensure_ascii=False) if slow else None
    liq_json = json.dumps(_dataclass_to_dict(liquidity), ensure_ascii=False) if liquidity else None
    cap_json = json.dumps(_dataclass_to_dict(capitulation), ensure_ascii=False) if capitulation else None

    now = int(time.time())
    insert_returning_id(
        conn,
        """
        INSERT INTO market_events_liquidity_trend_g1 (
          event_id, symbol, event_ts, signal_type, mtf_windows_json,
          consecutive_pattern_json, slow_trend_json, liquidity_accum_json,
          capitulation_json, continuation_probability, reversal_probability,
          adaptive_threshold_pct, historical_reversal_rate, plan_action,
          telegram_rendered, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?)
        """,
        (
            event_id, symbol, event_ts, signal_type, mtf_json, pat_json,
            slow_json, liq_json, cap_json,
            sweep.continuation_probability, sweep.reversal_probability,
            adaptive.threshold_pct, hist_rate, "Ждать R2", now,
        ),
    )

    for w in windows:
        conn.execute(
            """
            INSERT INTO market_events_mtf_trend_g1 (
              event_id, symbol, window_minutes, cumulative_return_pct, candle_count,
              green_pct, red_pct, max_streak, streak_direction, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, symbol, w.window_minutes, w.cumulative_return_pct,
                w.candle_count, w.green_pct, w.red_pct, w.max_streak,
                w.streak_direction, now,
            ),
        )

    rendered = build_telegram_from_g1(conn, event_id) or ""
    conn.execute(
        "UPDATE market_events_liquidity_trend_g1 SET telegram_rendered = ? WHERE event_id = ?",
        (rendered, event_id),
    )

    return LiquidityTrendG1(
        event_id=event_id,
        symbol=symbol,
        signal_type=signal_type,
        continuation_probability=sweep.continuation_probability,
        reversal_probability=sweep.reversal_probability,
        historical_reversal_rate=hist_rate,
        adaptive_threshold_pct=adaptive.threshold_pct,
        telegram_rendered=rendered,
    )


def load_liquidity_trend_g1(conn: Any, event_id: int) -> LiquidityTrendG1 | None:
    row = conn.execute(
        "SELECT * FROM market_events_liquidity_trend_g1 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if not row:
        return None
    rendered = str(row["telegram_rendered"] or "") or (build_telegram_from_g1(conn, event_id) or "")
    return LiquidityTrendG1(
        event_id=int(row["event_id"]),
        symbol=str(row["symbol"]),
        signal_type=str(row["signal_type"]),
        continuation_probability=float(row["continuation_probability"]),
        reversal_probability=float(row["reversal_probability"]),
        historical_reversal_rate=(
            float(row["historical_reversal_rate"]) if row["historical_reversal_rate"] is not None else None
        ),
        adaptive_threshold_pct=float(row["adaptive_threshold_pct"] or 0),
        telegram_rendered=rendered,
    )


def liquidity_trend_report(conn: Any, *, days: int = 7) -> str:
    since = int(time.time()) - days * 86400
    lines = [f"LIQUIDITY & TREND G1 — last {days}d", ""]
    for sig in (SIGNAL_CAPITULATION, SIGNAL_LIQUIDITY_ACCUM, SIGNAL_SLOW_TREND):
        row = conn.execute(
            """
            SELECT COUNT(*) AS n, AVG(reversal_probability) AS avg_rev
            FROM market_events_liquidity_trend_g1
            WHERE signal_type = ? AND event_ts >= ?
            """,
            (sig, since),
        ).fetchone()
        n = int(row["n"] if row else 0)
        avg = float(row["avg_rev"] or 0) if row else 0.0
        lines.append(f"  {sig}: count={n} avg_reversal_prob={avg:.0%}")
    stats = conn.execute(
        "SELECT COUNT(*) AS n FROM market_events_g1_pattern_stats WHERE samples >= 3",
    ).fetchone()
    lines.append(f"  learned_patterns: {int(stats['n'] if stats else 0)}")
    return "\n".join(lines)
