"""S45 — Intelligence Quality Engine (net_score, impact, macro/poly intel, reports)."""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from bot.research.market_events.signal_intelligence.narrative_engine.binding import (
    classify_source_type,
)

NET_SCORE_THRESHOLD = 0.12


def compute_net_score(
    bullish: float,
    bearish: float,
    *,
    neutral: float | None = None,
) -> float:
    """net_score = bullish - bearish (neutral does not flip polarity)."""
    _ = neutral
    return round(float(bullish) - float(bearish), 3)


def select_top_by_net_score(
    assets: list[dict[str, Any]],
    *,
    threshold: float = NET_SCORE_THRESHOLD,
    limit: int = 5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Top Bullish = max net_score; Top Bearish = min net_score.
    An asset cannot appear in both lists. |net_score| < threshold → excluded.
    """
    scored: list[dict[str, Any]] = []
    for a in assets:
        if int(a.get("news_count") or 0) <= 0 and not a.get("force_include"):
            continue
        row = dict(a)
        if "net_score" not in row or row.get("net_score") is None:
            row["net_score"] = compute_net_score(
                float(row.get("bullish_score") or 0),
                float(row.get("bearish_score") or 0),
                neutral=float(row.get("neutral_score") or 0),
            )
        scored.append(row)

    bullish = sorted(
        [a for a in scored if float(a.get("net_score") or 0) >= float(threshold)],
        key=lambda a: float(a.get("net_score") or 0),
        reverse=True,
    )[:limit]
    bull_syms = {str(a.get("symbol") or "").upper() for a in bullish}
    bearish = sorted(
        [
            a for a in scored
            if float(a.get("net_score") or 0) <= -float(threshold)
            and str(a.get("symbol") or "").upper() not in bull_syms
        ],
        key=lambda a: float(a.get("net_score") or 0),
    )[:limit]
    return bullish, bearish


def market_impact(
    *,
    importance: float,
    confidence: float,
    source_count: int,
    freshness: float | None = None,
    last_seen: int | None = None,
    now: int | None = None,
    affected_assets: list[str] | None = None,
) -> str:
    """Classify event market impact: LOW / MEDIUM / HIGH / CRITICAL."""
    now_ts = int(now if now is not None else time.time())
    fresh = freshness
    if fresh is None and last_seen:
        age_h = max(0.0, (now_ts - int(last_seen)) / 3600.0)
        fresh = max(0.0, 1.0 - age_h / 24.0)
    fresh = float(fresh if fresh is not None else 0.5)
    n_assets = len(affected_assets or [])
    score = (
        0.30 * float(importance)
        + 0.25 * float(confidence)
        + 0.15 * min(1.0, int(source_count) / 6.0)
        + 0.15 * fresh
        + 0.15 * min(1.0, n_assets / 3.0)
    )
    if score >= 0.78 or (importance >= 0.8 and source_count >= 4):
        return "CRITICAL"
    if score >= 0.58:
        return "HIGH"
    if score >= 0.38:
        return "MEDIUM"
    return "LOW"


def event_polarity(sentiment: float) -> str:
    if sentiment >= 0.12:
        return "Bullish"
    if sentiment <= -0.12:
        return "Bearish"
    return "Neutral"


def why_it_matters(ev: dict[str, Any]) -> str:
    """One-sentence analytical explanation (template AI, no trade advice)."""
    title = str(ev.get("title") or "This development").rstrip(".")
    syms = [str(s).upper() for s in (ev.get("symbols") or []) if s]
    assets = ", ".join(syms[:4]) if syms else "broader crypto risk assets"
    narr = str(ev.get("narrative") or "market").split(",")[0].strip() or "market"
    pol = event_polarity(float(ev.get("sentiment") or 0))
    impact = str(ev.get("market_impact") or market_impact(
        importance=float(ev.get("importance") or 0),
        confidence=float(ev.get("confidence") or 0),
        source_count=int(ev.get("source_count") or 0),
        freshness=float(ev.get("freshness") or 0) if ev.get("freshness") is not None else None,
        last_seen=int(ev.get("last_seen") or 0) or None,
        affected_assets=syms,
    ))
    conf = float(ev.get("confidence") or 0)
    return (
        f"{title} matters because it is a {pol.lower()} {narr} signal for {assets} "
        f"with {impact} estimated impact "
        f"(confidence {conf:.0%}, {int(ev.get('source_count') or 0)} sources)."
    )


def source_type_counts(sources: list[str] | None) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for s in sources or []:
        counts[classify_source_type(str(s))] += 1
    return dict(counts)


def format_confirmed_by(sources: list[str] | None) -> str:
    counts = source_type_counts(sources)
    order = ("rss", "telegram", "twitter", "macro", "polymarket")
    labels = {
        "rss": "RSS",
        "telegram": "Telegram",
        "twitter": "Twitter",
        "macro": "Macro",
        "polymarket": "Polymarket",
    }
    parts = []
    for key in order:
        n = int(counts.get(key) or 0)
        if n:
            parts.append(f"{labels[key]} ×{n}")
    # Include unknown leftovers
    for key, n in sorted(counts.items()):
        if key not in labels and n:
            parts.append(f"{key} ×{n}")
    return ", ".join(parts) if parts else "—"


def enrich_event_for_report(ev: dict[str, Any], *, now: int | None = None) -> dict[str, Any]:
    out = dict(ev)
    syms = [str(s).upper() for s in (out.get("symbols") or []) if s]
    out["market_impact"] = out.get("market_impact") or market_impact(
        importance=float(out.get("importance") or 0),
        confidence=float(out.get("confidence") or 0),
        source_count=int(out.get("source_count") or 0),
        freshness=float(out["freshness"]) if out.get("freshness") is not None else None,
        last_seen=int(out.get("last_seen") or 0) or None,
        now=now,
        affected_assets=syms,
    )
    out["polarity"] = out.get("polarity") or event_polarity(float(out.get("sentiment") or 0))
    out["why_it_matters"] = out.get("why_it_matters") or why_it_matters(out)
    out["source_breakdown"] = source_type_counts(out.get("sources") or [])
    out["confirmed_by"] = format_confirmed_by(out.get("sources") or [])
    return out


def build_executive_summary(
    *,
    events: list[dict[str, Any]],
    top_bullish: list[dict[str, Any]],
    top_bearish: list[dict[str, Any]],
    macro_summary: str,
    poly_summary: str,
) -> str:
    """3–5 sentences: what happened, why it matters, assets, market pricing — no trade advice."""
    sentences: list[str] = []
    if events:
        top = events[0]
        sentences.append(
            f"Lead development: {top.get('title')} "
            f"(impact {top.get('market_impact')}, "
            f"{top.get('polarity', 'Neutral').lower()})."
        )
        if top.get("why_it_matters"):
            sentences.append(str(top["why_it_matters"]))
    else:
        sentences.append(
            "Event flow is quiet across the intelligence window; "
            "no high-conviction cluster dominates the tape."
        )
    bull = ", ".join(str(a.get("symbol")) for a in top_bullish[:4]) or "none"
    bear = ", ".join(str(a.get("symbol")) for a in top_bearish[:4]) or "none"
    sentences.append(
        f"Assets under influence skew bullish on {bull} and bearish on {bear} "
        f"(ranked by net_score, mutually exclusive)."
    )
    if macro_summary and macro_summary != "—":
        sentences.append(f"Macro backdrop: {macro_summary.split(chr(10))[0][:220]}")
    if poly_summary and poly_summary != "—":
        sentences.append(f"Prediction markets: {poly_summary.split(chr(10))[0][:220]}")
    sentences.append(
        "This note is situational context only — not trading advice."
    )
    return " ".join(sentences[:5])


def load_macro_rows(conn: Any, *, since_ts: int, limit: int = 40) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT created_at, name, event_type, title, summary, value, unit
            FROM market_macro_events
            WHERE created_at >= ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (since_ts, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def load_polymarket_rows(
    conn: Any, *, since_ts: int, limit: int = 40,
) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT created_at, name, query, question, probability, condition_id
            FROM market_polymarket_signals
            WHERE created_at >= ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (since_ts, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def render_macro_intelligence(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "—"
    by_name: dict[str, dict[str, Any]] = {}
    for r in rows:
        name = str(r.get("name") or "").strip() or "macro"
        if name not in by_name:
            by_name[name] = r

    watch = ("Fed", "DXY", "US10Y", "Oil", "Gold", "CPI", "PPI", "NFP")
    lines = ["### Macro Snapshot", ""]
    for key in watch:
        hit = None
        for name, row in by_name.items():
            if key.lower() in name.lower() or key.lower() in str(row.get("title") or "").lower():
                hit = row
                break
        if hit:
            title = str(hit.get("title") or hit.get("summary") or name)[:120]
            lines.append(f"- **{key}**: {title}")
        else:
            # Still list if any row matches loosely later
            pass
    # Any remaining named macros
    for name, row in by_name.items():
        if any(k.lower() in name.lower() for k in watch):
            continue
        lines.append(f"- **{name}**: {str(row.get('title') or row.get('summary') or '')[:120]}")

    # AI-style summary
    names = {n.lower() for n in by_name}
    dollar = "strengthening" if "dxy" in names else "mixed"
    treasuries = "stable" if "us10y" in names else "unobserved"
    oil = "neutral" if "oil" in names else "unobserved"
    fed_tone = "in focus" if "fed" in names else "quiet"
    risk = "slightly bearish for risk assets" if (
        "dxy" in names or "cpi" in names or "fed" in names
    ) else "balanced for risk assets"
    lines.extend([
        "",
        "### Macro Summary",
        f"Dollar {dollar}. Treasuries {treasuries}. Oil {oil}. "
        f"Fed narrative {fed_tone}. Macro {risk}.",
    ])
    return "\n".join(lines)


def render_polymarket_intelligence(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "—"
    # Latest per name
    latest: dict[str, dict[str, Any]] = {}
    history: dict[str, list[float]] = {}
    for r in sorted(rows, key=lambda x: int(x.get("created_at") or 0)):
        name = str(r.get("name") or r.get("question") or "market")
        history.setdefault(name, [])
        if r.get("probability") is not None:
            history[name].append(float(r["probability"]))
        latest[name] = r

    scored = []
    for name, row in latest.items():
        prob = row.get("probability")
        series = history.get(name) or []
        change = (series[-1] - series[0]) if len(series) >= 2 else 0.0
        scored.append((name, row, float(prob) if prob is not None else None, change))

    high = sorted(
        [s for s in scored if s[2] is not None],
        key=lambda x: x[2] or 0,
        reverse=True,
    )[:5]
    movers = sorted(scored, key=lambda x: abs(x[3]), reverse=True)[:5]

    lines = [
        "### Highest probability markets",
        "",
    ]
    if high:
        for name, row, prob, _ch in high:
            q = str(row.get("question") or name)[:140]
            lines.append(f"- **{prob:.1%}** — {q}")
    else:
        lines.append("- (probabilities unavailable)")

    lines.extend(["", "### Probability changes / fastest movers", ""])
    any_move = False
    for name, row, prob, ch in movers:
        if abs(ch) < 1e-6:
            continue
        any_move = True
        q = str(row.get("question") or name)[:120]
        sign = "+" if ch >= 0 else ""
        lines.append(f"- {sign}{ch:.1%} — {q}")
    if not any_move:
        lines.append("- No multi-snapshot moves in the lookback window.")

    # Consensus
    probs = [s[2] for s in scored if s[2] is not None]
    if probs:
        avg = sum(probs) / len(probs)
        top_name = high[0][0] if high else "n/a"
        top_p = high[0][2] if high else 0
        biggest = movers[0] if movers else None
        consensus = (
            f"Markets imply an average watched probability near {avg:.0%}. "
            f"Highest conviction sits on {top_name} (~{top_p:.0%}). "
        )
        if biggest and abs(biggest[3]) > 0:
            consensus += (
                f"Biggest change: {biggest[0]} ({biggest[3]:+.1%}). "
            )
        consensus += "Consensus is descriptive of odds only — not a trade signal."
    else:
        consensus = (
            "Prediction markets were sampled but probabilities were sparse; "
            "treat coverage as incomplete."
        )

    lines.extend(["", "### Market consensus", "", consensus])
    lines.extend([
        "",
        "### Prediction Markets (AI)",
        "",
        consensus,
    ])
    return "\n".join(lines)


def build_ai_conclusion(
    *,
    top_bullish: list[dict[str, Any]],
    top_bearish: list[dict[str, Any]],
    events: list[dict[str, Any]],
    risk: str,
) -> str:
    bull = ", ".join(a.get("symbol") for a in top_bullish[:3]) or "—"
    bear = ", ".join(a.get("symbol") for a in top_bearish[:3]) or "—"
    lead = events[0].get("title") if events else "No dominant event"
    impact = events[0].get("market_impact") if events else "LOW"
    return (
        f"Risk posture: {risk}. Lead event ({impact}): {lead}. "
        f"Net-score leaders: bullish {bull} vs bearish {bear}. "
        "Use as context only — not trading advice."
    )
