"""Per-coin DNA: top 20 coins best / worst conditions."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import (
    trade_metrics,
)


def _cond_label(row: dict[str, Any]) -> str | None:
    parts = []
    d = str(row.get("direction") or "")
    if d in ("LONG", "SHORT"):
        parts.append(d)
    rsi = _safe_float(row.get("rsi"))
    if rsi is not None:
        if rsi < 34:
            parts.append("RSI<34")
        elif rsi > 70:
            parts.append("RSI>70")
        elif rsi < 45:
            parts.append("RSI<45")
    atr = _safe_float(row.get("atr_pct"))
    if atr is not None:
        if atr < 0.35:
            parts.append("ATR_low")
        elif atr > 1.0:
            parts.append("ATR_high")
    fs = str(row.get("funding_sign") or "")
    if fs in ("+", "-"):
        parts.append("Funding" + fs)
    oi = str(row.get("oi_sign") or "")
    if oi in ("+", "-"):
        parts.append("OI" + oi)
    h = row.get("hour")
    if h is not None:
        parts.append(f"H{int(h)}")
    if not parts:
        return None
    return " + ".join(parts[:4])


def coin_dna(rows: list[dict[str, Any]], *, top_n: int = 20) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        sym = str(r.get("symbol") or "")
        if sym:
            by[sym].append(r)
    ranked = sorted(by.items(), key=lambda kv: -len(kv[1]))[:top_n]
    out = []
    for sym, items in ranked:
        base = trade_metrics([float(x["pnl"]) for x in items])
        buckets: dict[str, list[float]] = defaultdict(list)
        for r in items:
            lab = _cond_label(r)
            if lab:
                buckets[lab].append(float(r["pnl"]))
            buckets[str(r.get("direction") or "UNK")].append(float(r["pnl"]))
        scored = []
        for lab, pnls in buckets.items():
            if len(pnls) < 8:
                continue
            m = trade_metrics(pnls)
            scored.append({"condition": lab, **m})
        best = sorted(
            [s for s in scored if (s.get("pf") is None and s.get("pf_inf")) or (s.get("pf") or 0) >= 1.0],
            key=lambda s: (
                float(s["pf"]) if s.get("pf") is not None else 99.0,
                float(s.get("ev") or 0),
            ),
            reverse=True,
        )[:3]
        worst = sorted(
            [s for s in scored if s.get("pf") is not None and float(s["pf"]) < 1.0],
            key=lambda s: (float(s.get("pf") or 0), float(s.get("ev") or 0)),
        )[:3]
        out.append({
            "symbol": sym,
            "n": base["n"],
            "pf": base["pf"],
            "ev": base["ev"],
            "wr": base["wr"],
            "best": best,
            "worst": worst,
        })
    return out


__all__ = ["coin_dna"]
