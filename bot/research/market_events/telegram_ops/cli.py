"""Phase E.5.3 — Telegram ops CLIs (alert test, AI test, demo, health)."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import patch

from bot.research.market_events.alert_config import resolve_alert_chat_id
from bot.research.market_events.market_event_alerts import ALERT_AI, ALERT_SHOCK, PAPER_LABEL

ALERT_TEST = "TELEGRAM_TEST"


def _try_alert_message_text(conn: Any, event_id: int) -> str | None:
    """Best-effort Telegram preview from alert log; never raises."""
    try:
        row = conn.execute(
            """
            SELECT message_text FROM market_event_alert_log
            WHERE event_id = ? ORDER BY id DESC LIMIT 1
            """,
            (event_id,),
        ).fetchone()
        if not row:
            return None
        return str(row["message_text"]) if row["message_text"] else None
    except Exception:
        return None


def _has_research_agent_telegram(
    conn: Any,
    event_id: int,
    *,
    g2_telegram_block: str | None,
) -> bool:
    try:
        if g2_telegram_block and "🧠 Research Agent" in g2_telegram_block:
            return True
        preview = _try_alert_message_text(conn, event_id)
        return bool(preview and "🧠 Research Agent" in preview)
    except Exception:
        return bool(g2_telegram_block and "🧠 Research Agent" in g2_telegram_block)


def _db_ok(conn: Any) -> bool:
    row = conn.execute("PRAGMA quick_check").fetchone()
    return bool(row and row[0] == "ok")


def _mask_chat(chat: str | None) -> str:
    if not chat:
        return "not configured"
    if len(chat) <= 4:
        return chat
    return f"{'*' * (len(chat) - 4)}{chat[-4:]}"


def _token_configured() -> bool:
    from bot.research.futures_agent.telegram_config import get_telegram_bot_token
    return bool(get_telegram_bot_token())


def build_test_message(*, db_ok: bool) -> str:
    resolution = resolve_alert_chat_id()
    return "\n".join([
        "🧪 MARKET EVENTS TEST",
        "",
        "Telegram alerts are working.",
        "",
        f"Database: {'OK' if db_ok else 'FAIL'}",
        "Notifier: OK",
        f"Chat: {_mask_chat(resolution.chat_id)}",
        "",
        PAPER_LABEL,
    ])


def run_telegram_alert_test(conn: Any | None = None) -> tuple[int, str]:
    """Send deterministic test alert. READ ONLY for SQLite — no delivery_log / heartbeat writes."""
    from bot.research.market_events.alert_engine.telegram_delivery import deliver_telegram

    db_status = _db_ok(conn) if conn is not None else False
    message = build_test_message(db_ok=db_status)
    resolution = resolve_alert_chat_id()
    token_ok = _token_configured()

    # FIX-3: conn=None → deliver_telegram skips INSERT into delivery log.
    result = deliver_telegram(
        message, conn=None, alert_type=ALERT_TEST, event_id=0,
    )

    lines = [
        "TELEGRAM ALERT TEST",
        "",
        f"Bot token configured: {'yes' if token_ok else 'no'}",
        f"Chat ID: {_mask_chat(resolution.chat_id) if resolution.chat_id else 'not configured'}",
    ]
    if resolution.source:
        lines.append(f"Chat resolved from: {resolution.source}")
    elif resolution.error:
        lines.append(f"Chat resolution: {resolution.error}")
    lines.extend([
        f"HTTP status: {result.http_code if result.http_code is not None else '—'}",
        f"Telegram message_id: {result.message_id if result.message_id is not None else '—'}",
        f"Latency: {result.latency_ms:.0f} ms",
        f"Delivered: {'yes' if result.ok else 'no'}",
    ])
    if result.error:
        lines.append(f"Error: {result.error}")
    lines.extend([
        "",
        "Mode: sendMessage only (no delivery_log / heartbeat / queue writes)",
        "",
        "— message sent —",
        "",
        message,
    ])
    return (0 if result.ok else 1), "\n".join(lines)


def run_ai_test(
    conn: Any,
    *,
    send_telegram: bool = False,
    symbol: str = "SOL",
) -> tuple[int, str]:
    """Synthetic event → AI queue → analysis; optional Telegram AI note."""
    from bot.research.market_events.ai_analyst import config as ai_cfg
    from bot.research.market_events.ai_analyst import job_queue as ai_jobs
    from bot.research.market_events.ai_analyst.job_queue import (
        JOB_COMPLETE,
        enqueue_analysis_job,
        process_pending_jobs,
    )
    from bot.research.market_events.ai_analyst.provider import DeterministicShadowProvider
    from bot.research.market_events.telegram_ops.synthetic_event import create_synthetic_shock_event
    import bot.research.market_events.market_event_alerts as alerts_mod

    event_id = create_synthetic_shock_event(conn, symbol=symbol, run_tag="ai-test")
    orig_ai, orig_jobs = ai_cfg.AI_ENABLED, ai_jobs.AI_ENABLED
    ai_cfg.AI_ENABLED = True
    ai_jobs.AI_ENABLED = True
    lines: list[str] = ["AI TEST", "", f"event_id: {event_id}"]

    try:
        job_id = enqueue_analysis_job(conn, event_id=event_id)
        lines.append(f"job_id: {job_id}")

        patches = [
            patch(
                "bot.research.market_events.ai_analyst.analysis_runner.get_analyst_provider",
                return_value=DeterministicShadowProvider(),
            ),
        ]
        if send_telegram:
            patches.extend([
                patch.object(alerts_mod, "alert_ai_commentary_enabled", return_value=True),
                patch.object(alerts_mod, "alerts_enabled", return_value=True),
            ])
        else:
            patches.append(
                patch(
                    "bot.research.market_events.market_event_alerts._send_telegram",
                    return_value=(True, None),
                ),
            )

        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            processed = process_pending_jobs(conn, max_jobs=1)

        lines.append(f"jobs processed: {processed}")

        job = conn.execute(
            "SELECT status FROM market_event_analysis_jobs WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        analysis = conn.execute(
            """
            SELECT movement_interpretation, reversal_bias, confidence
            FROM market_event_ai_analyses WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()

        ok = job and job["status"] == JOB_COMPLETE and analysis is not None
        if analysis:
            lines.extend([
                f"interpretation: {analysis['movement_interpretation']}",
                f"reversal_bias: {analysis['reversal_bias']}",
                f"confidence: {analysis['confidence']}",
            ])
        if send_telegram:
            from bot.research.market_events.alert_engine.telegram_delivery import format_delivery_status
            lines.append(format_delivery_status(
                conn, event_id=event_id, alert_type=ALERT_AI, label="telegram AI note",
            ))
        lines.append(f"status: {'OK' if ok else 'FAIL'}")
        return (0 if ok else 1), "\n".join(lines)
    finally:
        ai_cfg.AI_ENABLED = orig_ai
        ai_jobs.AI_ENABLED = orig_jobs


