"""S46/S46.1 Context Builder — factual JSON; live enrich when DB gaps exist."""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any

from bot.research.ai_analyst.market_data_fetch import (
    fetch_all_live_enrichment,
    metric_from_values,
    trend_from_change,
)
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.event_intelligence.engine import (
    load_recent_events,
)
from bot.research.market_events.signal_intelligence.narrative_engine.quality import (
    enrich_event_for_report,
    load_macro_rows,
    load_polymarket_rows,
)

logger = logging.getLogger(__name__)

LOOKBACK_SEC = 24 * 3600

# Fields counted toward context_completeness (equal weight).
_COMPLETENESS_PATHS: tuple[str, ...] = (
    "btc.price",
    "btc.change_24h_pct",
    "btc.dominance",
    "sp500.value",
    "nasdaq.value",
    "vix.value",
    "macro.dxy.value",
    "macro.us10y.value",
    "macro.us02y.value",
    "macro.gold.value",
    "macro.oil.value",
    "etf.btc_etf.netflow_5d",
    "etf.eth_etf.netflow_5d",
    "funding.current",
    "open_interest.current",
    "fear_greed.current",
    "intelligence.top_events",
    "polymarket.top_markets",
)


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
    if new is None or old is None or old == 0:
        return None
    return round(100.0 * (float(new) - float(old)) / float(old), 4)


def _snapshot_near(rows: list[dict[str, Any]], target_ts: int) -> dict[str, Any] | None:
    if not rows:
        return None
    best = min(rows, key=lambda r: abs(int(r.get("snapshot_ts") or 0) - target_ts))
    older = [r for r in rows if int(r.get("snapshot_ts") or 0) <= target_ts]
    if abs(int(best.get("snapshot_ts") or 0) - target_ts) > 6 * 3600 and older:
        return older[-1]
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
    out = [dict(r) for r in rows]
    out.reverse()
    return out


def _load_observation_aliases(conn: Any, *, since_ts: int) -> dict[str, float]:
    """Map latest observation prices; include S46.1 proxy aliases."""
    if not _table_exists(conn, "market_events_price_observations"):
        return {}
    if not _table_exists(conn, "market_events_instruments"):
        return {}
    try:
        rows = conn.execute(
            """
            SELECT i.canonical_asset, o.trade_price
            FROM market_events_price_observations o
            JOIN market_events_instruments i ON i.id = o.instrument_id
            WHERE o.observed_ts >= ?
            ORDER BY o.observed_ts ASC
            """,
            (since_ts,),
        ).fetchall()
    except Exception:
        return {}
    alias = {
        "DXY": "dxy",
        "SPX": "spx",
        "SP500_PROXY": "spx",
        "QQQ": "qqq",
        "NASDAQ100_PROXY": "qqq",
        "VIX": "vix",
        "GOLD": "gold",
        "OIL": "oil",
    }
    out: dict[str, float] = {}
    for r in rows:
        key = alias.get(str(r["canonical_asset"] or "").upper())
        if key and r["trade_price"] is not None:
            out[key] = float(r["trade_price"])
    return out


def _realized_vol(prices: list[float], *, periods_per_year: float = 365 * 24) -> float | None:
    if len(prices) < 3:
        return None
    rets = []
    for i in range(1, len(prices)):
        if prices[i - 1] and prices[i] is not None and prices[i - 1] != 0:
            rets.append(math.log(float(prices[i]) / float(prices[i - 1])))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / max(1, len(rets) - 1)
    return round(math.sqrt(var) * math.sqrt(periods_per_year) * 100.0, 4)


