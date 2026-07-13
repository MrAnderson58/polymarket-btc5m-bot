"""Phase G.3.3 — weighted multi-timeframe trend coverage."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.candles import (
    CandleBar,
    aggregate_bars,
    load_recent_candles,
)
from bot.research.market_events.signal_intelligence.trend_windows_g1 import (
    analyze_window_trend,
)
from bot.research.market_events.signal_intelligence.trend_windows_g3 import (
    G3_WINDOWS_MINUTES,
    TrendWindowG3,
)

WINDOW_WEIGHTS: dict[int, float] = {
    5: 15.0,
    15: 20.0,
    30: 20.0,
    60: 20.0,
    120: 15.0,
    240: 10.0,
}

COVERAGE_WARNING_PCT = 80.0
BARS_FOR_FULL_COVERAGE = 288  # 24h of 5m candles


@dataclass(frozen=True)
class WindowCoverageG33:
    window_minutes: int
    weight: float
    available: bool
    trend_score: float | None
    direction: str | None


@dataclass(frozen=True)
class TrendCoverageG33:
    coverage_pct: float
    weighted_trend_score: float
    direction: str
    windows: tuple[WindowCoverageG33, ...]
    best_trend: TrendWindowG3 | None
    provisional: bool


def _bars_required(window_minutes: int) -> int:
    return max(1, window_minutes // 5)


def _provisional_score(bars: list[CandleBar], *, window_minutes: int) -> tuple[float, str]:
    agg = aggregate_bars(bars, window_minutes=window_minutes, bar_minutes=5) if window_minutes > 5 else bars
    wt = analyze_window_trend(agg, window_minutes=window_minutes)
    if not wt:
        return 20.0, "NEUTRAL"
    score = min(100.0, abs(wt.cumulative_return_pct) * 4.0 + wt.max_streak * 3.0)
    return round(score, 1), wt.dominant_direction


def compute_trend_coverage(
    bars: list[CandleBar],
    trends: list[TrendWindowG3],
    *,
    symbol: str,
) -> TrendCoverageG33:
    """Weighted trend coverage; missing windows redistribute weight to available ones."""
    trend_map = {t.window_minutes: t for t in trends if t.symbol == symbol}
    windows: list[WindowCoverageG33] = []
    available_weight = 0.0
    weighted_sum = 0.0
    direction_votes: dict[str, float] = {}

    for window in G3_WINDOWS_MINUTES:
        weight = WINDOW_WEIGHTS[window]
        has_bars = len(bars) >= _bars_required(window)
        available = has_bars
        score: float | None = None
        direction: str | None = None

        if available:
            available_weight += weight
            tw = trend_map.get(window)
            if tw:
                score = tw.trend_score
                direction = tw.direction
            else:
                score, direction = _provisional_score(bars, window_minutes=window)
            weighted_sum += score * weight
            dir_key = "UP" if direction == "UP" else "DOWN"
            direction_votes[dir_key] = direction_votes.get(dir_key, 0.0) + weight

        windows.append(WindowCoverageG33(
            window_minutes=window,
            weight=weight,
            available=available,
            trend_score=score,
            direction=direction,
        ))

    coverage_pct = round(available_weight, 1)
    if len(bars) >= BARS_FOR_FULL_COVERAGE:
        coverage_pct = 100.0

    if available_weight > 0:
        weighted_score = round(weighted_sum / available_weight, 1)
    else:
        weighted_score = 0.0

    if direction_votes:
        dominant = max(direction_votes, key=direction_votes.get)  # type: ignore[arg-type]
    else:
        dominant = "DOWN"

    best_trend = max(
        (t for t in trends if t.symbol == symbol),
        key=lambda t: t.trend_score,
        default=None,
    )
    if not best_trend and available_weight > 0:
        best_window = max(
            (w for w in windows if w.available and w.trend_score is not None),
            key=lambda w: w.trend_score or 0,
            default=None,
        )
        if best_window:
            best_trend = TrendWindowG3(
                symbol=symbol,
                window_minutes=best_window.window_minutes,
                pattern_type="provisional",
                consecutive_candles=0,
                trend_score=weighted_score,
                direction=dominant,
                details={"description": f"weighted {best_window.window_minutes}m", "coverage_pct": coverage_pct},
            )

    return TrendCoverageG33(
        coverage_pct=coverage_pct,
        weighted_trend_score=weighted_score,
        direction=dominant,
        windows=tuple(windows),
        best_trend=best_trend,
        provisional=coverage_pct < 100.0,
    )


def compute_symbol_trend_coverage(
    conn: Any,
    *,
    symbol: str,
    trends: list[TrendWindowG3],
    limit: int = 300,
) -> TrendCoverageG33:
    bars = load_recent_candles(conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=limit)
    return compute_trend_coverage(bars, trends, symbol=symbol)


def windows_detail_json(coverage: TrendCoverageG33) -> str:
    payload = [
        {
            "window_minutes": w.window_minutes,
            "weight_pct": w.weight,
            "available": w.available,
            "trend_score": w.trend_score,
            "direction": w.direction,
        }
        for w in coverage.windows
    ]
    return json.dumps(payload)


def format_provisional_reason(coverage_pct: float) -> str:
    return f"Confidence provisional\nCoverage\n{coverage_pct:.0f}%"


def format_candidate_coverage_report(conn: Any, *, symbol: str | None = None) -> str:
    from bot.research.market_events.signal_intelligence.candidate_g31 import (
        _latest_cycle_ts,
        _TABLE,
    )

    ts = _latest_cycle_ts(conn)
    if not ts:
        return "No candidate cycles yet."

    if symbol:
        rows = conn.execute(
            f"""
            SELECT symbol, trend_coverage_pct, trend_windows_json, confidence, candidate_state
            FROM {_TABLE} WHERE candidate_ts = ? AND symbol = ?
            """,
            (ts, symbol.upper()),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT symbol, trend_coverage_pct, trend_windows_json, confidence, candidate_state
            FROM {_TABLE} WHERE candidate_ts = ?
            ORDER BY (trend_coverage_pct IS NULL), trend_coverage_pct DESC, confidence DESC
            LIMIT 30
            """,
            (ts,),
        ).fetchall()

    if not rows:
        return "No coverage data for latest cycle."

    lines = ["G3.3 Trend Coverage — latest cycle", ""]
    for r in rows:
        lines.extend([
            str(r["symbol"]),
            "",
            "Coverage",
            f"{float(r['trend_coverage_pct'] or 0):.0f}%",
            "",
        ])
        try:
            windows = json.loads(r["trend_windows_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            windows = []
        for w in windows:
            label = f"{w['window_minutes']}m"
            if w.get("available"):
                sc = w.get("trend_score")
                lines.append(f"  {label} ({w['weight_pct']:.0f}%) — score {sc:.0f}" if sc else f"  {label} — ok")
            else:
                lines.append(f"  {label} ({w['weight_pct']:.0f}%) — pending")
        lines.extend(["", "------------", ""])
    return "\n".join(lines).rstrip()


def trend_coverage_dashboard(conn: Any, *, limit: int = 50) -> dict[str, Any]:
    from bot.research.market_events.signal_intelligence.candidate_g31 import _latest_cycle_ts, _TABLE

    ts = _latest_cycle_ts(conn)
    if not ts:
        return {"tab": "Trend Coverage", "latest_cycle_ts": None, "symbols": []}

    rows = conn.execute(
        f"""
        SELECT symbol, trend_coverage_pct, trend_windows_json, confidence,
               candidate_state, rejection_reason
        FROM {_TABLE} WHERE candidate_ts = ?
        ORDER BY trend_coverage_pct DESC
        LIMIT ?
        """,
        (ts, limit),
    ).fetchall()

    symbols: list[dict[str, Any]] = []
    for r in rows:
        try:
            windows = json.loads(r["trend_windows_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            windows = []
        symbols.append({
            "symbol": r["symbol"],
            "coverage_pct": r["trend_coverage_pct"],
            "confidence": r["confidence"],
            "state": r["candidate_state"],
            "reason": r["rejection_reason"],
            "windows": windows,
            "history_accumulating": float(r["trend_coverage_pct"] or 0) < COVERAGE_WARNING_PCT,
        })
    return {"tab": "Trend Coverage", "latest_cycle_ts": ts, "symbols": symbols}
