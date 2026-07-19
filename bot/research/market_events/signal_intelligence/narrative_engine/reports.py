"""Claude + Telegram markdown reports for Narrative Engine."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

REPORTS_DIR = BASE_DIR / "reports"


def _parse_json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except Exception:
        return []


def _bullets_assets(rows: list[dict[str, Any]], *, empty: str = "—") -> str:
    if not rows:
        return empty
    lines = []
    for r in rows:
        lines.append(
            f"- **{r.get('symbol')}** "
            f"(news={r.get('news_count')}, conf={r.get('confidence')}, "
            f"risk={r.get('risk_level')}): {str(r.get('summary') or '')[:180]}"
        )
    return "\n".join(lines)


def render_claude_market_context(
    *,
    assets: list[dict[str, Any]],
    briefs: list[dict[str, Any]],
    now: int,
) -> str:
    active = [a for a in assets if int(a.get("news_count") or 0) > 0]
    top_bullish = sorted(
        active,
        key=lambda a: float(a["bullish_score"]) * float(a["importance"]),
        reverse=True,
    )[:5]
    top_bearish = sorted(
        active,
        key=lambda a: float(a["bearish_score"]) * float(a["importance"]),
        reverse=True,
    )[:5]
    most = sorted(active, key=lambda a: int(a["news_count"]), reverse=True)[:5]

    brief = briefs[0] if briefs else {}
    global_narrative = (
        brief.get("global_narrative")
        or (
            top_bullish[0]["summary"].split("\n")[0]
            if top_bullish
            else "Quiet news flow across the watchlist."
        )
    )
    macro = _parse_json_list(brief.get("macro_events_json"))
    fed = _parse_json_list(brief.get("fed_json"))
    poly = _parse_json_list(brief.get("polymarket_json"))
    whales = _parse_json_list(brief.get("whales_json"))
    risk = brief.get("risk_level") or (
        "HIGH" if any(a["risk_level"] == "HIGH" for a in active) else "MEDIUM"
        if any(a["risk_level"] == "MEDIUM" for a in active) else "LOW"
    )

    dt = datetime.fromtimestamp(now).isoformat(timespec="seconds")
    lines = [
        "# MARKET CONTEXT",
        "",
        f"_Generated: {dt}_",
        "",
        "## Global Narrative",
        str(global_narrative),
        "",
        "## Top Bullish Assets",
        _bullets_assets(top_bullish),
        "",
        "## Top Bearish Assets",
        _bullets_assets(top_bearish),
        "",
        "## Most Discussed",
        _bullets_assets(most),
        "",
        "## Macro",
        "\n".join(f"- {x}" for x in macro) if macro else "—",
        "",
        "## Fed",
        "\n".join(f"- {x}" for x in fed) if fed else "—",
        "",
        "## Polymarket",
        "\n".join(f"- {x}" for x in poly) if poly else "—",
        "",
        "## Whales",
        "\n".join(f"- {x}" for x in whales) if whales else "—",
        "",
        "## Risks",
        f"Aggregate risk level: **{risk}**",
        "",
        "## Asset Intelligence",
        "",
    ]
    for a in assets:
        lines.extend([
            f"### {a['symbol']}",
            f"- news_count: {a['news_count']}",
            f"- narrative: {a['narrative']}",
            f"- bullish/bearish/neutral: "
            f"{a['bullish_score']}/{a['bearish_score']}/{a['neutral_score']}",
            f"- importance: {a['importance']}",
            f"- confidence: {a['confidence']}",
            f"- risk_level: {a['risk_level']}",
            f"- macro/whale/polymarket/market: "
            f"{a['macro_score']}/{a['whale_score']}/"
            f"{a['polymarket_score']}/{a['market_score']}",
            f"- summary: {a['summary']}",
            "",
        ])
    lines.append(
        "Claude must answer using only this file. "
        "Do not invent prices or trades."
    )
    return "\n".join(lines)


def render_telegram_brief(
    *,
    assets: list[dict[str, Any]],
    briefs: list[dict[str, Any]],
    now: int,
) -> str:
    active = [a for a in assets if int(a.get("news_count") or 0) > 0]
    top_bullish = sorted(
        active,
        key=lambda a: float(a["bullish_score"]) * float(a["importance"]),
        reverse=True,
    )[:3]
    top_bearish = sorted(
        active,
        key=lambda a: float(a["bearish_score"]) * float(a["importance"]),
        reverse=True,
    )[:3]
    brief = briefs[0] if briefs else {}
    main = brief.get("global_narrative") or (
        active[0]["narrative"] if active else "General / quiet tape"
    )
    if isinstance(main, str) and len(main) > 220:
        main = main[:217] + "..."
    risk = brief.get("risk_level") or (
        "HIGH" if any(a["risk_level"] == "HIGH" for a in active) else "LOW"
    )
    macro = _parse_json_list(brief.get("macro_events_json"))
    poly = _parse_json_list(brief.get("polymarket_json"))
    bull_line = ", ".join(a["symbol"] for a in top_bullish) or "—"
    bear_line = ", ".join(a["symbol"] for a in top_bearish) or "—"
    conclusion = (
        f"Risk {risk}. Focus on {bull_line} vs {bear_line}. "
        "Context only — not trading advice."
    )
    _ = now
    return "\n".join([
        "🚨 AI Market Brief",
        "",
        "Main Narrative",
        str(main),
        "",
        "Bullish Assets",
        bull_line,
        "",
        "Bearish Assets",
        bear_line,
        "",
        "Macro",
        (macro[0] if macro else "—"),
        "",
        "Polymarket",
        (poly[0] if poly else "—"),
        "",
        "Risk",
        str(risk),
        "",
        "AI Conclusion",
        conclusion,
        "",
    ])


def write_narrative_reports_s42(
    *,
    assets: list[dict[str, Any]],
    briefs: list[dict[str, Any]],
    now: int,
    reports_dir: Path | None = None,
) -> dict[str, str]:
    out_dir = reports_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    claude_path = out_dir / "claude_market_context.md"
    tg_path = out_dir / "telegram_brief.md"
    claude_path.write_text(
        render_claude_market_context(assets=assets, briefs=briefs, now=now),
        encoding="utf-8",
    )
    tg_path.write_text(
        render_telegram_brief(assets=assets, briefs=briefs, now=now),
        encoding="utf-8",
    )
    return {
        "claude_market_context": str(claude_path),
        "telegram_brief": str(tg_path),
    }
