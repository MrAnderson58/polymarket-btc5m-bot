"""Telegram delivery with retry, latency tracking, and per-attempt logging."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from bot.research.market_events.alert_config import (
    ALERT_MAX_RETRIES,
    ALERT_RETRY_BACKOFF_MULTIPLIER,
    ALERT_RETRY_DELAY_SEC,
    ALERT_RETRY_MAX_DELAY_SEC,
    resolve_alert_chat_id,
)

logger = logging.getLogger(__name__)

RETRYABLE_HTTP_CODES = frozenset({429, 500, 502, 503, 504})
STATUS_SENT = "sent"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class TelegramDeliveryResult:
    ok: bool
    error: str | None
    latency_ms: float
    http_code: int | None
    message_id: int | None
    attempts: int
    chat_id: str | None


def _backoff_delay(attempt: int) -> float:
    delay = ALERT_RETRY_DELAY_SEC * (ALERT_RETRY_BACKOFF_MULTIPLIER ** attempt)
    return min(delay, ALERT_RETRY_MAX_DELAY_SEC)


def _is_retryable(http_code: int | None, exc: Exception | None) -> bool:
    if exc is not None:
        return isinstance(exc, (requests.Timeout, requests.ConnectionError))
    return http_code in RETRYABLE_HTTP_CODES


def _message_preview(text: str, limit: int = 120) -> str:
    one_line = " ".join(text.split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 3] + "..."


def log_delivery_attempt(
    conn: Any | None,
    *,
    event_id: int,
    alert_type: str,
    chat_id: str | None,
    message_text: str,
    status: str,
    latency_ms: float,
    http_code: int | None,
    message_id: int | None,
    attempt: int,
    error: str | None = None,
) -> None:
    if conn is None:
        return
    try:
        conn.execute(
            """
            INSERT INTO market_event_telegram_delivery_log (
              event_id, alert_type, chat_id, message_preview, message_text, status,
              latency_ms, http_code, telegram_message_id, attempt, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, alert_type, chat_id, _message_preview(message_text),
                message_text, status, latency_ms, http_code, message_id, attempt, error,
                int(time.time()),
            ),
        )
    except Exception as exc:
        logger.warning("telegram delivery log persist failed: %s", exc)


def get_last_delivery(conn: Any, *, event_id: int, alert_type: str) -> Any | None:
    return conn.execute(
        """
        SELECT status, http_code, error, telegram_message_id
        FROM market_event_telegram_delivery_log
        WHERE event_id = ? AND alert_type = ?
        ORDER BY id DESC LIMIT 1
        """,
        (event_id, alert_type),
    ).fetchone()


def format_delivery_status(
    conn: Any,
    *,
    event_id: int,
    alert_type: str,
    label: str,
) -> str:
    row = get_last_delivery(conn, event_id=event_id, alert_type=alert_type)
    if row and row["status"] == STATUS_SENT and row["http_code"] == 200:
        return f"{label} sent"
    if row:
        reason = row["error"] or f"http_{row['http_code']}"
        return f"{label} failed ({reason})"
    return f"{label} skipped (not attempted)"


def _post_telegram(token: str, chat_id: str | int, text: str) -> tuple[int | None, dict | None, Exception | None]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=15,
        )
        body: dict | None = None
        try:
            body = resp.json()
        except ValueError:
            body = None
        return resp.status_code, body, None
    except requests.RequestException as exc:
        return None, None, exc


def deliver_telegram(
    text: str,
    *,
    conn: Any | None = None,
    alert_type: str = "UNKNOWN",
    event_id: int = 0,
    chat_id: str | None = None,
    max_retries: int | None = None,
) -> TelegramDeliveryResult:
    """Send via Telegram Bot API with exponential backoff on transient failures."""
    from bot.research.futures_agent.telegram_config import get_telegram_bot_token

    token = get_telegram_bot_token()
    if not token:
        result = TelegramDeliveryResult(
            ok=False, error="no_token", latency_ms=0.0,
            http_code=None, message_id=None, attempts=0, chat_id=None,
        )
        log_delivery_attempt(
            conn, event_id=event_id, alert_type=alert_type, chat_id=None,
            message_text=text, status=STATUS_FAILED, latency_ms=0.0,
            http_code=None, message_id=None, attempt=0, error="no_token",
        )
        return result

    chat = chat_id or resolve_alert_chat_id().chat_id
    if not chat:
        resolution = resolve_alert_chat_id()
        chat_err = resolution.error or "no_chat_id"
        result = TelegramDeliveryResult(
            ok=False, error=chat_err, latency_ms=0.0,
            http_code=None, message_id=None, attempts=0, chat_id=None,
        )
        log_delivery_attempt(
            conn, event_id=event_id, alert_type=alert_type, chat_id=None,
            message_text=text, status=STATUS_FAILED, latency_ms=0.0,
            http_code=None, message_id=None, attempt=0, error=chat_err,
        )
        return result

    retries = ALERT_MAX_RETRIES if max_retries is None else max_retries
    total_attempts = retries + 1
    last_error: str | None = None
    last_http: int | None = None
    total_latency = 0.0

    for attempt in range(total_attempts):
        t0 = time.perf_counter()
        http_code, body, exc = _post_telegram(token, chat, text)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        total_latency += latency_ms
        last_http = http_code

        message_id: int | None = None
        ok = False
        err: str | None = None

        if exc is not None:
            err = f"timeout:{type(exc).__name__}"
            last_error = err
        elif body and body.get("ok"):
            ok = True
            result_msg = body.get("result") or {}
            if isinstance(result_msg, dict):
                mid = result_msg.get("message_id")
                message_id = int(mid) if mid is not None else None
        else:
            desc = (body or {}).get("description") if body else None
            err = desc or f"http_{http_code or 'error'}"
            last_error = err

        log_delivery_attempt(
            conn, event_id=event_id, alert_type=alert_type, chat_id=str(chat),
            message_text=text,
            status=STATUS_SENT if ok else STATUS_FAILED,
            latency_ms=latency_ms, http_code=http_code, message_id=message_id,
            attempt=attempt + 1, error=err,
        )

        if ok:
            return TelegramDeliveryResult(
                ok=True, error=None, latency_ms=total_latency,
                http_code=http_code, message_id=message_id,
                attempts=attempt + 1, chat_id=str(chat),
            )

        if attempt < retries and _is_retryable(http_code, exc):
            time.sleep(_backoff_delay(attempt))
            continue
        break

    return TelegramDeliveryResult(
        ok=False, error=last_error or "send_failed",
        latency_ms=total_latency, http_code=last_http,
        message_id=None, attempts=total_attempts, chat_id=str(chat),
    )
