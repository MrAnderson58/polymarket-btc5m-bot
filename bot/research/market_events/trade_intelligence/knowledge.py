"""Assemble and query TradeKnowledge envelopes."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.trade_intelligence.models import (
    AISummary,
    TradeKnowledge,
    TradeRecord,
)
from bot.research.market_events.trade_intelligence.repository import TradeRepository


def build_trade_knowledge(repo: TradeRepository, trade_id: int) -> TradeKnowledge | None:
    trade = repo.get_trade(trade_id)
    if trade is None:
        return None
    return TradeKnowledge(
        trade=trade,
        market_snapshots=repo.load_snapshots(trade_id),
        news=repo.load_news(trade_id),
        telegram=repo.load_telegram(trade_id),
        context=repo.load_context(trade_id),
        outcome=repo.load_outcome(trade_id),
        ai_summary=repo.load_ai_summary(trade_id),
        tags=repo.load_tags(trade_id),
        notes=repo.load_notes(trade_id),
    )


def ensure_ai_summary_placeholder(repo: TradeRepository, trade_id: int) -> AISummary:
    """Ensure an empty AI summary row exists for future LLM plug-in."""
    existing = repo.load_ai_summary(trade_id)
    if existing is not None:
        return existing
    summary = AISummary(
        trade_id=trade_id,
        model=None,
        summary_text="",
        payload={"status": "placeholder", "version": "ti_v1"},
    )
    sid = repo.add_ai_summary(summary)
    summary.id = sid
    return summary


def attach_default_layers(
    repo: TradeRepository,
    trade: TradeRecord,
    *,
    tags: list[str] | None = None,
    note: str | None = None,
) -> TradeKnowledge:
    """Persist trade + empty satellite slots (AI placeholder, optional tags/notes)."""
    tid = repo.upsert_trade(trade)
    trade.id = tid
    ensure_ai_summary_placeholder(repo, tid)
    for tag in tags or []:
        if tag.strip():
            repo.add_tag(tid, tag)
    if note:
        from bot.research.market_events.trade_intelligence.models import Note
        import time
        repo.add_note(Note(trade_id=tid, note_ts=int(time.time()), author="system", text=note))
    knowledge = build_trade_knowledge(repo, tid)
    assert knowledge is not None
    return knowledge


def knowledge_brief(k: TradeKnowledge) -> dict[str, Any]:
    t = k.trade
    return {
        "id": t.id,
        "source": t.source,
        "symbol": t.symbol,
        "side": t.side,
        "strategy": t.strategy,
        "status": t.status,
        "entry_ts": t.entry_ts,
        "exit_ts": t.exit_ts,
        "pnl_usd": t.pnl_usd,
        "pnl_pct": t.pnl_pct,
        "tags": [x.tag for x in k.tags],
        "has_outcome": k.outcome is not None,
        "has_ai_summary": bool(k.ai_summary and k.ai_summary.summary_text),
        "news_n": len(k.news),
        "telegram_n": len(k.telegram),
        "snapshots_n": len(k.market_snapshots),
        "notes_n": len(k.notes),
    }
