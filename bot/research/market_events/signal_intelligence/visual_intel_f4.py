"""Phase F.4 — visual intelligence orchestrator for Telegram chart images."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.visual_chart_analyze_f4 import analyze_chart_text
from bot.research.market_events.signal_intelligence.visual_crosscheck_f4 import crosscheck_visual_intel
from bot.research.market_events.signal_intelligence.visual_platform_f4 import detect_platform
from bot.research.market_events.signal_intelligence.visual_text_extract_f4 import extract_chart_text

PROMPT_VERSION_F4_VISUAL = "f4_visual_v1"


@dataclass(frozen=True)
class VisualIntelResult:
    event_id: int
    platform: str
    has_image: bool
    extracted: dict[str, Any]
    chart_analysis: dict[str, Any]
    crosscheck: dict[str, Any]
    structured_json: dict[str, Any]


def _fetch_telegram_text(conn: Any, event_id: int) -> tuple[str, bool, str]:
    """Load caption/text from linked market_event_context + futures_agent posts."""
    rows = conn.execute(
        """
        SELECT context_json, source, context_type FROM market_event_context
        WHERE event_id = ? AND context_type IN (
          'TELEGRAM_SIGNAL', 'MARKET_COMMENTARY', 'TRADER_THESIS', 'NEWS'
        )
        ORDER BY relevance_score DESC LIMIT 10
        """,
        (event_id,),
    ).fetchall()

    parts: list[str] = []
    has_image = False
    filename = ""
    for r in rows:
        try:
            ctx = json.loads(r["context_json"] or "{}")
        except Exception:
            ctx = {}
        if ctx.get("has_photo") or ctx.get("content_type") == "TECHNICAL_LEVELS":
            has_image = True
        if ctx.get("filename"):
            filename = str(ctx["filename"])
        post_id = ctx.get("post_id")
        if post_id:
            text = _fetch_post_text(post_id)
            if text:
                parts.append(text)
        if ctx.get("raw_text"):
            parts.append(str(ctx["raw_text"]))

    return "\n".join(parts), has_image, filename


def _fetch_post_text(post_id: int | str) -> str | None:
    try:
        from bot.research.futures_agent.db import agent_connection
        with agent_connection() as agent:
            row = agent.execute(
                """
                SELECT raw_text, content_type FROM futures_agent_trader_posts
                WHERE id = ? LIMIT 1
                """,
                (int(post_id),),
            ).fetchone()
            if row and row["raw_text"]:
                return str(row["raw_text"])
    except Exception:
        pass
    return None


def run_visual_intel(conn: Any, event_id: int) -> VisualIntelResult | None:
    row = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return None

    text, has_image, filename = _fetch_telegram_text(conn, event_id)
    if not text and not has_image:
        return None

    platform = detect_platform(text, filename=filename)
    extracted = extract_chart_text(text)
    chart = analyze_chart_text(text)

    from bot.research.market_events.signal_intelligence.signal_report_f2 import load_signal_report_f2

    f2 = load_signal_report_f2(conn, event_id)
    mlabels = f2.market_structure_labels if f2 else []
    conf = f2.confidence_score if f2 else 0.0

    cross = crosscheck_visual_intel(
        symbol=str(row["symbol"]),
        shock_direction=str(row["direction"]),
        shock_return_pct=float(row["return_pct"] or 0),
        extracted_ticker=extracted.ticker,
        extracted_direction=extracted.direction,
        chart_structure={
            "demand_zones": chart.demand_zones,
            "supply_zones": chart.supply_zones,
            "liquidity_levels": chart.liquidity_levels,
            "structure_labels": chart.structure_labels,
            "forecast_direction": chart.forecast_direction,
            "author_scenario": chart.author_scenario,
        },
        market_structure_labels=mlabels,
        confidence=conf,
    )

    structured = {
        "platform": platform,
        "has_image": has_image,
        "ticker": extracted.ticker,
        "timeframe": extracted.timeframe,
        "entry": extracted.entry,
        "tp": extracted.tp,
        "sl": extracted.sl,
        "direction": extracted.direction,
        "comments": extracted.comments[:3],
        "chart": {
            "demand_zones": chart.demand_zones,
            "supply_zones": chart.supply_zones,
            "liquidity_levels": chart.liquidity_levels,
            "structure_labels": chart.structure_labels,
            "forecast_direction": chart.forecast_direction,
            "author_scenario": chart.author_scenario,
        },
        "crosscheck": cross.json_payload,
    }

    result = VisualIntelResult(
        event_id=event_id,
        platform=platform,
        has_image=has_image,
        extracted={
            "ticker": extracted.ticker,
            "timeframe": extracted.timeframe,
            "entry": extracted.entry,
            "tp": extracted.tp,
            "sl": extracted.sl,
            "direction": extracted.direction,
        },
        chart_analysis=structured["chart"],
        crosscheck=cross.json_payload,
        structured_json=structured,
    )
    persist_visual_intel(conn, result)
    return result


def persist_visual_intel(conn: Any, result: VisualIntelResult) -> int:
    now = int(time.time())
    return insert_returning_id(
        conn,
        """
        INSERT INTO market_events_visual_intel_f4 (
          event_id, platform, has_image, extracted_json, chart_analysis_json,
          crosscheck_json, structured_json, prompt_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(event_id) DO UPDATE SET
          platform = excluded.platform,
          has_image = excluded.has_image,
          extracted_json = excluded.extracted_json,
          chart_analysis_json = excluded.chart_analysis_json,
          crosscheck_json = excluded.crosscheck_json,
          structured_json = excluded.structured_json,
          prompt_version = excluded.prompt_version,
          created_at = excluded.created_at
        """,
        (
            result.event_id,
            result.platform,
            1 if result.has_image else 0,
            json.dumps(result.extracted, ensure_ascii=False),
            json.dumps(result.chart_analysis, ensure_ascii=False),
            json.dumps(result.crosscheck, ensure_ascii=False),
            json.dumps(result.structured_json, ensure_ascii=False),
            PROMPT_VERSION_F4_VISUAL,
            now,
        ),
    )


def load_visual_intel(conn: Any, event_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM market_events_visual_intel_f4 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "platform": row["platform"],
        "has_image": bool(row["has_image"]),
        "structured_json": json.loads(row["structured_json"] or "{}"),
        "chart_analysis": json.loads(row["chart_analysis_json"] or "{}"),
        "crosscheck": json.loads(row["crosscheck_json"] or "{}"),
    }
