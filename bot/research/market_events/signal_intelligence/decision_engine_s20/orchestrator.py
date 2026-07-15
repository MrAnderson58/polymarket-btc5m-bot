"""S2.0/S3.1 orchestrator — Market → News → Pattern → Decision (RO by default)."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.decision_engine_s20.decision_agent import (
    run_decision_agent_s20,
)
from bot.research.market_events.signal_intelligence.decision_engine_s20.market_agent import (
    run_market_agent_s20,
)
from bot.research.market_events.signal_intelligence.decision_engine_s20.news_agent import (
    run_news_agent_s20,
)
from bot.research.market_events.signal_intelligence.decision_engine_s20.persist import (
    persist_decision_run_s20,
)
from bot.research.market_events.signal_intelligence.decision_engine_s20.x_twitter import (
    XClientInterface,
)
from bot.research.market_events.signal_intelligence.pattern_agent_s31 import (
    format_pattern_block_for_decision_s31,
    run_pattern_agent_s31,
)


def _emoji(decision: str) -> str:
    d = (decision or "").upper()
    if d == "LONG":
        return "🟢"
    if d == "SHORT":
        return "🔴"
    return "⚪"


def format_decision_telegram_s20(
    *,
    symbol: str,
    market: dict[str, Any],
    news: dict[str, Any],
    decision: dict[str, Any],
    pattern: dict[str, Any] | None = None,
) -> str:
    sym = symbol.upper()
    dec = str(decision.get("decision") or "FLAT").upper()
    lines = [
        f"{_emoji(dec)} {sym} {dec}",
        "",
        f"Probability: {int(decision.get('probability') or 0)}%",
        f"Confidence: {decision.get('confidence')}/10",
        "",
        "Entry",
        f"{market.get('entry')}",
        "",
        "Stop",
        f"{market.get('stop')}",
        "",
        "TP1",
        f"{market.get('tp1')}",
        "",
        "TP2",
        f"{market.get('tp2')}",
        "",
        "Market Agent",
    ]
    for r in (market.get("reasons") or [])[:5]:
        lines.append(f"• {r}")
    lines.extend(["", "News Agent"])
    lines.append(
        f"Sentiment: {news.get('sentiment')} | conf {news.get('confidence')} | "
        f"importance {news.get('importance')}"
    )
    for r in (news.get("reasons") or [])[:4]:
        lines.append(f"• {r}")
    if pattern is not None:
        lines.extend(["", format_pattern_block_for_decision_s31(pattern)])
    lines.extend(["", "Decision Agent"])
    lines.append(str(decision.get("summary") or "")[:400])
    lines.extend(["", "Risk"])
    for r in (decision.get("risks") or [])[:5]:
        lines.append(f"• {r}")
    return "\n".join(lines)


def run_decision_engine_s20(
    conn: Any,
    symbol: str,
    *,
    persist: bool = False,
    allow_claude: bool = True,
    force_fallback: bool = False,
    headlines: list[dict[str, str]] | None = None,
    market_data: Any | None = None,
    x_client: XClientInterface | None = None,
    timeframe: str | int | None = "60m",
) -> dict[str, Any]:
    """Full cycle with Pattern Agent. Default persist=False — pure READ ONLY."""
    sym = symbol.upper().replace("USDT", "").strip() or "BTC"
    market = run_market_agent_s20(conn, sym, market_data=market_data)
    news = run_news_agent_s20(sym, headlines=headlines, x_client=x_client)
    pattern = run_pattern_agent_s31(
        conn,
        symbol=sym,
        direction=str(market.get("direction") or ""),
        timeframe=timeframe,
        market_snapshot=market,
    )
    decision = run_decision_agent_s20(
        market,
        news,
        pattern=pattern,
        allow_claude=allow_claude,
        force_fallback=force_fallback,
    )
    telegram = format_decision_telegram_s20(
        symbol=sym, market=market, news=news, decision=decision, pattern=pattern,
    )
    run_id = None
    if persist:
        run_id = persist_decision_run_s20(
            conn,
            symbol=sym,
            market=market,
            news=news,
            decision=decision,
            telegram_rendered=telegram,
        )
    return {
        "run_id": run_id,
        "symbol": sym,
        "market": market,
        "news": news,
        "pattern": pattern,
        "decision": decision,
        "telegram": telegram,
    }
