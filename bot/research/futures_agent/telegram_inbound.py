"""Telegram inbound adapter — Stage 1b observe-only signal ingestion."""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ReadTimeout

from bot.research.futures_agent.db import AgentDbError, agent_connection
from bot.research.futures_agent.env_bootstrap import project_root, resolve_agent_db_config
from bot.research.futures_agent.ingestion import ingest_from_telegram
from bot.research.futures_agent.pipeline import ProcessResult, process_input
from bot.research.futures_agent.responses import send_telegram_reply
from bot.research.futures_agent.schema import apply_migrations
from bot.research.futures_agent.snapshot import SnapshotResult, snapshot_signal
from bot.research.futures_agent.telegram_config import (
    get_allowed_chat_ids,
    get_telegram_bot_token,
    is_chat_allowed,
    require_telegram_inbound_config,
)
from bot.research.futures_agent.telegram_intake_f52 import (
    IGNORE_CHAT_NOT_ALLOWED,
    IGNORE_DATABASE_ERROR,
    IGNORE_DUPLICATE,
    IGNORE_EMPTY_MESSAGE,
    IGNORE_PARSER_REJECTED,
    IGNORE_SNAPSHOT_UNAVAILABLE,
    STATS_INTERVAL_SEC,
    PollSessionStats,
    check_telegram_connected,
    format_periodic_stats,
    format_poll_startup,
    log_ignored,
    save_poll_stats,
)
from bot.research.futures_agent.telegram_replies import (
    format_telegram_accepted,
    format_telegram_duplicate,
    format_telegram_rejected,
)

logger = logging.getLogger(__name__)

POLL_TIMEOUT_SEC = 30
API_BASE = "https://api.telegram.org/bot{token}/{method}"
OFFSET_FILE = "data/futures_agent_telegram_offset.json"
LOCK_FILE = "data/futures_agent_telegram_poll.lock"
CONN_BACKOFF_INITIAL_SEC = 1
CONN_BACKOFF_MAX_SEC = 60


@dataclass
class InboundResult:
    chat_id: int
    message_id: int
    reply_text: str | None
    skipped: bool = False
    unauthorized: bool = False
    processed: bool = False
    ignore_reason: str | None = None
    parser_failed: bool = False
    snapshot_failed: bool = False
    reply_sent: bool = False
    reply_failed: bool = False
    processing_ms: int | None = None


def extract_message_text(message: dict[str, Any]) -> str | None:
    """Plain text or forwarded message caption/text — preserved exactly."""
    if not message:
        return None
    text = message.get("text")
    if text is not None:
        return text
    caption = message.get("caption")
    if caption is not None:
        return caption
    return None


def extract_forward_origin(message: dict[str, Any]) -> dict[str, Any] | None:
    fo = message.get("forward_origin")
    if fo:
        return fo
    if message.get("forward_from_chat"):
        return {
            "type": "legacy",
            "chat": message.get("forward_from_chat"),
            "message_id": message.get("forward_from_message_id"),
        }
    if message.get("forward_from"):
        return {"type": "legacy_user", "from": message.get("forward_from")}
    return None


