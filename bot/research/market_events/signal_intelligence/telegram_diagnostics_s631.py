"""S63.1 — Telegram Diagnostics (read-only).

Find why Telegram reports stopped updating. Diagnostics only — no automatic fixes.
Writes research/reports/system/telegram_health.md (+ .json).
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HOUR = 3600


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "bot" / "research" / "market_events" / "__main__.py").exists():
            return p
    return Path.cwd()


def system_report_dir(root: Path | None = None) -> Path:
    return (root or _repo_root()) / "research" / "reports" / "system"


def _fmt_ts(ts: Any) -> str:
    if ts is None:
        return "—"
    try:
        iv = int(ts)
        return datetime.fromtimestamp(iv, tz=timezone.utc).isoformat()
    except Exception:
        return str(ts)


def _safe_section(name: str, fn) -> dict[str, Any]:
    try:
        data = fn()
        if isinstance(data, dict):
            data.setdefault("ok", True)
            return data
        return {"ok": True, "value": data}
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc)[:400],
            "traceback": traceback.format_exc()[-2500:],
            "probable_reason": f"{name} probe failed: {type(exc).__name__}",
        }


def _check_collector() -> dict[str, Any]:
    from bot.ops.process_utils import assess_telegram_lock, find_telegram_poll_processes, telegram_lock_path
    from bot.research.futures_agent.telegram_intake_f52 import (
        is_poll_running,
        load_poll_stats,
    )

    procs = find_telegram_poll_processes()
    running_lock = is_poll_running()
    running_ps = bool(procs)
    running = running_lock or running_ps
    stats = load_poll_stats()
    out: dict[str, Any] = {
        "ok": running,
        "collector_running": running,
        "lock_held": running_lock,
        "process_found": running_ps,
        "pid": procs[0].pid if procs else None,
        "command": (procs[0].command[:140] if procs else None),
        "lock_status": assess_telegram_lock(),
        "lock_path": str(telegram_lock_path()),
        "poll_stats": {
            "started_at": stats.started_at,
            "received": stats.received,
            "processed": stats.processed,
            "ignored": stats.ignored,
            "parser_errors": stats.parser_errors,
            "last_message_at": stats.last_message_at,
            "avg_processing_ms": stats.avg_processing_ms,
        },
    }
    if not running:
        out["probable_reason"] = (
            "Telegram poll process is not running (no lock holder / no matching PID). "
            "Morning Telegram reports cannot update without the collector."
        )
        out["recommended_fix"] = (
            "Start collector: `python -m bot.research.market_events start-all` "
            "or `python -m bot.research.futures_agent telegram-poll`. "
            "Then re-run telegram-health-report."
        )
    return out


def _check_authorization() -> dict[str, Any]:
    from bot.research.futures_agent.telegram_config import (
        get_allowed_chat_ids,
        get_telegram_bot_token,
    )
    from bot.research.futures_agent.telegram_inbound import check_telegram_connected, run_diagnose

    token = get_telegram_bot_token()
    chats = sorted(get_allowed_chat_ids())
    connected = False
    connect_error = None
    connect_tb = None
    if token:
        try:
            connected = bool(check_telegram_connected(token))
        except Exception as exc:
            connect_error = str(exc)[:300]
            connect_tb = traceback.format_exc()[-2000:]
    diagnose = {}
    try:
        diagnose = run_diagnose()
    except Exception as exc:
        diagnose = {"error": str(exc)[:300], "traceback": traceback.format_exc()[-1500:]}

    missing_perms: list[str] = []
    if not token:
        missing_perms.append("TELEGRAM_BOT_TOKEN not set")
    if not chats:
        missing_perms.append("TELEGRAM_AGENT_ALLOWED_CHAT_IDS empty (no inbound chats allowed)")
    if token and not connected:
        missing_perms.append("Bot token present but getMe/API connection failed")

    ok = bool(token) and connected and bool(chats)
    out: dict[str, Any] = {
        "ok": ok,
        "token_configured": bool(token),
        "telegram_connected": connected,
        "allowed_chat_ids": chats,
        "allowed_chats_count": len(chats),
        "diagnose": diagnose,
        "missing_permissions": missing_perms,
        "connect_error": connect_error,
        "traceback": connect_tb,
    }
    if not ok:
        if not token:
            out["probable_reason"] = "Bot token missing — Telegram API authorization impossible"
            out["recommended_fix"] = "Set TELEGRAM_BOT_TOKEN in env / .env and restart telegram-poll."
        elif not connected:
            out["probable_reason"] = connect_error or "Telegram API getMe failed (invalid token, network, or ban)"
            out["recommended_fix"] = (
                "Verify token with BotFather; check network; ensure webhook is deleted if polling. "
                "See diagnose.polling_conflict_risk."
            )
        elif not chats:
            out["probable_reason"] = "No allowed chat IDs — collector ignores all inbound dialogs"
            out["recommended_fix"] = (
                "Set TELEGRAM_AGENT_ALLOWED_CHAT_IDS to the channel/group chat IDs that should be collected."
            )
    # Conflict risk from diagnose
    risk = (diagnose or {}).get("polling_conflict_risk")
    if risk == "high_webhook_configured":
        out["ok"] = False
        out["probable_reason"] = "Webhook is active — getUpdates polling will conflict / receive no updates"
        out["recommended_fix"] = (
            "Delete webhook for this bot token before polling "
            "(Bot API deleteWebhook), then restart telegram-poll."
        )
        missing_perms.append("Webhook conflicts with polling mode")
        out["missing_permissions"] = missing_perms
    return out


def _check_dialogs_channels(auth: dict[str, Any]) -> dict[str, Any]:
    """No Telethon dialog listing — report configured allowlist + optional S44 sources."""
    channels_yaml: list[str] = []
    try:
        from bot.research.market_events.signal_intelligence.multi_source.config_loader import (
            enabled_sources,
        )
        for src in enabled_sources("telegram_sources") or []:
            name = src.get("id") or src.get("channel") or src.get("name")
            if name:
                channels_yaml.append(str(name))
    except Exception as exc:
        return {
            "ok": bool(auth.get("allowed_chat_ids")),
            "dialogs_api": "not_available",
            "note": "Bot stack has no dialog enumeration API (config allowlist only)",
            "allowed_chat_ids": auth.get("allowed_chat_ids") or [],
            "s44_channels_error": str(exc)[:200],
        }

    chats = auth.get("allowed_chat_ids") or []
    ok = bool(chats) or bool(channels_yaml)
    out: dict[str, Any] = {
        "ok": ok,
        "dialogs_api": "not_available",
        "note": (
            "Collector uses Bot API getUpdates + TELEGRAM_AGENT_ALLOWED_CHAT_IDS. "
            "There is no Telethon dialog listing."
        ),
        "dialogs_available": len(chats),
        "allowed_chat_ids": chats,
        "channels_available_s44": channels_yaml,
        "channels_available_count": len(channels_yaml),
    }
    if not ok:
        out["probable_reason"] = "No inbound chats and no S44 telegram sources configured"
        out["recommended_fix"] = (
            "Configure TELEGRAM_AGENT_ALLOWED_CHAT_IDS and/or config/telegram_sources.yaml."
        )
    return out


def _check_messages(*, now: int) -> dict[str, Any]:
    from bot.research.futures_agent.db import agent_connection
    from bot.research.futures_agent.telegram_inbound import _load_last_message, _load_offset

    hour_ago = now - HOUR
    day_ago = now - 24 * HOUR
    out: dict[str, Any] = {
        "ok": True,
        "last_offset": None,
        "last_message_file": None,
        "last_message_id": None,
        "last_successful_collection_ts": None,
        "messages_last_hour": 0,
        "messages_last_24h": 0,
        "messages_per_hour": 0.0,
        "db_errors": [],
    }
    try:
        out["last_offset"] = _load_offset()
    except Exception as exc:
        out["db_errors"].append(f"offset: {exc}")
    try:
        last = _load_last_message()
        out["last_message_file"] = last
        if last:
            out["last_message_id"] = last.get("telegram_message_id") or last.get("message_id")
            out["last_successful_collection_ts"] = last.get("received_at") or last.get("date")
    except Exception as exc:
        out["db_errors"].append(f"last_message_file: {exc}")

    try:
        with agent_connection() as conn:
            row = conn.execute(
                """
                SELECT telegram_message_id, received_at, source, processing_status
                FROM futures_agent_inputs
                WHERE source LIKE 'telegram:%'
                ORDER BY received_at DESC LIMIT 1
                """,
            ).fetchone()
            if row:
                out["last_message_id"] = out.get("last_message_id") or row["telegram_message_id"]
                out["last_successful_collection_ts"] = (
                    out.get("last_successful_collection_ts") or row["received_at"]
                )
                out["last_db_row"] = dict(row)
            n1 = conn.execute(
                """
                SELECT COUNT(*) AS n FROM futures_agent_inputs
                WHERE source LIKE 'telegram:%' AND received_at >= ?
                """,
                (hour_ago,),
            ).fetchone()["n"]
            n24 = conn.execute(
                """
                SELECT COUNT(*) AS n FROM futures_agent_inputs
                WHERE source LIKE 'telegram:%' AND received_at >= ?
                """,
                (day_ago,),
            ).fetchone()["n"]
            out["messages_last_hour"] = int(n1 or 0)
            out["messages_last_24h"] = int(n24 or 0)
            out["messages_per_hour"] = round(float(n24 or 0) / 24.0, 2)
    except Exception as exc:
        out["ok"] = False
        out["error"] = str(exc)[:300]
        out["traceback"] = traceback.format_exc()[-2000:]
        out["probable_reason"] = "Cannot read futures_agent_inputs (agent DB unavailable/migrations)"
        out["recommended_fix"] = (
            "Check futures_agent DB config (AGENT_DATABASE_URL / sqlite path). "
            "Run futures_agent migrate if schema missing."
        )
        return out

    # Stale collection?
    last_ts = out.get("last_successful_collection_ts")
    if last_ts:
        try:
            age = now - int(last_ts)
            out["seconds_since_last_collection"] = age
            if age > 6 * HOUR and out["messages_last_hour"] == 0:
                out["ok"] = False
                out["probable_reason"] = (
                    f"No Telegram messages for {age // 3600}h "
                    f"(last_collection={_fmt_ts(last_ts)})"
                )
                out["recommended_fix"] = (
                    "Confirm collector running + authorization + allowed chats. "
                    "Send a test message in an allowed chat and watch me-telegram.log."
                )
        except Exception:
            pass
    elif out["messages_last_24h"] == 0:
        out["ok"] = False
        out["probable_reason"] = "No telegram:% rows in futures_agent_inputs (never collected or wrong DB)"
        out["recommended_fix"] = "Start telegram-poll with valid token/chats; verify agent DB path."
    return out


def _check_summarizer(*, now: int) -> dict[str, Any]:
    from bot.research.market_events.db import market_events_readonly_connection

    out: dict[str, Any] = {
        "ok": True,
        "last_summary_id": None,
        "last_summary_ts": None,
        "summaries_last_24h": 0,
        "summarizer": "market_events_signal_reports_f2.ai_summary_v2_ru",
    }
    try:
        with market_events_readonly_connection() as conn:
            try:
                row = conn.execute(
                    """
                    SELECT id, created_at
                    FROM market_events_signal_reports_f2
                    WHERE ai_summary_v2_ru IS NOT NULL AND ai_summary_v2_ru != ''
                    ORDER BY created_at DESC LIMIT 1
                    """,
                ).fetchone()
                if row:
                    out["last_summary_id"] = row["id"]
                    out["last_summary_ts"] = row["created_at"]
                n = conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM market_events_signal_reports_f2
                    WHERE created_at >= ?
                      AND ai_summary_v2_ru IS NOT NULL AND ai_summary_v2_ru != ''
                    """,
                    (now - 24 * HOUR,),
                ).fetchone()["n"]
                out["summaries_last_24h"] = int(n or 0)
            except Exception as exc:
                out["ok"] = False
                out["error"] = str(exc)[:300]
                out["traceback"] = traceback.format_exc()[-1500:]
                out["probable_reason"] = "signal_reports_f2 missing or unreadable"
                out["recommended_fix"] = "Ensure market_events schema includes F2 signal reports; run market-event-migrate."
                return out
    except Exception as exc:
        out["ok"] = False
        out["error"] = str(exc)[:300]
        out["traceback"] = traceback.format_exc()[-1500:]
        out["probable_reason"] = "market_events DB readonly connection failed"
        out["recommended_fix"] = "Fix MARKET_EVENTS_DATABASE_PATH / DB availability."
        return out

    if out["last_summary_id"] is None:
        out["ok"] = False
        out["probable_reason"] = "No AI summaries found in market_events_signal_reports_f2"
        out["recommended_fix"] = (
            "AI summarizer (analyze_ai_v2 / signal_report_f2) has not produced summaries. "
            "Check ai-worker-run and Claude/API keys — diagnostics only, no auto-start."
        )
    elif out["summaries_last_24h"] == 0:
        out["ok"] = False
        out["probable_reason"] = (
            f"Last summary is stale (id={out['last_summary_id']}, ts={_fmt_ts(out['last_summary_ts'])})"
        )
        out["recommended_fix"] = "Check AI worker / analysis jobs queue; inspect pending analysis jobs."
    return out


