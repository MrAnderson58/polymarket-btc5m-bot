"""Phase G.3.6 — Market Memory (historical per-symbol state, written every minute)."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.candles import load_recent_candles
from bot.research.market_events.signal_intelligence.config import G36_MEMORY_INTERVAL_SEC
from bot.research.market_events.signal_intelligence.health_g3 import get_g3_ops_state, set_g3_ops_state

_TABLE = "market_market_memory"


def _latest_snapshot(conn: Any) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT funding, open_interest, fear_greed, btc_dominance, volume, atr,
               btc_price, snapshot_ts
        FROM market_snapshots_g3 ORDER BY snapshot_ts DESC LIMIT 1
        """,
    ).fetchone()
    return dict(row) if row else None


def _symbol_price(conn: Any, symbol: str) -> float | None:
    bars = load_recent_candles(conn, symbol=symbol, timeframe="5m", limit=3)
    if bars:
        return float(bars[-1].close)
    col = f"{symbol.lower()}_price"
    if col in ("btc_price", "eth_price", "sol_price", "bnb_price"):
        snap = conn.execute(
            f"SELECT {col} AS p FROM market_snapshots_g3 ORDER BY snapshot_ts DESC LIMIT 1",
        ).fetchone()
        if snap and snap["p"]:
            return float(snap["p"])
    return None


def _latest_candidate(conn: Any, symbol: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT trend_score, market_score, confidence, liquidity_score,
               candidate_state, btc_alignment
        FROM market_candidate_g31
        WHERE symbol = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (symbol.upper(),),
    ).fetchone()
    return dict(row) if row else None


def _claude_summary(conn: Any, symbol: str) -> str | None:
    row = conn.execute(
        """
        SELECT summary_ru, market_story FROM market_events_ai_research_g2
        WHERE symbol = ? ORDER BY created_at DESC LIMIT 1
        """,
        (symbol.upper(),),
    ).fetchone()
    if not row:
        return None
    return str(row["summary_ru"] or row["market_story"] or "")[:500] or None


def append_market_memory_g36(conn: Any) -> int:
    from bot.research.market_events.signal_intelligence.candidate_g31 import load_g31_universe_symbols
    from bot.research.market_events.signal_intelligence.watchlist_g36 import list_watchlist_symbols_g36

    now = int(time.time())
    snap = _latest_snapshot(conn) or {}
    symbols = set(load_g31_universe_symbols(conn))
    symbols.update(list_watchlist_symbols_g36(conn))
    symbols.add("BTC")
    symbols.add("ETH")

    n = 0
    for sym in sorted(symbols):
        if not sym:
            continue
        cand = _latest_candidate(conn, sym) or {}
        price = _symbol_price(conn, sym)
        insert_returning_id(
            conn,
            f"""
            INSERT INTO {_TABLE} (
              ts, symbol, price, funding, oi, fear, dominance, volume, atr,
              btc_regime, trend, liquidity, market_score, confidence,
              claude_summary, candidate_state, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                sym.upper(),
                price,
                snap.get("funding"),
                snap.get("open_interest"),
                snap.get("fear_greed"),
                snap.get("btc_dominance"),
                snap.get("volume"),
                snap.get("atr"),
                cand.get("btc_alignment"),
                cand.get("trend_score"),
                cand.get("liquidity_score"),
                cand.get("market_score"),
                cand.get("confidence"),
                _claude_summary(conn, sym),
                cand.get("candidate_state"),
                now,
            ),
        )
        n += 1

    set_g3_ops_state(conn, "last_memory_ts", str(now))
    set_g3_ops_state(conn, "memory_rows_last", str(n))
    return n


def maybe_append_market_memory_g36(conn: Any) -> int:
    from bot.research.market_events.signal_intelligence.config import G36_ENABLED
    if not G36_ENABLED:
        return 0
    now = int(time.time())
    last_raw = get_g3_ops_state(conn, "last_memory_ts")
    last = int(last_raw) if last_raw else 0
    if last and (now - last) < G36_MEMORY_INTERVAL_SEC:
        return 0
    return append_market_memory_g36(conn)


def format_market_memory_cli_g36(conn: Any, symbol: str) -> str:
    sym = symbol.upper()
    since = int(time.time()) - 86400
    rows = conn.execute(
        f"""
        SELECT ts, trend, funding, oi, fear, market_score, confidence, candidate_state
        FROM {_TABLE}
        WHERE symbol = ? AND ts >= ?
        ORDER BY ts ASC
        """,
        (sym, since),
    ).fetchall()

    if not rows:
        return f"Market Memory — no data for {sym} yet. Run g3-run."

    latest = dict(rows[-1])
    trend_vals = [float(r["trend"]) for r in rows if r["trend"] is not None]
    fund_vals = [float(r["funding"]) for r in rows if r["funding"] is not None]
    oi_vals = [float(r["oi"]) for r in rows if r["oi"] is not None]
    fear_vals = [float(r["fear"]) for r in rows if r["fear"] is not None]
    ms_vals = [float(r["market_score"]) for r in rows if r["market_score"] is not None]
    conf_vals = [float(r["confidence"]) for r in rows if r["confidence"] is not None]

    def _avg(vals: list[float]) -> str:
        return f"{sum(vals)/len(vals):.2f}" if vals else "—"

    lines = [
        f"Market Memory — {sym}",
        "",
        "24h",
        f"{len(rows)} snapshots",
        "",
        "Trend",
        _avg(trend_vals),
        "",
        "Funding",
        _avg(fund_vals),
        "",
        "OI",
        _avg(oi_vals),
        "",
        "Fear",
        _avg(fear_vals),
        "",
        "Market Score",
        _avg(ms_vals),
        "",
        "Confidence",
        _avg(conf_vals),
        "",
        "Latest state",
        str(latest.get("candidate_state") or "—"),
    ]
    return "\n".join(lines)


def market_memory_dashboard_g36(conn: Any) -> dict[str, Any]:
    rows = conn.execute(
        f"""
        SELECT symbol, ts, price, funding, market_score, confidence, candidate_state
        FROM {_TABLE} ORDER BY ts DESC LIMIT 200
        """,
    ).fetchall()
    by_symbol: dict[str, list[dict]] = {}
    for r in rows:
        sym = str(r["symbol"])
        by_symbol.setdefault(sym, []).append(dict(r))

    last_ts = get_g3_ops_state(conn, "last_memory_ts")
    return {
        "tab": "Market Memory",
        "last_write_ts": int(last_ts) if last_ts else None,
        "total_rows": conn.execute(f"SELECT COUNT(*) AS n FROM {_TABLE}").fetchone()["n"],
        "recent": [dict(r) for r in rows[:50]],
        "symbols": list(by_symbol.keys())[:30],
    }
