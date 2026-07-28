"""Similar trade explorer for manual inspection."""

from __future__ import annotations

import json
from typing import Any

from bot.research.market_events.expectancy_intelligence.row_features import (
    row_to_features,
)
from bot.research.market_events.signal_intelligence.trade_intelligence_s55 import (
    find_similar_trades,
)

_TABLE = "market_events_trade_features_s55"


def _normalize_symbol(symbol: str) -> str:
    s = str(symbol or "BTC").upper().strip()
    if not s.endswith("USDT") and len(s) <= 6:
        return f"{s}USDT"
    return s


def _anchor_features_for_symbol(conn: Any, symbol: str) -> dict[str, Any] | None:
    sym = _normalize_symbol(symbol)
    coin = sym.replace("USDT", "")
    try:
        row = conn.execute(
            f"""
            SELECT * FROM {_TABLE}
            WHERE symbol IN (?, ?)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (sym, coin),
        ).fetchone()
    except Exception:
        row = None
    if row:
        return row_to_features(row)
    return {"symbol": sym, "direction": "LONG", "coin": coin}


def build_similar_trades_report(
    conn: Any,
    *,
    symbol: str,
    limit: int = 20,
) -> dict[str, Any]:
    feats = _anchor_features_for_symbol(conn, symbol)
    if feats is None:
        return {"symbol": symbol, "anchor": None, "neighbors": []}
    neighbors = find_similar_trades(conn, feats, k=limit)
    items: list[dict[str, Any]] = []
    for n in neighbors:
        sim = float(n.get("similarity") or 0.0)
        items.append(
            {
                "distance": round(1.0 - sim, 4),
                "similarity": sim,
                "pnl_pct": n.get("pnl_pct"),
                "result": n.get("result"),
                "symbol": n.get("symbol"),
                "direction": n.get("direction"),
                "features": {
                    "fear_greed": n.get("fear_greed"),
                    "funding": n.get("funding"),
                    "trend": n.get("trend"),
                    "volatility": n.get("volatility"),
                    "market_regime": n.get("market_regime"),
                },
                "outcome": {
                    "exit_reason": n.get("exit_reason"),
                    "mfe_pct": n.get("mfe_pct"),
                    "mae_pct": n.get("mae_pct"),
                    "reached_tp1": n.get("reached_tp1"),
                    "stopped": n.get("stopped"),
                },
                "closed_at": n.get("closed_at"),
            }
        )
    return {"symbol": symbol, "anchor": feats, "neighbors": items}


def format_similar_trades(conn: Any, *, symbol: str, limit: int = 20) -> str:
    data = build_similar_trades_report(conn, symbol=symbol, limit=limit)
    anchor = data.get("anchor") or {}
    lines = [
        f"SIMILAR TRADES — anchor {data['symbol']} {anchor.get('direction', '')}",
        f"  anchor_features={json.dumps({k: anchor.get(k) for k in ('symbol', 'direction', 'fear_greed', 'trend', 'funding')}, default=str)}",
        "",
    ]
    for i, it in enumerate(data["neighbors"], 1):
        lines.append(
            f"{i:2}. dist={it['distance']:.3f} sim={it['similarity']:.3f}  "
            f"{it['symbol']} {it['direction']}  PnL={it['pnl_pct']}%  {it['result']}  "
            f"feat={it['features']}  outcome={it['outcome']}"
        )
    if not data["neighbors"]:
        lines.append("  (no similar closed trades in pool)")
    return "\n".join(lines)
