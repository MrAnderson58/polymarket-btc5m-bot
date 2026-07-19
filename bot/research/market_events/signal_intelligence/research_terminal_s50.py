"""S5.0 — AI Research Terminal (Telegram-only Claude, single-shot context)."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Any

import requests

from bot.research.market_events.db import execute_with_retry, market_events_connection, market_events_readonly_connection
from bot.research.market_events.signal_intelligence.claude_channel_s50 import (
    s50_max_context_tokens,
    s50_daily_limit,
    telegram_claude_session,
)
from bot.research.market_events.signal_intelligence.claude_client_g2 import (
    ClaudeClientError,
    ClaudeResponseG2,
    call_claude_g2,
    is_claude_configured,
)
from bot.research.market_events.signal_intelligence.research_artifacts_s50 import (
    ARTIFACT_IMAGE,
    ARTIFACT_URL,
    classify_url_domain,
    extract_urls,
    get_research_artifact_s50,
    list_research_artifacts_s50,
    format_artifacts_report_s50,
)

logger = logging.getLogger(__name__)

_REQUESTS = "market_events_claude_requests_s50"
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,12}$")


def _day_start_local(ts: int | None = None) -> int:
    ts = ts or int(time.time())
    dt = datetime.fromtimestamp(ts)
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20] + "\n… [truncated]"


def _normalize_symbol(raw: str | None) -> str | None:
    if not raw:
        return None
    sym = raw.upper().replace("USDT", "").strip()
    if _SYMBOL_RE.match(sym):
        return sym
    return None


def _requests_today_s50(conn: Any) -> int:
    start = _day_start_local()
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_REQUESTS} WHERE created_at >= ? AND status = 'ok'",
        (start,),
    ).fetchone()
    return int(row["n"] or 0)


def _can_request_s50(conn: Any) -> tuple[bool, str | None]:
    used = _requests_today_s50(conn)
    limit = s50_daily_limit()
    if used >= limit:
        return False, f"daily_limit {used}/{limit}"
    return True, None


def _record_request_s50(
    conn: Any,
    *,
    command: str,
    symbol: str | None,
    prompt_chars: int,
    resp: ClaudeResponseG2 | None,
    duration_ms: float,
    artifacts_used: list[int],
    telegram_user: str | None,
    status: str = "ok",
    error: str | None = None,
) -> None:
    now = int(time.time())
    execute_with_retry(
        conn,
        f"""
        INSERT INTO {_REQUESTS} (
            command, symbol, prompt_chars, input_tokens, output_tokens,
            duration_ms, estimated_cost_usd, artifacts_used_json, model,
            telegram_user, status, error, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            command,
            symbol,
            prompt_chars,
            resp.usage.input_tokens if resp else None,
            resp.usage.output_tokens if resp else None,
            round(duration_ms, 1),
            resp.usage.cost_usd if resp else None,
            json.dumps(artifacts_used) if artifacts_used else None,
            resp.model if resp else None,
            telegram_user,
            status,
            error,
            now,
        ),
    )


