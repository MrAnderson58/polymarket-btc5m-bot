"""Phase F.7.1 — terminal live dashboard (5s refresh)."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.dominance_context_f7 import classify_dominance
from bot.research.market_events.signal_intelligence.liquidation_intelligence_f7 import analyze_liquidations
from bot.research.market_events.signal_intelligence.market_score_f7 import compute_market_score
from bot.research.market_events.signal_intelligence.ops_reports_f71 import _today_bounds


def _clear_screen() -> None:
    if sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
    else:
        print("\n" + "=" * 48)


def _btc_snapshot(conn: Any) -> dict[str, Any]:
    from bot.research.market_events.signal_intelligence.candles import load_recent_candles
    from bot.research.market_events.signal_intelligence.funding_oi_history_f2 import _fetch_current_funding_oi

    dom = classify_dominance(conn, shock_symbol="BTC")
    bars = load_recent_candles(conn, symbol="BTC", venue="binance_futures", timeframe="5m", limit=24)
    ret = 0.0
    if len(bars) >= 2 and bars[0].close > 0:
        ret = (bars[-1].close / bars[0].close - 1.0) * 100.0

    liq = analyze_liquidations(conn, symbol="BTC", trend=None, shock_return=ret)
    market = compute_market_score(
        conn, report=None, trend=None,
        dominance_regime=dom.regime, liq_intel={"regime": liq.regime, "exhaustion": liq.exhaustion},
    )
    funding, oi = _fetch_current_funding_oi("BTC")
    vol_mult = 1.0
    if len(bars) >= 10:
        recent = sum(b.volume for b in bars[-3:]) / 3
        base = sum(b.volume for b in bars[-20:-3]) / max(len(bars) - 3, 1)
        vol_mult = recent / base if base > 0 else 1.0

    trend_label = "↑" if ret > 0.3 else "↓" if ret < -0.3 else "→"
    return {
        "market_score": market.score,
        "funding": funding,
        "oi": oi,
        "volume": vol_mult,
        "trend": f"{trend_label} {ret:+.1f}%",
        "dominance": dom.regime,
    }


def _today_stats(conn: Any) -> dict[str, Any]:
    start, _, _ = _today_bounds()
    signals = conn.execute(
        "SELECT COUNT(*) AS n FROM market_events WHERE event_ts >= ?", (start,),
    ).fetchone()["n"]
    telegram = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_signal_priority_f5
        WHERE telegram_sent = 1 AND created_at >= ?
        """,
        (start,),
    ).fetchone()["n"]
    filtered = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_events_signal_reports_f5 f
        JOIN market_events e ON e.id = f.event_id
        WHERE e.event_ts >= ? AND f.telegram_eligible = 0
        """,
        (start,),
    ).fetchone()["n"]
    paper_open = conn.execute(
        "SELECT COUNT(*) AS n FROM paper_strategy_runs WHERE exit_ts IS NULL AND entry_ts IS NOT NULL",
    ).fetchone()["n"]
    pnl_row = conn.execute(
        """
        SELECT SUM(net_return) AS s FROM paper_strategy_runs
        WHERE exit_ts >= ? AND net_return IS NOT NULL
        """,
        (start,),
    ).fetchone()
    return {
        "signals": int(signals or 0),
        "telegram": int(telegram or 0),
        "filtered": int(filtered or 0),
        "paper_open": int(paper_open or 0),
        "paper_pnl": float(pnl_row["s"] or 0) if pnl_row else 0.0,
    }


def render_live_dashboard(conn: Any) -> str:
    btc = _btc_snapshot(conn)
    today = _today_stats(conn)
    now = time.strftime("%H:%M:%S UTC", time.gmtime())
    fund = btc["funding"]
    fund_s = f"{fund:+.3f}%" if fund is not None else "n/a"
    oi = btc["oi"]
    oi_s = f"{oi:,.0f}" if oi is not None else "n/a"
    pnl = today["paper_pnl"]
    pnl_sign = "+" if pnl >= 0 else ""
    return "\n".join([
        f"LIVE DASHBOARD (F.7.1)  {now}",
        "",
        "BTC",
        "",
        f"Market Score {int(round(btc['market_score']))}",
        "",
        "Funding",
        fund_s,
        "",
        "OI",
        oi_s,
        "",
        "Volume",
        f"{btc['volume']:.1f}×",
        "",
        "Trend",
        btc["trend"],
        "",
        f"Dominance: {btc['dominance']}",
        "",
        "─" * 24,
        "",
        f"Signals today {today['signals']}",
        "",
        f"Telegram sent {today['telegram']}",
        "",
        f"Filtered {today['filtered']}",
        "",
        f"Paper open {today['paper_open']}",
        "",
        f"Paper pnl {pnl_sign}{pnl:.1f}%",
        "",
        "(Ctrl+C to exit)",
    ])


def run_live_dashboard(*, interval_sec: float = 5.0) -> None:
    print("Starting live dashboard…")
    try:
        while True:
            with market_events_connection() as conn:
                apply_migrations(conn)
                text = render_live_dashboard(conn)
            _clear_screen()
            print(text)
            time.sleep(interval_sec)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
