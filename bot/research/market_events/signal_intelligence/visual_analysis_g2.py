"""Phase G.2 Task D — Claude visual chart analysis."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.db import insert_returning_id

_VISUAL_SYSTEM = """You are a market structure analyst reviewing trading chart images or chart metadata.
Identify BOS/CHoCH, Demand/Supply zones, Liquidity Sweep, entry zones.
Assess agreement with the provided quantitative shock model.
You do NOT place trades or change confidence scores. Output JSON only."""

_VISUAL_SCHEMA = {
    "bos_choch": "string — BOS / CHoCH / none",
    "demand_zones": ["list of zone descriptions"],
    "supply_zones": ["list of zone descriptions"],
    "liquidity_sweep": "string — detected sweep or none",
    "entry_zones": ["list of entry zone descriptions"],
    "model_agreement": "string — agrees / partial / diverges",
    "agreement_score": "0-100 number",
    "summary_ru": "one sentence in Russian",
}


def _load_image_artifacts(conn: Any, event_id: int) -> tuple[bool, str | None, str | None, dict[str, Any]]:
    """Return has_image, image_path, image_base64, chart_context."""
    rows = conn.execute(
        """
        SELECT context_json FROM market_event_context
        WHERE event_id = ? AND context_type IN (
          'TELEGRAM_SIGNAL', 'MARKET_COMMENTARY', 'TRADER_THESIS'
        )
        ORDER BY relevance_score DESC LIMIT 5
        """,
        (event_id,),
    ).fetchall()

    has_image = False
    image_path = None
    image_b64 = None
    for r in rows:
        try:
            ctx = json.loads(r["context_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if ctx.get("has_photo") or ctx.get("content_type") == "TECHNICAL_LEVELS":
            has_image = True
        image_path = image_path or ctx.get("image_path") or ctx.get("photo_path")
        image_b64 = image_b64 or ctx.get("image_base64")

    visual = conn.execute(
        "SELECT structured_json FROM market_events_visual_intel_f4 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    f7_img = conn.execute(
        "SELECT image_intel_json FROM market_events_market_intelligence_f7 WHERE event_id = ?",
        (event_id,),
    ).fetchone()

    chart_ctx: dict[str, Any] = {}
    if visual and visual["structured_json"]:
        try:
            chart_ctx["f4"] = json.loads(visual["structured_json"])
        except (json.JSONDecodeError, TypeError):
            pass
    if f7_img and f7_img["image_intel_json"]:
        try:
            chart_ctx["f7"] = json.loads(f7_img["image_intel_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    return has_image or bool(image_path or image_b64), image_path, image_b64, chart_ctx


def run_visual_analysis_g2(conn: Any, event_id: int, *, shock_context: dict[str, Any]) -> dict[str, Any] | None:
    has_image, image_path, image_b64, chart_ctx = _load_image_artifacts(conn, event_id)
    if not has_image and not chart_ctx:
        return None

    existing = conn.execute(
        "SELECT 1 FROM market_events_g2_visual_analysis WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if existing:
        row = conn.execute(
            "SELECT visual_json FROM market_events_g2_visual_analysis WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return json.loads(row["visual_json"]) if row else None

    prompt = json.dumps({
        "task": "Analyze chart for market structure",
        "required_schema": _VISUAL_SCHEMA,
        "shock_model": shock_context,
        "chart_metadata": chart_ctx,
        "has_image_bytes": bool(image_path or image_b64),
    }, ensure_ascii=False, default=str)

    result: dict[str, Any]
    in_tok = out_tok = 0
    cost = 0.0
    provider = "deterministic"
    ai_status = "DETERMINISTIC"
    skip_error: str | None = None

    try:
        from bot.research.market_events.signal_intelligence.claude_client_g2 import (
            ClaudeClientError,
            call_claude_vision_json_g2,
            call_claude_json_g2,
            default_model,
            is_claude_configured,
        )
        from bot.research.market_events.signal_intelligence.claude_ops_g2 import (
            record_claude_failure,
            record_claude_success,
            try_consume_claude_quota,
        )
        if is_claude_configured():
            allowed, limit_reason = try_consume_claude_quota(conn)
            if not allowed:
                result = _visual_deterministic(chart_ctx, shock_context)
                ai_status, skip_error = "RATE_LIMITED", limit_reason
            elif image_path or image_b64:
                parsed, resp = call_claude_vision_json_g2(
                    system=_VISUAL_SYSTEM,
                    prompt=prompt,
                    image_path=image_path,
                    image_base64=image_b64,
                    label="g2_visual",
                )
                result = _normalize_visual(parsed, chart_ctx)
                in_tok, out_tok, cost = resp.usage.input_tokens, resp.usage.output_tokens, resp.usage.cost_usd
                provider, ai_status = "anthropic", "OK"
                record_claude_success(conn, usage=resp.usage, model=resp.model)
            else:
                parsed, resp = call_claude_json_g2(system=_VISUAL_SYSTEM, prompt=prompt, label="g2_visual_text")
                result = _normalize_visual(parsed, chart_ctx)
                in_tok, out_tok, cost = resp.usage.input_tokens, resp.usage.output_tokens, resp.usage.cost_usd
                provider, ai_status = "anthropic", "OK"
                record_claude_success(conn, usage=resp.usage, model=resp.model)
        else:
            result = _visual_deterministic(chart_ctx, shock_context)
    except ClaudeClientError as exc:
        record_claude_failure(conn, error=f"{exc.kind}: {exc.message}", model=default_model())
        result = _visual_deterministic(chart_ctx, shock_context)
        ai_status, skip_error = "AI_SKIPPED", str(exc)
    except Exception as exc:
        record_claude_failure(conn, error=str(exc), model=default_model())
        result = _visual_deterministic(chart_ctx, shock_context)
        ai_status, skip_error = "AI_SKIPPED", str(exc)

    now = int(time.time())
    insert_returning_id(
        conn,
        """
        INSERT INTO market_events_g2_visual_analysis (
          event_id, has_image, platform, bos_choch, demand_supply_json,
          liquidity_sweep, entry_zones_json, model_agreement, agreement_score,
          visual_json, input_tokens, output_tokens, cost_usd, provider, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            1 if has_image else 0,
            (chart_ctx.get("f4") or {}).get("platform") or (chart_ctx.get("f7") or {}).get("platform"),
            result.get("bos_choch"),
            json.dumps({
                "demand": result.get("demand_zones", []),
                "supply": result.get("supply_zones", []),
            }, ensure_ascii=False),
            result.get("liquidity_sweep"),
            json.dumps(result.get("entry_zones", []), ensure_ascii=False),
            result.get("model_agreement"),
            float(result.get("agreement_score") or 0),
            json.dumps(result, ensure_ascii=False),
            in_tok, out_tok, cost, provider, now,
        ),
    )
    result["_ai_status"] = ai_status
    if skip_error:
        result["_skip_error"] = skip_error
    return result