def _check_database_inserts(*, now: int) -> dict[str, Any]:
    from bot.research.market_events.db import market_events_readonly_connection

    out: dict[str, Any] = {
        "ok": True,
        "inbound_traces_24h": None,
        "inbound_errors_24h": None,
        "delivery_sent_24h": None,
        "delivery_failed_24h": None,
        "state": {},
        "errors": [],
        "exceptions": [],
    }
    since = now - 24 * HOUR
    try:
        with market_events_readonly_connection() as conn:
            probes = [
                (
                    "inbound_traces_24h",
                    "SELECT COUNT(*) AS n FROM market_events_inbound_trace_g04 WHERE created_at >= ?",
                    (since,),
                ),
                (
                    "inbound_errors_24h",
                    "SELECT COUNT(*) AS n FROM market_events_inbound_trace_g04 "
                    "WHERE created_at >= ? AND error IS NOT NULL AND error != ''",
                    (since,),
                ),
                (
                    "delivery_sent_24h",
                    "SELECT COUNT(*) AS n FROM market_event_telegram_delivery_log "
                    "WHERE status='sent' AND created_at >= ?",
                    (since,),
                ),
                (
                    "delivery_failed_24h",
                    "SELECT COUNT(*) AS n FROM market_event_telegram_delivery_log "
                    "WHERE status='failed' AND created_at >= ?",
                    (since,),
                ),
            ]
            for key, sql, params in probes:
                try:
                    out[key] = int(conn.execute(sql, params).fetchone()["n"] or 0)
                except Exception as exc:
                    out["errors"].append(f"{key}: {exc}")
                    out["exceptions"].append(traceback.format_exc()[-800:])

            # Recent error samples
            try:
                rows = conn.execute(
                    """
                    SELECT created_at, command, error FROM market_events_inbound_trace_g04
                    WHERE error IS NOT NULL AND error != ''
                    ORDER BY created_at DESC LIMIT 5
                    """,
                ).fetchall()
                out["recent_inbound_errors"] = [dict(r) for r in rows]
            except Exception as exc:
                out["errors"].append(f"recent_inbound_errors: {exc}")

            try:
                rows = conn.execute(
                    """
                    SELECT created_at, alert_type, error, http_code
                    FROM market_event_telegram_delivery_log
                    WHERE status='failed'
                    ORDER BY created_at DESC LIMIT 5
                    """,
                ).fetchall()
                out["recent_delivery_failures"] = [dict(r) for r in rows]
            except Exception as exc:
                out["errors"].append(f"recent_delivery_failures: {exc}")

            out["state"] = {
                "inbound_traces_24h": out.get("inbound_traces_24h"),
                "inbound_errors_24h": out.get("inbound_errors_24h"),
                "delivery_sent_24h": out.get("delivery_sent_24h"),
                "delivery_failed_24h": out.get("delivery_failed_24h"),
            }
    except Exception as exc:
        out["ok"] = False
        out["error"] = str(exc)[:300]
        out["traceback"] = traceback.format_exc()[-2000:]
        out["probable_reason"] = "market_events DB probe failed"
        out["recommended_fix"] = "Verify market_events SQLite/Postgres is readable."
        return out

    if out.get("delivery_failed_24h") and int(out["delivery_failed_24h"] or 0) > 0 and not out.get("delivery_sent_24h"):
        out["ok"] = False
        out["probable_reason"] = "All recent Telegram deliveries failed (0 sent, >0 failed)"
        out["recommended_fix"] = (
            "Inspect market_event_telegram_delivery_log errors (chat id / 401 / 403). "
            "Run telegram-config and telegram-health (ops) for delivery path."
        )
    return out


