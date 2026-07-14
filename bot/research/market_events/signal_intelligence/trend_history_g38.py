"""Phase G.3.8 — Trend History Builder (candle backfill + diagnostics)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from bot.research.futures_agent.market_provider import BinanceMarketProvider, symbol_pair
from bot.research.market_events.signal_intelligence.candles import CandleBar, load_recent_candles
from bot.research.market_events.signal_intelligence.trend_coverage_g33 import (
    BARS_FOR_FULL_COVERAGE,
    compute_symbol_trend_coverage,
)

logger = logging.getLogger(__name__)

VENUE = "binance_futures"
TIMEFRAME_5M = "5m"
BARS_24H_5M = 288
MIN_BARS_FOR_TREND = 24

# window_minutes, display label, required aggregated bar count for 24h coverage
G38_WINDOW_SPECS: tuple[tuple[int, str, int], ...] = (
    (5, "5m", 288),
    (15, "15m", 96),
    (30, "30m", 48),
    (60, "1h", 24),
    (120, "2h", 12),
    (240, "4h", 6),
)

_SNAPSHOT_PRICE_COLS = {
    "BTC": "btc_price",
    "ETH": "eth_price",
    "SOL": "sol_price",
    "BNB": "bnb_price",
}


@dataclass(frozen=True)
class WindowStatusG38:
    window_minutes: int
    label: str
    count: int
    required: int
    passed: bool


@dataclass(frozen=True)
class SymbolHistoryResultG38:
    symbol: str
    bars_before: int
    bars_after: int
    loaded: int
    source: str
    status: str
    message: str


def count_5m_bars(conn: Any, *, symbol: str, hours: int = 24) -> int:
    since = int(time.time()) - hours * 3600
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_historical_candles
        WHERE venue = ? AND symbol = ? AND timeframe = ? AND open_ts >= ?
        """,
        (VENUE, symbol.upper(), TIMEFRAME_5M, since),
    ).fetchone()
    return int(row["n"] or 0) if row else 0