def process_telegram_message(
    message: dict[str, Any],
    *,
    db_url: str | None = None,
) -> InboundResult:
    """Full pipeline: ingest → parse → snapshot (if gated) → reply text."""
    t0 = time.perf_counter()
    chat = message.get("chat") or {}
    chat_id = int(chat.get("id", 0))
    message_id = int(message.get("message_id", 0))

    def _elapsed() -> int:
        return int((time.perf_counter() - t0) * 1000)

    if not is_chat_allowed(chat_id):
        logger.info("Rejected unauthorized chat_id=%s", chat_id)
        return InboundResult(
            chat_id, message_id, None,
            unauthorized=True, skipped=True,
            ignore_reason=IGNORE_CHAT_NOT_ALLOWED,
            processing_ms=_elapsed(),
        )

    text = extract_message_text(message)
    if not text or not text.strip():
        return InboundResult(
            chat_id, message_id, "Empty message ignored.",
            skipped=True, ignore_reason=IGNORE_EMPTY_MESSAGE,
            processing_ms=_elapsed(),
        )

    received_at = int(message.get("date", time.time()))
    forward_origin = extract_forward_origin(message)

    try:
        with agent_connection(db_url) as conn:
            apply_migrations(conn)
            ing = ingest_from_telegram(
                conn,
                raw_text=text,
                chat_id=chat_id,
                message_id=message_id,
                forward_origin=forward_origin,
                received_at=received_at,
            )
            from bot.research.futures_agent.telegram_inbound_bridge import bridge_input_to_research
            bridge_input_to_research(conn, ing.input_id)
            if ing.duplicate:
                return InboundResult(
                    chat_id, message_id, format_telegram_duplicate(),
                    skipped=True, ignore_reason=IGNORE_DUPLICATE,
                    processing_ms=_elapsed(),
                )
            proc = process_input(conn, ing.input_id)
            input_id = ing.input_id
            signal_id = proc.signal_id
            passes_gate = proc.passes_gate
            parser_failed = proc.parse_status == "FAILED"
    except AgentDbError as exc:
        logger.error("database error processing message_id=%s: %s", message_id, exc)
        return InboundResult(
            chat_id, message_id, None,
            skipped=True, ignore_reason=IGNORE_DATABASE_ERROR,
            processing_ms=_elapsed(),
        )
    except Exception as exc:
        logger.error("unexpected error processing message_id=%s: %s", message_id, exc)
        return InboundResult(
            chat_id, message_id, None,
            skipped=True, ignore_reason=IGNORE_DATABASE_ERROR,
            processing_ms=_elapsed(),
        )

    if not passes_gate:
        return InboundResult(
            chat_id, message_id, format_telegram_rejected(proc, raw_text=text),
            skipped=True, ignore_reason=IGNORE_PARSER_REJECTED,
            parser_failed=parser_failed or True,
            processing_ms=_elapsed(),
        )

    snap: SnapshotResult | None = None
    if signal_id:
        try:
            with agent_connection(db_url) as conn:
                apply_migrations(conn)
                snap = snapshot_signal(conn, signal_id)
        except Exception as exc:
            logger.error("snapshot error signal_id=%s: %s", signal_id, exc)
            return InboundResult(
                chat_id, message_id, None,
                skipped=True, ignore_reason=IGNORE_SNAPSHOT_UNAVAILABLE,
                snapshot_failed=True, processing_ms=_elapsed(),
            )
        if snap and not snap.success and not snap.skipped:
            return InboundResult(
                chat_id, message_id, None,
                skipped=True, ignore_reason=IGNORE_SNAPSHOT_UNAVAILABLE,
                snapshot_failed=True, processing_ms=_elapsed(),
            )

    try:
        with agent_connection(db_url) as conn:
            reply = format_telegram_accepted(
                conn, input_id=input_id, signal_id=signal_id or 0, snap=snap,
            )
    except Exception as exc:
        logger.error("reply format error input_id=%s: %s", input_id, exc)
        return InboundResult(
            chat_id, message_id, None,
            skipped=True, ignore_reason=IGNORE_DATABASE_ERROR,
            processing_ms=_elapsed(),
        )

    return InboundResult(
        chat_id, message_id, reply,
        processed=True, processing_ms=_elapsed(),
    )


def _apply_inbound_stats(result: InboundResult, stats: PollSessionStats | None) -> None:
    if stats is None:
        return
    stats.record_received()
    stats.touch_message()
    if result.processed:
        stats.record_processed(processing_ms=result.processing_ms)
    elif result.skipped or result.unauthorized:
        stats.record_ignored(
            parser_error=result.parser_failed,
            snapshot_failure=result.snapshot_failed,
        )
    if result.reply_sent:
        stats.record_reply(sent=True)
    elif result.reply_failed:
        stats.record_reply(sent=False)


