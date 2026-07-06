"""Telegram inbound adapter — Stage 1b observe-only signal ingestion."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from bot.research.futures_agent.db import agent_connection
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
from bot.research.futures_agent.telegram_replies import (
    format_telegram_accepted,
    format_telegram_duplicate,
    format_telegram_rejected,
    format_telegram_unauthorized,
)

logger = logging.getLogger(__name__)

POLL_TIMEOUT_SEC = 30
API_BASE = "https://api.telegram.org/bot{token}/{method}"
OFFSET_FILE = "data/futures_agent_telegram_offset.json"
LOCK_FILE = "data/futures_agent_telegram_poll.lock"


@dataclass
class InboundResult:
    chat_id: int
    message_id: int
    reply_text: str | None
    skipped: bool = False
    unauthorized: bool = False


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
    postgres: bool,
) -> InboundResult:
    """Full pipeline: ingest → parse → snapshot (if gated) → reply text."""
    chat = message.get("chat") or {}
    chat_id = int(chat.get("id", 0))
    message_id = int(message.get("message_id", 0))

    if not is_chat_allowed(chat_id):
        logger.info("Rejected unauthorized chat_id=%s", chat_id)
        return InboundResult(chat_id, message_id, None, unauthorized=True)

    text = extract_message_text(message)
    if not text or not text.strip():
        return InboundResult(chat_id, message_id, "Empty message ignored.", skipped=True)

    received_at = int(message.get("date", time.time()))
    forward_origin = extract_forward_origin(message)

    with agent_connection() as conn:
        apply_migrations(conn, postgres=postgres)
        ing = ingest_from_telegram(
            conn,
            raw_text=text,
            chat_id=chat_id,
            message_id=message_id,
            forward_origin=forward_origin,
            received_at=received_at,
        )
        if ing.duplicate:
            return InboundResult(chat_id, message_id, format_telegram_duplicate(), skipped=True)
        proc = process_input(conn, ing.input_id)
        input_id = ing.input_id
        signal_id = proc.signal_id
        passes_gate = proc.passes_gate

    if not passes_gate:
        return InboundResult(chat_id, message_id, format_telegram_rejected(proc))

    snap: SnapshotResult | None = None
    if signal_id:
        with agent_connection() as conn:
            apply_migrations(conn, postgres=postgres)
            snap = snapshot_signal(conn, signal_id)

    with agent_connection() as conn:
        reply = format_telegram_accepted(
            conn, input_id=input_id, signal_id=signal_id or 0, snap=snap,
        )
    return InboundResult(chat_id, message_id, reply)


def handle_update(update: dict[str, Any], *, postgres: bool) -> InboundResult | None:
    message = update.get("message") or update.get("edited_message")
    if not message:
        return None
    result = process_telegram_message(message, postgres=postgres)
    if result.unauthorized:
        return result
    if result.reply_text:
        send_telegram_reply(result.chat_id, result.reply_text)
    _record_last_message(result.chat_id, result.message_id)
    return result


def poll_once(*, offset: int | None = None, postgres: bool = False) -> int:
    """Fetch and process one batch. Returns new offset."""
    token, _ = require_telegram_inbound_config()
    params: dict[str, Any] = {"timeout": POLL_TIMEOUT_SEC, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    data = _api_call(token, "getUpdates", params, timeout=POLL_TIMEOUT_SEC + 10)
    updates = data.get("result", [])
    new_offset = offset or 0
    for upd in updates:
        new_offset = max(new_offset, int(upd["update_id"]) + 1)
        handle_update(upd, postgres=postgres)
    if new_offset:
        _save_offset(new_offset)
    return new_offset


def run_poll_loop() -> None:
    """Long-polling loop with file lock (single local consumer)."""
    require_telegram_inbound_config()
    cfg = resolve_agent_db_config()
    _check_polling_conflicts(get_telegram_bot_token())

    lock_path = project_root() / LOCK_FILE
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        try:
            import fcntl
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "Another futures-agent telegram-poll process holds the lock. "
                "Only one getUpdates consumer per bot token."
            ) from None

        offset = _load_offset()
        logger.info("Telegram poll started backend=%s offset=%s", cfg.backend, offset)
        while True:
            offset = poll_once(offset=offset or None, postgres=cfg.is_postgres)
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