def _check_daily_report(root: Path) -> dict[str, Any]:
    morning = root / "research" / "reports" / "morning" / "latest.json"
    out: dict[str, Any] = {
        "ok": True,
        "morning_report_path": str(morning),
        "morning_report_exists": morning.exists(),
        "morning_report_mtime": None,
        "morning_telegram_status": None,
    }
    if not morning.exists():
        out["ok"] = False
        out["probable_reason"] = "Morning report not generated yet"
        out["recommended_fix"] = (
            "Run `python -m bot.research.market_events morning-report` "
            "(diagnostics only — not auto-run here)."
        )
        return out
    try:
        out["morning_report_mtime"] = int(morning.stat().st_mtime)
        data = json.loads(morning.read_text(encoding="utf-8"))
        tg = (data.get("telegram") or {}) if isinstance(data, dict) else {}
        out["morning_telegram_status"] = tg.get("collector_status")
        out["morning_telegram_ok"] = tg.get("ok")
        age = int(time.time()) - int(out["morning_report_mtime"])
        out["morning_report_age_sec"] = age
        if age > 36 * HOUR:
            out["ok"] = False
            out["probable_reason"] = f"Morning report stale ({age // 3600}h old)"
            out["recommended_fix"] = "Re-run morning-report after fixing collector."
        if tg.get("ok") is False:
            out["ok"] = False
            out["probable_reason"] = tg.get("probable_reason") or "Morning report flagged Telegram failure"
            out["recommended_fix"] = tg.get("probable_reason") or "Fix collector/auth then regenerate morning-report."
            out["traceback"] = tg.get("traceback")
            out["error"] = tg.get("error")
    except Exception as exc:
        out["ok"] = False
        out["error"] = str(exc)[:300]
        out["traceback"] = traceback.format_exc()[-1500:]
        out["probable_reason"] = "Cannot parse morning report JSON"
        out["recommended_fix"] = "Regenerate morning-report."
    return out


