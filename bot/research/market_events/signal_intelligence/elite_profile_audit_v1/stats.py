"""Pure stats helpers for Elite Profile Audit V1."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Sequence

from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    normalize_coin,
    session_from_hour,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import trade_metrics

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _f(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def pnl_of(row: dict[str, Any]) -> float | None:
    return _f(row.get("pnl"))


def is_closed(row: dict[str, Any]) -> bool:
    r = str(row.get("result") or "").upper()
    if r in ("OPEN", "UNKNOWN", "REJECTED", ""):
        # still closed if pnl present and not rejected-only
        if r == "REJECTED":
            return False
        return pnl_of(row) is not None
    return r in ("WIN", "LOSS", "BE", "BREAKEVEN")


def opened_parts(row: dict[str, Any]) -> dict[str, Any]:
    try:
        ts = int(row.get("opened_at") or 0)
    except Exception:
        ts = 0
    if ts <= 0:
        return {"hour": None, "weekday": None, "session": None, "month": None, "year": None}
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    sess = session_from_hour(dt.hour)
    if sess == "NewYork":
        sess = "NY"
    return {
        "hour": dt.hour,
        "weekday": WEEKDAYS[dt.weekday()],
        "session": sess,
        "month": f"{dt.year}-{dt.month:02d}",
        "year": dt.year,
    }


def chi2_pvalue_2x2(a: int, b: int, c: int, d: int) -> float:
    """
    Chi-square independence p-value for 2x2 (approx via survival of chi2 df=1).
    a=elite&feature, b=elite&~feature, c=~elite&feature, d=~elite&~feature
    """
    n = a + b + c + d
    if n <= 0:
        return 1.0
    row1, row2 = a + b, c + d
    col1, col2 = a + c, b + d
    if row1 == 0 or row2 == 0 or col1 == 0 or col2 == 0:
        return 1.0
    ea = row1 * col1 / n
    eb = row1 * col2 / n
    ec = row2 * col1 / n
    ed = row2 * col2 / n
    chi = 0.0
    for obs, exp in ((a, ea), (b, eb), (c, ec), (d, ed)):
        if exp <= 0:
            continue
        chi += (obs - exp) ** 2 / exp
    # P(chi2_1 > chi) ≈ erfc(sqrt(chi/2))
    return float(max(0.0, min(1.0, math.erfc(math.sqrt(max(chi, 0.0) / 2.0)))))


def rate(counter: Counter[str], key: str, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(100.0 * counter.get(key, 0) / total, 2)


def distribution(rows: Sequence[dict[str, Any]], key_fn) -> list[dict[str, Any]]:
    c: Counter[str] = Counter()
    pnls: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        k = key_fn(r)
        if k is None or k == "":
            continue
        k = str(k)
        c[k] += 1
        p = pnl_of(r)
        if p is not None:
            pnls[k].append(p)
    n = max(1, sum(c.values()))
    out = []
    for k, cnt in c.most_common():
        met = trade_metrics(pnls.get(k) or [])
        out.append({
            "key": k,
            "n": cnt,
            "pct": round(100.0 * cnt / n, 2),
            "wr": met.get("wr"),
            "pf": met.get("pf"),
            "ev": met.get("ev"),
        })
    return out


def coin_of(row: dict[str, Any]) -> str:
    return normalize_coin(row.get("symbol")) or "UNKNOWN"


def direction_of(row: dict[str, Any]) -> str:
    d = str(row.get("direction") or "").upper()
    if d in ("UP", "BUY"):
        return "LONG"
    if d in ("DOWN", "SELL"):
        return "SHORT"
    return d if d in ("LONG", "SHORT") else "UNKNOWN"


__all__ = [
    "WEEKDAYS",
    "chi2_pvalue_2x2",
    "coin_of",
    "direction_of",
    "distribution",
    "is_closed",
    "opened_parts",
    "pnl_of",
    "rate",
]