def handle_update(
    update: dict[str, Any],
    *,
    db_url: str | None = None,
    stats: PollSessionStats | None = None,
) -> InboundResult | None:
    message = update.get("message") or update.get("edited_message")
    if not message:
        if stats is not None:
            stats.record_received()
            stats.record_ignored()
        log_ignored("non-message update", logger=logger)
        return None

    text = extract_message_text(message)
    if text and text.strip().startswith("/"):
        t0 = time.perf_counter()
        chat = message.get("chat") or {}
        chat_id = int(chat.get("id", 0))
        message_id = int(message.get("message_id", 0))

        from bot.research.market_events.signal_intelligence.telegram_inbound_g04 import (
            InboundTraceG04,
            persist_inbound_trace_g04,
        )
        from bot.research.market_events.sqlite_manager_g05 import PURE_READONLY_COMMANDS
        from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
            normalize_command,
            route_telegram_command,
        )

        cmd_name = normalize_command(text.strip())
        pure_ro = cmd_name in PURE_READONLY_COMMANDS
        # G0.5: pure RO commands never open write DB (no inbound trace INSERT).
        trace = InboundTraceG04(
            message_id=message_id,
            chat_id=chat_id,
            preview=text.strip().replace("\n", " ")[:80],
            received="OK",
            router="COMMAND",
            command=cmd_name,
        )
        trace.set_stage("Telegram Update", "OK")
        trace.set_stage("Router", "COMMAND")

        def _cmd_elapsed() -> int:
            return int((time.perf_counter() - t0) * 1000)

        if not is_chat_allowed(chat_id):
            trace.error = IGNORE_CHAT_NOT_ALLOWED
            trace.set_stage("Reply", "UNAUTHORIZED")
            if not pure_ro:
                persist_inbound_trace_g04(trace)
            result = InboundResult(
                chat_id, message_id, None,
                unauthorized=True, skipped=True,
                ignore_reason=IGNORE_CHAT_NOT_ALLOWED,
                processing_ms=_cmd_elapsed(),
            )
            _apply_inbound_stats(result, stats)
            log_ignored(result.ignore_reason, logger=logger)
            if stats is not None:
                save_poll_stats(stats)
            return result

        try:
            route = route_telegram_command(text.strip(), message_id=message_id, chat_id=chat_id)
            reply = route.reply_text if route else "Unknown command. Use /help."
            trace.command = route.command if route else "—"
            trace.parser = "SKIPPED"
            trace.taxonomy = "N/A"
            trace.signal_label = "N/A"
            trace.set_stage("Command", trace.command)
            trace.set_stage("Parser", "SKIPPED")
            trace.set_stage("Taxonomy", "N/A")
            trace.set_stage("Signal", "N/A")
            result = InboundResult(
                chat_id, message_id, reply,
                processed=True, processing_ms=_cmd_elapsed(),
            )
            _apply_inbound_stats(result, stats)
            sent = send_telegram_reply(result.chat_id, result.reply_text)
            result.reply_sent = sent
            result.reply_failed = not sent
            trace.reply = "OK" if sent else "FAIL"
            trace.set_stage("Reply", "OK" if sent else "FAIL")
            if not sent:
                trace.error = "reply_send_failed"
                logger.warning("command reply failed chat_id=%s message_id=%s", chat_id, message_id)
        except Exception as exc:
            trace.error = str(exc)
            trace.set_stage("Reply", f"ERROR: {exc}")
            result = InboundResult(
                chat_id, message_id, f"Command failed: {exc}",
                processed=True, processing_ms=_cmd_elapsed(),
            )
            _apply_inbound_stats(result, stats)
        if not pure_ro:
            persist_inbound_trace_g04(trace)
        if stats is not None:
            stats.record_reply(sent=bool(getattr(result, "reply_sent", False)))
        _record_last_message(chat_id, message_id)
        if stats is not None:
            save_poll_stats(stats)
        return result

    from bot.research.market_events.signal_intelligence.telegram_photo_g36 import is_image_message
    if is_image_message(message):
        t0 = time.perf_counter()
        chat = message.get("chat") or {}
        chat_id = int(chat.get("id", 0))
        message_id = int(message.get("message_id", 0))

        if not is_chat_allowed(chat_id):
            result = InboundResult(
                chat_id, message_id, None,
                unauthorized=True, skipped=True,
                ignore_reason=IGNORE_CHAT_NOT_ALLOWED,
                processing_ms=int((time.perf_counter() - t0) * 1000),
            )
            _apply_inbound_stats(result, stats)
            return result

        try:
            from bot.research.futures_agent.telegram_config import get_telegram_bot_token
            from bot.research.market_events.signal_intelligence.telegram_vision_g36 import (
                handle_telegram_photo_message,
            )
            reply = handle_telegram_photo_message(message, token=get_telegram_bot_token())
            result = InboundResult(
                chat_id, message_id, reply,
                processed=True,
                processing_ms=int((time.perf_counter() - t0) * 1000),
            )
        except Exception as exc:
            logger.warning("g36 vision pipeline failed: %s", exc)
            result = InboundResult(
                chat_id, message_id, f"Vision failed: {exc}",
                skipped=True, processing_ms=int((time.perf_counter() - t0) * 1000),
            )

        _apply_inbound_stats(result, stats)
        if result.reply_text:
            sent = send_telegram_reply(result.chat_id, result.reply_text)
            result.reply_sent = sent
            result.reply_failed = not sent
            if stats is not None:
                stats.record_reply(sent=sent)
        _record_last_message(chat_id, message_id)
        if stats is not None:
            save_poll_stats(stats)
        return result

    from bot.research.market_events.signal_intelligence.telegram_inbound_g04 import (
        InboundTraceG04,
        diagnose_signal_g04,
    )
    chat = message.get("chat") or {}
    chat_id_pre = int(chat.get("id", 0))
    message_id_pre = int(message.get("message_id", 0))
    raw_preview = (text or "").strip().replace("\n", " ")[:80]
    t0 = time.perf_counter()

    if not is_chat_allowed(chat_id_pre):
        result = InboundResult(
            chat_id_pre, message_id_pre, None,
            unauthorized=True, skipped=True,
            ignore_reason=IGNORE_CHAT_NOT_ALLOWED,
            processing_ms=int((time.perf_counter() - t0) * 1000),
        )
        _apply_inbound_stats(result, stats)
        log_ignored(result.ignore_reason, logger=logger)
        if stats is not None:
            save_poll_stats(stats)
        return result

    if not text or not text.strip():
        result = InboundResult(
            chat_id_pre, message_id_pre, "Empty message ignored.",
            skipped=True, ignore_reason=IGNORE_EMPTY_MESSAGE,
            processing_ms=int((time.perf_counter() - t0) * 1000),
        )
        _apply_inbound_stats(result, stats)
        if stats is not None:
            save_poll_stats(stats)
        return result

    # S2.3: Signal Inbox — always persist raw_text; Decision Engine READ ONLY.
    # Avoid heavy futures_agent write path (primary source of database is locked).
    try:
        from bot.research.market_events.signal_intelligence.signal_inbox_s23 import (
            process_telegram_signal_inbox_s23,
        )
        user = None
        frm = message.get("from") or {}
        if frm.get("username"):
            user = str(frm["username"])
        elif frm.get("id"):
            user = str(frm["id"])
        reply, _inbox_id = process_telegram_signal_inbox_s23(
            raw_text=text,
            chat_id=chat_id_pre,
            message_id=message_id_pre,
            telegram_user=user,
        )
        result = InboundResult(
            chat_id_pre, message_id_pre, reply,
            processed=True,
            processing_ms=int((time.perf_counter() - t0) * 1000),
        )
    except Exception as exc:
        logger.error("signal inbox failed message_id=%s: %s", message_id_pre, exc)
        result = InboundResult(
            chat_id_pre, message_id_pre, f"Signal inbox error: {exc}",
            skipped=True, ignore_reason=IGNORE_DATABASE_ERROR,
            processing_ms=int((time.perf_counter() - t0) * 1000),
        )

    _apply_inbound_stats(result, stats)
    if result.ignore_reason:
        log_ignored(result.ignore_reason, logger=logger)

    # Soft diagnose for logs only — no inbound_trace INSERT (reduces lock vs recorder).
    try:
        diag = diagnose_signal_g04(text or "")
        logger.info(
            "inbox signal message_id=%s label=%s symbol=%s",
            message_id_pre, diag.label, diag.symbol,
        )
    except Exception:
        pass

    if not result.unauthorized and result.reply_text:
        sent = send_telegram_reply(result.chat_id, result.reply_text)
        result.reply_sent = sent
        result.reply_failed = not sent
        if stats is not None:
            stats.record_reply(sent=sent)
        if not sent:
            logger.warning("reply failed chat_id=%s message_id=%s", result.chat_id, result.message_id)

    if result.processed or result.reply_text:
        _record_last_message(result.chat_id, result.message_id)

    if stats is not None:
        save_poll_stats(stats)

    return result


