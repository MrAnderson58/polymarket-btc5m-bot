"""F.0 deterministic AI analyst — strict JSON output."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.ai_context_f0 import build_f0_context_bundle, build_f0_prompt
from bot.research.market_events.signal_intelligence.ai_schema_f0 import F0AnalysisResponse, parse_f0_response
from bot.research.market_events.signal_intelligence.config import PROMPT_VERSION_F0


def analyze_f0_deterministic(conn: Any, event_id: int) -> F0AnalysisResponse:
    bundle = build_f0_context_bundle(conn, event_id)
    me = bundle.get("market_event", {})
    f0 = bundle.get("f0", {})
    ret = abs(float(me.get("return_pct") or 0))
    exh_score = float((f0.get("exhaustion") or {}).get("score") or 0)
    opp = float((f0.get("opportunity_v2") or {}).get("score") or 50)

    if exh_score >= 70:
        bias, cont, rev = "FADE", 0.25, 0.72
    elif ret >= 5:
        bias, cont, rev = "CONTINUATION", 0.65, 0.35
    else:
        bias, cont, rev = "WAIT", 0.45, 0.55

    conf = min(0.95, 0.4 + opp / 200.0 + exh_score / 200.0)
    symbol = me.get("symbol", "?")
    direction = me.get("direction", "?")

    return F0AnalysisResponse(
        bias=bias,
        confidence=round(conf, 2),
        continuation_probability=round(cont, 2),
        reversal_probability=round(rev, 2),
        summary_ru=f"{symbol} {direction}: score={opp:.0f}, exhaustion={exh_score:.0f}. Bias={bias}.",
        summary_en=f"{symbol} {direction}: score={opp:.0f}, exhaustion={exh_score:.0f}. Bias={bias}.",
    )


def persist_f0_analysis(conn: Any, event_id: int, response: F0AnalysisResponse) -> int:
    payload = response.to_dict()
    return insert_returning_id(
        conn,
        """
        INSERT INTO market_event_ai_analyses_f0 (
          event_id, job_id, prompt_version, response_json, bias, confidence,
          continuation_probability, reversal_probability, summary_ru, summary_en, created_at
        ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, PROMPT_VERSION_F0, json.dumps(payload),
            response.bias, response.confidence,
            response.continuation_probability, response.reversal_probability,
            response.summary_ru, response.summary_en, int(time.time()),
        ),
    )


def run_f0_ai_analysis(conn: Any, event_id: int) -> F0AnalysisResponse:
    resp = analyze_f0_deterministic(conn, event_id)
    persist_f0_analysis(conn, event_id, resp)
    return resp
