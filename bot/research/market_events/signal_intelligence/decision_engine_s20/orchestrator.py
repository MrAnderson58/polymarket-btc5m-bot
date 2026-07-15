"""S2.0 orchestrator — run Market → News → Decision and format Telegram card."""

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
) -> dict[str, Any]:
    """Full MVP cycle. Default persist=False — pure READ ONLY (S2.2).

    Research-only; does not touch production scoring. Opt-in persist for offline analysis.
    """
    sym = symbol.upper().replace("USDT", "").strip() or "BTC"
    market = run_market_agent_s20(conn, sym, market_data=market_data)
    news = run_news_agent_s20(sym, headlines=headlines, x_client=x_client)
    decision = run_decision_agent_s20(
        market, news, allow_claude=allow_claude, force_fallback=force_fallback,
    )
    telegram = format_decision_telegram_s20(
        symbol=sym, market=market, news=news, decision=decision,
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
        "decision": decision,
        "telegram": telegram,
    }