def window_statuses_g38(bars_5m: int) -> list[WindowStatusG38]:
    rows: list[WindowStatusG38] = []
    for window_min, label, full_day_count in G38_WINDOW_SPECS:
        step = max(1, window_min // 5)
        count = bars_5m if window_min == 5 else bars_5m // step
        min_bars_5m = max(1, window_min // 5)
        passed = bars_5m >= min_bars_5m
        rows.append(WindowStatusG38(
            window_minutes=window_min,
            label=label,
            count=count,
            required=full_day_count,
            passed=passed,
        ))
    return rows


def diagnose_trend_zero_g38(conn: Any, *, symbol: str, hours: int = 24) -> dict[str, Any]:
    """Explain why trend=0 for a symbol — per-window counts, not just 'no data'."""
    sym = symbol.upper()
    bars_5m = count_5m_bars(conn, symbol=sym, hours=hours)
    windows = window_statuses_g38(bars_5m)
    coverage = None
    trend_count = 0
    weighted_trend = 0.0

    if bars_5m >= MIN_BARS_FOR_TREND:
        from bot.research.market_events.signal_intelligence.trend_windows_g3 import (
            detect_trends_g3,
        )
        trends = detect_trends_g3(conn, symbol=sym)
        trend_count = len(trends)
        coverage = compute_symbol_trend_coverage(conn, symbol=sym, trends=trends)
        weighted_trend = coverage.weighted_trend_score

    root_causes: list[str] = []
    if bars_5m == 0:
        root_causes.append("no 5m candles in market_events_historical_candles")
    elif bars_5m < MIN_BARS_FOR_TREND:
        root_causes.append(f"insufficient 5m bars ({bars_5m} < {MIN_BARS_FOR_TREND})")
    else:
        failed = [w for w in windows if not w.passed]
        if failed:
            root_causes.append(
                f"missing aggregated bars: {failed[-1].label} {failed[-1].count}/{failed[-1].required}",
            )
        if trend_count == 0:
            root_causes.append("no trend pattern detected (streak < 5 or choppy market)")

    source = candle_source_status_g38(conn, symbol=sym, hours=hours)
    return {
        "symbol": sym,
        "bars_5m": bars_5m,
        "windows": windows,
        "coverage_pct": coverage.coverage_pct if coverage else 0.0,
        "weighted_trend_score": weighted_trend,
        "trend_windows_detected": trend_count,
        "root_causes": root_causes,
        "candle_source": source,
    }


def candle_source_status_g38(
    conn: Any,
    *,
    symbol: str,
    hours: int = 24,
) -> dict[str, Any]:
    sym = symbol.upper()
    since = int(time.time()) - hours * 3600
    row = conn.execute(
        """
        SELECT COUNT(*) AS n, MAX(source) AS source
        FROM market_events_historical_candles
        WHERE venue = ? AND symbol = ? AND timeframe = ? AND open_ts >= ?
        """,
        (VENUE, sym, TIMEFRAME_5M, since),
    ).fetchone()
    count = int(row["n"] or 0) if row else 0
    source = str(row["source"] or "") if row else ""
    ok = count >= BARS_24H_5M * 0.95
    return {
        "symbol": sym,
        "venue": "Binance Futures",
        "ok": ok,
        "count": count,
        "required": BARS_24H_5M,
        "source": source or "none",
        "status": "OK" if ok else ("PARTIAL" if count > 0 else "FAIL"),
    }


def format_trend_status_g38(conn: Any, *, symbol: str | None = None, hours: int = 24) -> str:
    from bot.research.market_events.signal_intelligence.candidate_g31 import load_g31_universe_symbols

    symbols = [symbol.upper()] if symbol else list(load_g31_universe_symbols(conn))
    parts: list[str] = []
    for sym in symbols:
        diag = diagnose_trend_zero_g38(conn, symbol=sym, hours=hours)
        lines = [sym, ""]
        for w in diag["windows"]:
            lines.extend([
                w.label,
                str(w.count),
                "PASS" if w.passed else "FAIL",
                "",
            ])
        lines.extend([
            "Coverage",
            f"{diag['coverage_pct']:.0f}%",
        ])
        if diag["root_causes"]:
            lines.extend(["", "Why trend=0", ""])
            for cause in diag["root_causes"]:
                lines.extend([cause, ""])
        parts.append("\n".join(lines).rstrip())
    return "\n\n---\n\n".join(parts)


def format_candle_source_report_g38(conn: Any, *, hours: int = 24) -> str:
    from bot.research.market_events.signal_intelligence.candidate_g31 import load_g31_universe_symbols

    lines = ["Candle Source", "", "Binance Futures", ""]
    for sym in load_g31_universe_symbols(conn):
        st = candle_source_status_g38(conn, symbol=sym, hours=hours)
        lines.extend([
            st["symbol"],
            st["status"],
            str(st["count"]),
            "",
        ])
    return "\n".join(lines).rstrip()


def format_trend_history_report_g38(conn: Any, *, hours: int = 24) -> str:
    from bot.research.market_events.signal_intelligence.candidate_g31 import load_g31_universe_symbols

    target = BARS_24H_5M if hours >= 24 else max(MIN_BARS_FOR_TREND, hours * 12)
    lines = [f"Trend History ({hours}h 5m)", ""]
    for sym in load_g31_universe_symbols(conn):
        count = count_5m_bars(conn, symbol=sym, hours=hours)
        lines.extend([sym, f"{count}/{target}", ""])
    return "\n".join(lines).rstrip()


def _klines_to_bars(klines: list[list]) -> list[CandleBar]:
    return [
        CandleBar(
            open_ts=int(k[0] // 1000),
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            volume=float(k[5]),
        )
        for k in klines
    ]


def fetch_5m_from_binance(
    provider: BinanceMarketProvider,
    symbol: str,
    *,
    hours: int = 24,
    end_ts: int | None = None,
    conn: Any | None = None,
) -> tuple[list[CandleBar], str]:
    limit = min(1000, max(MIN_BARS_FOR_TREND, hours * 12))
    end = end_ts or int(time.time())
    if conn is not None:
        from bot.research.market_events.signal_intelligence.market_data_source_g01 import (
            fetch_symbol_market_data_g01,
        )
        data = fetch_symbol_market_data_g01(conn, symbol, end_ts=end, limit=limit, provider=provider)
        if data.bars:
            return data.bars, data.source
    pair = symbol_pair(symbol)
    klines = provider.fetch_futures_klines(pair, TIMEFRAME_5M, end, limit=limit) or []
    return _klines_to_bars(klines), "binance_futures"


def snapshot_fallback_bars_g38(
    conn: Any,
    symbol: str,
    *,
    hours: int = 24,
) -> list[CandleBar]:
    """Build synthetic 5m bars from G3 snapshot prices when live API fails."""
    col = _SNAPSHOT_PRICE_COLS.get(symbol.upper())
    if not col:
        return []
    since = int(time.time()) - hours * 3600
    rows = conn.execute(
        f"""
        SELECT snapshot_ts, {col} AS price
        FROM market_snapshots_g3
        WHERE snapshot_ts >= ? AND {col} IS NOT NULL
        ORDER BY snapshot_ts ASC
        """,
        (since,),
    ).fetchall()
    if not rows:
        return []

    buckets: dict[int, list[float]] = {}
    for r in rows:
        ts = int(r["snapshot_ts"])
        bucket = ts - (ts % 300)
        buckets.setdefault(bucket, []).append(float(r["price"]))

    bars: list[CandleBar] = []
    for open_ts in sorted(buckets):
        prices = buckets[open_ts]
        o, c = prices[0], prices[-1]
        hi, lo = max(prices), min(prices)
        bars.append(CandleBar(
            open_ts=open_ts,
            open=o,
            high=hi,
            low=lo,
            close=c,
            volume=0.0,
        ))
    return bars


def persist_bars_g38(
    conn: Any,
    *,
    symbol: str,
    bars: list[CandleBar],
    source: str,
) -> int:
    from bot.research.market_events.signal_intelligence.market_data_source_g01 import upsert_candles_g01

    return upsert_candles_g01(
        conn,
        symbol=symbol,
        bars=bars,
        source=source,
        venue=VENUE,
        timeframe=TIMEFRAME_5M,
    )


def ensure_symbol_history_g38(
    conn: Any,
    symbol: str,
    *,
    provider: BinanceMarketProvider | None = None,
    hours: int = 24,
    target_bars: int | None = None,
) -> SymbolHistoryResultG38:
    sym = symbol.upper()
    target = target_bars or (BARS_24H_5M if hours >= 24 else max(MIN_BARS_FOR_TREND, hours * 12))
    before = count_5m_bars(conn, symbol=sym, hours=hours)
    if before >= target:
        from bot.research.market_events.signal_intelligence.market_data_source_g01 import (
            refresh_symbol_candles_g01,
        )
        loaded = refresh_symbol_candles_g01(conn, sym, provider=provider, limit=12)
        after = count_5m_bars(conn, symbol=sym, hours=hours)
        logger.info("%s history complete (%s bars), refreshed %s recent", sym, before, loaded)
        return SymbolHistoryResultG38(
            symbol=sym,
            bars_before=before,
            bars_after=after,
            loaded=loaded,
            source="refresh",
            status="complete",
            message=f"{sym}\nhistory complete\nrefreshed {loaded} candles",
        )

    missing = target - before
    provider = provider or BinanceMarketProvider()
    loaded = 0
    source = "binance_futures"
    logger.info("%s missing %s candles\nloading...", sym, missing)

    try:
        bars, source = fetch_5m_from_binance(provider, sym, hours=hours, conn=conn)
        if bars:
            loaded = persist_bars_g38(conn, symbol=sym, bars=bars, source=source)
        elif before == 0:
            after = 0
            return SymbolHistoryResultG38(
                symbol=sym,
                bars_before=before,
                bars_after=after,
                loaded=0,
                source="none",
                status="failed",
                message=f"{sym}\nmissing\n{missing} candles\nno API data",
            )
    except Exception as exc:
        logger.warning("%s Binance fetch failed: %s — trying snapshot fallback", sym, exc)
        bars = snapshot_fallback_bars_g38(conn, sym, hours=hours)
        if bars:
            source = "snapshot_fallback"
            loaded = persist_bars_g38(conn, symbol=sym, bars=bars, source=source)
        else:
            after = count_5m_bars(conn, symbol=sym, hours=hours)
            return SymbolHistoryResultG38(
                symbol=sym,
                bars_before=before,
                bars_after=after,
                loaded=0,
                source="failed",
                status="failed",
                message=f"{sym}\nmissing\n{missing} candles\nAPI failed",
            )

    after = count_5m_bars(conn, symbol=sym, hours=hours)
    if after >= target:
        msg = f"{sym}\nhistory complete"
        status = "complete"
    else:
        still_missing = max(0, target - after)
        msg = f"{sym}\npartial\n{still_missing} candles remaining"
        status = "partial"
    logger.info("%s → %s bars (+%s)", sym, after, loaded)
    return SymbolHistoryResultG38(
        symbol=sym,
        bars_before=before,
        bars_after=after,
        loaded=loaded,
        source=source,
        status=status,
        message=msg,
    )


def ensure_universe_history_g38(
    conn: Any,
    symbols: tuple[str, ...] | list[str] | None = None,
    *,
    provider: BinanceMarketProvider | None = None,
    hours: int = 24,
) -> list[SymbolHistoryResultG38]:
    from bot.research.market_events.signal_intelligence.candidate_g31 import load_g31_universe_symbols

    syms = list(symbols) if symbols else list(load_g31_universe_symbols(conn))
    provider = provider or BinanceMarketProvider()
    return [
        ensure_symbol_history_g38(conn, sym, provider=provider, hours=hours)
        for sym in syms
    ]


def run_post_backfill_pipeline_g38(
    conn: Any,
    *,
    provider: BinanceMarketProvider | None = None,
) -> dict[str, Any]:
    """After backfill: snapshot → trends → liquidity → candidates (no wait for next cycle)."""
    from bot.research.market_events.signal_intelligence.candidate_g31 import (
        load_g31_universe_symbols,
        run_candidate_pipeline_g31,
    )
    from bot.research.market_events.signal_intelligence.liquidity_engine_g3 import (
        compute_liquidity_state_g3,
        persist_liquidity_state_g3,
    )
    from bot.research.market_events.signal_intelligence.recorder_g3 import record_market_snapshot_g3
    from bot.research.market_events.signal_intelligence.trend_windows_g3 import run_trend_detection_g3

    snapshot_id, _payload = record_market_snapshot_g3(conn, provider=provider)
    universe = load_g31_universe_symbols(conn)
    trends = run_trend_detection_g3(conn, snapshot_id=snapshot_id, symbols=universe)
    liquidity = compute_liquidity_state_g3(conn, snapshot_id=snapshot_id)
    persist_liquidity_state_g3(conn, snapshot_id=snapshot_id, state=liquidity)
    candidates = run_candidate_pipeline_g31(
        conn, snapshot_id=snapshot_id, trends=trends, liquidity=liquidity,
    )

    coverages: list[float] = []
    trend_scores: list[float] = []
    for c in candidates:
        if c.trend_coverage_pct is not None:
            coverages.append(float(c.trend_coverage_pct))
        if c.trend_score is not None and c.trend_score > 0:
            trend_scores.append(float(c.trend_score))

    return {
        "snapshot_id": snapshot_id,
        "trends": len(trends),
        "candidates": len(candidates),
        "avg_coverage_pct": round(sum(coverages) / len(coverages), 1) if coverages else 0.0,
        "symbols_with_trend": len(trend_scores),
        "max_trend_score": max(trend_scores) if trend_scores else 0.0,
    }


def run_history_backfill_g38(
    conn: Any,
    *,
    symbols: list[str] | None = None,
    hours: int = 24,
    run_pipeline: bool = True,
    provider: BinanceMarketProvider | None = None,
) -> dict[str, Any]:
    """Load last N hours of 5m candles for universe; optionally rebuild pipeline."""
    from bot.research.market_events.signal_intelligence.candidate_g31 import load_g31_universe_symbols

    target_symbols = symbols or list(load_g31_universe_symbols(conn))
    provider = provider or BinanceMarketProvider()
    results = ensure_universe_history_g38(
        conn, target_symbols, provider=provider, hours=hours,
    )

    log_lines = [r.message for r in results]
    for line in log_lines:
        print(line)
        print("")

    stats = {
        "symbols": len(results),
        "complete": sum(1 for r in results if r.status == "complete"),
        "partial": sum(1 for r in results if r.status == "partial"),
        "failed": sum(1 for r in results if r.status == "failed"),
        "bars_loaded": sum(r.loaded for r in results),
        "results": results,
    }

    if run_pipeline:
        pipe = run_post_backfill_pipeline_g38(conn, provider=provider)
        stats["pipeline"] = pipe
        print(
            f"Pipeline rebuilt: trends={pipe['trends']} candidates={pipe['candidates']} "
            f"avg_coverage={pipe['avg_coverage_pct']}%",
        )

    return stats


def format_history_backfill_summary_g38(stats: dict[str, Any]) -> str:
    lines = [
        "History Backfill",
        "",
        f"symbols={stats.get('symbols', 0)} complete={stats.get('complete', 0)} "
        f"partial={stats.get('partial', 0)} failed={stats.get('failed', 0)}",
        f"bars_loaded={stats.get('bars_loaded', 0)}",
        "",
    ]
    pipe = stats.get("pipeline")
    if pipe:
        lines.extend([
            "Pipeline",
            f"trends={pipe.get('trends', 0)}",
            f"candidates={pipe.get('candidates', 0)}",
            f"avg_coverage={pipe.get('avg_coverage_pct', 0)}%",
            "",
        ])
    return "\n".join(lines)
