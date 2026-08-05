"""Lightweight trade metrics for Reality Validation (research-only)."""

from __future__ import annotations

import math
from typing import Any, Sequence


def extract_pnls(trades: Sequence[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    for t in trades:
        try:
            out.append(float(t.get("pnl")))
        except Exception:
            continue
    return out


def basic_metrics(pnls: Sequence[float]) -> dict[str, Any]:
    xs = [float(x) for x in pnls]
    n = len(xs)
    if n == 0:
        return {
            "n": 0,
            "pnl": 0.0,
            "wr": None,
            "pf": None,
            "avg": None,
            "sharpe": None,
            "max_dd": 0.0,
            "expectancy": None,
        }
    wins = [x for x in xs if x > 0]
    losses = [x for x in xs if x < 0]
    pnl = float(sum(xs))
    wr = len(wins) / n
    gw = sum(wins)
    gl = abs(sum(losses))
    pf = (gw / gl) if gl > 1e-12 else (None if gw <= 0 else 99.0)
    avg = pnl / n
    # trade-level sharpe
    mu = avg
    var = sum((x - mu) ** 2 for x in xs) / max(n - 1, 1)
    sd = math.sqrt(var) if var > 0 else 0.0
    sharpe = (mu / sd * math.sqrt(n)) if sd > 1e-12 else None
    # equity max DD on unit curve
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for x in xs:
        eq += x
        peak = max(peak, eq)
        dd = (eq - peak) if peak > 0 else min(0.0, eq)
        # relative to peak magnitude; if peak<=0 use absolute drop
        if peak > 1e-12:
            max_dd = min(max_dd, (eq - peak) / abs(peak) if peak else 0.0)
        else:
            max_dd = min(max_dd, eq - peak)
    return {
        "n": n,
        "pnl": round(pnl, 6),
        "wr": round(wr, 6),
        "pf": round(pf, 6) if pf is not None else None,
        "avg": round(avg, 6),
        "sharpe": round(sharpe, 6) if sharpe is not None else None,
        "max_dd": round(float(max_dd), 6),
        "expectancy": round(avg, 6),
    }


def sort_chrono(trades: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(trades, key=lambda t: int(t.get("opened_at") or 0))


def symbol_of(t: dict[str, Any]) -> str:
    s = str(t.get("symbol") or t.get("coin") or "UNK").upper()
    for prefix in ("BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "AVAX", "LINK", "MATIC"):
        if s.startswith(prefix):
            return prefix
    return s.split("-")[0].split("/")[0][:8] or "UNK"


def month_key(ts: int) -> str:
    if ts <= 0:
        return "unknown"
    # UTC YYYY-MM
    import datetime as _dt

    return _dt.datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m")


def infer_regime(t: dict[str, Any]) -> str:
    """Map row fields → bull / bear / range / mixed."""
    raw = str(
        t.get("regime")
        or t.get("current_regime")
        or t.get("market_regime")
        or ""
    ).upper()
    if any(k in raw for k in ("BULL", "UP", "LONG_BIAS")):
        return "bull"
    if any(k in raw for k in ("BEAR", "DOWN", "SHORT_BIAS")):
        return "bear"
    if any(k in raw for k in ("RANGE", "SIDE", "CHOP", "NEUTRAL")):
        return "range"
    # fallback from direction×pnl sign heuristic
    d = str(t.get("direction") or "").upper()
    try:
        pnl = float(t.get("pnl") or 0)
    except Exception:
        pnl = 0.0
    if d in ("LONG", "BUY", "UP") and pnl > 0:
        return "bull"
    if d in ("SHORT", "SELL", "DOWN") and pnl > 0:
        return "bear"
    if abs(pnl) < 1e-9:
        return "range"
    return "mixed"


__all__ = [
    "basic_metrics",
    "extract_pnls",
    "infer_regime",
    "month_key",
    "sort_chrono",
    "symbol_of",
]
