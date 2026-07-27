"""Trade Intelligence V1 reports."""

from __future__ import annotations

from collections import Counter
from typing import Any

from bot.research.market_events.trade_intelligence.knowledge import (
    build_trade_knowledge,
    knowledge_brief,
)
from bot.research.market_events.trade_intelligence.repository import TradeRepository
from bot.research.market_events.trade_intelligence.similarity import find_similar_trades


def format_trade_list(repo: TradeRepository, *, source: str | None = None, limit: int = 50) -> str:
    trades = repo.list_trades(source=source, limit=limit)
    lines = [
        "TRADE INTELLIGENCE — LIST",
        f"Count: {len(trades)}" + (f"  source={source}" if source else ""),
        "",
        f"{'id':>5}  {'src':<8}  {'symbol':<8}  {'side':<6}  {'pnl%':>8}  {'status':<8}  strategy",
    ]
    for t in trades:
        pnl = f"{t.pnl_pct:+.2f}" if t.pnl_pct is not None else "—"
        lines.append(
            f"{t.id or 0:>5}  {t.source:<8}  {t.symbol:<8}  {t.side:<6}  {pnl:>8}  "
            f"{t.status:<8}  {t.strategy or '—'}",
        )
    if not trades:
        lines.append("(no trades)")
    return "\n".join(lines)


def format_trade_report(repo: TradeRepository, *, trade_id: int | None = None) -> str:
    if trade_id is not None:
        k = build_trade_knowledge(repo, trade_id)
        if k is None:
            return f"Trade {trade_id} not found"
        brief = knowledge_brief(k)
        lines = [
            "TRADE INTELLIGENCE — REPORT",
            f"Trade ID ....... {brief['id']}",
            f"Source ......... {brief['source']}",
            f"Symbol ......... {brief['symbol']}",
            f"Side ........... {brief['side']}",
            f"Strategy ....... {brief['strategy'] or '—'}",
            f"Status ......... {brief['status']}",
            f"PnL USD ........ {brief['pnl_usd']}",
            f"PnL % .......... {brief['pnl_pct']}",
            f"Tags ........... {', '.join(brief['tags']) or '—'}",
            f"Snapshots ...... {brief['snapshots_n']}",
            f"News ........... {brief['news_n']}",
            f"Telegram ....... {brief['telegram_n']}",
            f"Notes .......... {brief['notes_n']}",
            f"Outcome ........ {'yes' if brief['has_outcome'] else 'no'}",
            f"AI Summary ..... {'filled' if brief['has_ai_summary'] else 'placeholder'}",
        ]
        if k.outcome:
            lines.append(f"Result ......... {k.outcome.result}")
            lines.append(f"Exit reason .... {k.outcome.exit_reason or '—'}")
        if k.notes:
            lines.append("")
            lines.append("Notes")
            for n in k.notes[:5]:
                lines.append(f"  - {n.text[:120]}")
        return "\n".join(lines)

    trades = repo.list_trades(limit=5000)
    by_source = Counter(t.source for t in trades)
    closed = [t for t in trades if t.status == "closed"]
    wins = sum(1 for t in closed if (t.pnl_usd or 0) > 0)
    losses = sum(1 for t in closed if (t.pnl_usd or 0) < 0)
    pnl = sum(t.pnl_usd or 0.0 for t in closed)
    counts = repo.counts()
    lines = [
        "TRADE INTELLIGENCE — SUMMARY REPORT",
        "",
        f"Trades ........... {len(trades)}",
        f"Closed ........... {len(closed)}",
        f"Wins / Losses .... {wins} / {losses}",
        f"Net PnL USD ...... {pnl:+.2f}",
        "",
        "By source",
    ]
    for src, n in sorted(by_source.items()):
        lines.append(f"  {src:<12} {n}")
    lines.extend([
        "",
        "Knowledge tables",
        f"  snapshots ...... {counts.get('ti_market_snapshots', 0)}",
        f"  news ........... {counts.get('ti_news', 0)}",
        f"  telegram ....... {counts.get('ti_telegram', 0)}",
        f"  outcomes ....... {counts.get('ti_outcomes', 0)}",
        f"  ai_summaries ... {counts.get('ti_ai_summaries', 0)}",
        f"  tags ........... {counts.get('ti_tags', 0)}",
        f"  notes .......... {counts.get('ti_notes', 0)}",
    ])
    return "\n".join(lines)


def format_similar_trades(
    repo: TradeRepository,
    *,
    trade_id: int,
    limit: int = 10,
) -> str:
    target = repo.get_trade(trade_id)
    if target is None:
        return f"Trade {trade_id} not found"
    candidates = repo.list_trades(limit=2000)
    similar = find_similar_trades(target, candidates, limit=limit)
    lines = [
        "TRADE INTELLIGENCE — SIMILAR",
        f"Target: id={target.id} {target.symbol} {target.side} pnl%={target.pnl_pct}",
        "",
        f"{'score':>6}  {'id':>5}  {'src':<8}  {'symbol':<8}  {'side':<6}  {'pnl%':>8}  strategy",
    ]
    for trade, score in similar:
        pnl = f"{trade.pnl_pct:+.2f}" if trade.pnl_pct is not None else "—"
        lines.append(
            f"{score:6.2f}  {trade.id or 0:>5}  {trade.source:<8}  {trade.symbol:<8}  "
            f"{trade.side:<6}  {pnl:>8}  {trade.strategy or '—'}",
        )
    if not similar:
        lines.append("(no similar trades)")
    return "\n".join(lines)