def _prune(obj: Any) -> Any:
    """Drop None leaves; drop empty dict/list after pruning."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            pv = _prune(v)
            if pv is None:
                continue
            if pv == {} or pv == []:
                continue
            out[k] = pv
        return out
    if isinstance(obj, list):
        out_list = []
        for x in obj:
            px = _prune(x)
            if px is None or px == {} or px == []:
                continue
            out_list.append(px)
        return out_list
    return obj


def _get_path(ctx: dict[str, Any], path: str) -> Any:
    cur: Any = ctx
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _path_filled(ctx: dict[str, Any], path: str) -> bool:
    val = _get_path(ctx, path)
    if val is None:
        return False
    if isinstance(val, (list, dict)) and not val:
        return False
    if isinstance(val, str) and not val.strip():
        return False
    return True


def compute_context_completeness(ctx: dict[str, Any]) -> dict[str, Any]:
    filled = [p for p in _COMPLETENESS_PATHS if _path_filled(ctx, p)]
    missing = [p for p in _COMPLETENESS_PATHS if p not in filled]
    score = int(round(100.0 * len(filled) / max(1, len(_COMPLETENESS_PATHS))))
    return {
        "context_completeness": score,
        "filled_fields": filled,
        "missing_fields": missing,
        "filled_count": len(filled),
        "total_fields": len(_COMPLETENESS_PATHS),
    }


def _prefer_metric(
    live: dict[str, Any] | None,
    snap_value: float | None,
    snap_prev: float | None,
    *,
    source_snap: str,
    unit: str = "",
) -> dict[str, Any] | None:
    if live and live.get("value") is not None:
        return live
    return metric_from_values(snap_value, snap_prev, source=source_snap, unit=unit)


def _event_headlines(events: list[dict[str, Any]], *needles: str) -> list[dict[str, Any]]:
    out = []
    for e in events:
        blob = f"{e.get('title') or ''} {e.get('narrative') or ''}".lower()
        if any(n in blob for n in needles):
            out.append({
                "title": e.get("title"),
                "sentiment": e.get("sentiment"),
                "importance": e.get("importance"),
                "symbols": e.get("symbols"),
            })
    return out[:6]


def _poly_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    latest: dict[str, dict[str, Any]] = {}
    history: dict[str, list[float]] = {}
    for r in sorted(rows, key=lambda x: int(x.get("created_at") or 0)):
        name = str(r.get("name") or r.get("question") or "market")
        history.setdefault(name, [])
        if r.get("probability") is not None:
            history[name].append(float(r["probability"]))
        latest[name] = r
    top = sorted(
        [r for r in latest.values() if r.get("probability") is not None],
        key=lambda r: float(r["probability"]),
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
    probs = [float(r["probability"]) for r in top]
    block: dict[str, Any] = {
        "top_markets": [
            {
                "name": r.get("name"),
                "question": r.get("question"),
                "probability": r.get("probability"),
            }
            for r in top
        ],
    }
    if movers:
        block["probability_changes"] = movers[:8]
        block["fastest_movers"] = movers[:5]
    if probs:
        block["consensus"] = {
            "avg_probability": round(sum(probs) / len(probs), 4),
            "n_markets": len(probs),
        }
    return block


def build_market_context(
    *,
    now: int | None = None,
    lookback_sec: int = LOOKBACK_SEC,
    conn: Any | None = None,
    live_enrich: bool = True,
    live_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build analysis JSON.
    S46.1: normalize macro/ETF to value/change/trend; live-fill SPX/Nasdaq/VIX/DXY/yields;
    omit nulls; attach context_completeness.
    """
    now_ts = int(now if now is not None else time.time())
    since = now_ts - int(lookback_sec)

    def _run(c: Any) -> dict[str, Any]:
        apply_migrations(c)
        snaps = _load_snapshots(c)
        latest = snaps[-1] if snaps else None
        s1h = _snapshot_near(snaps, now_ts - 3600) if snaps else None
        s24 = _snapshot_near(snaps, now_ts - 86400) if snaps else None
        s7 = _snapshot_near(snaps, now_ts - 7 * 86400) if snaps else None
        obs = _load_observation_aliases(c, since_ts=since)

        live = live_payload
        if live is None and live_enrich:
            try:
                live = fetch_all_live_enrichment()
            except Exception as exc:
                logger.warning("live enrichment failed: %s", exc)
                live = {"quotes": {}, "etf": {}}
        live = live or {"quotes": {}, "etf": {}}
        quotes = live.get("quotes") or {}

        # Merge snapshot + observation proxies into quote fallbacks
        def snap_pair(key: str) -> tuple[float | None, float | None]:
            cur = None
            if latest and latest.get(key) is not None:
                cur = float(latest[key])
            elif key in obs:
                cur = float(obs[key])
            prev = None
            if s24 and s24.get(key) is not None:
                prev = float(s24[key])
            return cur, prev

        spx_m = _prefer_metric(
            quotes.get("spx"), *snap_pair("spx"), source_snap="snapshot:spx", unit="index",
        )
        qqq_m = _prefer_metric(
            quotes.get("qqq"), *snap_pair("qqq"), source_snap="snapshot:qqq", unit="USD",
        )
        nasdaq_m = quotes.get("nasdaq")
        if nasdaq_m is None and qqq_m is not None:
            # Fallback label when only QQQ proxy available
            nasdaq_m = {
                **qqq_m,
                "note": "NDX unavailable; using QQQ proxy levels",
            }
        vix_m = _prefer_metric(
            quotes.get("vix"), *snap_pair("vix"), source_snap="snapshot:vix", unit="index",
        )
        dxy_m = _prefer_metric(
            quotes.get("dxy"), *snap_pair("dxy"), source_snap="snapshot:dxy", unit="index",
        )
        gold_m = _prefer_metric(
            quotes.get("gold"), *snap_pair("gold"), source_snap="snapshot:gold", unit="USD/oz",
        )
        oil_m = _prefer_metric(
            quotes.get("oil"), *snap_pair("oil"), source_snap="snapshot:oil", unit="USD/bbl",
        )
        us10y_m = quotes.get("us10y")
        us02y_m = quotes.get("us02y")

        btc_price = latest.get("btc_price") if latest else None
        btc_prices = [
            float(r["btc_price"])
            for r in snaps
            if r.get("btc_price") is not None
            and int(r.get("snapshot_ts") or 0) >= now_ts - 86400
        ]
        btc: dict[str, Any] = {}
        if btc_price is not None:
            btc["price"] = float(btc_price)
        for label, snap in (("change_1h_pct", s1h), ("change_24h_pct", s24), ("change_7d_pct", s7)):
            ch = _pct_change(btc_price, snap.get("btc_price") if snap else None)
            if ch is not None:
                btc[label] = ch
        if latest and latest.get("volume") is not None:
            btc["volume"] = float(latest["volume"])
        if latest and latest.get("btc_dominance") is not None:
            btc["dominance"] = float(latest["btc_dominance"])
        rv = _realized_vol(btc_prices) if btc_prices else None
        if rv is not None:
            btc["realized_volatility"] = rv
        if latest and latest.get("atr") is not None:
            btc["atr"] = float(latest["atr"])
        if "change_24h_pct" in btc:
            btc["trend"] = trend_from_change(btc["change_24h_pct"])

        # Macro event rows — only keep Fed/CPI narrative when no numeric value;
        # numeric series replaced by live metrics.
        macro_rows = (
            load_macro_rows(c, since_ts=since, limit=60)
            if _table_exists(c, "market_macro_events")
            else []
        )
        fed_headlines = []
        cpi_headlines = []
        for r in macro_rows:
            name = str(r.get("name") or "").lower()
            title = str(r.get("title") or r.get("summary") or "")[:200]
            if not title:
                continue
            if "fed" in name or "fed" in title.lower():
                fed_headlines.append(title)
            if "cpi" in name or "cpi" in title.lower():
                cpi_headlines.append(title)

        macro: dict[str, Any] = {}
        if dxy_m:
            macro["dxy"] = dxy_m
        if us10y_m:
            macro["us10y"] = us10y_m
        if us02y_m:
            macro["us02y"] = us02y_m
        if gold_m:
            macro["gold"] = gold_m
        if oil_m:
            macro["oil"] = oil_m
        if fed_headlines:
            macro["fed"] = {"headlines": fed_headlines[:3], "source": "macro_events"}
        if cpi_headlines:
            macro["cpi"] = {"headlines": cpi_headlines[:3], "source": "macro_events"}

        events_raw = (
            load_recent_events(c, since_ts=since, limit=40)
            if _table_exists(c, "market_intel_events")
            else []
        )
        events = [enrich_event_for_report(e, now=now_ts) for e in events_raw]

        # ETF: numeric first
        etf_live = live.get("etf") or {}
        etf: dict[str, Any] = {"unit": etf_live.get("unit") or "USD_millions"}
        if etf_live.get("source"):
            etf["source"] = etf_live["source"]
        for key in ("btc_etf", "eth_etf"):
            block = etf_live.get(key)
            if isinstance(block, dict) and (
                block.get("netflow_5d") is not None or block.get("netflow_1d") is not None
            ):
                etf[key] = {
                    k: v for k, v in block.items()
                    if v is not None
                }

        # Optional news overlay (separate from numbers)
        etf_news = _event_headlines(events, "etf")
        if etf_news:
            etf["related_headlines"] = etf_news

        funding: dict[str, Any] = {}
        if latest and latest.get("funding") is not None:
            vals = [float(r["funding"]) for r in snaps if r.get("funding") is not None]
            cur = float(latest["funding"])
            funding["current"] = cur
            if vals:
                funding["average_funding"] = round(sum(vals) / len(vals), 8)
                funding["extreme_funding"] = abs(cur) >= max(
                    0.0003, abs(funding["average_funding"]) * 2.5,
                )
            funding["trend"] = trend_from_change(cur)

        oi: dict[str, Any] = {}
        if latest and latest.get("open_interest") is not None:
            cur = float(latest["open_interest"])
            oi["current"] = cur
            d24 = _pct_change(cur, s24.get("open_interest") if s24 else None)
            d7 = _pct_change(cur, s7.get("open_interest") if s7 else None)
            if d24 is not None:
                oi["delta_24h"] = d24
            if d7 is not None:
                oi["delta_7d"] = d7

        liq: dict[str, Any] = {}
        if latest and latest.get("liquidations") is not None:
            liq["total_24h_proxy"] = float(latest["liquidations"])
            liq["note"] = "G3 liquidations field may be funding-derived proxy"

        fg: dict[str, Any] = {}
        if latest and latest.get("fear_greed") is not None:
            fg["current"] = float(latest["fear_greed"])
            if s24 and s24.get("fear_greed") is not None:
                fg["yesterday"] = float(s24["fear_greed"])
            if s7 and s7.get("fear_greed") is not None:
                fg["week"] = float(s7["fear_greed"])
            fg["trend"] = trend_from_change(
                (fg["current"] - fg["yesterday"]) if "yesterday" in fg else None,
            )

        whales = {}
        whale_ev = _event_headlines(events, "whale", "transfer")
        if whale_ev:
            whales["largest_transfers"] = whale_ev

        poly_rows = (
            load_polymarket_rows(c, since_ts=since, limit=80)
            if _table_exists(c, "market_polymarket_signals")
            else []
        )

        sp500_block = dict(spx_m) if spx_m else {}
        # Keep backward-compatible aliases expected by prompts
        if sp500_block and "price" not in sp500_block and "value" in sp500_block:
            sp500_block["price"] = sp500_block["value"]

        ctx: dict[str, Any] = {
            "generated_at": now_ts,
            "lookback_sec": lookback_sec,
            "btc": btc,
            "sp500": sp500_block,
            "nasdaq": dict(nasdaq_m) if nasdaq_m else {},
            "vix": dict(vix_m) if vix_m else {},
            "macro": macro,
            "etf": etf,
            "funding": funding,
            "open_interest": oi,
            "liquidations": liq,
            "fear_greed": fg,
            "whales": whales,
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
                        "source_count": e.get("source_count"),
                    }
                    for e in events[:12]
                ],
            },
            "live_enrichment": {
                "enabled": bool(live_enrich),
                "elapsed_ms": live.get("elapsed_ms"),
                "quotes_fetched": sorted(quotes.keys()),
            },
            "notes": [
                "Numeric macro/index fields use value/change_24h/trend.",
                "ETF block prefers Farside netflow (USD millions), not headlines.",
                "Omit nulls; use context_completeness + missing_fields for gaps.",
            ],
        }

        ctx = _prune(ctx)
        completeness = compute_context_completeness(ctx)
        ctx["context_completeness"] = completeness["context_completeness"]
        ctx["completeness"] = completeness
        if completeness["missing_fields"]:
            ctx["data_gaps"] = completeness["missing_fields"]
        return ctx

    if conn is not None:
        return _run(conn)
    with market_events_connection() as c:
        return _run(c)


def dump_context_json(context: dict[str, Any]) -> str:
    return json.dumps(context, ensure_ascii=False, indent=2, default=str)
