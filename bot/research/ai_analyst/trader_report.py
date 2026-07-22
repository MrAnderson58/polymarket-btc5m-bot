"""S49/S52 — trader-facing report + compact news (signal card, not full analytics)."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from bot.research.ai_analyst.paper_trading.signal_builder import build_signal_from_context
from bot.research.ai_analyst.paper_trading.signals import TradingSignal
from bot.research.ai_analyst.signal_consistency.reasons import (
    aggregate_and_rank_reasons,
    sanitize_reasons,
)
from bot.research.ai_analyst.telegram_formatter import (
    escape,
    format_separator,
    section_header,
    truncate_telegram,
)

# Soft cap: trader card should stay scannable (~30–40 lines).
_TRADER_MAX_LINES = 42
_NEWS_LIMIT = 8
_TITLE_MAX = 64
_NEWS_SIM_THRESHOLD = 0.82


def news_polarity_icon(event: dict[str, Any]) -> str:
    """Map event polarity / score → traffic-light icon."""
    raw = (
        event.get("polarity")
        or event.get("sentiment_label")
        or event.get("tone")
        or ""
    )
    label = str(raw).strip().lower()
    if label in {"positive", "bullish", "pos", "green", "+"}:
        return "🟢"
    if label in {"negative", "bearish", "neg", "red", "-"}:
        return "🔴"
    if label in {"neutral", "mixed", "upcoming", "watch", "yellow"}:
        return "🟡"

    score = event.get("net_score")
    if score is None:
        score = event.get("sentiment")
    if score is None:
        score = event.get("impact_score")
    try:
        val = float(score)
    except (TypeError, ValueError):
        val = None
    if val is not None:
        if val >= 0.25:
            return "🟢"
        if val <= -0.25:
            return "🔴"
        return "🟡"

    impact = str(event.get("impact") or event.get("importance") or "").lower()
    if any(k in impact for k in ("high", "critical", "extreme")):
        return "🟡"
    return "🟡"


def polish_headline(title: str) -> str:
    """
    Fix redundant / broken titles before display.
    e.g. 'Polymarket War: war' → 'Polymarket: War'
    """
    text = " ".join(str(title or "").split()).strip()
    if not text:
        return "—"

    # 'Polymarket War: war' / 'Foo Bar: bar'
    m = re.match(r"^(.+?)\s+(\w+)\s*:\s*\2\s*$", text, flags=re.IGNORECASE)
    if m:
        head = m.group(1).strip()
        topic = m.group(2).strip()
        if head.lower() in {"polymarket", "poly"}:
            return f"Polymarket: {topic[:1].upper()}{topic[1:].lower()}"
        return f"{head}: {topic[:1].upper()}{topic[1:].lower()}"

    # 'War: war'
    m = re.match(r"^(\w+)\s*:\s*\1\s*$", text, flags=re.IGNORECASE)
    if m:
        topic = m.group(1)
        return f"{topic[:1].upper()}{topic[1:].lower()}"

    # Collapse repeated words: 'ETF ETF inflows'
    text = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", text, flags=re.IGNORECASE)
    return text


def shorten_news_title(title: str, *, limit: int = _TITLE_MAX) -> str:
    text = polish_headline(title)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def format_news_headline(event: dict[str, Any]) -> str:
    """Single line: icon + short title. No body / why_it_matters."""
    icon = news_polarity_icon(event)
    title = shorten_news_title(
        str(event.get("title") or event.get("headline") or event.get("summary") or "—"),
    )
    return f"{icon} {escape(title)}"


def _etf_flow_key(title: str) -> str | None:
    """Normalize ETF flow magnitude for duplicate collapse."""
    low = title.lower()
    if "etf" not in low and "netflow" not in low:
        return None
    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*(?:m|million)?", low)
    if not m:
        return "etf"
    try:
        return f"etf:{abs(float(m.group(1))):.0f}"
    except ValueError:
        return "etf"


def merge_similar_news(
    events: list[dict[str, Any]] | None,
    *,
    limit: int = _NEWS_LIMIT,
) -> list[dict[str, Any]]:
    """Collapse near-duplicate headlines (ETF repeats, same story reworded)."""
    rows = [e for e in (events or []) if isinstance(e, dict)]
    kept: list[dict[str, Any]] = []
    titles: list[str] = []
    etf_keys: set[str] = set()
    for ev in rows:
        raw = str(ev.get("title") or ev.get("headline") or "")
        title = polish_headline(raw).lower()
        if not title or title == "—":
            continue
        etf_key = _etf_flow_key(title)
        if etf_key and etf_key in etf_keys:
            continue
        dup = False
        for prev in titles:
            if title == prev or SequenceMatcher(None, title, prev).ratio() >= _NEWS_SIM_THRESHOLD:
                dup = True
                break
            if "etf" in title and "etf" in prev and (
                SequenceMatcher(None, title, prev).ratio() >= 0.45
            ):
                dup = True
                break
        if dup:
            continue
        kept.append(ev)
        titles.append(title)
        if etf_key:
            etf_keys.add(etf_key)
        if len(kept) >= limit:
            break
    return kept


def format_top_news_compact(
    events: list[dict[str, Any]] | None,
    *,
    limit: int = _NEWS_LIMIT,
) -> str:
    rows = merge_similar_news(events, limit=limit)
    lines = [section_header("Top News", "📰"), ""]
    if not rows:
        lines.append(escape("(no headlines)"))
        return "\n".join(lines)
    for ev in rows:
        lines.append(format_news_headline(ev))
    return "\n".join(lines)


def _fmt_px(v: Any) -> str:
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v or "—")


def _fmt_pct(v: Any) -> str:
    try:
        return f"{float(v):.1f}%"
    except (TypeError, ValueError):
        return str(v or "—")


def _macro_line(ctx: dict[str, Any]) -> str:
    macro = ctx.get("macro") or {}
    bits: list[str] = []
    dxy = macro.get("dxy")
    if isinstance(dxy, dict):
        ch = dxy.get("change_pct") or dxy.get("change_1d_pct")
        px = dxy.get("price") or dxy.get("last")
        if ch is not None:
            bits.append(f"DXY {_fmt_pct(ch)}")
        elif px is not None:
            bits.append(f"DXY {_fmt_px(px)}")
    elif dxy is not None:
        bits.append(f"DXY {dxy}")

    us10 = macro.get("us10y")
    if isinstance(us10, dict):
        px = us10.get("yield") or us10.get("price") or us10.get("last")
        if px is not None:
            bits.append(f"10Y {px}")
    elif us10 is not None:
        bits.append(f"10Y {us10}")

    vix = ctx.get("vix") or {}
    if isinstance(vix, dict) and vix.get("price") is not None:
        bits.append(f"VIX {_fmt_px(vix.get('price'))}")

    return " · ".join(bits) if bits else "—"


def _flow_line(ctx: dict[str, Any]) -> str:
    etf = ((ctx.get("etf") or {}).get("btc_etf") or {})
    nf1 = etf.get("netflow_1d")
    nf5 = etf.get("netflow_5d")
    trend = etf.get("trend")
    bits: list[str] = []
    if nf1 is not None:
        try:
            bits.append(f"ETF 1d {float(nf1):+.0f}M")
        except (TypeError, ValueError):
            bits.append(f"ETF 1d {nf1}")
    if nf5 is not None:
        try:
            bits.append(f"5d {float(nf5):+.0f}M")
        except (TypeError, ValueError):
            bits.append(f"5d {nf5}")
    if trend:
        bits.append(str(trend))
    fund = ctx.get("funding") or {}
    if fund.get("current") is not None:
        bits.append(f"funding {fund.get('current')}")
    return " · ".join(bits) if bits else "—"


def _market_conditions_score(
    ctx: dict[str, Any],
    hist: dict[str, Any] | None,
) -> float | None:
    quality = ctx.get("analysis_quality") or {}
    if quality.get("confidence") is not None:
        try:
            return float(quality["confidence"])
        except (TypeError, ValueError):
            pass
    if ctx.get("context_completeness") is not None:
        try:
            return float(ctx["context_completeness"])
        except (TypeError, ValueError):
            pass
    if hist and hist.get("score") is not None:
        try:
            return float(hist["score"])
        except (TypeError, ValueError):
            pass
    return None


def _confidence_gap_note(market: float | None, trade: float | None) -> str | None:
    if market is None or trade is None:
        return None
    if trade + 5 < market:
        gap = market - trade
        return (
            f"Trade confidence {trade:.0f} is below market conditions {market:.0f} "
            f"(−{gap:.0f}: calibrated on history / incomplete setup evidence)."
        )
    return None


def resolve_trader_signal(
    ctx: dict[str, Any],
    *,
    history_row: dict[str, Any] | None = None,
) -> tuple[TradingSignal | None, dict[str, Any] | None]:
    """Prefer latest S48 history row, else rule-based context signal."""
    if history_row:
        try:
            sig = TradingSignal(
                symbol=str(history_row.get("market") or "BTC"),
                direction=str(history_row.get("direction") or "LONG"),
                entry_low=float(history_row["entry_low"]),
                entry_high=float(history_row["entry_high"]),
                stop_loss=float(history_row["stop_loss"]),
                tp1=float(history_row["tp1"]),
                tp2=float(history_row["tp2"]),
                tp3=float(history_row["tp3"]),
                risk_pct=float(history_row.get("risk_pct") or 1.0),
                confidence=float(history_row.get("confidence") or 50),
                reasons=list(history_row.get("reasons") or []),
                strategy=str(history_row.get("strategy") or "ai_default"),
                signal_id=str(history_row.get("signal_id") or ""),
                status=str(history_row.get("status") or "PENDING"),
            )
            return sig, history_row
        except Exception:
            pass
    return build_signal_from_context(ctx), None


def load_latest_signal_row() -> dict[str, Any] | None:
    """S51 — latest signal from SignalTruthRepository (same book as /signals)."""
    try:
        from bot.research.ai_analyst.signal_consistency.repository import get_repository
        return get_repository().latest_signal()
    except Exception:
        return None


def load_open_trade_count() -> int:
    try:
        from bot.research.ai_analyst.signal_consistency.repository import get_repository
        return get_repository().count_open_trades()
    except Exception:
        return 0


def format_pending_signal_block(signal: TradingSignal) -> list[str]:
    """Show AI signal separately when there is no open paper trade."""
    reasons = aggregate_and_rank_reasons(signal.reasons, limit=4)
    lines = [
        section_header("Current AI Signal", "📡"),
        "",
        f"<b>{escape(signal.symbol)} {escape(signal.direction)}</b>  "
        f"(pending — no open trade)",
        f"Entry {escape(_fmt_px(signal.entry_low))}–{escape(_fmt_px(signal.entry_high))}  "
        f"SL {escape(_fmt_px(signal.stop_loss))}",
        f"TP {escape(_fmt_px(signal.tp1))} / {escape(_fmt_px(signal.tp2))} / "
        f"{escape(_fmt_px(signal.tp3))}",
        f"Trade confidence {escape(str(round(float(signal.confidence), 1)))}",
    ]
    if reasons:
        lines.append("Reasons: " + escape(" · ".join(reasons[:3])))
    return lines


def format_trader_report_html(
    ctx: dict[str, Any],
    *,
    signal: TradingSignal | None = None,
    history_row: dict[str, Any] | None = None,
    open_count: int | None = None,
) -> str:
    """
    Compact signal-centric /report for Telegram traders.
    Full AI analytics stay on disk / in DB — not rendered here.
    """
    if signal is None:
        signal, history_row = resolve_trader_signal(ctx, history_row=history_row)

    events = (ctx.get("intelligence") or {}).get("top_events") or []
    market = _market_conditions_score(ctx, history_row)
    market_s = f"{market:.0f}" if market is not None else "—"
    if open_count is None:
        open_count = load_open_trade_count()

    lines: list[str] = []

    if signal is None:
        lines.extend([
            section_header("Signal", "📡"),
            "",
            escape("No actionable BTC signal yet."),
            "",
            format_separator(),
            "",
            section_header("Market Conditions", "🌡"),
            "",
            f"<b>Score</b>  {escape(market_s)}",
            f"<b>Macro</b>  {escape(_macro_line(ctx))}",
            f"<b>Flow</b>  {escape(_flow_line(ctx))}",
            "",
            format_separator(),
            "",
            format_top_news_compact(events),
            "",
            format_separator(),
            "",
            section_header("AI Verdict", "🤖"),
            "",
            escape("WAIT"),
            "",
            "<b>Reasons</b>",
            "• " + escape("No concrete ETF/funding/OI setup yet"),
        ])
        return truncate_telegram("\n".join(lines))

    # No open trade → show pending AI signal card first (S52)
    if int(open_count or 0) <= 0 and str(signal.status or "").upper() in {
        "PENDING", "TRIGGERED", "",
    }:
        lines.extend(format_pending_signal_block(signal))
        lines.extend(["", format_separator(), ""])

    trade_conf = float(signal.confidence)
    gap = _confidence_gap_note(market, trade_conf)

    lines.extend([
        f"<b>{escape(signal.symbol)} {escape(signal.direction)}</b>",
        "",
        f"<b>Entry</b>  {escape(_fmt_px(signal.entry_low))} – {escape(_fmt_px(signal.entry_high))}",
        f"<b>Stop</b>  {escape(_fmt_px(signal.stop_loss))}",
        f"<b>TP1</b>  {escape(_fmt_px(signal.tp1))}",
        f"<b>TP2</b>  {escape(_fmt_px(signal.tp2))}",
        f"<b>TP3</b>  {escape(_fmt_px(signal.tp3))}",
        f"<b>Risk</b>  {escape(_fmt_pct(signal.risk_pct))}",
        "",
        format_separator(),
        "",
        section_header("Market Conditions", "🌡"),
        "",
        f"<b>Score</b>  {escape(market_s)}",
        f"<b>Macro</b>  {escape(_macro_line(ctx))}",
        f"<b>Flow</b>  {escape(_flow_line(ctx))}",
        "",
        section_header("Trade Confidence", "🎯"),
        "",
        f"<b>Score</b>  {escape(str(round(trade_conf, 1)))}",
    ])
    if gap:
        lines.append(escape(gap))
    lines.extend([
        "",
        format_separator(),
        "",
        format_top_news_compact(events),
        "",
        format_separator(),
        "",
        section_header("AI Verdict", "🤖"),
        "",
        f"<b>{escape(signal.direction)}</b>",
        "",
        "<b>Reasons</b>",
    ])
    reasons = aggregate_and_rank_reasons(list(signal.reasons), limit=6)
    if not reasons and history_row and history_row.get("reasoning"):
        reasons = sanitize_reasons([str(history_row["reasoning"])[:120]], limit=3)
    if not reasons:
        reasons = aggregate_and_rank_reasons(
            [_flow_line(ctx), _macro_line(ctx)],
            limit=3,
        )
    if not reasons:
        reasons = ["Funding / flow evidence incomplete"]
    for r in reasons:
        lines.append(f"• {escape(str(r)[:100])}")

    if len(lines) > _TRADER_MAX_LINES:
        lines = lines[:_TRADER_MAX_LINES]
        lines.append(escape("…"))
    return truncate_telegram("\n".join(lines))


def format_doctor_telegram_html(report_text: str) -> str:
    body = escape(report_text or "")
    return truncate_telegram(
        section_header("Platform Doctor", "⚙") + "\n\n<pre>" + body + "</pre>"
    )