def poll_once(*, offset: int | None = None, db_url: str | None = None) -> int:
    """Fetch and process one batch. Returns committed offset."""
    token, _ = require_telegram_inbound_config()
    updates = _fetch_updates(token, offset=offset)
    return _commit_update_batch(updates, start_offset=offset or 0, db_url=db_url)


def _fetch_updates(token: str, *, offset: int | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"timeout": POLL_TIMEOUT_SEC, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    data = _api_call(token, "getUpdates", params, timeout=POLL_TIMEOUT_SEC + 10)
    return data.get("result", [])


def _commit_update_batch(
    updates: list[dict[str, Any]],
    *,
    start_offset: int,
    db_url: str | None = None,
    stats: PollSessionStats | None = None,
) -> int:
    """Process updates; persist offset only after each update succeeds."""
    committed_offset = start_offset
    for upd in updates:
        handle_update(upd, db_url=db_url, stats=stats)
        next_offset = int(upd["update_id"]) + 1
        if next_offset > committed_offset:
            committed_offset = next_offset
            _save_offset(committed_offset)
    return committed_offset


def run_poll_loop() -> None:
    """Long-polling loop with file lock (single local consumer)."""
    from bot.research.futures_agent.telegram_runtime_audit import (
        print_get_me_probe,
        print_polling_exit_traceback,
        print_pre_api_audit,
        print_telegram_startup_audit,
        probe_get_me,
    )

    print_telegram_startup_audit()
    token_preview = get_telegram_bot_token()
    print_pre_api_audit(token=token_preview)
    getme = probe_get_me(token_preview)
    print_get_me_probe(getme)

    try:
        require_telegram_inbound_config()
    except Exception as exc:
        print_polling_exit_traceback(exc)
        raise

    cfg = resolve_agent_db_config()
    token = get_telegram_bot_token()

    if cfg.is_postgres:
        try:
            with agent_connection():
                pass
        except AgentDbError as exc:
            print_polling_exit_traceback(exc)
            raise SystemExit(str(exc)) from exc

    telegram_ok = bool(getme.get("ok")) or check_telegram_connected(token)
    print(format_poll_startup(cfg, telegram_connected=telegram_ok))
    if telegram_ok:
        print("Polling started", flush=True)

    db_url = cfg.url
    try:
        _check_polling_conflicts(token)
    except Exception as exc:
        print_polling_exit_traceback(exc)
        raise

    stats = PollSessionStats()
    save_poll_stats(stats)

    lock_path = project_root() / LOCK_FILE
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        try:
            import fcntl
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            err = RuntimeError(
                "Another futures-agent telegram-poll process holds the lock. "
                "Only one getUpdates consumer per bot token."
            )
            print_polling_exit_traceback(err)
            raise err from exc

        signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)

        offset = _load_offset()
        backoff = CONN_BACKOFF_INITIAL_SEC
        connection_degraded = False
        last_stats_log = time.time()
        logger.info("Telegram poll started backend=%s offset=%s", cfg.backend, offset)
        try:
            while True:
                try:
                    token, _ = require_telegram_inbound_config()
                    updates = _fetch_updates(token, offset=offset or None)
                    offset = _commit_update_batch(
                        updates, start_offset=offset or 0, db_url=db_url, stats=stats,
                    )
                    if connection_degraded:
                        logger.info("polling recovered")
                        connection_degraded = False
                    backoff = CONN_BACKOFF_INITIAL_SEC
                except ReadTimeout:
                    logger.info("poll timeout, continuing")
                except RequestsConnectionError:
                    logger.warning("connection error, retry in %ss", backoff)
                    connection_degraded = True
                    time.sleep(backoff)
                    backoff = min(backoff * 2, CONN_BACKOFF_MAX_SEC)

                if time.time() - last_stats_log >= STATS_INTERVAL_SEC:
                    logger.info("\n%s", format_periodic_stats(stats))
                    save_poll_stats(stats)
                    last_stats_log = time.time()
        except KeyboardInterrupt:
            logger.info("Telegram poll stopped")
            save_poll_stats(stats)
            print("Polling stopped (KeyboardInterrupt)", flush=True)
        except Exception as exc:
            print_polling_exit_traceback(exc)
            raise
    finally:
        os.close(lock_fd)


