"""Phase G.3.6 — /analyze SYMBOL on-demand market report."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.candles import load_recent_candles
from bot.research.market_events.signal_intelligence.market_memory_g36 import _claude_summary, _latest_candidate


def _trend_label(score: float | None) -> str:
    if score is None:
        return "—"
    if score >= 60:
        return "Bullish"
    if score <= 40:
        return "Bearish"
    return "Neutral"


def format_analyze_symbol_g36(conn: Any, symbol: str) -> str:
    sym = symbol.upper().strip()
    if not sym:
        return "Usage: /analyze BTC"

    snap = conn.execute(
        """
        SELECT funding, open_interest, fear_greed, btc_dominance, atr
        FROM market_snapshots_g3 ORDER BY snapshot_ts DESC LIMIT 1
        """,
    ).fetchone()
    cand = _latest_candidate(conn, sym) or {}

    ms = cand.get("market_score")
    conf = cand.get("confidence")
    trend_score = cand.get("trend_score")
    state = str(cand.get("candidate_state") or "")

    bars = load_recent_candles(conn, symbol=sym, timeframe="5m", limit=13)
    ret = None
    if len(bars) >= 2 and bars[0].close > 0:
        ret = round((bars[-1].close / bars[0].close - 1.0) * 100.0, 2)

    signal_row = conn.execute(
        """
        SELECT status FROM market_live_signals_g3
        WHERE symbol = ? AND status IN ('ACTIVE', 'TP1_HIT')
        ORDER BY created_at DESC LIMIT 1
        """,
        (sym,),
    ).fetchone()

    claude = _claude_summary(conn, sym)

    lines = [
        sym,
        "",
        "Market Score",
        str(int(ms)) if ms is not None else "—",
        "",
        "Confidence",
        f"{float(conf):.1f}" if conf is not None else "—",
        "",
        "Trend",
        _trend_label(float(trend_score) if trend_score is not None else None),
        "",
        "Funding",
        f"{float(snap['funding']):.4f}" if snap and snap["funding"] is not None else "—",
        "",
        "OI",
        f"{float(snap['open_interest']):.0f}" if snap and snap["open_interest"] is not None else "—",
        "",
        "Fear",
        str(int(snap["fear_greed"])) if snap and snap["fear_greed"] is not None else "—",
    ]
    if ret is not None:
        lines.extend(["", "24h move", f"{ret:+.2f}%"])

    lines.extend(["", "Claude opinion", claude or "—"])

    if signal_row:
        lines.extend(["", "Signal", str(signal_row["status"])])
    elif state == "accepted":
        lines.extend(["", "Signal", "Accepted"])
    else:
        lines.extend(["", "No signal yet"])

    return "\n".join(lines)