def run_demo_event(conn: Any, *, force_g2: bool = False) -> tuple[int, str]:
    """Full deterministic pipeline without exchange or paper orders."""
    from bot.research.market_events.alert_engine.opportunity_score import compute_opportunity_score
    from bot.research.market_events.alert_engine.scheduler import on_shock_detected
    from bot.research.market_events.alert_engine.telegram_delivery import format_delivery_status
    from bot.research.market_events.alert_engine.timeline import build_event_timeline
    from bot.research.market_events.market_event_alerts import ALERT_SHOCK, alert_shock_detected
    from bot.research.market_events.signal_intelligence.hooks import on_shock_f0
    from bot.research.market_events.telegram_ops.synthetic_event import (
        create_synthetic_shock_event,
        seed_demo_candles,
    )
    import bot.research.market_events.market_event_alerts as alerts_mod

    symbol = "SOL"
    lines = ["DEMO EVENT — deterministic pipeline", ""]
    if force_g2:
        lines.append("Mode: force-g2 (Claude research pipeline)")
        lines.append("")
    event_id = create_synthetic_shock_event(conn, symbol=symbol, run_tag="demo-event")
    seed_demo_candles(conn, symbol=symbol)
    lines.append(f"1. Synthetic shock persisted  event_id={event_id}")

    if force_g2:
        with patch.object(alerts_mod, "alerts_enabled", return_value=True):
            with patch.object(alerts_mod, "alert_shock_enabled", return_value=True):
                with patch(
                    "bot.research.market_events.signal_intelligence.config.TREND_SHOCK_DEFER_ALERT",
                    False,
                ):
                    with patch(
                        "bot.research.market_events.signal_intelligence.config.F5_MIN_TELEGRAM_CONFIDENCE",
                        0.0,
                    ):
                        with patch(
                            "bot.research.market_events.signal_intelligence.config.F7_MIN_FINAL_CONFIDENCE",
                            0.0,
                        ):
                            with patch(
                                "bot.research.market_events.signal_intelligence.config.F7_MIN_MARKET_SCORE",
                                0,
                            ):
                                on_shock_f0(conn, event_id=event_id, force_g2=True)

        g2 = conn.execute(
            """
            SELECT provider, input_tokens, output_tokens, cost_usd, ai_status, telegram_block
            FROM market_events_ai_research_g2 WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
        usage = conn.execute(
            "SELECT value FROM market_events_g2_ops_state WHERE key = 'requests_today'",
        ).fetchone()
        lines.append("2. F0→G1→F5→F7→G2 pipeline     OK")
        from bot.research.market_events.signal_intelligence.research_g2 import _f7_trace_status
        f7_label, f7_parts = _f7_trace_status(conn, event_id)
        if f7_label.startswith("F7 skipped"):
            lines.append(f"   F7 skipped — {f7_parts[0] if f7_parts else 'unknown'}")
        else:
            score_line = next((p for p in f7_parts if p.startswith("score")), None)
            lines.append(f"   F7 completed — {score_line or 'score available'}")
        if force_g2:
            lines.append("   force-g2: bypasses G2 eligibility filters (F5/F7/G1 thresholds)")
        if g2:
            called = g2["provider"] == "anthropic"
            total_tok = int(g2["input_tokens"] or 0) + int(g2["output_tokens"] or 0)
            lines.extend([
                f"3. Claude called              {'yes' if called else 'no (deterministic fallback)'}",
                f"4. Tokens                     {total_tok}",
                f"5. Cost                       ${float(g2['cost_usd'] or 0):.4f}",
                f"6. Saved to g2 table          yes (status={g2['ai_status']})",
                f"7. Requests today             {usage['value'] if usage else '0'}",
            ])
            has_research = False
            preview: str | None = None
            try:
                has_research = _has_research_agent_telegram(
                    conn, event_id, g2_telegram_block=g2["telegram_block"],
                )
                preview = _try_alert_message_text(conn, event_id)
            except Exception:
                has_research = "🧠 Research Agent" in (g2["telegram_block"] or "")
            lines.append(
                f"8. Telegram Research Agent    {'yes' if has_research else 'no'}"
                + ("" if preview is not None else " (preview unavailable)"),
            )
        else:
            lines.append("3. G2 research                NOT SAVED")

        from bot.research.market_events.signal_intelligence.research_g2 import format_g2_trace
        lines.extend(["", format_g2_trace(conn, event_id), PAPER_LABEL])
        ok = bool(g2)
        return (0 if ok else 1), "\n".join(lines)

    from bot.research.market_events.ai_analyst import config as ai_cfg
    from bot.research.market_events.ai_analyst import job_queue as ai_jobs
    from bot.research.market_events.ai_analyst.job_queue import enqueue_analysis_job, process_pending_jobs
    from bot.research.market_events.ai_analyst.provider import DeterministicShadowProvider
    from bot.research.market_events.market_event_alerts import alert_shock_detected

    orig_ai, orig_jobs = ai_cfg.AI_ENABLED, ai_jobs.AI_ENABLED
    ai_cfg.AI_ENABLED = True
    ai_jobs.AI_ENABLED = True

    try:
        with patch(
            "bot.research.market_events.signal_intelligence.config.TREND_SHOCK_DEFER_ALERT",
            False,
        ):
            with patch(
                "bot.research.market_events.signal_intelligence.config.F5_ENABLED",
                False,
            ):
                with patch.object(alerts_mod, "alerts_enabled", return_value=True):
                    with patch.object(alerts_mod, "alert_shock_enabled", return_value=True):
                        alert_shock_detected(conn, event_id)
        lines.append(format_delivery_status(
            conn,
            event_id=event_id,
            alert_type=ALERT_SHOCK,
            label="2. Telegram shock alert      ",
        ))

        on_shock_detected(conn, event_id=event_id)
        opp = compute_opportunity_score(conn, event_id=event_id)
        timeline = build_event_timeline(conn, event_id=event_id)
        lines.append(f"3. Opportunity score          {opp.get('score', '—')}")
        lines.append(f"4. Timeline entries           {len(timeline)}")

        job_id = enqueue_analysis_job(conn, event_id=event_id)
        lines.append(f"5. AI job enqueued            job_id={job_id}")

        from bot.research.market_events.signal_intelligence.config import F41_TELEGRAM_DEDUPE

        with patch.object(alerts_mod, "alerts_enabled", return_value=True):
            if F41_TELEGRAM_DEDUPE:
                provider_ctx = patch(
                    "bot.research.market_events.ai_analyst.analysis_runner.get_analyst_provider",
                    return_value=DeterministicShadowProvider(),
                )
                with provider_ctx:
                    processed = process_pending_jobs(conn, max_jobs=1)
            else:
                with patch.object(alerts_mod, "alert_ai_commentary_enabled", return_value=True):
                    with patch(
                        "bot.research.market_events.ai_analyst.analysis_runner.get_analyst_provider",
                        return_value=DeterministicShadowProvider(),
                    ):
                        processed = process_pending_jobs(conn, max_jobs=1)

        ai_row = conn.execute(
            "SELECT id FROM market_event_ai_analyses WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        lines.append(f"6. AI analysis stored         {'yes' if ai_row else 'no'} (processed={processed})")
        if F41_TELEGRAM_DEDUPE:
            lines.append("7. Telegram AI note          merged into shock (F.4.1)")
        else:
            lines.append(format_delivery_status(
                conn,
                event_id=event_id,
                alert_type=ALERT_AI,
                label="7. Telegram AI note          ",
            ))

        shock_row = conn.execute(
            """
            SELECT status, http_code FROM market_event_telegram_delivery_log
            WHERE event_id = ? AND alert_type = ?
            ORDER BY id DESC LIMIT 1
            """,
            (event_id, ALERT_SHOCK),
        ).fetchone()
        shock_ok = bool(
            shock_row and shock_row["status"] == "sent" and shock_row["http_code"] == 200,
        )
        steps_ok = bool(ai_row) and processed >= 1 and shock_ok
        lines.extend(["", f"Pipeline: {'OK' if steps_ok else 'PARTIAL'}", PAPER_LABEL])
        return (0 if steps_ok else 1), "\n".join(lines)
    finally:
        ai_cfg.AI_ENABLED = orig_ai
        ai_jobs.AI_ENABLED = orig_jobs


def run_telegram_health(conn: Any) -> str:
    """Operational Telegram + queue health report."""
    from bot.research.futures_agent.telegram_config import get_telegram_bot_token
    from bot.research.market_events.telegram_ops.config_report import fetch_bot_info

    resolution = resolve_alert_chat_id()
    token = get_telegram_bot_token()
    bot_info = fetch_bot_info() if token else None

    lines = [
        "TELEGRAM HEALTH",
        "",
        f"Bot Token: {'configured' if token else 'missing'}",
        f"Resolved Chat ID: {resolution.chat_id if resolution.chat_id else 'not resolved'}",
        f"Configuration source: {resolution.source or resolution.error or 'none'}",
    ]
    if bot_info:
        lines.append(f"Bot username: @{bot_info.get('username', '—')}")
        lines.append(f"Bot ID: {bot_info.get('id', '—')}")

    last_ok = conn.execute(
        """
        SELECT created_at, latency_ms, telegram_message_id, alert_type
        FROM market_event_telegram_delivery_log
        WHERE status = 'sent'
        ORDER BY created_at DESC LIMIT 1
        """,
    ).fetchone()
    if last_ok:
        age = int(time.time()) - int(last_ok["created_at"])
        lines.append(
            f"Last successful delivery: {_format_age(age)} ago "
            f"({last_ok['alert_type']}, msg_id={last_ok['telegram_message_id']}, "
            f"{last_ok['latency_ms']:.0f} ms)"
        )
    else:
        lines.append("Last successful delivery: never")

    last_fail = conn.execute(
        """
        SELECT created_at, alert_type, error, http_code
        FROM market_event_telegram_delivery_log
        WHERE status = 'failed'
        ORDER BY created_at DESC LIMIT 1
        """,
    ).fetchone()
    if last_fail:
        age = int(time.time()) - int(last_fail["created_at"])
        reason = last_fail["error"] or f"http_{last_fail['http_code']}"
        lines.append(
            f"Last failed delivery: {_format_age(age)} ago "
            f"({last_fail['alert_type']}, {reason})",
        )
    else:
        lines.append("Last failed delivery: none")

    failed = conn.execute(
        "SELECT COUNT(*) AS n FROM market_event_telegram_delivery_log WHERE status = 'failed'",
    ).fetchone()
    lines.append(f"Failed sends (total): {int(failed['n'] if failed else 0)}")

    avg = conn.execute(
        "SELECT AVG(latency_ms) AS avg_ms FROM market_event_telegram_delivery_log WHERE status = 'sent'",
    ).fetchone()
    avg_ms = avg["avg_ms"] if avg and avg["avg_ms"] is not None else None
    lines.append(f"Average latency: {avg_ms:.0f} ms" if avg_ms is not None else "Average latency: —")

    pending_alerts = conn.execute(
        "SELECT COUNT(*) AS n FROM market_event_alert_log WHERE sent = 0",
    ).fetchone()
    lines.append(f"Alert queue (unsent log): {int(pending_alerts['n'] if pending_alerts else 0)}")

    ai_pending = conn.execute(
        "SELECT COUNT(*) AS n FROM market_event_analysis_jobs WHERE status = 'pending'",
    ).fetchone()
    lines.append(f"AI queue (pending): {int(ai_pending['n'] if ai_pending else 0)}")

    retryable_codes = ",".join(str(c) for c in (429, 500, 502, 503, 504))
    retry_q = conn.execute(
        f"""
        SELECT COUNT(*) AS n FROM market_event_telegram_delivery_log
        WHERE status = 'failed'
          AND (http_code IN ({retryable_codes}) OR error LIKE 'timeout:%')
        """,
    ).fetchone()
    lines.append(f"Retry queue (retryable failures): {int(retry_q['n'] if retry_q else 0)}")

    recent = conn.execute(
        """
        SELECT alert_type, status, http_code, latency_ms, attempt, created_at
        FROM market_event_telegram_delivery_log
        ORDER BY created_at DESC LIMIT 5
        """,
    ).fetchall()
    if recent:
        lines.append("")
        lines.append("Recent deliveries:")
        for r in recent:
            lines.append(
                f"  {r['alert_type']} {r['status']} http={r['http_code']} "
                f"{r['latency_ms']:.0f}ms attempt={r['attempt']}"
            )
    return "\n".join(lines)


def _format_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"
