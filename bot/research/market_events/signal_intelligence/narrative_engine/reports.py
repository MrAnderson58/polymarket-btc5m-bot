"""Claude + Telegram markdown reports for Narrative Engine (S42/S43)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR

REPORTS_DIR = BASE_DIR / "reports"

_EVENT_EMOJI = {
    "ETF": "🔥",
    "Fed": "🏦",
    "Hack": "🚨",
    "Whales": "🐋",
    "Macro": "🌐",
    "Regulation": "⚖️",
    "DeFi": "💱",
    "Layer2": "⛓️",
}


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


def _event_emoji(narrative: str) -> str:
    for key, emoji in _EVENT_EMOJI.items():
        if key.lower() in (narrative or "").lower():
            return emoji
    return "📌"


def _assets_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive bullish/bearish asset rows from intel events when DB assets are empty."""
    by_sym: dict[str, dict[str, Any]] = {}
    for ev in events:
        syms = [str(s).upper() for s in (ev.get("symbols") or []) if s] or ["BTC"]
        sent = float(ev.get("sentiment") or 0)
        imp = float(ev.get("importance") or 0.3)
        title = str(ev.get("title") or "")[:160]
        for sym in syms:
            row = by_sym.setdefault(
                sym,
                {
                    "symbol": sym,
                    "news_count": 0,
                    "bullish_score": 0.0,
                    "bearish_score": 0.0,
                    "importance": 0.0,
                    "confidence": 0.0,
                    "risk_level": "LOW",
                    "summary": "",
                    "narrative": str(ev.get("narrative") or "General"),
                },
            )
            row["news_count"] = int(row["news_count"]) + 1
            if sent >= 0:
                row["bullish_score"] = float(row["bullish_score"]) + max(0.15, sent)
            else:
                row["bearish_score"] = float(row["bearish_score"]) + max(0.15, -sent)
            row["importance"] = max(float(row["importance"]), imp)
            row["confidence"] = max(
                float(row["confidence"]), float(ev.get("confidence") or 0),
            )
            if not row["summary"]:
                row["summary"] = title
            if float(row["importance"]) >= 0.7 or int(row["news_count"]) >= 4:
                row["risk_level"] = "HIGH"
            elif float(row["importance"]) >= 0.4 or int(row["news_count"]) >= 2:
                row["risk_level"] = "MEDIUM"
    return list(by_sym.values())


def _bullets_assets(rows: list[dict[str, Any]], *, empty: str = "—") -> str:
    if not rows:
        return empty
    lines = []
    for r in rows:
        lines.append(
            f"- **{r.get('symbol')}** "
            f"(events={r.get('news_count')}, conf={r.get('confidence')}, "
            f"risk={r.get('risk_level')}): {str(r.get('summary') or '')[:180]}"
        )
    return "\n".join(lines)


def _render_top_events(events: list[dict[str, Any]], *, limit: int = 8) -> str:
    if not events:
        return "—"
    ranked = sorted(
        events,
        key=lambda e: (
            float(e.get("freshness") or 0)
            * float(e.get("confidence") or 0)
            * (0.5 + float(e.get("importance") or 0))
        ),
        reverse=True,
    )[:limit]
    blocks: list[str] = []
    for ev in ranked:
        emoji = _event_emoji(str(ev.get("narrative") or ""))
        title = str(ev.get("title") or "Untitled")
        sources = int(ev.get("source_count") or len(ev.get("sources") or []))
        conf = float(ev.get("confidence") or 0)
        syms = ev.get("symbols") or []
        narr = ev.get("narrative") or "General"
        blocks.append(
            "\n".join([
                f"{emoji} **{title}**",
                f"Sources: {sources}",
                f"Confidence: {conf:.2f}",
                "Assets",
                ", ".join(str(s) for s in syms) if syms else "Macro",
                "Narrative",
                str(narr),
            ])
        )
    return "\n\n------------------------------------------------\n\n".join(blocks)


def _source_sections() -> tuple[str, str, str]:
    """Build Source Distribution / Top Sources / Source Health markdown blocks."""
    try:
        from bot.research.market_events.signal_intelligence.multi_source.health import (
            fetch_source_health,
        )
        rows = fetch_source_health()
    except Exception:
        rows = []
    if not rows:
        empty = "—"
        return empty, empty, empty

    by_type: dict[str, int] = {}
    ok_count = 0
    err_count = 0
    for r in rows:
        t = str(r.get("source_type") or "unknown")
        by_type[t] = by_type.get(t, 0) + 1
        if str(r.get("status") or "") == "ok":
            ok_count += 1
        elif str(r.get("status") or "") == "error":
            err_count += 1

    dist = "\n".join(f"- **{k}**: {v} sources" for k, v in sorted(by_type.items())) or "—"
    top = sorted(
        rows,
        key=lambda r: (0 if r.get("status") == "ok" else 1, -(r.get("items") or 0)),
    )[:8]
    top_md = "\n".join(
        f"- {r.get('source_name')} ({r.get('source_type')}) "
        f"status={r.get('status')} items={r.get('items') or 0}"
        for r in top
    ) or "—"
    health_lines = [
        f"- {r.get('source_name')} | {r.get('source_type')} | "
        f"{r.get('status')} | latency={r.get('latency_ms')}ms | "
        f"err={(r.get('error') or '—')[:60]}"
        for r in rows[:20]
    ]
    health = "\n".join(health_lines) or "—"
    _ = (ok_count, err_count)
    return dist, top_md, health


