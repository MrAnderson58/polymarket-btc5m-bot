"""S49 — trader-facing report + compact news (signal card, not full analytics)."""

from __future__ import annotations

from typing import Any

from bot.research.ai_analyst.paper_trading.signal_builder import build_signal_from_context
from bot.research.ai_analyst.paper_trading.signals import TradingSignal
from bot.research.ai_analyst.telegram_formatter import (
    escape,
    format_separator,
    section_header,
    truncate_telegram,
)

# Soft cap: trader card should stay scannable (~30–40 lines).
_TRADER_MAX_LINES = 42
_NEWS_LIMIT = 8
_TITLE_MAX = 72


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
        # Unknown direction but elevated — treat as watch
        return "🟡"
    return "🟡"


def shorten_news_title(title: str, *, limit: int = _TITLE_MAX) -> str:
    text = " ".join(str(title or "").split())
    if not text:
        return "—"
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


def format_top_news_compact(
    events: list[dict[str, Any]] | None,
    *,
    limit: int = _NEWS_LIMIT,
) -> str:
    rows = [e for e in (events or []) if isinstance(e, dict)]
    lines = [section_header("Top News", "📰"), ""]
    if not rows:
        lines.append(escape("(no headlines)"))
        return "\n".join(lines)
    for ev in rows[:limit]:
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


def _market_score(ctx: dict[str, Any], signal: TradingSignal | None, hist: dict[str, Any] | None) -> str:
    if hist and hist.get("score") is not None:
        return str(hist.get("score"))
    quality = ctx.get("analysis_quality") or {}
    if quality.get("confidence") is not None:
        return str(quality.get("confidence"))
    if signal is not None:
        return str(round(signal.confidence, 1))
    if ctx.get("context_completeness") is not None:
        return str(ctx.get("context_completeness"))
    return "—"


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


def format_trader_report_html(
    ctx: dict[str, Any],
    *,
    signal: TradingSignal | None = None,
    history_row: dict[str, Any] | None = None,
) -> str:
    """
    Compact signal-centric /report for Telegram traders.
    Full AI analytics stay on disk / in DB — not rendered here.
    """
    if signal is None:
        signal, history_row = resolve_trader_signal(ctx, history_row=history_row)

    events = (ctx.get("intelligence") or {}).get("top_events") or []
    lines: list[str] = []

    if signal is None:
        lines.extend([
            section_header("Signal", "📡"),
            "",
            escape("No actionable BTC signal yet."),
            "",
            format_separator(),
            "",
            f"<b>Market Score</b>  {escape(_market_score(ctx, None, history_row))}",
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
            "• " + escape("Insufficient bias/price context for a paper setup"),
        ])
        return truncate_telegram("\n".join(lines))

    sym = signal.symbol
    direction = signal.direction
    lines.extend([
        f"<b>{escape(sym)} {escape(direction)}</b>",
        "",
        f"<b>Confidence</b>  {escape(str(round(float(signal.confidence), 1)))}",
        f"<b>Entry</b>  {escape(_fmt_px(signal.entry_low))} – {escape(_fmt_px(signal.entry_high))}",
        f"<b>Stop</b>  {escape(_fmt_px(signal.stop_loss))}",
        f"<b>TP1</b>  {escape(_fmt_px(signal.tp1))}",
        f"<b>TP2</b>  {escape(_fmt_px(signal.tp2))}",
        f"<b>TP3</b>  {escape(_fmt_px(signal.tp3))}",
        f"<b>Risk</b>  {escape(_fmt_pct(signal.risk_pct))}",
        "",
        format_separator(),
        "",
        f"<b>Market Score</b>  {escape(_market_score(ctx, signal, history_row))}",
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
        f"<b>{escape(direction)}</b>",
        "",
        "<b>Reasons</b>",
    ])
    reasons = list(signal.reasons)[:6]
    if not reasons and history_row and history_row.get("reasoning"):
        reasons = [str(history_row["reasoning"])[:120]]
    if not reasons:
        reasons = ["See internal AI context (debug mode)"]
    for r in reasons:
        lines.append(f"• {escape(str(r)[:100])}")

    # Soft line budget
    if len(lines) > _TRADER_MAX_LINES:
        lines = lines[:_TRADER_MAX_LINES]
        lines.append(escape("…"))
    return truncate_telegram("\n".join(lines))


def format_doctor_telegram_html(report_text: str) -> str:
    body = escape(report_text or "")
    return truncate_telegram(
        section_header("Platform Doctor", "⚙") + "\n\n<pre>" + body + "</pre>"
    )