def _build_research_context_s50(conn: Any, *, symbol: str | None) -> tuple[str, list[int]]:
    """Assemble one prompt block from local DB only (no nested Claude calls)."""
    max_chars = s50_max_context_tokens() * 4  # rough chars/token
    sections: list[str] = []
    artifact_ids: list[int] = []

    sym = _normalize_symbol(symbol) or "BTC"

    try:
        from bot.research.market_events.signal_intelligence.decision_engine_s20 import (
            run_decision_engine_s20,
        )
        dec = run_decision_engine_s20(conn, sym, persist=False, force_fallback=True)
        sections.append("=== Decision (local) ===\n" + str(dec.get("telegram") or dec.get("summary") or ""))
    except Exception as exc:
        sections.append(f"=== Decision ===\n(unavailable: {exc})")

    try:
        from bot.research.market_events.signal_intelligence.pattern_agent_s31 import (
            run_pattern_agent_s31,
            format_pattern_report_s31,
        )
        pat = run_pattern_agent_s31(conn, symbol=sym, timeframe="60m")
        sections.append("=== Pattern ===\n" + format_pattern_report_s31(pat, show_examples=False))
    except Exception as exc:
        sections.append(f"=== Pattern ===\n(unavailable: {exc})")

    try:
        from bot.research.market_events.signal_intelligence.news_collector_n11 import (
            fetch_latest_news_n11,
            format_news_latest_n11,
        )
        news = fetch_latest_news_n11(conn, limit=5)
        sections.append("=== News ===\n" + format_news_latest_n11(news))
    except Exception as exc:
        sections.append(f"=== News ===\n(unavailable: {exc})")

    try:
        from bot.research.market_events.signal_intelligence.signal_paper_performance_s42 import (
            format_paper_performance_s42,
        )
        sections.append("=== Paper Performance ===\n" + format_paper_performance_s42(conn))
    except Exception as exc:
        sections.append(f"=== Paper ===\n(unavailable: {exc})")

    try:
        from bot.research.market_events.signal_intelligence.signal_learning_s40 import (
            learning_health_s40,
        )
        sections.append("=== Learning Health ===\n" + learning_health_s40())
    except Exception as exc:
        sections.append(f"=== Learning ===\n(unavailable: {exc})")

    try:
        rows = conn.execute(
            """
            SELECT s.symbol, s.direction, r.win_loss_be, r.pnl_pct,
                   substr(r.analysis_text, 1, 400) AS snippet
            FROM market_events_signal_learning_s40_reviews r
            JOIN market_events_signal_learning_s40_signals s
              ON s.signal_type = r.signal_type AND s.signal_id = r.signal_id
            WHERE s.symbol = ?
            ORDER BY s.timestamp DESC
            LIMIT 5
            """,
            (sym,),
        ).fetchall()
        if rows:
            lines = [f"{r['symbol']} {r['direction']} {r['win_loss_be']} pnl={r['pnl_pct']}" for r in rows]
            sections.append("=== Similar Cases ===\n" + "\n".join(lines))
    except Exception:
        pass

    arts = list_research_artifacts_s50(conn, symbol=sym, limit=8)
    if arts:
        artifact_ids = [int(a["id"]) for a in arts]
        art_lines = [
            f"#{a['id']} {a['artifact_type']} {(a.get('caption') or a.get('url') or '')[:80]}"
            for a in arts
        ]
        sections.append("=== Research Artifacts ===\n" + "\n".join(art_lines))

    body = "\n\n".join(sections)
    return _truncate(body, max_chars), artifact_ids


def _call_claude_research_s50(
    conn: Any,
    *,
    command: str,
    symbol: str | None,
    system: str,
    user: str,
    image_path: str | None = None,
    telegram_user: str | None = None,
    artifacts_used: list[int] | None = None,
    max_tokens: int = 2048,
) -> str:
    if not is_claude_configured():
        return "Claude not configured (ANTHROPIC_API_KEY missing)."

    ok, reason = _can_request_s50(conn)
    if not ok:
        return f"Daily Claude budget exhausted: {reason}"

    content: list[dict[str, Any]] | str
    if image_path:
        from bot.research.market_events.signal_intelligence.claude_client_g2 import _image_block_from_path
        blocks: list[dict[str, Any]] = []
        block = _image_block_from_path(image_path)
        if block:
            blocks.append(block)
        blocks.append({"type": "text", "text": user})
        content = blocks
    else:
        content = user

    t0 = time.perf_counter()
    used_artifacts = list(artifacts_used or [])
    try:
        with telegram_claude_session():
            resp = call_claude_g2(
                system=system,
                user_content=content,
                label=f"s50_{command}",
                max_tokens=max_tokens,
            )
        duration = (time.perf_counter() - t0) * 1000.0
        _record_request_s50(
            conn,
            command=command,
            symbol=symbol,
            prompt_chars=len(system) + len(user),
            resp=resp,
            duration_ms=duration,
            artifacts_used=used_artifacts,
            telegram_user=telegram_user,
        )
        conn.commit()
        return resp.text.strip()
    except ClaudeClientError as exc:
        duration = (time.perf_counter() - t0) * 1000.0
        _record_request_s50(
            conn,
            command=command,
            symbol=symbol,
            prompt_chars=len(system) + len(user),
            resp=None,
            duration_ms=duration,
            artifacts_used=used_artifacts,
            telegram_user=telegram_user,
            status="error",
            error=str(exc),
        )
        conn.commit()
        return f"Claude error: {exc}"