def _tail_log_errors(root: Path, *, limit: int = 15) -> dict[str, Any]:
    from bot.ops.process_utils import logs_dir

    log_path = logs_dir() / "me-telegram.log"
    errors: list[str] = []
    exceptions: list[str] = []
    if not log_path.exists():
        return {
            "ok": True,
            "log_path": str(log_path),
            "exists": False,
            "errors": [],
            "exceptions": [],
            "note": "me-telegram.log not found (collector may never have started)",
        }
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")[-30000:]
        lines = text.splitlines()
        buf: list[str] = []
        in_tb = False
        for line in lines:
            low = line.lower()
            if "traceback" in low:
                in_tb = True
                buf = [line]
                continue
            if in_tb:
                buf.append(line)
                if line.strip() and not line.startswith(" ") and "traceback" not in low:
                    exceptions.append("\n".join(buf[-40:]))
                    in_tb = False
                    buf = []
                continue
            if "error" in low or "exception" in low or "failed" in low:
                errors.append(line[:220])
        if in_tb and buf:
            exceptions.append("\n".join(buf[-40:]))
    except Exception as exc:
        return {"ok": False, "error": str(exc), "traceback": traceback.format_exc()[-1000:]}
    return {
        "ok": True,
        "log_path": str(log_path),
        "exists": True,
        "errors": errors[-limit:],
        "exceptions": exceptions[-5:],
    }