def run_diagnose() -> dict[str, Any]:
    token = get_telegram_bot_token()
    allowed = get_allowed_chat_ids()
    cfg = resolve_agent_db_config()
    report: dict[str, Any] = {
        "token_configured": bool(token),
        "allowed_chats_count": len(allowed),
        "db_backend": cfg.backend,
        "db_config_source": cfg.config_source,
        "database_name": cfg.database_name,
        "sqlite_path": cfg.sqlite_path,
        "webhook_active": False,
        "polling_conflict_risk": "unknown",
        "last_offset": _load_offset(),
        "last_processed": _load_last_message(),
        "telegram_connected": check_telegram_connected(token),
    }

    if token:
        try:
            wh = _api_call(token, "getWebhookInfo", {})
            info = wh.get("result", {})
            report["webhook_active"] = bool(info.get("url"))
            report["webhook_url_set"] = bool(info.get("url"))
            if info.get("url"):
                report["polling_conflict_risk"] = "high_webhook_configured"
            else:
                report["polling_conflict_risk"] = _assess_poll_conflict()
        except Exception as exc:
            report["polling_conflict_risk"] = f"check_failed:{type(exc).__name__}"
    else:
        report["polling_conflict_risk"] = "no_token"

    try:
        with agent_connection() as conn:
            row = conn.execute(
                """
                SELECT telegram_message_id, received_at, source
                FROM futures_agent_inputs
                WHERE source LIKE 'telegram:%'
                ORDER BY received_at DESC LIMIT 1
                """
            ).fetchone()
            if row:
                report["last_db_telegram_message"] = {
                    "telegram_message_id": row["telegram_message_id"],
                    "received_at": row["received_at"],
                    "source": row["source"],
                }
    except Exception:
        pass

    return report