def render_claude_market_context(
    *,
    assets: list[dict[str, Any]],
    briefs: list[dict[str, Any]],
    now: int,
    events: list[dict[str, Any]] | None = None,
) -> str:
    events = events or []
    active = [a for a in assets if int(a.get("news_count") or 0) > 0]
    # If asset rows are empty but events exist, synthesize scores from events.
    if not active and events:
        active = _assets_from_events(events)
    top_bullish = sorted(
        active,
        key=lambda a: float(a.get("bullish_score") or 0) * float(a.get("importance") or 0.1),
        reverse=True,
    )[:5]
    top_bearish = sorted(
        active,
        key=lambda a: float(a.get("bearish_score") or 0) * float(a.get("importance") or 0.1),
        reverse=True,
    )[:5]

    brief = briefs[0] if briefs else {}
    global_narrative = (
        brief.get("global_narrative")
        or (events[0]["title"] if events else None)
        or (
            top_bullish[0]["summary"].split("\n")[0]
            if top_bullish
            else "Quiet event flow across the watchlist."
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
    dist, top_sources, health = _source_sections()

    dt = datetime.fromtimestamp(now).isoformat(timespec="seconds")
    lines = [
        "# MARKET CONTEXT",
        "",
        f"_Generated: {dt}_",
        "",
        "## Global Narrative",
        str(global_narrative),
        "",
        "## Top Events",
        _render_top_events(events),
        "",
        "## Source Distribution",
        dist,
        "",
        "## Top Sources",
        top_sources,
        "",
        "## Source Health",
        health,
        "",
        "## Top Bullish Assets",
        _bullets_assets(top_bullish),
        "",
        "## Top Bearish Assets",
        _bullets_assets(top_bearish),
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
            f"- event_count: {a['news_count']}",
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
        "Do not invent prices or trades. Prefer Top Events over raw headlines."
    )
    return "\n".join(lines)


def render_telegram_brief(
    *,
    assets: list[dict[str, Any]],
    briefs: list[dict[str, Any]],
    now: int,
    events: list[dict[str, Any]] | None = None,
) -> str:
    events = events or []
    active = [a for a in assets if int(a.get("news_count") or 0) > 0]
    if not active and events:
        active = _assets_from_events(events)
    top_bullish = sorted(
        active,
        key=lambda a: float(a.get("bullish_score") or 0) * float(a.get("importance") or 0.1),
        reverse=True,
    )[:3]
    top_bearish = sorted(
        active,
        key=lambda a: float(a.get("bearish_score") or 0) * float(a.get("importance") or 0.1),
        reverse=True,
    )[:3]
    brief = briefs[0] if briefs else {}
    main = brief.get("global_narrative") or (
        events[0]["title"] if events else None
    ) or (
        active[0].get("narrative") if active else "General / quiet tape"
    )
    if isinstance(main, str) and len(main) > 220:
        main = main[:217] + "..."
    risk = brief.get("risk_level") or (
        "HIGH" if any(a.get("risk_level") == "HIGH" for a in active) else "LOW"
    )
    macro = _parse_json_list(brief.get("macro_events_json"))
    poly = _parse_json_list(brief.get("polymarket_json"))
    bull_line = ", ".join(a["symbol"] for a in top_bullish) or "—"
    bear_line = ", ".join(a["symbol"] for a in top_bearish) or "—"

    top_ev_lines: list[str] = []
    ranked = sorted(
        events,
        key=lambda e: float(e.get("confidence") or 0) * float(e.get("freshness") or 0.5),
        reverse=True,
    )[:3]
    for ev in ranked:
        emoji = _event_emoji(str(ev.get("narrative") or ""))
        top_ev_lines.append(
            f"{emoji} {ev.get('title')} "
            f"(src={ev.get('source_count')}, conf={float(ev.get('confidence') or 0):.2f})"
        )
    if not top_ev_lines:
        top_ev_lines = ["—"]

    conclusion = (
        f"Risk {risk}. Focus on {bull_line} vs {bear_line}. "
        "Context only — not trading advice."
    )
    _ = now
    return "\n".join([
        "🚨 AI Market Brief",
        "",
        "Top Events",
        *top_ev_lines,
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
    events: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    out_dir = reports_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    claude_path = out_dir / "claude_market_context.md"
    tg_path = out_dir / "telegram_brief.md"
    claude_path.write_text(
        render_claude_market_context(
            assets=assets, briefs=briefs, events=events or [], now=now,
        ),
        encoding="utf-8",
    )
    tg_path.write_text(
        render_telegram_brief(
            assets=assets, briefs=briefs, events=events or [], now=now,
        ),
        encoding="utf-8",
    )
    return {
        "claude_market_context": str(claude_path),
        "telegram_brief": str(tg_path),
    }
