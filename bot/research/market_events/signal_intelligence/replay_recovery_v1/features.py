"""Feature comparison for Replay-rejected winners vs losers (research-only)."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from bot.research.market_events.signal_intelligence.replay_recovery_v1.classify import (
    pnl_of,
)

NUMERIC_KEYS = (
    "atr",
    "adx",
    "rsi",
    "ema20",
    "ema50",
    "ema200",
    "macd",
    "funding",
    "oi_delta",
    "oi",
    "timeline_similarity",
    "fingerprint_similarity",
    "dna",
    "brain",
    "edge",
    "causality",
    "confidence",
    "replay",
    "historical_wr",
    "historical_ev",
    "historical_pf",
)

# aliases pulled from lake feature blobs
NUMERIC_ALIASES: dict[str, tuple[str, ...]] = {
    "atr": ("atr", "ATR"),
    "adx": ("adx", "ADX"),
    "rsi": ("rsi", "RSI"),
    "ema20": ("ema20", "ema_20", "EMA20"),
    "ema50": ("ema50", "ema_50", "EMA50"),
    "ema200": ("ema200", "ema_200", "EMA200"),
    "macd": ("macd", "MACD"),
    "funding": ("funding", "funding_rate"),
    "oi_delta": ("oi_delta", "oiDelta"),
    "oi": ("oi", "open_interest"),
}


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def get_numeric(row: dict[str, Any], key: str) -> float | None:
    aliases = NUMERIC_ALIASES.get(key, (key,))
    for a in aliases:
        x = _f(row.get(a))
        if x is not None:
            return x
    return _f(row.get(key))


def session_bucket(opened_at: Any) -> str:
    try:
        ts = int(opened_at or 0)
    except Exception:
        return "unknown"
    if ts <= 0:
        return "unknown"
    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    if 0 <= hour < 8:
        return "asia"
    if 8 <= hour < 16:
        return "europe"
    return "us"


def mean(xs: list[float]) -> float | None:
    if not xs:
        return None
    return round(sum(xs) / len(xs), 6)


def compare_numeric(winners: list[dict[str, Any]], losers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in NUMERIC_KEYS:
        w_vals = [v for r in winners if (v := get_numeric(r, key)) is not None]
        l_vals = [v for r in losers if (v := get_numeric(r, key)) is not None]
        mw, ml = mean(w_vals), mean(l_vals)
        delta = None if mw is None or ml is None else round(mw - ml, 6)
        out.append({
            "feature": key,
            "n_winners": len(w_vals),
            "n_losers": len(l_vals),
            "mean_winner": mw,
            "mean_loser": ml,
            "delta_w_minus_l": delta,
        })
    return out


def compare_categorical(
    winners: list[dict[str, Any]],
    losers: list[dict[str, Any]],
    *,
    key: str,
    top: int = 12,
) -> dict[str, Any]:
    def _counts(rows: list[dict[str, Any]]) -> Counter[str]:
        c: Counter[str] = Counter()
        for r in rows:
            if key == "session":
                v = session_bucket(r.get("opened_at"))
            else:
                v = str(r.get(key) or r.get("market_regime") or "unknown")
            c[v] += 1
        return c

    cw, cl = _counts(winners), _counts(losers)
    keys = set(cw) | set(cl)
    rows = []
    for k in keys:
        nw, nl = cw.get(k, 0), cl.get(k, 0)
        rows.append({
            "value": k,
            "n_winners": nw,
            "n_losers": nl,
            "winner_share": round(nw / len(winners), 4) if winners else 0.0,
            "loser_share": round(nl / len(losers), 4) if losers else 0.0,
        })
    rows.sort(key=lambda x: -(x["n_winners"] + x["n_losers"]))
    return {"key": key, "rows": rows[:top]}


def max_drawdown(pnls: list[float]) -> float:
    if not pnls:
        return 0.0
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return round(abs(max_dd), 6)


__all__ = [
    "NUMERIC_KEYS",
    "compare_categorical",
    "compare_numeric",
    "get_numeric",
    "max_drawdown",
    "mean",
    "session_bucket",
]