def run_ai_s50_cli(
    *,
    symbol: str = "BTC",
    telegram_user: str | None = None,
) -> str:
    sym = _normalize_symbol(symbol) or "BTC"
    with market_events_connection() as conn:
        ctx, artifact_ids = _build_research_context_s50(conn, symbol=sym)
        system = (
            "You are an expert crypto research consultant. "
            "Analyze the provided local system context. "
            "No trading advice — research only. Be concise."
        )
        user = "\n".join([
            f"Symbol: {sym}",
            "",
            "Local context:",
            ctx,
            "",
            "Provide: market read, key risks, alignment of Decision/Pattern/News, "
            "what paper/learning data suggests, and open questions.",
        ])
        return _call_claude_research_s50(
            conn,
            command="/ai",
            symbol=sym,
            system=system,
            user=user,
            telegram_user=telegram_user,
            artifacts_used=artifact_ids,
        )


def run_analyze_text_s50_cli(
    *,
    text: str,
    symbol: str | None = None,
    telegram_user: str | None = None,
) -> str:
    sym = _normalize_symbol(symbol)
    with market_events_connection() as conn:
        ctx, artifact_ids = _build_research_context_s50(conn, symbol=sym)
        system = (
            "Classify and analyze the user text for crypto research. "
            "Identify: news/rumor/fundamental, impact, probability, affected coins."
        )
        user = "\n".join([
            f"Focus symbol: {sym or 'general'}",
            "",
            "User text:",
            text.strip(),
            "",
            "Local context:",
            ctx,
        ])
        return _call_claude_research_s50(
            conn,
            command="/analyze",
            symbol=sym,
            system=system,
            user=user,
            telegram_user=telegram_user,
            artifacts_used=artifact_ids,
        )


def run_analyze_url_s50_cli(
    *,
    url: str,
    symbol: str | None = None,
    telegram_user: str | None = None,
) -> str:
    sym = _normalize_symbol(symbol)
    fetched = ""
    try:
        resp = requests.get(url, timeout=12, headers={"User-Agent": "polymarket-research-s50/1.0"})
        if resp.ok:
            fetched = _truncate(resp.text, 6000)
    except Exception as exc:
        fetched = f"(fetch failed: {exc})"

    with market_events_connection() as conn:
        from bot.research.market_events.signal_intelligence.research_artifacts_s50 import (
            save_research_artifact_s50,
        )
        save_research_artifact_s50(
            conn,
            artifact_type=ARTIFACT_URL,
            symbol=sym,
            url=url,
            content_text=fetched[:2000] if fetched else None,
            telegram_user=telegram_user,
        )
        ctx, artifact_ids = _build_research_context_s50(conn, symbol=sym)
        system = "Analyze the URL and content for crypto market impact. Research only."
        user = "\n".join([
            f"URL: {url}",
            f"Domain: {classify_url_domain(url)}",
            f"Focus symbol: {sym or 'general'}",
            "",
            "Fetched content:",
            fetched or "(none)",
            "",
            "Local context:",
            ctx,
        ])
        return _call_claude_research_s50(
            conn,
            command="/analyze",
            symbol=sym,
            system=system,
            user=user,
            telegram_user=telegram_user,
            artifacts_used=artifact_ids,
        )


def run_analyze_image_s50_cli(
    *,
    image_path: str,
    symbol: str | None = None,
    caption: str | None = None,
    artifact_id: int | None = None,
    telegram_user: str | None = None,
) -> str:
    sym = _normalize_symbol(symbol)
    with market_events_connection() as conn:
        ctx, artifact_ids = _build_research_context_s50(conn, symbol=sym)
        if artifact_id:
            artifact_ids.append(artifact_id)
        system = (
            "Analyze the chart/image for crypto research. Describe: "
            "structure, strong/weak zones, whether it confirms or contradicts "
            "the local Decision signal, and what's missing."
        )
        user = "\n".join([
            f"Symbol: {sym or 'infer from chart'}",
            f"Caption: {caption or '—'}",
            "",
            "Local context:",
            ctx,
        ])
        return _call_claude_research_s50(
            conn,
            command="/analyze",
            symbol=sym,
            system=system,
            user=user,
            image_path=image_path,
            telegram_user=telegram_user,
            artifacts_used=artifact_ids,
        )


