"""S5.1 — AI Audit Engine (System Auditor, Telegram-only Claude)."""

from __future__ import annotations

import json
import logging
from typing import Any

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.claude_channel_s50 import (
    s50_max_context_tokens,
)
from bot.research.market_events.signal_intelligence.research_artifacts_s50 import (
    list_research_artifacts_s50,
)
from bot.research.market_events.signal_intelligence.research_terminal_s50 import (
    _call_claude_research_s50,
    _normalize_symbol,
    _truncate,
)

logger = logging.getLogger(__name__)

_AUDIT_SYSTEM_PROMPT = """You are a System Auditor for a crypto research trading stack.
You are NOT a trader and you must NOT rewrite Decision Agent or invent a new trade.

Your job is to evaluate the SYSTEM and the CURRENT SIGNAL QUALITY using only the
provided local data. Hunt for:
- internal contradictions between agents
- weak / inconsistent metrics
- suspicious paper or learning anomalies (possible bugs)
- agent disagreement
- low specificity (macro news vs micro trade)

Return a structured report in this exact style (Telegram-friendly plain text):

AI AUDIT
Symbol: <SYM or SYSTEM>

Overall Reliability
X.X / 10

Recommendation
PASS | PASS WITH CAUTION | FAIL

1. Agent Consistency
X/10
...

2. Pattern Quality
X/10
...

3. News Quality
X/10
...

4. Market Quality
X/10
...

5. Paper Confidence
X/10
...

6. Learning Quality
X/10
...

7. Contradictions
Found contradictions
1. ...
2. ...

8. Bugs
Possible bugs
...

9. Final Verdict
Trade quality
X/10
System quality
X/10
Reason
...

Rules:
- Prefer contradictions over cheerleading.
- Distinguish trade quality vs system quality.
- If data is missing, say so explicitly — do not invent numbers.
- Keep under ~900 words.
"""

_AUDIT_SYSTEM_MODE_PROMPT = """You are a System Auditor for a crypto research trading stack.
You are NOT a trader. Do not invent market forecasts.

Audit the FULL platform health using only the provided local data:
Workers, Learning, Paper, Decision path, Errors, Queue, Claude, Telegram, Database.

Return Telegram-friendly plain text in this style:

AI AUDIT
Mode: SYSTEM

System Score
X.X / 10

Weakest module
...

Strongest module
...

Highest error rate
...

Module scores (brief)
- Workers: X/10
- Learning: X/10
- Paper: X/10
- Decision: X/10
- Claude: X/10
- Telegram: X/10
- Database: X/10

Possible bugs / anomalies
...

Suggested improvements
1. ...
2. ...

Rules:
- Prefer evidence from the context over generic advice.
- Call out missing data explicitly.
- Keep under ~700 words.
"""


def _json_snip(obj: Any, *, limit: int = 2500) -> str:
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str, indent=0)
    except Exception:
        text = str(obj)
    return _truncate(text, limit)


