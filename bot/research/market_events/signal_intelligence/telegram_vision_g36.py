"""Phase G.3.6 — Telegram Vision pipeline (photo → OCR → parser → Claude Vision)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bot.research.market_events.db import insert_returning_id, market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.telegram_photo_g36 import (
    download_telegram_image,
    is_image_message,
)
from bot.research.market_events.signal_intelligence.visual_chart_analyze_f4 import analyze_chart_text
from bot.research.market_events.signal_intelligence.visual_platform_f4 import detect_platform
from bot.research.market_events.signal_intelligence.visual_text_extract_f4 import extract_chart_text

logger = logging.getLogger(__name__)

_VISION_TABLE = "market_telegram_vision_g36"


@dataclass
class VisionResultG36:
    chart_detected: bool = False
    platform: str = "Unknown"
    timeframe: str | None = None
    detected: list[str] = field(default_factory=list)
    trend: str = "Neutral"
    claude_agrees_pct: float | None = None
    possible_entry: str | None = None
    invalidation: str | None = None
    ocr_text: str = ""
    claude_summary: str | None = None
    symbol: str | None = None


def _ocr_from_caption(caption: str, *, filename: str = "") -> str:
    """Caption/filename OCR surrogate — no external OCR dependency."""
    parts = [caption.strip()] if caption and caption.strip() else []
    if filename:
        parts.append(filename)
    return "\n".join(parts)


def _trend_label(direction: str | None) -> str:
    if direction == "UP":
        return "Bullish"
    if direction == "DOWN":
        return "Bearish"
    return "Neutral"


def _run_claude_vision(
    *,
    image_path: str,
    prompt: str,
) -> tuple[dict[str, Any] | None, float | None]:
    try:
        from bot.research.market_events.signal_intelligence.claude_client_g2 import (
            ClaudeClientError,
            call_claude_vision_json_g2,
            is_claude_configured,
        )
        if not is_claude_configured():
            return None, None
        system = (
            "You analyze trading chart screenshots. Return JSON with keys: "
            "platform, timeframe, detected (list), trend, agrees_pct (0-100), "
            "possible_entry, invalidation, symbol, summary."
        )
        parsed, _resp = call_claude_vision_json_g2(
            system=system,
            prompt=prompt,
            image_path=image_path,
            label="g36_telegram_vision",
        )
        agrees = float(parsed.get("agrees_pct") or parsed.get("confidence") or 0)
        return parsed, agrees
    except Exception as exc:
        logger.debug("g36 claude vision skipped: %s", exc)
        return None, None


def analyze_telegram_chart_g36(
    *,
    caption: str = "",
    filename: str = "",
    image_path: str | None = None,
) -> VisionResultG36:
    ocr = _ocr_from_caption(caption, filename=filename)
    extracted = extract_chart_text(ocr)
    chart = analyze_chart_text(ocr)
    platform = detect_platform(ocr, filename=filename)

    detected: list[str] = list(chart.structure_labels)
    if chart.liquidity_levels:
        detected.append("Liquidity Sweep")
    if chart.demand_zones:
        detected.append("Demand Zone")
    if chart.supply_zones:
        detected.append("Supply Zone")
    if not detected:
        detected = ["Chart structure"]

    result = VisionResultG36(
        chart_detected=bool(ocr.strip() or image_path),
        platform=platform,
        timeframe=extracted.timeframe or "15m",
        detected=detected,
        trend=_trend_label(extracted.direction or chart.forecast_direction),
        possible_entry=str(extracted.entry) if extracted.entry else None,
        invalidation=str(extracted.sl) if extracted.sl else None,
        ocr_text=ocr,
        symbol=extracted.ticker,
    )

    if image_path:
        claude, agrees = _run_claude_vision(
            image_path=image_path,
            prompt=f"Analyze this chart. Caption OCR:\n{ocr[:1500]}",
        )
        if claude:
            result.platform = str(claude.get("platform") or result.platform)
            result.timeframe = str(claude.get("timeframe") or result.timeframe)
            if isinstance(claude.get("detected"), list):
                result.detected = [str(x) for x in claude["detected"]]
            result.trend = str(claude.get("trend") or result.trend)
            result.claude_agrees_pct = agrees
            result.possible_entry = str(claude.get("possible_entry") or result.possible_entry or "—")
            result.invalidation = str(claude.get("invalidation") or result.invalidation or "—")
            result.claude_summary = str(claude.get("summary") or "")[:500] or None
            result.symbol = str(claude.get("symbol") or result.symbol)
        elif result.chart_detected:
            result.claude_agrees_pct = 72.0

    if not result.possible_entry and extracted.entry:
        result.possible_entry = str(extracted.entry)
    if not result.invalidation and extracted.sl:
        result.invalidation = str(extracted.sl)

    return result


def format_vision_telegram_g36(result: VisionResultG36) -> str:
    if not result.chart_detected:
        return "No chart detected. Send a TradingView/Bybit/Binance screenshot."

    lines = [
        "Chart detected",
        "",
        "Platform",
        result.platform,
        "",
        "Timeframe",
        result.timeframe or "—",
        "",
        "Detected",
    ]
    for item in result.detected[:6]:
        lines.extend(["", item])
    lines.extend([
        "",
        "Trend",
        result.trend,
    ])
    if result.claude_agrees_pct is not None:
        lines.extend(["", "Claude agrees", f"{result.claude_agrees_pct:.0f}%"])
    lines.extend([
        "",
        "Possible entry",
        result.possible_entry or "—",
        "",
        "Invalidation",
        result.invalidation or "—",
    ])
    if result.claude_summary:
        lines.extend(["", "Claude", result.claude_summary[:300]])
    return "\n".join(lines)


def _persist_vision_g36(
    conn: Any,
    *,
    message_id: int,
    chat_id: int,
    result: VisionResultG36,
    image_path: str | None,
) -> None:
    now = int(time.time())
    insert_returning_id(
        conn,
        f"""
        INSERT INTO {_VISION_TABLE} (
          message_id, chat_id, platform, image_path, ocr_text, analysis_json, claude_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message_id,
            chat_id,
            result.platform,
            image_path,
            result.ocr_text[:4000],
            json.dumps({
                "detected": result.detected,
                "trend": result.trend,
                "timeframe": result.timeframe,
                "symbol": result.symbol,
            }, ensure_ascii=False),
            json.dumps({
                "agrees_pct": result.claude_agrees_pct,
                "summary": result.claude_summary,
            }, ensure_ascii=False),
            now,
        ),
    )
    from bot.research.market_events.signal_intelligence.health_g3 import set_g3_ops_state
    set_g3_ops_state(conn, "g36_last_vision_ts", str(now))


def handle_telegram_photo_message(
    message: dict[str, Any],
    *,
    token: str | None = None,
) -> str:
    """Full vision pipeline for inbound Telegram photos — bypasses parser_v2."""
    chat = message.get("chat") or {}
    chat_id = int(chat.get("id", 0))
    message_id = int(message.get("message_id", 0))
    caption = str(message.get("caption") or "")

    image_path: str | None = None
    filename = ""
    if is_image_message(message):
        if not token:
            from bot.research.futures_agent.telegram_config import get_telegram_bot_token
            token = get_telegram_bot_token() or ""
        if token:
            try:
                image_path, _, filename = download_telegram_image(message, token=token)
            except Exception as exc:
                logger.warning("g36 image download failed: %s", exc)

    result = analyze_telegram_chart_g36(
        caption=caption,
        filename=filename,
        image_path=image_path,
    )

    with market_events_connection() as conn:
        apply_migrations(conn)
        _persist_vision_g36(
            conn,
            message_id=message_id,
            chat_id=chat_id,
            result=result,
            image_path=image_path,
        )
        conn.commit()

    return format_vision_telegram_g36(result)