def render_diagnose(report: dict[str, Any]) -> str:
    lines = [
        "TELEGRAM INBOUND DIAGNOSE",
        f"  token configured: {'yes' if report['token_configured'] else 'no'}",
        f"  telegram connected: {'yes' if report.get('telegram_connected') else 'no'}",
        f"  allowed chats configured: {report['allowed_chats_count']}",
        f"  polling conflict risk: {report['polling_conflict_risk']}",
        f"  webhook active: {report['webhook_active']}",
        f"  DB backend: {report['db_backend']} ({report['db_config_source']})",
    ]
    if report.get("database_name"):
        lines.append(f"  database: {report['database_name']}")
    if report.get("sqlite_path"):
        lines.append(f"  sqlite: {report['sqlite_path']}")
    lines.append(f"  last offset: {report.get('last_offset')}")
    last = report.get("last_processed") or report.get("last_db_telegram_message")
    if last:
        lines.append(f"  last processed message: {last}")
    return "\n".join(lines)


def _api_call(token: str, method: str, params: dict | None = None, *, timeout: int = 15) -> dict:
    url = API_BASE.format(token=token, method=method)
    resp = requests.get(url, params=params or {}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API {method} failed")
    return data


def _raise_keyboard_interrupt(signum: int, frame: Any) -> None:
    raise KeyboardInterrupt


def _check_polling_conflicts(token: str) -> None:
    wh = _api_call(token, "getWebhookInfo", {})
    if wh.get("result", {}).get("url"):
        raise RuntimeError(
            "Telegram webhook is active on this bot token. "
            "Delete webhook before starting getUpdates polling."
        )


def _assess_poll_conflict() -> str:
    lock_path = project_root() / LOCK_FILE
    if not lock_path.exists():
        return "low_no_local_lock"
    try:
        import fcntl
        fd = os.open(str(lock_path), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return "low_lock_available"
        except BlockingIOError:
            return "high_local_poller_running"
        finally:
            os.close(fd)
    except Exception:
        return "unknown"


def _offset_path() -> Path:
    return project_root() / OFFSET_FILE


def _load_offset() -> int:
    path = _offset_path()
    if not path.is_file():
        return 0
    try:
        return int(json.loads(path.read_text()).get("offset", 0))
    except (json.JSONDecodeError, ValueError, OSError):
        return 0


def _save_offset(offset: int) -> None:
    path = _offset_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"offset": offset}), encoding="utf-8")


def _last_msg_path() -> Path:
    return project_root() / "data" / "futures_agent_telegram_last.json"


def _record_last_message(chat_id: int, message_id: int) -> None:
    path = _last_msg_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"chat_id": chat_id, "message_id": message_id, "ts": int(time.time())}),
        encoding="utf-8",
    )


def _load_last_message() -> dict[str, Any] | None:
    path = _last_msg_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