def _build_symbol_audit_context_s51(conn: Any, *, symbol: str) -> tuple[str, list[int]]:
    """Raw multi-agent context for symbol audit (local only, no nested Claude)."""
    max_chars = s50_max_context_tokens() * 4
    sections: list[str] = []
    artifact_ids: list[int] = []
    sym = _normalize_symbol(symbol) or "BTC"

    decision_raw: dict[str, Any] | None = None
    try:
        from bot.research.market_events.signal_intelligence.decision_engine_s20 import (
            run_decision_engine_s20,
        )
        decision_raw = run_decision_engine_s20(conn, sym, persist=False, force_fallback=True)
        sections.append(
            "=== Decision Agent (telegram) ===\n"
            + str(decision_raw.get("telegram") or "")
        )
        sections.append(
            "=== Decision Agent (raw) ===\n"
            + _json_snip({
                "decision": decision_raw.get("decision"),
                "market_keys": list((decision_raw.get("market") or {}).keys()),
            })
        )
        market = decision_raw.get("market") or {}
        news = decision_raw.get("news") or {}
        pattern = decision_raw.get("pattern") or {}
        sections.append("=== Market Agent (raw) ===\n" + _json_snip(market, limit=3000))
        sections.append("=== News Agent (raw) ===\n" + _json_snip(news, limit=2000))
        sections.append("=== Pattern Agent (raw) ===\n" + _json_snip(pattern, limit=3000))
    except Exception as exc:
        sections.append(f"=== Decision/Agents ===\n(unavailable: {exc})")

    try:
        from bot.research.market_events.signal_intelligence.explain_decision_s22 import (
            format_explain_decision_s22,
        )
        sections.append(
            "=== Decision Explanation ===\n"
            + _truncate(format_explain_decision_s22(conn, sym), 3500)
        )
    except Exception as exc:
        sections.append(f"=== Decision Explanation ===\n(unavailable: {exc})")

    try:
        from bot.research.market_events.signal_intelligence.pattern_agent_s31 import (
            format_pattern_report_s31,
            run_pattern_agent_s31,
        )
        pat = run_pattern_agent_s31(conn, symbol=sym, timeframe="60m")
        sections.append("=== Pattern Report ===\n" + format_pattern_report_s31(pat, show_examples=False))
    except Exception as exc:
        sections.append(f"=== Pattern Report ===\n(unavailable: {exc})")

    try:
        from bot.research.market_events.signal_intelligence.news_collector_n11 import (
            fetch_latest_news_n11,
            format_news_latest_n11,
        )
        news_rows = fetch_latest_news_n11(conn, limit=8)
        sections.append("=== News Feed ===\n" + format_news_latest_n11(news_rows))
    except Exception as exc:
        sections.append(f"=== News Feed ===\n(unavailable: {exc})")

    try:
        from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
            format_paper_performance_s42,
            paper_performance_dashboard_s42,
        )
        sections.append("=== Paper Statistics ===\n" + format_paper_performance_s42(conn, symbol=sym))
        sections.append(
            "=== Paper Dashboard (raw) ===\n"
            + _json_snip(paper_performance_dashboard_s42(conn))
        )
    except Exception as exc:
        sections.append(f"=== Paper ===\n(unavailable: {exc})")

    try:
        from bot.research.market_events.signal_intelligence.signal_learning_s40 import (
            learning_health_s40,
            learning_review_analytics_s40,
        )
        sections.append("=== Learning Health ===\n" + learning_health_s40())
        sections.append(
            "=== Learning Analytics (raw) ===\n"
            + _json_snip(learning_review_analytics_s40(conn))
        )
    except Exception as exc:
        sections.append(f"=== Learning ===\n(unavailable: {exc})")

    try:
        rows = conn.execute(
            """
            SELECT s.symbol, s.direction, s.entry, s.stop, s.tp1, s.tp2,
                   r.win_loss_be, r.pnl_pct, r.rr_achieved, r.review_type,
                   substr(r.analysis_text, 1, 280) AS snippet
            FROM market_events_signal_learning_s40_reviews r
            JOIN market_events_signal_learning_s40_signals s
              ON s.signal_type = r.signal_type AND s.signal_id = r.signal_id
            WHERE s.symbol = ?
            ORDER BY s.timestamp DESC
            LIMIT 8
            """,
            (sym,),
        ).fetchall()
        if rows:
            lines = [
                f"{r['symbol']} {r['direction']} {r['win_loss_be']} "
                f"pnl={r['pnl_pct']} rr={r['rr_achieved']} type={r['review_type']} "
                f"| {r['snippet'] or ''}"
                for r in rows
            ]
            sections.append("=== Similar Trades ===\n" + "\n".join(lines))
        else:
            sections.append("=== Similar Trades ===\n(none)")
    except Exception as exc:
        sections.append(f"=== Similar Trades ===\n(unavailable: {exc})")

    # Current features from latest s40 signal snapshot when present.
    try:
        snap = conn.execute(
            """
            SELECT snapshot_funding, snapshot_open_interest, snapshot_volume, snapshot_atr,
                   snapshot_fear_greed, snapshot_trend, snapshot_news_impact,
                   snapshot_decision_confidence, direction, entry, stop, tp1, tp2
            FROM market_events_signal_learning_s40_signals
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (sym,),
        ).fetchone()
        if snap:
            sections.append("=== Current Features / Snapshot ===\n" + _json_snip(dict(snap)))
        elif decision_raw and decision_raw.get("market"):
            m = decision_raw["market"]
            sections.append(
                "=== Current Features (from Market Agent) ===\n"
                + _json_snip({
                    "direction": m.get("direction"),
                    "entry": m.get("entry"),
                    "stop": m.get("stop"),
                    "tp1": m.get("tp1"),
                    "tp2": m.get("tp2"),
                    "funding": m.get("funding"),
                    "open_interest": m.get("open_interest"),
                    "atr": m.get("atr"),
                    "trend": m.get("trend"),
                    "reasons": m.get("reasons"),
                }, limit=2000)
            )
    except Exception as exc:
        sections.append(f"=== Current Features ===\n(unavailable: {exc})")

    arts = list_research_artifacts_s50(conn, symbol=sym, limit=10)
    if arts:
        artifact_ids = [int(a["id"]) for a in arts]
        art_lines = [
            f"#{a['id']} {a['artifact_type']} {(a.get('caption') or a.get('url') or a.get('content_text') or '')[:100]}"
            for a in arts
        ]
        sections.append("=== Research Artifacts ===\n" + "\n".join(art_lines))
    else:
        sections.append("=== Research Artifacts ===\n(none)")

    body = "\n\n".join(sections)
    return _truncate(body, max_chars), artifact_ids


def _build_system_audit_context_s51(conn: Any) -> tuple[str, list[int]]:
    max_chars = s50_max_context_tokens() * 4
    sections: list[str] = []
    try:
        from bot.research.market_events.process_manager import system_health_report
        sections.append("=== System Health ===\n" + system_health_report())
    except Exception as exc:
        sections.append(f"=== System Health ===\n(unavailable: {exc})")
    try:
        from bot.research.market_events.process_manager import status_report, telegram_status_report
        sections.append("=== Workers Status ===\n" + status_report())
        sections.append("=== Telegram Status ===\n" + telegram_status_report())
    except Exception as exc:
        sections.append(f"=== Workers/Telegram ===\n(unavailable: {exc})")
    try:
        from bot.research.market_events.signal_intelligence.signal_learning_s40 import (
            learning_health_s40,
            learning_review_analytics_s40,
        )
        sections.append("=== Learning Health ===\n" + learning_health_s40())
        sections.append("=== Learning Analytics ===\n" + _json_snip(learning_review_analytics_s40(conn)))
    except Exception as exc:
        sections.append(f"=== Learning ===\n(unavailable: {exc})")
    try:
        from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
            format_paper_performance_s42,
            paper_performance_dashboard_s42,
        )
        sections.append("=== Paper ===\n" + format_paper_performance_s42(conn))
        sections.append("=== Paper raw ===\n" + _json_snip(paper_performance_dashboard_s42(conn)))
    except Exception as exc:
        sections.append(f"=== Paper ===\n(unavailable: {exc})")
    try:
        from bot.research.market_events.signal_intelligence.research_terminal_s50 import (
            format_cost_s50_cli,
        )
        sections.append("=== Claude Budget ===\n" + format_cost_s50_cli())
    except Exception as exc:
        sections.append(f"=== Claude ===\n(unavailable: {exc})")
    return _truncate("\n\n".join(sections), max_chars), []


def run_audit_s51_cli(
    *,
    symbol: str | None = "BTC",
    system: bool = False,
    telegram_user: str | None = None,
) -> str:
    """Run AI Audit for a symbol or full system (Telegram-only Claude)."""
    with market_events_connection() as conn:
        apply_migrations(conn)
        if system or (symbol and str(symbol).lower() in {"system", "sys", "all"}):
            ctx, artifact_ids = _build_system_audit_context_s51(conn)
            user = "\n".join([
                "Mode: FULL SYSTEM AUDIT (not a single coin).",
                "Score Workers, Learning, Paper, Decision, Errors, Queue, Claude, Telegram, Database.",
                "Return Overall System Score, weakest/strongest modules, highest error rate, suggested improvements.",
                "",
                "Local system context:",
                ctx,
            ])
            return _call_claude_research_s50(
                conn,
                command="/audit",
                symbol="SYSTEM",
                system=_AUDIT_SYSTEM_MODE_PROMPT,
                user=user,
                telegram_user=telegram_user,
                artifacts_used=artifact_ids,
                max_tokens=4096,
            )

        sym = _normalize_symbol(symbol) or "BTC"
        ctx, artifact_ids = _build_symbol_audit_context_s51(conn, symbol=sym)
        user = "\n".join([
            f"Mode: SYMBOL AUDIT for {sym}.",
            "Audit the system output for this symbol. Do not rewrite Decision.",
            "Emphasize contradictions, weak metrics, and possible bugs.",
            "",
            "Local raw context:",
            ctx,
        ])
        return _call_claude_research_s50(
            conn,
            command="/audit",
            symbol=sym,
            system=_AUDIT_SYSTEM_PROMPT,
            user=user,
            telegram_user=telegram_user,
            artifacts_used=artifact_ids,
            max_tokens=4096,
        )


def dispatch_audit_command_s51(args: list[str], *, telegram_user: str | None = None) -> str:
    """Route /audit BTC | /audit system."""
    if not args:
        return "Usage: /audit BTC  |  /audit system"
    first = args[0].strip().lower()
    if first in {"system", "sys", "all"}:
        return run_audit_s51_cli(system=True, telegram_user=telegram_user)
    return run_audit_s51_cli(symbol=args[0], telegram_user=telegram_user)


__all__ = [
    "dispatch_audit_command_s51",
    "run_audit_s51_cli",
]