def _build_failures(sections: dict[str, Any]) -> list[dict[str, Any]]:
    fails: list[dict[str, Any]] = []
    for name, sec in sections.items():
        if not isinstance(sec, dict):
            continue
        if sec.get("ok") is False:
            fails.append({
                "section": name,
                "reason": sec.get("probable_reason") or sec.get("error") or "failed",
                "error": sec.get("error"),
                "traceback": sec.get("traceback"),
                "recommended_fix": sec.get("recommended_fix") or "Investigate section details below.",
            })
    return fails


def run_telegram_diagnostics(
    *,
    now: int | None = None,
    report_dir: Path | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    t0 = time.time()
    root = root or _repo_root()
    now = int(now if now is not None else time.time())

    collector = _safe_section("collector", _check_collector)
    auth = _safe_section("authorization", _check_authorization)
    dialogs = _safe_section("dialogs", lambda: _check_dialogs_channels(auth if auth.get("ok") is not False else auth))
    messages = _safe_section("messages", lambda: _check_messages(now=now))
    summarizer = _safe_section("summarizer", lambda: _check_summarizer(now=now))
    database = _safe_section("database", lambda: _check_database_inserts(now=now))
    daily = _safe_section("daily_report", lambda: _check_daily_report(root))
    logs = _safe_section("logs", lambda: _tail_log_errors(root))

    # Optional delivery health string (read-only conn)
    delivery_health_text = None
    try:
        from bot.research.market_events.db import market_events_readonly_connection
        from bot.research.market_events.telegram_ops.cli import run_telegram_health
        with market_events_readonly_connection() as conn:
            delivery_health_text = run_telegram_health(conn)
    except Exception as exc:
        delivery_health_text = f"(unavailable: {exc})"

    sections = {
        "collector": collector,
        "authorization": auth,
        "dialogs_channels": dialogs,
        "messages": messages,
        "summarizer": summarizer,
        "database_inserts": database,
        "daily_report": daily,
        "logs": logs,
    }
    failures = _build_failures(sections)
    overall_ok = len(failures) == 0

    report = {
        "ok": overall_ok,
        "stage": "S63.1",
        "generated_at": now,
        "generated_at_iso": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "collector_status": "running" if collector.get("collector_running") else "stopped",
        "last_successful_collection": messages.get("last_successful_collection_ts"),
        "last_message_id": messages.get("last_message_id"),
        "last_summary_id": summarizer.get("last_summary_id"),
        "messages_per_hour": messages.get("messages_per_hour"),
        "errors": [
            *(logs.get("errors") or []),
            *([e.get("reason") for e in failures]),
        ],
        "exceptions": logs.get("exceptions") or [],
        "missing_permissions": auth.get("missing_permissions") or [],
        "database_state": database.get("state") or {},
        "failures": failures,
        "sections": sections,
        "delivery_health_text": delivery_health_text,
        "elapsed_sec": round(time.time() - t0, 3),
    }

    out_dir = Path(report_dir) if report_dir else system_report_dir(root)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "telegram_health.md"
    json_path = out_dir / "telegram_health.json"
    md_path.write_text(format_telegram_health_markdown(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["export_paths"] = {
        "markdown": str(md_path.resolve()),
        "json": str(json_path.resolve()),
    }
    return report


def format_telegram_health_markdown(report: dict[str, Any]) -> str:
    s = report.get("sections") or {}
    col = s.get("collector") or {}
    auth = s.get("authorization") or {}
    dia = s.get("dialogs_channels") or {}
    msg = s.get("messages") or {}
    sm = s.get("summarizer") or {}
    db = s.get("database_inserts") or {}
    daily = s.get("daily_report") or {}
    logs = s.get("logs") or {}

    lines = [
        "# Telegram Health (S63.1)",
        "",
        f"_generated={report.get('generated_at_iso')} elapsed={report.get('elapsed_sec')}s "
        f"overall={'OK' if report.get('ok') else 'FAIL'}_",
        "",
        "Diagnostics only. No automatic fixes.",
        "",
        "## Collector status",
        "",
        f"- Status: **{report.get('collector_status')}**",
        f"- Lock held: {col.get('lock_held')}",
        f"- Process found: {col.get('process_found')} PID={col.get('pid') or '—'}",
        f"- Lock: {col.get('lock_status')} (`{col.get('lock_path')}`)",
        f"- Poll stats: received={((col.get('poll_stats') or {}).get('received'))} "
        f"processed={((col.get('poll_stats') or {}).get('processed'))} "
        f"ignored={((col.get('poll_stats') or {}).get('ignored'))} "
        f"parser_errors={((col.get('poll_stats') or {}).get('parser_errors'))}",
        "",
        "## Authorization",
        "",
        f"- Token configured: {auth.get('token_configured')}",
        f"- Telegram connected: {auth.get('telegram_connected')}",
        f"- Allowed chats: {auth.get('allowed_chats_count')} → {auth.get('allowed_chat_ids')}",
        f"- Missing permissions: {auth.get('missing_permissions') or '—'}",
        f"- Diagnose conflict risk: {(auth.get('diagnose') or {}).get('polling_conflict_risk', '—')}",
        "",
        "## Dialogs / Channels",
        "",
        f"- Dialogs API: {dia.get('dialogs_api')}",
        f"- Note: {dia.get('note')}",
        f"- Dialogs available (allowlist): {dia.get('dialogs_available')}",
        f"- S44 channels: {dia.get('channels_available_s44') or '—'}",
        "",
        "## New messages",
        "",
        f"- Last successful collection: **{_fmt_ts(report.get('last_successful_collection'))}**",
        f"- Last message id: **{report.get('last_message_id')}**",
        f"- Last offset: {msg.get('last_offset')}",
        f"- Messages last hour: {msg.get('messages_last_hour')}",
        f"- Messages last 24h: {msg.get('messages_last_24h')}",
        f"- Messages/hour (24h avg): **{report.get('messages_per_hour')}**",
        "",
        "## Summarizer",
        "",
        f"- Path: {sm.get('summarizer')}",
        f"- Last summary id: **{report.get('last_summary_id')}**",
        f"- Last summary ts: {_fmt_ts(sm.get('last_summary_ts'))}",
        f"- Summaries last 24h: {sm.get('summaries_last_24h')}",
        "",
        "## Database inserts / state",
        "",
        f"- Inbound traces 24h: {db.get('inbound_traces_24h')}",
        f"- Inbound errors 24h: {db.get('inbound_errors_24h')}",
        f"- Delivery sent 24h: {db.get('delivery_sent_24h')}",
        f"- Delivery failed 24h: {db.get('delivery_failed_24h')}",
        f"- State: `{json.dumps(report.get('database_state') or {}, default=str)}`",
        "",
        "## Daily report generation",
        "",
        f"- Morning report exists: {daily.get('morning_report_exists')}",
        f"- Path: `{daily.get('morning_report_path')}`",
        f"- Age sec: {daily.get('morning_report_age_sec')}",
        f"- Morning telegram status: {daily.get('morning_telegram_status')}",
        "",
        "## Errors",
        "",
    ]
    for e in report.get("errors") or []:
        lines.append(f"- {e}")
    if not (report.get("errors") or []):
        lines.append("- _none_")

    lines.extend(["", "## Exceptions", ""])
    for ex in report.get("exceptions") or []:
        lines.extend(["```", str(ex)[:2000], "```", ""])
    if not (report.get("exceptions") or []):
        lines.append("_none_")

    lines.extend(["", "## Failures (exact reason + recommended fix)", ""])
    for i, f in enumerate(report.get("failures") or [], 1):
        lines.extend([
            f"### {i}. {f.get('section')}",
            "",
            f"- Exact reason: **{f.get('reason')}**",
            f"- Error: {f.get('error') or '—'}",
            f"- Recommended fix: {f.get('recommended_fix')}",
            "",
        ])
        if f.get("traceback"):
            lines.extend(["```", str(f.get("traceback"))[-2000:], "```", ""])
    if not (report.get("failures") or []):
        lines.append("_No section failures._")

    lines.extend([
        "",
        "## Delivery health (ops)",
        "",
        "```",
        str(report.get("delivery_health_text") or "—")[:3000],
        "```",
        "",
        f"_Log: {(logs.get('log_path') or '—')}_",
        "",
        "---",
        "_Diagnostics only. No automatic fixes._",
        "",
    ])
    return "\n".join(lines)


def format_telegram_diagnostics_summary(report: dict[str, Any]) -> str:
    lines = [
        "S63.1 Telegram Diagnostics",
        f"  overall={'OK' if report.get('ok') else 'FAIL'} elapsed={report.get('elapsed_sec')}s",
        f"  collector={report.get('collector_status')} "
        f"last_msg={report.get('last_message_id')} "
        f"msg/h={report.get('messages_per_hour')} "
        f"last_summary={report.get('last_summary_id')}",
        f"  failures={len(report.get('failures') or [])} "
        f"missing_perms={report.get('missing_permissions')}",
    ]
    for f in (report.get("failures") or [])[:5]:
        lines.append(f"  - [{f.get('section')}] {f.get('reason')}")
    for k, p in (report.get("export_paths") or {}).items():
        lines.append(f"  {k}: {p}")
    return "\n".join(lines)


__all__ = [
    "format_telegram_diagnostics_summary",
    "format_telegram_health_markdown",
    "run_telegram_diagnostics",
    "system_report_dir",
]
