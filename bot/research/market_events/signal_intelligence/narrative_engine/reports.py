"""Claude + Telegram markdown reports — S45 Intelligence Quality Engine."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.narrative_engine.quality import (
    build_ai_conclusion,
    build_executive_summary,
    compute_net_score,
    enrich_event_for_report,
    render_macro_intelligence,
    render_polymarket_intelligence,
    select_top_by_net_score,
)

REPORTS_DIR = BASE_DIR / "reports"

_EVENT_EMOJI = {
    "ETF": "🔥",
    "Fed": "🏦",
    "Hack": "🚨",
    "Security": "🚨",
    "Whales": "🐋",
    "Macro": "🌐",
    "Regulation": "⚖️",
    "DeFi": "💱",
    "L2": "⛓️",
    "Stablecoins": "💵",
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
    by_sym: dict[str, dict[str, Any]] = {}
    for ev in events:
        syms = [str(s).upper() for s in (ev.get("symbols") or []) if s]
        if not syms:
            continue
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
                    "neutral_score": 0.0,
                    "importance": 0.0,
                    "confidence": 0.0,
                    "risk_level": "LOW",
                    "summary": "",
                    "narrative": str(ev.get("narrative") or "") or "Uncategorized",
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
    for row in by_sym.values():
        row["net_score"] = compute_net_score(
            float(row["bullish_score"]),
            float(row["bearish_score"]),
            neutral=float(row.get("neutral_score") or 0),
        )
    return list(by_sym.values())


def _bullets_assets(rows: list[dict[str, Any]], *, empty: str = "—") -> str:
    if not rows:
        return empty
    lines = []
    for r in rows:
        lines.append(
            f"- **{r.get('symbol')}** "
            f"(net={r.get('net_score')}, events={r.get('news_count')}, "
            f"conf={r.get('confidence')}, risk={r.get('risk_level')}): "
            f"{str(r.get('summary') or '')[:180]}"
        )
    return "\n".join(lines)


def _render_top_events(
    events: list[dict[str, Any]], *, limit: int = 8, now: int | None = None,
) -> str:
    if not events:
        return "—"
    enriched = [enrich_event_for_report(e, now=now) for e in events]
    ranked = sorted(
        enriched,
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
        syms = ev.get("symbols") or []
        blocks.append(
            "\n".join([
                f"{emoji} **{title}**",
                f"Affected assets: {', '.join(str(s) for s in syms) if syms else '—'}",
                f"Bullish/Bearish: {ev.get('polarity')}",
                f"Confidence: {float(ev.get('confidence') or 0):.2f}",
                f"Importance: {float(ev.get('importance') or 0):.2f}",
                f"Impact: {ev.get('market_impact')}",
                f"Why it matters: {ev.get('why_it_matters')}",
                "Sources",
                f"Confirmed by: {ev.get('confirmed_by')}",
                f"Raw sources ({int(ev.get('source_count') or 0)}): "
                f"{', '.join(str(s) for s in (ev.get('sources') or [])[:8]) or '—'}",
                f"Narrative: {ev.get('narrative') or 'Uncategorized'}",
            ])
        )
    return "\n\n------------------------------------------------\n\n".join(blocks)


def _source_confidence_section() -> str:
    try:
        from bot.research.market_events.signal_intelligence.multi_source.health import (
            fetch_source_health,
        )
        rows = fetch_source_health()
    except Exception:
        rows = []
    if not rows:
        return "—"
    by_type: dict[str, int] = {}
    ok = err = 0
    for r in rows:
        t = str(r.get("source_type") or "unknown")
        by_type[t] = by_type.get(t, 0) + 1
        if str(r.get("status") or "") == "ok":
            ok += 1
        elif str(r.get("status") or "") == "error":
            err += 1
    lines = [
        f"Collectors healthy: {ok}, errors: {err}.",
        "",
        "### Source Distribution",
        "\n".join(f"- **{k}**: {v} sources" for k, v in sorted(by_type.items())) or "—",
        "",
        "### Source Health",
    ]
    for r in rows[:20]:
        lines.append(
            f"- {r.get('source_name')} | {r.get('source_type')} | "
            f"{r.get('status')} | items={r.get('items') or 0}"
        )
    return "\n".join(lines)


def _section_or_dash(text: str) -> str:
    t = (text or "").strip()
    return t if t and t != "—" else "—"


def render_claude_market_context(
    *,
    assets: list[dict[str, Any]],
    briefs: list[dict[str, Any]],
    now: int,
    events: list[dict[str, Any]] | None = None,
    macro_rows: list[dict[str, Any]] | None = None,
    poly_rows: list[dict[str, Any]] | None = None,
) -> str:
    events = [enrich_event_for_report(e, now=now) for e in (events or [])]
    active = [a for a in assets if int(a.get("news_count") or 0) > 0]
    if not active and events:
        active = _assets_from_events(events)
    for a in active:
        if a.get("net_score") is None:
            a["net_score"] = compute_net_score(
                float(a.get("bullish_score") or 0),
                float(a.get("bearish_score") or 0),
                neutral=float(a.get("neutral_score") or 0),
            )
    top_bullish, top_bearish = select_top_by_net_score(active, limit=5)

    brief = briefs[0] if briefs else {}
    risk = brief.get("risk_level") or (
        "HIGH" if any(a.get("risk_level") == "HIGH" for a in active) else "MEDIUM"
        if any(a.get("risk_level") == "MEDIUM" for a in active) else "LOW"
    )

    macro_md = render_macro_intelligence(macro_rows or [])
    if macro_md == "—":
        # Fall back to brief JSON lists if present
        macro_list = _parse_json_list(brief.get("macro_events_json"))
        fed_list = _parse_json_list(brief.get("fed_json"))
        if macro_list or fed_list:
            bits = [str(x) for x in (macro_list + fed_list)[:8]]
            macro_md = "\n".join(f"- {b}" for b in bits)

    poly_md = render_polymarket_intelligence(poly_rows or [])
    if poly_md == "—":
        poly_list = _parse_json_list(brief.get("polymarket_json"))
        if poly_list:
            poly_md = "\n".join(f"- {x}" for x in poly_list[:8])

    exec_sum = build_executive_summary(
        events=events,
        top_bullish=top_bullish,
        top_bearish=top_bearish,
        macro_summary=macro_md,
        poly_summary=poly_md,
    )
    conclusion = build_ai_conclusion(
        top_bullish=top_bullish,
        top_bearish=top_bearish,
        events=events,
        risk=str(risk),
    )
    source_conf = _source_confidence_section()

    dt = datetime.fromtimestamp(now).isoformat(timespec="seconds")
    lines = [
        "# MARKET CONTEXT",
        "",
        f"_Generated: {dt}_",
        "",
        "## Executive Summary",
        exec_sum,
        "",
        "## Top Events",
        _render_top_events(events, now=now),
        "",
        "## Macro Outlook",
        _section_or_dash(macro_md),
        "",
        "## Prediction Markets",
        _section_or_dash(poly_md),
        "",
        "## Top Bullish Assets",
        _bullets_assets(top_bullish),
        "",
        "## Top Bearish Assets",
        _bullets_assets(top_bearish),
        "",
        "## Source Confidence",
        source_conf,
        "",
        "## AI Conclusion",
        conclusion,
        "",
        "## Asset Intelligence",
        "",
    ]
    for a in assets:
        lines.extend([
            f"### {a['symbol']}",
            f"- event_count: {a['news_count']}",
            f"- narrative: {a.get('narrative') or 'Uncategorized'}",
            f"- bullish/bearish/neutral/net: "
            f"{a['bullish_score']}/{a['bearish_score']}/"
            f"{a.get('neutral_score')}/{a.get('net_score')}",
            f"- importance: {a['importance']}",
            f"- confidence: {a['confidence']}",
            f"- risk_level: {a['risk_level']}",
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
    macro_rows: list[dict[str, Any]] | None = None,
    poly_rows: list[dict[str, Any]] | None = None,
) -> str:
    events = [enrich_event_for_report(e, now=now) for e in (events or [])]
    active = [a for a in assets if int(a.get("news_count") or 0) > 0]
    if not active and events:
        active = _assets_from_events(events)
    for a in active:
        if a.get("net_score") is None:
            a["net_score"] = compute_net_score(
                float(a.get("bullish_score") or 0),
                float(a.get("bearish_score") or 0),
            )
    top_bullish, top_bearish = select_top_by_net_score(active, limit=3)
    brief = briefs[0] if briefs else {}
    risk = brief.get("risk_level") or (
        "HIGH" if any(a.get("risk_level") == "HIGH" for a in active) else "LOW"
    )
    macro_md = render_macro_intelligence(macro_rows or [])
    poly_md = render_polymarket_intelligence(poly_rows or [])
    exec_sum = build_executive_summary(
        events=events,
        top_bullish=top_bullish,
        top_bearish=top_bearish,
        macro_summary=macro_md,
        poly_summary=poly_md,
    )
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
            f"[{ev.get('market_impact')}] "
            f"({ev.get('polarity')}, conf={float(ev.get('confidence') or 0):.2f})"
        )
        top_ev_lines.append(f"  → {ev.get('why_it_matters')}")
    if not top_ev_lines:
        top_ev_lines = ["—"]

    macro_one = "—"
    if macro_md and macro_md != "—":
        for line in macro_md.splitlines():
            if "Macro Summary" in line:
                continue
            if line.startswith("Dollar") or "Macro " in line:
                macro_one = line.strip()
                break
        if macro_one == "—":
            macro_one = next(
                (ln.strip() for ln in macro_md.splitlines() if ln.startswith("-")),
                "—",
            )

    poly_one = "—"
    if poly_md and poly_md != "—":
        for ln in poly_md.splitlines():
            if ln.startswith("Markets imply") or ln.startswith("- **"):
                poly_one = ln.lstrip("- ").strip()
                break

    conclusion = build_ai_conclusion(
        top_bullish=top_bullish,
        top_bearish=top_bearish,
        events=events,
        risk=str(risk),
    )
    _ = now
    return "\n".join([
        "🚨 AI Market Brief",
        "",
        "Executive Summary",
        exec_sum,
        "",
        "Top Events",
        *top_ev_lines,
        "",
        "Macro Outlook",
        macro_one,
        "",
        "Prediction Markets",
        poly_one,
        "",
        "Bullish Assets",
        bull_line,
        "",
        "Bearish Assets",
        bear_line,
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
    macro_rows: list[dict[str, Any]] | None = None,
    poly_rows: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    out_dir = reports_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    claude_path = out_dir / "claude_market_context.md"
    tg_path = out_dir / "telegram_brief.md"
    claude_path.write_text(
        render_claude_market_context(
            assets=assets,
            briefs=briefs,
            events=events or [],
            now=now,
            macro_rows=macro_rows,
            poly_rows=poly_rows,
        ),
        encoding="utf-8",
    )
    tg_path.write_text(
        render_telegram_brief(
            assets=assets,
            briefs=briefs,
            events=events or [],
            now=now,
            macro_rows=macro_rows,
            poly_rows=poly_rows,
        ),
        encoding="utf-8",
    )
    return {
        "claude_market_context": str(claude_path),
        "telegram_brief": str(tg_path),
    }