def _normalize_visual(data: dict[str, Any], chart_ctx: dict[str, Any]) -> dict[str, Any]:
    f4 = chart_ctx.get("f4") or {}
    chart = f4.get("chart_analysis") or {}
    return {
        "bos_choch": str(data.get("bos_choch") or "none"),
        "demand_zones": [str(x) for x in (data.get("demand_zones") or chart.get("demand_zones") or [])][:5],
        "supply_zones": [str(x) for x in (data.get("supply_zones") or chart.get("supply_zones") or [])][:5],
        "liquidity_sweep": str(data.get("liquidity_sweep") or "none"),
        "entry_zones": [str(x) for x in (data.get("entry_zones") or [])][:5],
        "model_agreement": str(data.get("model_agreement") or "partial"),
        "agreement_score": float(data.get("agreement_score") or 50),
        "summary_ru": str(data.get("summary_ru") or ""),
    }


def _visual_deterministic(chart_ctx: dict[str, Any], shock: dict[str, Any]) -> dict[str, Any]:
    f4 = chart_ctx.get("f4") or {}
    chart = f4.get("chart_analysis") or {}
    labels = chart.get("structure_labels") or []
    bos = "CHoCH" if any("CHoCH" in str(l) for l in labels) else (
        "BOS" if any("BOS" in str(l) for l in labels) else "none"
    )
    liq = chart.get("liquidity_levels") or []
    sweep = "Liquidity Sweep" if liq else "none"
    agreement = f4.get("crosscheck", {}).get("agreement_pct", 50)
    return {
        "bos_choch": bos,
        "demand_zones": list(chart.get("demand_zones") or [])[:3],
        "supply_zones": list(chart.get("supply_zones") or [])[:3],
        "liquidity_sweep": sweep,
        "entry_zones": [str(f4.get("extracted", {}).get("entry"))] if f4.get("extracted", {}).get("entry") else [],
        "model_agreement": "agrees" if float(agreement) >= 70 else "partial",
        "agreement_score": float(agreement),
        "summary_ru": "Структура извлечена из метаданных графика (без LLM).",
    }
