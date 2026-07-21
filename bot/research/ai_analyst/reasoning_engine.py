"""S46.2 — analytical reasoning hints and quality scoring (no new data sources)."""

from __future__ import annotations

from typing import Any


def _fval(obj: Any, key: str = "value") -> float | None:
    if obj is None:
        return None
    if isinstance(obj, dict):
        v = obj.get(key)
        if v is None and key == "value":
            v = obj.get("price")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    try:
        return float(obj)
    except (TypeError, ValueError):
        return None


def _trend(obj: Any) -> str | None:
    if isinstance(obj, dict):
        t = obj.get("trend")
        return str(t) if t else None
    return None


def detect_contradictions(ctx: dict[str, Any]) -> list[str]:
    """Rule-based contradictions from existing context (hints for LLM)."""
    out: list[str] = []
    btc = ctx.get("btc") or {}
    sp500 = ctx.get("sp500") or {}
    nasdaq = ctx.get("nasdaq") or {}
    vix = ctx.get("vix") or {}
    fg = ctx.get("fear_greed") or {}
    macro = ctx.get("macro") or {}
    etf = ctx.get("etf") or {}

    btc_etf = etf.get("btc_etf") or {}
    nf5 = _fval(btc_etf, "netflow_5d")
    ch24 = _fval(btc, "change_24h_pct")
    if nf5 is not None and nf5 > 0 and ch24 is not None and abs(ch24) < 0.8:
        out.append("Positive ETF inflows BUT BTC price remains relatively flat")

    sp_trend = _trend(sp500) or _trend(nasdaq)
    fg_cur = _fval(fg, "current")
    if sp_trend == "Bullish" and fg_cur is not None and fg_cur < 35:
        out.append("Stocks rally BUT Fear & Greed remains low")

    dxy = macro.get("dxy") or {}
    if _trend(dxy) == "Bullish" and sp_trend == "Bullish":
        out.append("Dollar strengthening BUT equities also rising (unusual risk-on mix)")

    us10y = macro.get("us10y") or {}
    if _trend(us10y) == "Bullish" and sp_trend == "Bullish":
        out.append("Rising yields BUT stocks holding up (rate sensitivity muted for now)")

    if _trend(vix) == "Bearish" and fg_cur is not None and fg_cur < 40:
        out.append("VIX falling BUT sentiment gauge still cautious")

    intel = (ctx.get("intelligence") or {}).get("top_events") or []
    bullish_ev = sum(1 for e in intel if str(e.get("polarity") or "").lower() == "bullish")
    bearish_ev = sum(1 for e in intel if str(e.get("polarity") or "").lower() == "bearish")
    if bullish_ev >= 2 and bearish_ev >= 2:
        out.append("Mixed bullish and bearish intel events in the same window")

    return out[:6]


