"""Load Research Lake + preload candles/snapshots once (no N+1)."""

from __future__ import annotations

from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)


def _row(r: Any) -> dict[str, Any]:
    if hasattr(r, "keys"):
        return {k: r[k] for k in r.keys()}
    return dict(r)


def normalize_trade(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    features = out.get("features") if isinstance(out.get("features"), dict) else {}
    macro = out.get("macro") if isinstance(out.get("macro"), dict) else {}
    news = out.get("news") if isinstance(out.get("news"), dict) else {}
    patterns = out.get("patterns") if isinstance(out.get("patterns"), dict) else {}
    alpha = out.get("alpha_labels") if isinstance(out.get("alpha_labels"), dict) else {}
    opt = out.get("optimizer_state") if isinstance(out.get("optimizer_state"), dict) else {}

    for src in (features, macro, news, patterns):
        for k, v in src.items():
            if out.get(k) is None and v is not None:
                out[k] = v

    out["trade_id"] = int(out.get("trade_id") or out.get("id") or 0)
    out["symbol"] = str(out.get("symbol") or "").upper()
    out["direction"] = str(out.get("direction") or "").upper()
    out["gate_decision"] = str(out.get("gate_decision") or out.get("gate") or "").upper()
    out["regime"] = str(out.get("regime") or out.get("market_regime") or "").upper()
    disc = alpha.get("discovery") if isinstance(alpha.get("discovery"), dict) else {}
    out["alpha_cluster"] = str(disc.get("cluster") or alpha.get("cluster") or "")
    out["edge_cluster"] = str(
        patterns.get("edge_cluster")
        or out.get("edge_cluster")
        or disc.get("edge_cluster")
        or ""
    )
    out["pattern"] = str(patterns.get("pattern") or out.get("pattern") or "")
    out["optimizer_state"] = opt or out.get("optimizer_state") or {}
    out["news_score"] = _safe_float(news.get("news_score") or out.get("news_score"))
    out["ai_score"] = _safe_float(news.get("ai_score") or out.get("ai_score"))
    pnl = _safe_float(out.get("pnl"))
    if pnl is None:
        pnl = _safe_float(out.get("pnl_pct")) or _safe_float(out.get("pnl_usd"))
    out["pnl"] = float(pnl or 0.0)
    entry_ts = out.get("opened_at") or out.get("created_at") or out.get("closed_at")
    try:
        out["entry_ts"] = int(entry_ts) if entry_ts is not None else 0
    except Exception:
        out["entry_ts"] = 0
    try:
        out["closed_ts"] = int(out.get("closed_at") or out["entry_ts"])
    except Exception:
        out["closed_ts"] = out["entry_ts"]
    out["entry"] = _safe_float(out.get("entry"))
    out["exit"] = _safe_float(out.get("exit"))
    return out


def load_replay_trades(
    conn: Any,
    *,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stats: dict[str, Any] = {"source": "empty", "n_raw": 0}
    rows: list[dict[str, Any]] = []
    try:
        from bot.research.market_events.signal_intelligence.research_lake_v1 import (
            load_research_lake_rows,
            research_lake_row_count,
        )

        n = research_lake_row_count(conn)
        stats["n_lake"] = n
        if n > 0:
            rows = load_research_lake_rows(conn, limit=limit)
            stats["source"] = "research_lake_v1"
        else:
            from bot.research.market_events.signal_intelligence.market_math_v1.dataset import (
                load_market_math_dataset,
            )

            rows = load_market_math_dataset(conn, limit=limit, print_stats=False)
            stats["source"] = "market_math_fallback"
    except Exception as exc:
        stats["error"] = str(exc)
        return [], stats
    stats["n_raw"] = len(rows)
    return [normalize_trade(r) for r in rows], stats


def preload_candles_by_symbol(
    conn: Any,
    symbols: list[str],
    *,
    timeframe: str = "5m",
    max_per_symbol: int = 5000,
) -> dict[str, list[dict[str, Any]]]:
    """One-shot candle preload; returns symbol -> sorted bars."""
    out: dict[str, list[dict[str, Any]]] = {s: [] for s in symbols}
    if not symbols:
        return out
    try:
        # Pull a generous window once (no per-trade query).
        cur = conn.execute(
            """
            SELECT symbol, open_ts, open, high, low, close, volume
            FROM market_events_historical_candles
            WHERE timeframe = ?
            ORDER BY symbol ASC, open_ts ASC
            """,
            (timeframe,),
        )
        counts: dict[str, int] = {s: 0 for s in symbols}
        sym_set = {s.upper().replace("USDT", "") for s in symbols}
        for r in cur:
            d = _row(r)
            raw = str(d.get("symbol") or "").upper()
            base = raw.replace("USDT", "")
            if base not in sym_set and raw not in sym_set:
                continue
            key = base if base in out or base in sym_set else raw
            # map to requested key
            target = None
            for s in symbols:
                if s.upper().replace("USDT", "") == base or s.upper() == raw:
                    target = s
                    break
            if target is None:
                continue
            if counts[target] >= max_per_symbol:
                continue
            counts[target] += 1
            out[target].append({
                "open_ts": int(d["open_ts"]),
                "open": float(d["open"]),
                "high": float(d["high"]),
                "low": float(d["low"]),
                "close": float(d["close"]),
                "volume": float(d.get("volume") or 0),
            })
    except Exception:
        pass
    return out


def preload_snapshots_g3(conn: Any, *, limit: int = 200_000) -> list[dict[str, Any]]:
    """Load market_snapshots_g3 ordered by time (single scan)."""
    out: list[dict[str, Any]] = []
    try:
        cur = conn.execute(
            """
            SELECT snapshot_ts, funding, open_interest AS oi, liquidations, volume,
                   atr, btc_price, eth_price, btc_dominance AS dominance, fear_greed
            FROM market_snapshots_g3
            ORDER BY snapshot_ts ASC
            LIMIT ?
            """,
            (limit,),
        )
        for r in cur:
            d = _row(r)
            d["snapshot_ts"] = int(d.get("snapshot_ts") or 0)
            out.append(d)
    except Exception:
        pass
    return out


def nearest_by_ts(
    items: list[dict[str, Any]],
    ts: int,
    *,
    key: str = "open_ts",
) -> dict[str, Any] | None:
    """Binary search nearest item with item[key] <= ts (else closest)."""
    if not items:
        return None
    times = [int(x.get(key) or 0) for x in items]
    arr = np.asarray(times, dtype=np.int64)
    i = int(np.searchsorted(arr, ts, side="right") - 1)
    if i < 0:
        return items[0]
    if i >= len(items):
        return items[-1]
    return items[i]


__all__ = [
    "load_replay_trades",
    "nearest_by_ts",
    "normalize_trade",
    "preload_candles_by_symbol",
    "preload_snapshots_g3",
]
