"""F.0 AI context bundle — extends E.3.3 bundle with exchange/F0 fields."""

from __future__ import annotations

import json
from typing import Any


def build_f0_context_bundle(conn: Any, event_id: int) -> dict[str, Any]:
    from bot.research.market_events.ai_analyst.context_bundle import build_context_bundle

    bundle = build_context_bundle(conn, event_id)
    if bundle.get("error"):
        return bundle

    exch = conn.execute(
        "SELECT * FROM market_event_exchange_context WHERE event_id = ? LIMIT 1",
        (event_id,),
    ).fetchone()
    opp = conn.execute(
        "SELECT score, score_breakdown_json FROM market_events_opportunity_scores_v2 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    exh = conn.execute(
        "SELECT exhaustion_score, reasons_json FROM market_events_exhaustion WHERE source_event_id = ? ORDER BY created_at DESC LIMIT 1",
        (event_id,),
    ).fetchone()
    mtf = conn.execute(
        "SELECT detector_id, return_pct, window_minutes FROM market_events_multitimeframe WHERE source_event_id = ?",
        (event_id,),
    ).fetchall()

    bundle["f0"] = {
        "exchange": dict(exch) if exch else None,
        "opportunity_v2": {
            "score": float(opp["score"]) if opp else None,
            "breakdown": json.loads(opp["score_breakdown_json"]) if opp else {},
        },
        "exhaustion": {
            "score": float(exh["exhaustion_score"]) if exh else None,
            "reasons": json.loads(exh["reasons_json"]) if exh else [],
        },
        "multitimeframe": [dict(r) for r in mtf],
    }
    return bundle


def build_f0_prompt(bundle: dict[str, Any]) -> str:
    """Strict JSON-only response prompt."""
    me = bundle.get("market_event", {})
    f0 = bundle.get("f0", {})
    return json.dumps({
        "instruction": "Respond with JSON only. No free text outside JSON.",
        "required_fields": [
            "bias", "confidence", "continuation_probability",
            "reversal_probability", "summary_ru", "summary_en",
        ],
        "inputs": {
            "market": me,
            "telegram": bundle.get("telegram_context", [])[:5],
            "news": [c for c in bundle.get("telegram_context", []) if c.get("type") == "NEWS"][:3],
            "history": bundle.get("historical_research"),
            "funding": (f0.get("exchange") or {}).get("funding"),
            "open_interest": (f0.get("exchange") or {}).get("open_interest"),
            "vwap": (f0.get("exchange") or {}).get("vwap"),
            "atr": (f0.get("exchange") or {}).get("atr"),
            "ema20_distance_pct": (f0.get("exchange") or {}).get("ema20_distance_pct"),
            "ema50_distance_pct": (f0.get("exchange") or {}).get("ema50_distance_pct"),
            "exhaustion": f0.get("exhaustion"),
            "opportunity": f0.get("opportunity_v2"),
            "multitimeframe": f0.get("multitimeframe"),
        },
    }, default=str)
