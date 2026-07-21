"""Data freshness timestamps for AI Analyst reports."""

from __future__ import annotations

import time
from typing import Any


def _fmt_ts(ts: int | float | None) -> str:
    if ts is None:
        return "unknown"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(int(ts)))
    except (TypeError, ValueError, OSError):
        return "unknown"


def _max_ts(*values: int | float | None) -> int | None:
    nums = [int(v) for v in values if v is not None]
    return max(nums) if nums else None


def _metric_asof(block: dict[str, Any] | None) -> int | None:
    if not isinstance(block, dict):
        return None
    raw = block.get("asof_ts")
    return int(raw) if raw is not None else None


def build_data_timestamps(
    ctx: dict[str, Any],
    *,
    now_ts: int,
    latest_snapshot_ts: int | None = None,
    events_raw: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute per-layer freshness from assembled context + raw events."""
    btc = ctx.get("btc") or {}
    sp500 = ctx.get("sp500") or {}
    nasdaq = ctx.get("nasdaq") or {}
    macro = ctx.get("macro") or {}
    etf = ctx.get("etf") or {}
    live = ctx.get("live_enrichment") or {}

    market_ts = _max_ts(
        _metric_asof(btc if isinstance(btc, dict) else None),
        _metric_asof(sp500),
        _metric_asof(nasdaq),
        _metric_asof(ctx.get("vix")),
        latest_snapshot_ts,
    )

    macro_ts = _max_ts(
        *(_metric_asof(macro.get(k)) for k in ("dxy", "us10y", "us02y", "gold", "oil")),
        _metric_asof(etf if isinstance(etf, dict) else None),
        etf.get("asof_ts") if isinstance(etf, dict) else None,
    )

    intel_ts = None
    for ev in events_raw or []:
        cand = _max_ts(ev.get("last_seen"), ev.get("updated_at"), ev.get("created_at"))
        intel_ts = _max_ts(intel_ts, cand)
    if intel_ts is None:
        for ev in (ctx.get("intelligence") or {}).get("top_events") or []:
            intel_ts = _max_ts(intel_ts, ev.get("last_seen"))

    def _age(ts: int | None) -> int | None:
        if ts is None:
            return None
        return max(0, int(now_ts) - int(ts))

    out = {
        "market": _fmt_ts(market_ts),
        "macro": _fmt_ts(macro_ts),
        "intelligence": _fmt_ts(intel_ts),
        "context": _fmt_ts(now_ts),
        "sources": {
            k: v for k, v in {
                "btc": btc.get("source") if isinstance(btc, dict) else None,
                "sp500": sp500.get("source") if isinstance(sp500, dict) else None,
                "live_fetched_at": live.get("fetched_at"),
                "live_elapsed_ms": live.get("elapsed_ms"),
            }.items() if v is not None
        },
    }
    if market_ts is not None:
        out["market_ts"] = market_ts
        age = _age(market_ts)
        if age is not None:
            out["market_age_sec"] = age
    if macro_ts is not None:
        out["macro_ts"] = macro_ts
        age = _age(macro_ts)
        if age is not None:
            out["macro_age_sec"] = age
    if intel_ts is not None:
        out["intelligence_ts"] = intel_ts
        age = _age(intel_ts)
        if age is not None:
            out["intelligence_age_sec"] = age
    out["context_ts"] = now_ts
    out["context_age_sec"] = 0
    return out


def format_data_timestamp_block(ctx: dict[str, Any]) -> str:
    ts = ctx.get("data_timestamps") or {}
    lines = [
        "## Data Timestamp",
        f"Market: {ts.get('market', 'unknown')}",
        f"Macro: {ts.get('macro', 'unknown')}",
        f"Intelligence: {ts.get('intelligence', 'unknown')}",
        f"Context: {ts.get('context', 'unknown')}",
    ]
    sources = ts.get("sources") or {}
    btc_src = sources.get("btc")
    sp_src = sources.get("sp500")
    if btc_src or sp_src:
        detail = ", ".join(
            f"{k}={v}" for k, v in (("BTC", btc_src), ("SP500", sp_src)) if v
        )
        lines.append(f"Sources: {detail}")
    return "\n".join(lines)
