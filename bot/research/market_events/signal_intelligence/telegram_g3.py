"""Phase G.3 — professional unified Telegram signal card."""

from __future__ import annotations

import json
from typing import Any

from bot.research.market_events.signal_intelligence.config import G3_MODEL_VERSION


def _stars(confidence: float) -> str:
    n = min(5, max(1, int(round(confidence / 2.0))))
    return "⭐" * n


def _funding_arrow(funding: float | None) -> str:
    if funding is None:
        return "→"
    if funding < -0.0001:
        return "↓"
    if funding > 0.0001:
        return "↑"
    return "→"


def _oi_arrow(oi_rising: bool | None) -> str:
    if oi_rising is True:
        return "↑"
    if oi_rising is False:
        return "↓"
    return "→"


def format_professional_telegram_g3(
    *,
    symbol: str,
    direction: str,
    confidence: float,
    probability: float,
    market_score: float,
    liquidity_state: str,
    trend_summary: str,
    funding: float | None,
    oi_rising: bool | None,
    btc_context: str,
    reasons: list[str],
    trade_plan: dict[str, Any],
    claude_summary: str | None,
    historical: list[dict[str, Any]],
    signal_uuid: str,
    paper_mode: bool = True,
    trend_coverage_pct: float | None = None,
) -> str:
    side = "LONG" if direction.upper() in ("UP", "LONG") else "SHORT"
    pair = f"{symbol}USDT" if not symbol.endswith("USDT") else symbol

    tv_symbol = f"BINANCE:{pair}.P"
    tradingview = f"https://www.tradingview.com/chart/?symbol={tv_symbol}"
    binance = f"https://www.binance.com/en/futures/{pair}"
    coinglass = f"https://www.coinglass.com/tv/Binance_{pair}"

    lines = [
        f"🚨 {pair} {side}",
        "",
        _stars(confidence),
        "",
        "Confidence",
        f"{confidence:.1f} / 10",
        "",
    ]
    if trend_coverage_pct is not None:
        lines.extend([
            "Coverage",
            f"{trend_coverage_pct:.0f}%",
            "",
        ])
        if trend_coverage_pct < 80.0:
            lines.extend([
                "⚠ История ещё накапливается.",
                "",
            ])

    lines.extend([
        "Probability",
        f"{probability * 100:.0f}%",
        "",
        "Market Score",
        f"{market_score:.0f}",
        "",
        "Liquidity",
        liquidity_state,
        "",
        "Trend",
        trend_summary or "—",
        "",
        "Funding",
        _funding_arrow(funding),
        "",
        "OI",
        _oi_arrow(oi_rising),
        "",
        "BTC",
        btc_context or "Neutral",
        "",
        "Reason",
    ])
    for r in reasons[:6]:
        lines.append(f"• {r}")

    lines.extend([
        "",
        "Trade Plan",
        "",
        "Entry",
        str(trade_plan.get("entry", "—")),
        "",
        "SL",
        str(trade_plan.get("sl", "—")),
        "",
        "TP1",
        str(trade_plan.get("tp1", "—")),
        "",
        "TP2",
        str(trade_plan.get("tp2", "—")),
        "",
        "TP3",
        str(trade_plan.get("tp3", "—")),
        "",
        "RR",
        str(trade_plan.get("risk_reward", "—")),
        "",
        "Position Size",
        f"{trade_plan.get('position_size_pct', '—')}%",
    ])

    if claude_summary:
        lines.extend(["", "Claude Research", claude_summary.strip()])

    if historical:
        lines.extend(["", "Historical examples"])
        rev_rates = [float(h.get("reversal_rate", 0)) for h in historical if h.get("reversal_rate")]
        avg_rev = sum(rev_rates) / len(rev_rates) if rev_rates else 0.0
        for i, h in enumerate(historical[:3], 1):
            lines.append(
                f"{i}. {h.get('symbol', symbol)} {h.get('pattern', 'setup')} "
                f"→ {float(h.get('reversal_rate', 0)) * 100:.0f}% reversal",
            )
        if avg_rev:
            lines.extend(["", f"Average reversal", f"{avg_rev * 100:.0f}%"])

    lines.extend([
        "",
        "Paper Mode" if paper_mode else "Live Mode",
        "",
        "Signal ID",
        signal_uuid,
        "",
        f"Model {G3_MODEL_VERSION}",
    ])
    lines.extend([
        "",
        "Charts",
        "",
        "TradingView",
        tradingview,
        "",
        "Binance Futures",
        binance,
        "",
        "Coinglass",
        coinglass,
    ])
    return "\n".join(lines)


def format_tp_hit_g3(*, level: str, symbol: str, signal_uuid: str) -> str:
    return f"✅ {level} HIT\n\n{symbol}USDT\nSignal ID\n{signal_uuid}"


def format_result_g3(
    *,
    symbol: str,
    signal_uuid: str,
    pnl_pct: float,
    risk_reward: float,
    holding_seconds: int,
    max_drawdown_pct: float,
    max_profit_pct: float,
    model_version: str = G3_MODEL_VERSION,
) -> str:
    hours = holding_seconds // 3600
    mins = (holding_seconds % 3600) // 60
    hold = f"{hours}h {mins}m" if hours else f"{mins}m"
    return "\n".join([
        "📊 RESULT",
        "",
        f"{symbol}USDT",
        "",
        "PnL",
        f"{pnl_pct:+.2f}%",
        "",
        "RR",
        f"{risk_reward:.2f}",
        "",
        "Holding time",
        hold,
        "",
        "Drawdown",
        f"{max_drawdown_pct:.2f}%",
        "",
        "Maximum profit",
        f"{max_profit_pct:.2f}%",
        "",
        "Author",
        "G3 Signal Engine",
        "",
        "Model version",
        model_version,
        "",
        "Signal ID",
        signal_uuid,
    ])
