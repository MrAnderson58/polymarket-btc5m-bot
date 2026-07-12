"""Phase F.7 Task E — news impact weighting."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

IMPACT_LOW = "LOW"
IMPACT_MEDIUM = "MEDIUM"
IMPACT_HIGH = "HIGH"

KEYWORDS: dict[str, tuple[str, ...]] = {
    "HIGH": (
        "war", "attack", "strike", "missile", "invasion", "sanctions",
        "israel", "iran", "hamas", "hezbollah",
        "sec lawsuit", "sec charges", "exchange hack", "bankruptcy",
        "cpi surprise", "rate hike emergency",
        "война", "удар", "иран", "израиль", "санкции",
    ),
    "MEDIUM": (
        "etf", "fed", "fomc", "cpi", "ppi", "nfp", "sec", "regulation",
        "tariff", "treasury", "inflation", "rate cut", "rate hike",
        "etf approval", "etf rejection", "halving",
    ),
}


@dataclass(frozen=True)
class NewsWeightF7:
    impact: str
    keywords: list[str]
    headlines: list[str]
    summary: str


def _match_keywords(text: str) -> tuple[str, list[str]]:
    low = text.lower()
    found: list[str] = []
    impact = IMPACT_LOW
    for kw in KEYWORDS["HIGH"]:
        if kw in low:
            found.append(kw)
            impact = IMPACT_HIGH
    if impact != IMPACT_HIGH:
        for kw in KEYWORDS["MEDIUM"]:
            if kw in low:
                found.append(kw)
                impact = IMPACT_MEDIUM
    return impact, list(dict.fromkeys(found))


def analyze_news_weight(conn: Any, *, event_id: int) -> NewsWeightF7:
    rows = conn.execute(
        """
        SELECT context_json, source FROM market_event_context
        WHERE event_id = ? AND context_type IN ('NEWS', 'NEWS_EVENT', 'MARKET_COMMENTARY')
        ORDER BY relevance_score DESC LIMIT 20
        """,
        (event_id,),
    ).fetchall()

    headlines: list[str] = []
    all_text_parts: list[str] = []
    max_impact = IMPACT_LOW
    all_keywords: list[str] = []

    for row in rows:
        try:
            ctx = json.loads(row["context_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            ctx = {}
        text = " ".join(filter(None, [
            str(ctx.get("headline") or ""),
            str(ctx.get("title") or ""),
            str(ctx.get("raw_text") or ""),
            str(ctx.get("summary") or ""),
            str(row["source"] or ""),
        ]))
        if not text.strip():
            continue
        impact, kws = _match_keywords(text)
        if impact == IMPACT_HIGH:
            max_impact = IMPACT_HIGH
        elif impact == IMPACT_MEDIUM and max_impact != IMPACT_HIGH:
            max_impact = IMPACT_MEDIUM
        all_keywords.extend(kws)
        headline = (ctx.get("headline") or ctx.get("title") or text[:120]).strip()
        if headline:
            headlines.append(headline[:200])
        all_text_parts.append(text)

    if not all_text_parts:
        return NewsWeightF7(
            impact=IMPACT_LOW,
            keywords=[],
            headlines=[],
            summary="Новостной фон спокойный",
        )

    unique_kw = list(dict.fromkeys(all_keywords))
    if max_impact == IMPACT_HIGH:
        summary = f"Высокое новостное влияние: {', '.join(unique_kw[:4])}"
    elif max_impact == IMPACT_MEDIUM:
        summary = f"Среднее новостное влияние: {', '.join(unique_kw[:4])}"
    else:
        summary = "Низкое новостное влияние"

    return NewsWeightF7(
        impact=max_impact,
        keywords=unique_kw,
        headlines=headlines[:5],
        summary=summary,
    )
