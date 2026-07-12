"""Phase F.7 Task H — Telegram image pipeline (OCR text + crosscheck)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.signal_intelligence.visual_chart_analyze_f4 import analyze_chart_text
from bot.research.market_events.signal_intelligence.visual_platform_f4 import detect_platform
from bot.research.market_events.signal_intelligence.visual_text_extract_f4 import extract_chart_text

IMAGE_PLATFORMS = ("TradingView", "Bybit", "Binance", "OKX", "Hyperliquid")

_TP_LEVELS = re.compile(
    r"(?:tp\s*(\d+)|target\s*(\d+)|цель\s*(\d+))\s*[:=@]?\s*([\d.,]+)",
    re.I,
)
_LEVEL = re.compile(
    r"(?:level|уровень|zone|зона)\s*[:=@]?\s*([\d.,]+)",
    re.I,
)


@dataclass
class ImageIntelF7:
    platform: str
    has_image: bool
    ticker: str | None = None
    timeframe: str | None = None
    entry: float | None = None
    tp_levels: list[float] = field(default_factory=list)
    sl: float | None = None
    direction: str | None = None
    structure: list[str] = field(default_factory=list)
    zones: list[str] = field(default_factory=list)
    agreement_pct: float = 0.0
    agreement_text: str = ""
    author_view: str = ""
    divergence: list[str] = field(default_factory=list)


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", "").strip())
    except ValueError:
        return None


def _extract_levels(text: str) -> tuple[list[float], list[str]]:
    tps: list[float] = []
    zones: list[str] = []
    for m in _TP_LEVELS.finditer(text):
        val = _num(m.group(4))
        if val:
            tps.append(val)
    for m in _LEVEL.finditer(text):
        val = _num(m.group(1))
        if val:
            zones.append(f"level_{val}")
    return tps, zones


def _fetch_telegram_content(conn: Any, event_id: int) -> tuple[str, bool, str]:
    rows = conn.execute(
        """
        SELECT context_json, source FROM market_event_context
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
        except (json.JSONDecodeError, TypeError):
            ctx = {}
        if ctx.get("has_photo") or ctx.get("content_type") == "TECHNICAL_LEVELS":
            has_image = True
        if ctx.get("filename"):
            filename = str(ctx["filename"])
        if ctx.get("raw_text"):
            parts.append(str(ctx["raw_text"]))
        post_id = ctx.get("post_id")
        if post_id:
            try:
                from bot.research.futures_agent.db import agent_connection
                with agent_connection() as agent:
                    row = agent.execute(
                        "SELECT raw_text FROM futures_agent_trader_posts WHERE id = ? LIMIT 1",
                        (int(post_id),),
                    ).fetchone()
                    if row and row["raw_text"]:
                        parts.append(str(row["raw_text"]))
            except Exception:
                pass
    return "\n".join(parts), has_image, filename


def _crosscheck(
    *,
    extracted: Any,
    chart: Any,
    shock_direction: str,
    reversal_prob: float,
    tp_levels: list[float],
) -> tuple[float, str, list[str]]:
    score = 50.0
    divergences: list[str] = []
    author_dir = extracted.direction or chart.forecast_direction
    if author_dir:
        if author_dir == "UP" and shock_direction == "DOWN":
            score += 25
        elif author_dir == "DOWN" and shock_direction == "UP":
            score += 25
        elif author_dir != shock_direction:
            divergences.append(f"Автор: {author_dir}, шок: {shock_direction}")
            score -= 15
        else:
            score += 10

    if extracted.entry:
        score += 5
    if extracted.tp or tp_levels:
        score += 5
    if chart.demand_zones:
        score += 10
    if chart.structure_labels:
        score += min(10, len(chart.structure_labels) * 3)

    score = min(95.0, max(20.0, score + reversal_prob * 20))
    agreement = round(score, 0)

    if agreement >= 75:
        text = f"Мы согласны на {agreement:.0f}%"
    elif agreement >= 55:
        text = f"Частичное согласие — {agreement:.0f}%"
    else:
        text = "Есть расхождение"

    return agreement, text, divergences


def analyze_image_pipeline(
    conn: Any,
    *,
    event_id: int,
    shock_direction: str,
    reversal_prob: float,
) -> ImageIntelF7 | None:
    text, has_image, filename = _fetch_telegram_content(conn, event_id)
    if not text and not has_image:
        return None

    platform = detect_platform(text, filename=filename)
    extracted = extract_chart_text(text)
    chart = analyze_chart_text(text)
    tp_levels, level_zones = _extract_levels(text)
    if extracted.tp and extracted.tp not in tp_levels:
        tp_levels.insert(0, extracted.tp)

    structure = list(chart.structure_labels)
    zones = list(chart.demand_zones + chart.supply_zones + level_zones)
    if chart.liquidity_levels:
        zones.extend(chart.liquidity_levels)

    agreement, agreement_text, divergences = _crosscheck(
        extracted=extracted,
        chart=chart,
        shock_direction=shock_direction,
        reversal_prob=reversal_prob,
        tp_levels=tp_levels,
    )

    author_parts: list[str] = []
    if extracted.direction:
        author_parts.append(f"{'LONG' if extracted.direction == 'UP' else 'SHORT'}")
    if extracted.ticker:
        author_parts.append(extracted.ticker)
    if extracted.entry:
        author_parts.append(f"Entry {extracted.entry}")
    if tp_levels:
        author_parts.append(f"TP {', '.join(str(t) for t in tp_levels[:3])}")
    if extracted.sl:
        author_parts.append(f"SL {extracted.sl}")
    if chart.author_scenario:
        author_parts.append(chart.author_scenario)

    return ImageIntelF7(
        platform=platform,
        has_image=has_image,
        ticker=extracted.ticker,
        timeframe=extracted.timeframe,
        entry=extracted.entry,
        tp_levels=tp_levels,
        sl=extracted.sl,
        direction=extracted.direction or chart.forecast_direction,
        structure=structure,
        zones=zones,
        agreement_pct=agreement,
        agreement_text=agreement_text,
        author_view=" — ".join(author_parts) if author_parts else "Автор не указал уровни",
        divergence=divergences,
    )
