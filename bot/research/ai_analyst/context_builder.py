"""S46 Context Builder — assemble JSON from Intelligence DB (no LLM analysis)."""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.event_intelligence.engine import (
    load_recent_events,
)
from bot.research.market_events.signal_intelligence.narrative_engine.quality import (
    enrich_event_for_report,
    load_macro_rows,
    load_polymarket_rows,
    render_macro_intelligence,
    render_polymarket_intelligence,
)

logger = logging.getLogger(__name__)

LOOKBACK_SEC = 24 * 3600


def _table_exists(conn: Any, name: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        return bool(row)
    except Exception:
        return False


def _pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None:
        return None
    if old == 0:
        return None
    return round(100.0 * (float(new) - float(old)) / float(old), 4)


def _snapshot_near(rows: list[dict[str, Any]], target_ts: int) -> dict[str, Any] | None:
    if not rows:
        return None
    best = min(rows, key=lambda r: abs(int(r.get("snapshot_ts") or 0) - target_ts))
    if abs(int(best.get("snapshot_ts") or 0) - target_ts) > 3 * 3600:
        # Prefer any older row if nothing close
        older = [r for r in rows if int(r.get("snapshot_ts") or 0) <= target_ts]
        return older[-1] if older else best
    return best


def _load_snapshots(conn: Any, *, limit: int = 500) -> list[dict[str, Any]]:
    if not _table_exists(conn, "market_snapshots_g3"):
        return []
    rows = conn.execute(
        """
        SELECT snapshot_ts, btc_price, eth_price, sol_price, bnb_price,
               total3, btc_dominance, funding, open_interest, liquidations,
               volume, atr, fear_greed, dxy, spx, qqq, vix, gold, oil,
               usdt_dominance, volume_delta, raw_json
        FROM market_snapshots_g3
        ORDER BY snapshot_ts DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    # chronological ascending for window math
    out = [dict(r) for r in rows]
    out.reverse()
    return out


def _realized_vol(prices: list[float], *, periods_per_year: float = 365 * 24) -> float | None:
    if len(prices) < 3:
        return None
    rets = []
    for i in range(1, len(prices)):
        if prices[i - 1] and prices[i - 1] != 0 and prices[i] is not None:
            rets.append(math.log(float(prices[i]) / float(prices[i - 1])))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / max(1, len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(periods_per_year) * 100.0, 4)


def _macro_named(rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = ("Fed", "DXY", "US10Y", "US02Y", "Gold", "Oil", "CPI", "PPI", "NFP")
    by: dict[str, Any] = {k.lower(): None for k in keys}
    for r in rows:
        name = str(r.get("name") or "")
        title = str(r.get("title") or r.get("summary") or "")
        blob = f"{name} {title}".lower()
        for k in keys:
            if k.lower() in blob and by[k.lower()] is None:
                by[k.lower()] = {
                    "name": name,
                    "title": title[:200],
                    "summary": str(r.get("summary") or "")[:400],
                    "value": r.get("value"),
                    "unit": r.get("unit"),
                    "created_at": r.get("created_at"),
                }
    # Flatten convenience fields
    return {
        "dxy": by["dxy"],
        "us10y": by["us10y"],
        "us02y": by["us02y"],
        "gold": by["gold"],
        "oil": by["oil"],
        "fed": by["fed"],
        "cpi": by["cpi"],
        "ppi": by["ppi"],
        "nfp": by["nfp"],
        "rows_available": len(rows),
        "markdown_snapshot": None,  # filled later
    }


def _etf_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    btc_etf = []
    eth_etf = []
    for e in events:
        narr = str(e.get("narrative") or "").lower()
        title = str(e.get("title") or "")
        blob = f"{title} {e.get('summary') or ''}".lower()
        if "etf" not in narr and "etf" not in blob:
            continue
        item = {
            "title": title[:200],
            "sentiment": e.get("sentiment"),
            "importance": e.get("importance"),
            "symbols": e.get("symbols"),
        }
        if "eth" in blob or "ether" in blob:
            eth_etf.append(item)
        else:
            btc_etf.append(item)
    return {
        "btc_etf": btc_etf[:8],
        "eth_etf": eth_etf[:8],
        "netflow": {"d5": None, "d30": None, "note": "numeric ETF netflow series not in DB"},
        "coverage": "intel_events" if (btc_etf or eth_etf) else "unavailable",
    }


def _whales_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    whale_ev = []
    for e in events:
        narr = str(e.get("narrative") or "").lower()
        blob = f"{e.get('title') or ''} {e.get('summary') or ''}".lower()
        if "whale" in narr or "whale" in blob or "transfer" in blob:
            whale_ev.append({
                "title": e.get("title"),
                "summary": str(e.get("summary") or "")[:240],
                "symbols": e.get("symbols"),
            })
    return {
        "largest_transfers": whale_ev[:5],
        "exchange_inflow": None,
        "exchange_outflow": None,
        "note": "on-chain inflow/outflow series not wired; whale items from intel events only",
    }


def _funding_block(latest: dict[str, Any] | None, hist: list[dict[str, Any]]) -> dict[str, Any]:
    vals = [float(r["funding"]) for r in hist if r.get("funding") is not None]
    current = latest.get("funding") if latest else None
    avg = round(sum(vals) / len(vals), 8) if vals else None
    extreme = None
    if current is not None and avg is not None:
        extreme = abs(float(current)) >= max(0.0003, abs(avg) * 2.5)
    return {
        "current": current,
        "average_funding": avg,
        "extreme_funding": extreme,
    }


def _oi_block(latest: dict[str, Any] | None, hist: list[dict[str, Any]], now: int) -> dict[str, Any]:
    current = latest.get("open_interest") if latest else None
    s24 = _snapshot_near(hist, now - 86400)
    s7 = _snapshot_near(hist, now - 7 * 86400)
    return {
        "current": current,
        "delta_24h": _pct_change(current, s24.get("open_interest") if s24 else None),
        "delta_7d": _pct_change(current, s7.get("open_interest") if s7 else None),
    }


def _liquidations_block(latest: dict[str, Any] | None) -> dict[str, Any]:
    # G3 liquidations field is a proxy — expose honestly.
    liq = latest.get("liquidations") if latest else None
    return {
        "long": None,
        "short": None,
        "total_24h": liq,
        "largest_event": None,
        "note": "true long/short liquidation tape not in DB; total_24h may be funding proxy",
    }


def _fear_greed_block(latest: dict[str, Any] | None, hist: list[dict[str, Any]], now: int) -> dict[str, Any]:
    current = latest.get("fear_greed") if latest else None
    y = _snapshot_near(hist, now - 86400)
    w = _snapshot_near(hist, now - 7 * 86400)
    return {
        "current": current,
        "yesterday": y.get("fear_greed") if y else None,
        "week": w.get("fear_greed") if w else None,
    }


def _poly_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "top_markets": [],
            "probability_changes": [],
            "fastest_movers": [],
            "consensus": None,
            "markdown": "—",
        }
    latest: dict[str, dict[str, Any]] = {}
    history: dict[str, list[float]] = {}
    for r in sorted(rows, key=lambda x: int(x.get("created_at") or 0)):
        name = str(r.get("name") or r.get("question") or "market")
        history.setdefault(name, [])
        if r.get("probability") is not None:
            history[name].append(float(r["probability"]))
        latest[name] = r
    top = sorted(
        latest.values(),
        key=lambda r: float(r["probability"]) if r.get("probability") is not None else -1,
        reverse=True,
    )[:8]
    movers = []
    for name, series in history.items():
        if len(series) >= 2:
            movers.append({
                "name": name,
                "change": round(series[-1] - series[0], 4),
                "latest": series[-1],
            })
    movers.sort(key=lambda x: abs(x["change"]), reverse=True)
    probs = [float(r["probability"]) for r in latest.values() if r.get("probability") is not None]
    consensus = None
    if probs:
        consensus = {
            "avg_probability": round(sum(probs) / len(probs), 4),
            "n_markets": len(probs),
        }
    return {
        "top_markets": [
            {
                "name": r.get("name"),
                "question": r.get("question"),
                "probability": r.get("probability"),
            }
            for r in top
        ],
        "probability_changes": movers[:8],
        "fastest_movers": movers[:5],
        "consensus": consensus,
        "markdown": render_polymarket_intelligence(rows),
    }


def build_market_context(
    *,
    now: int | None = None,
    lookback_sec: int = LOOKBACK_SEC,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Build a single analysis JSON. No interpretation — numbers and sourced fields only."""
    now_ts = int(now if now is not None else time.time())
    since = now_ts - int(lookback_sec)
    availability: dict[str, str] = {}

    def _run(c: Any) -> dict[str, Any]:
        apply_migrations(c)
        snaps = _load_snapshots(c)
        latest = snaps[-1] if snaps else None
        if latest:
            availability["market_snapshots_g3"] = "ok"
        else:
            availability["market_snapshots_g3"] = "empty"

        # BTC window metrics
        btc_price = latest.get("btc_price") if latest else None
        s1h = _snapshot_near(snaps, now_ts - 3600) if snaps else None
        s24 = _snapshot_near(snaps, now_ts - 86400) if snaps else None
        s7 = _snapshot_near(snaps, now_ts - 7 * 86400) if snaps else None
        btc_prices = [
            float(r["btc_price"])
            for r in snaps
            if r.get("btc_price") is not None
            and int(r.get("snapshot_ts") or 0) >= now_ts - 86400
        ]
        btc = {
            "price": btc_price,
            "change_1h_pct": _pct_change(btc_price, s1h.get("btc_price") if s1h else None),
            "change_24h_pct": _pct_change(btc_price, s24.get("btc_price") if s24 else None),
            "change_7d_pct": _pct_change(btc_price, s7.get("btc_price") if s7 else None),
            "volume": latest.get("volume") if latest else None,
            "dominance": latest.get("btc_dominance") if latest else None,
            "realized_volatility": _realized_vol(btc_prices) if btc_prices else None,
            "atr": latest.get("atr") if latest else None,
        }

        # S&P / VIX — ATH distance needs ATH; unavailable → null
        spx_price = latest.get("spx") if latest else None
        spx_hist = [float(r["spx"]) for r in snaps if r.get("spx") is not None]
        ath = max(spx_hist) if spx_hist else None
        dist_ath = None
        if spx_price is not None and ath is not None and ath != 0:
            dist_ath = round(100.0 * (float(ath) - float(spx_price)) / float(ath), 4)
        sp500 = {
            "price": spx_price,
            "change_pct": _pct_change(spx_price, s24.get("spx") if s24 else None),
            "distance_to_ath_pct": dist_ath,
            "ath": ath,
        }
        vix = {
            "current": latest.get("vix") if latest else None,
            "change_pct": _pct_change(
                latest.get("vix") if latest else None,
                s24.get("vix") if s24 else None,
            ),
        }

        macro_rows = load_macro_rows(c, since_ts=since, limit=60) if _table_exists(c, "market_macro_events") else []
        availability["market_macro_events"] = "ok" if macro_rows else "empty"
        macro = _macro_named(macro_rows)
        # Prefer snapshot DXY/gold/oil when present
        if latest:
            if latest.get("dxy") is not None:
                macro["dxy"] = {"value": latest.get("dxy"), "source": "snapshot"}
            if latest.get("gold") is not None:
                macro["gold"] = {"value": latest.get("gold"), "source": "snapshot"}
            if latest.get("oil") is not None:
                macro["oil"] = {"value": latest.get("oil"), "source": "snapshot"}
        macro["markdown_snapshot"] = render_macro_intelligence(macro_rows)

        events_raw = (
            load_recent_events(c, since_ts=since, limit=40)
            if _table_exists(c, "market_intel_events")
            else []
        )
        availability["market_intel_events"] = "ok" if events_raw else "empty"
        events = [enrich_event_for_report(e, now=now_ts) for e in events_raw]

        poly_rows = (
            load_polymarket_rows(c, since_ts=since, limit=80)
            if _table_exists(c, "market_polymarket_signals")
            else []
        )
        availability["market_polymarket_signals"] = "ok" if poly_rows else "empty"

        return {
            "generated_at": now_ts,
            "lookback_sec": lookback_sec,
            "btc": btc,
            "sp500": sp500,
            "vix": vix,
            "macro": macro,
            "etf": _etf_from_events(events),
            "funding": _funding_block(latest, snaps),
            "open_interest": _oi_block(latest, snaps, now_ts),
            "liquidations": _liquidations_block(latest),
            "fear_greed": _fear_greed_block(latest, snaps, now_ts),
            "whales": _whales_from_events(events),
            "polymarket": _poly_block(poly_rows),
            "intelligence": {
                "top_events": [
                    {
                        "title": e.get("title"),
                        "symbols": e.get("symbols"),
                        "sentiment": e.get("sentiment"),
                        "polarity": e.get("polarity"),
                        "importance": e.get("importance"),
                        "confidence": e.get("confidence"),
                        "market_impact": e.get("market_impact"),
                        "why_it_matters": e.get("why_it_matters"),
                        "narrative": e.get("narrative"),
                        "confirmed_by": e.get("confirmed_by"),
                        "sources": e.get("sources"),
                        "source_count": e.get("source_count"),
                    }
                    for e in events[:12]
                ],
            },
            "data_availability": availability,
            "notes": [
                "Context is factual aggregation only; LLM must not invent missing fields.",
                "ETF netflow / true liquidation tape / whale exchange flows may be unavailable.",
            ],
        }

    if conn is not None:
        return _run(conn)
    with market_events_connection() as c:
        return _run(c)


def dump_context_json(context: dict[str, Any]) -> str:
    return json.dumps(context, ensure_ascii=False, indent=2, default=str)
