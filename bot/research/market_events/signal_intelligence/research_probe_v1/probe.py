"""Canonical Research Lake probe — shared symbol/time for all engines."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from bot.research.market_events.signal_intelligence.research_lake_v1.loader import (
    load_latest_lake_trade,
    research_lake_row_count,
)


def probe_time_label(opened_at: int | None, *, now: int | None = None) -> str:
    """Human label: 'now' if within 24h else ISO date."""
    if not opened_at:
        return "—"
    now_i = int(now or time.time())
    if now_i - int(opened_at) <= 86400:
        return "now"
    return datetime.fromtimestamp(int(opened_at), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def canonical_probe(conn: Any) -> dict[str, Any]:
    """
    Latest CLOSED lake trade by opened_at — same probe for all research engines.
    """
    trade = load_latest_lake_trade(conn)
    n_lake = research_lake_row_count(conn)
    if not trade:
        return {
            "ok": False,
            "source": "research_lake_v1",
            "n_lake": n_lake,
            "trade_id": None,
            "symbol": None,
            "opened_at": None,
            "closed_at": None,
            "direction": None,
            "regime": None,
            "time_label": "—",
        }
    opened = trade.get("opened_at") or trade.get("closed_at")
    try:
        opened_i = int(opened) if opened is not None else None
    except Exception:
        opened_i = None
    return {
        "ok": True,
        "source": "research_lake_v1",
        "n_lake": n_lake,
        "trade_id": int(trade.get("trade_id") or trade.get("id") or 0),
        "symbol": str(trade.get("symbol") or ""),
        "opened_at": opened_i,
        "closed_at": int(trade.get("closed_at") or 0) if trade.get("closed_at") else None,
        "direction": str(trade.get("direction") or ""),
        "regime": str(trade.get("regime") or trade.get("market_regime") or ""),
        "time_label": probe_time_label(opened_i),
        "trade": trade,
    }


def probe_matches(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    """True when symbol + trade_id align with canonical probe."""
    if not a or not b:
        return False
    if not a.get("ok") or not b.get("ok"):
        return False
    sym_a = str(a.get("symbol") or "").upper()
    sym_b = str(b.get("symbol") or "").upper()
    if sym_a != sym_b:
        return False
    tid_a = int(a.get("trade_id") or 0)
    tid_b = int(b.get("trade_id") or 0)
    if tid_a and tid_b and tid_a != tid_b:
        return False
    return True


def current_market_from_probe(
    probe: dict[str, Any],
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not probe.get("ok"):
        return None
    out = {
        "trade_id": probe.get("trade_id"),
        "symbol": probe.get("symbol"),
        "direction": probe.get("direction"),
        "opened_at": probe.get("opened_at"),
        "regime": probe.get("regime"),
        "probe_source": probe.get("source"),
    }
    if extra:
        out.update(extra)
    return out


__all__ = [
    "canonical_probe",
    "current_market_from_probe",
    "probe_matches",
    "probe_time_label",
]