def run_compare_s50_cli(
    *,
    symbol: str = "BTC",
    telegram_user: str | None = None,
) -> str:
    sym = _normalize_symbol(symbol) or "BTC"
    with market_events_connection() as conn:
        ctx, artifact_ids = _build_research_context_s50(conn, symbol=sym)
        try:
            from bot.research.market_events.signal_intelligence.decision_engine_s20 import (
                run_decision_engine_s20,
            )
            dec = run_decision_engine_s20(conn, sym, persist=False, force_fallback=True)
            decision_line = str(dec.get("telegram") or "")
        except Exception:
            decision_line = "unknown"
        system = (
            "You are a devil's advocate research analyst. "
            "Find maximum arguments why the local system's decision may be WRONG. "
            "No cheerleading — contradictions only."
        )
        user = "\n".join([
            f"Symbol: {sym}",
            f"Local Decision says: {decision_line}",
            "",
            "Full local context:",
            ctx,
            "",
            "Task: list contradictions between Decision, Pattern, News, stats, and chart artifacts. "
            "What would make this trade fail?",
        ])
        return _call_claude_research_s50(
            conn,
            command="/compare",
            symbol=sym,
            system=system,
            user=user,
            telegram_user=telegram_user,
            artifacts_used=artifact_ids,
        )


def format_cost_s50_cli() -> str:
    with market_events_readonly_connection() as conn:
        start = _day_start_local()
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(input_tokens), 0) AS in_t,
                   COALESCE(SUM(output_tokens), 0) AS out_t,
                   COALESCE(SUM(estimated_cost_usd), 0) AS cost
            FROM {_REQUESTS}
            WHERE created_at >= ? AND status = 'ok'
            """,
            (start,),
        ).fetchone()
        used = int(row["n"] or 0)
        limit = s50_daily_limit()
        return "\n".join([
            "Claude Budget (S5.0 Telegram)",
            "",
            f"Today requests: {used}/{limit}",
            f"Input tokens:   {int(row['in_t'] or 0)}",
            f"Output tokens:  {int(row['out_t'] or 0)}",
            f"Est. cost:      ${float(row['cost'] or 0):.4f}",
            "",
            f"automatic=false  telegram_only=true",
        ])


def format_history_s50_cli(*, limit: int = 15) -> str:
    with market_events_readonly_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT command, symbol, input_tokens, output_tokens,
                   duration_ms, estimated_cost_usd, status, created_at, telegram_user
            FROM {_REQUESTS}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, min(limit, 50)),),
        ).fetchall()
    lines = ["Claude Request History", ""]
    if not rows:
        lines.append("No requests yet.")
        return "\n".join(lines)
    for r in rows:
        lines.append(
            f"{r['command']} {r.get('symbol') or '—'} "
            f"in={r.get('input_tokens') or 0} out={r.get('output_tokens') or 0} "
            f"${float(r.get('estimated_cost_usd') or 0):.4f} {r['status']}"
        )
    return "\n".join(lines)


def dispatch_analyze_command_s50(
    args: list[str],
    *,
    full_text: str | None = None,
    telegram_user: str | None = None,
) -> str:
    """Route /analyze to symbol, URL, or free-text analysis."""
    body = " ".join(args).strip() if args else (full_text or "").strip()
    if not body:
        return "Usage: /analyze BTC  |  /analyze <text>  |  /analyze <url>"

    urls = extract_urls(body)
    if urls and body.strip().startswith("http"):
        return run_analyze_url_s50_cli(url=urls[0], telegram_user=telegram_user)

    sym = _normalize_symbol(args[0]) if len(args) == 1 else None
    if sym and len(args) == 1:
        return run_ai_s50_cli(symbol=sym, telegram_user=telegram_user)

    return run_analyze_text_s50_cli(text=body, telegram_user=telegram_user)


__all__ = [
    "dispatch_analyze_command_s50",
    "format_artifacts_report_s50",
    "format_cost_s50_cli",
    "format_history_s50_cli",
    "run_ai_s50_cli",
    "run_analyze_image_s50_cli",
    "run_analyze_text_s50_cli",
    "run_analyze_url_s50_cli",
    "run_compare_s50_cli",
]