def build_narrative_candidates(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Narrative themes with strength/evidence from context only."""
    candidates: list[dict[str, Any]] = []
    etf = ctx.get("etf") or {}
    macro = ctx.get("macro") or {}
    intel = (ctx.get("intelligence") or {}).get("top_events") or []
    sp500 = ctx.get("sp500") or {}
    vix = ctx.get("vix") or {}

    btc_etf = etf.get("btc_etf") or {}
    nf5 = _fval(btc_etf, "netflow_5d")
    if nf5 is not None and nf5 > 0:
        candidates.append({
            "narrative": "ETF Demand",
            "strength": "Strong" if nf5 > 200 else "Moderate",
            "evidence": f"BTC ETF 5d netflow {nf5} USD millions",
        })

    if _trend(sp500) == "Bullish" and _trend(vix) == "Bearish":
        candidates.append({
            "narrative": "Risk-On",
            "strength": "Moderate",
            "evidence": "Equities rising with VIX compressing",
        })

    dxy = macro.get("dxy") or {}
    us10y = macro.get("us10y") or {}
    if _trend(dxy) == "Bullish" or _trend(us10y) == "Bullish":
        candidates.append({
            "narrative": "Macro Headwinds",
            "strength": "Moderate",
            "evidence": "Dollar and/or yields trending firmer",
        })

    narr_counts: dict[str, int] = {}
    for ev in intel:
        raw = str(ev.get("narrative") or "")
        for part in raw.split(","):
            tag = part.strip()
            if tag and tag != "General":
                narr_counts[tag] = narr_counts.get(tag, 0) + 1
    for tag, n in sorted(narr_counts.items(), key=lambda x: -x[1])[:4]:
        candidates.append({
            "narrative": tag,
            "strength": "Strong" if n >= 3 else "Moderate",
            "evidence": f"{n} intel event(s) tagged {tag}",
        })

    if not candidates:
        candidates.append({
            "narrative": "Mixed / Unclear",
            "strength": "Weak",
            "evidence": "Limited thematic clustering in available events",
        })
    return candidates[:8]


def _cross_asset_pairs_available(ctx: dict[str, Any]) -> list[str]:
    pairs = []
    btc_ok = _fval(ctx.get("btc"), "price") is not None
    sp_ok = _fval(ctx.get("sp500")) is not None
    nas_ok = _fval(ctx.get("nasdaq")) is not None
    vix_ok = _fval(ctx.get("vix")) is not None
    dxy_ok = _fval((ctx.get("macro") or {}).get("dxy")) is not None
    y10_ok = _fval((ctx.get("macro") or {}).get("us10y")) is not None
    gold_ok = _fval((ctx.get("macro") or {}).get("gold")) is not None

    if sp_ok and btc_ok:
        pairs.append("Stocks vs BTC")
    if dxy_ok and btc_ok:
        pairs.append("Dollar vs BTC")
    if y10_ok and btc_ok:
        pairs.append("Yields vs BTC")
    if vix_ok and btc_ok:
        pairs.append("VIX vs BTC")
    if gold_ok and btc_ok:
        pairs.append("Gold vs BTC")
    if nas_ok and sp_ok:
        pairs.append("Nasdaq vs SP500")
    return pairs


def compute_reasoning_depth(ctx: dict[str, Any]) -> int:
    """0–100 score: how many cross-asset links + events + macro blocks are usable."""
    pairs = len(_cross_asset_pairs_available(ctx))
    events = len((ctx.get("intelligence") or {}).get("top_events") or [])
    macro = ctx.get("macro") or {}
    macro_n = sum(
        1 for k in ("dxy", "us10y", "us02y", "gold", "oil")
        if isinstance(macro.get(k), dict) and macro[k].get("value") is not None
    )
    etf = ctx.get("etf") or {}
    etf_n = sum(
        1 for k in ("btc_etf", "eth_etf")
        if isinstance(etf.get(k), dict) and etf[k].get("netflow_5d") is not None
    )
    raw = pairs * 12 + min(events, 5) * 6 + macro_n * 5 + etf_n * 8
    return min(100, raw)


def compute_analysis_quality(ctx: dict[str, Any]) -> dict[str, Any]:
    completeness = int(ctx.get("context_completeness") or 0)
    gaps = ctx.get("data_gaps") or (ctx.get("completeness") or {}).get("missing_fields") or []
    depth = compute_reasoning_depth(ctx)
    confidence = min(100, int(round(completeness * 0.55 + depth * 0.45)))
    return {
        "context_completeness": completeness,
        "reasoning_depth": depth,
        "data_gaps": gaps,
        "confidence": confidence,
    }


def enrich_context_for_analysis(ctx: dict[str, Any]) -> dict[str, Any]:
    """Attach reasoning hints — derived only from existing context fields."""
    enriched = dict(ctx)
    enriched["analysis_quality"] = compute_analysis_quality(ctx)
    enriched["reasoning_hints"] = {
        "contradictions_detected": detect_contradictions(ctx),
        "narrative_candidates": build_narrative_candidates(ctx),
        "cross_asset_pairs_available": _cross_asset_pairs_available(ctx),
    }
    return enriched


def format_analysis_quality_block(quality: dict[str, Any]) -> str:
    gaps = quality.get("data_gaps") or []
    gap_line = ", ".join(str(g) for g in gaps[:8]) if gaps else "none"
    return "\n".join([
        "## Analysis Quality",
        f"- Context completeness: {quality.get('context_completeness')}%",
        f"- Reasoning depth: {quality.get('reasoning_depth')}%",
        f"- Confidence: {quality.get('confidence')}%",
        f"- Data gaps: {gap_line}",
    ])
