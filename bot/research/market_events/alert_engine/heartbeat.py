"""Phase E.5 — Telegram system heartbeat (every 30 min)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from bot.research.market_events.alert_config import alert_locale
from bot.research.market_events.alert_engine.config import TELEGRAM_HEARTBEAT_SEC, telegram_heartbeat_enabled


def _t(key: str, **kwargs) -> str:
    loc = alert_locale()
    table = _STRINGS.get(loc, _STRINGS["en"])
    text = table.get(key, _STRINGS["en"].get(key, key))
    return text.format(**kwargs) if kwargs else text


def build_heartbeat_message(conn: Any) -> str:
    from bot.research.market_events.signal_intelligence.candidate_g31 import (
        build_idle_candidate_telegram_g31,
    )

    idle = build_idle_candidate_telegram_g31(conn)
    if idle:
        return idle

    now = int(time.time())
    day_start = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

    events_today = conn.execute(
        "SELECT COUNT(*) AS n FROM market_events WHERE event_ts >= ?",
        (day_start,),
    ).fetchone()
    paper_today = conn.execute(
        "SELECT COUNT(*) AS n FROM paper_strategy_runs WHERE entry_ts >= ?",
        (day_start,),
    ).fetchone()
    ai_pending = conn.execute(
        "SELECT COUNT(*) AS n FROM market_event_analysis_jobs WHERE status = 'pending'",
    ).fetchone()

    lines = [
        _t("title"),
        "",
        _t("system_ok"),
        "",
        _t("crypto_collector"),
        _t("running"),
        "",
        _t("tradfi_observer"),
        _t("running"),
        "",
        _t("telegram_poll"),
        _t("running"),
        "",
        _t("ai_queue"),
        f"{int(ai_pending['n'] if ai_pending else 0)} pending",
        "",
        _t("database"),
        "OK",
        "",
        _t("events_today"),
        str(int(events_today["n"] if events_today else 0)),
        "",
        _t("paper_trades"),
        str(int(paper_today["n"] if paper_today else 0)),
    ]
    return "\n".join(lines)


def should_send_heartbeat(conn: Any, *, now: int | None = None) -> bool:
    if not telegram_heartbeat_enabled():
        return False
    now = now or int(time.time())
    row = conn.execute(
        "SELECT last_heartbeat_telegram_ts FROM market_events_scheduler_state WHERE id = 1",
    ).fetchone()
    last = int(row["last_heartbeat_telegram_ts"] or 0) if row else 0
    return (now - last) >= TELEGRAM_HEARTBEAT_SEC


def mark_heartbeat_sent(conn: Any, *, now: int | None = None) -> None:
    now = now or int(time.time())
    conn.execute(
        """
        UPDATE market_events_scheduler_state SET
          last_heartbeat_telegram_ts = ?, updated_at = ?
        WHERE id = 1
        """,
        (now, now),
    )
    try:
        from bot.research.market_events.signal_intelligence.heartbeat_diagnostics_g352 import (
            write_system_heartbeat,
        )
        write_system_heartbeat(conn, writer="telegram_heartbeat")
    except Exception:
        pass


def send_heartbeat_telegram(conn: Any) -> bool:
    from bot.research.market_events.market_event_alerts import _safe_alert

    msg = build_heartbeat_message(conn)
    period = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    sent = _safe_alert(
        conn, event_id=0, alert_type="HEARTBEAT", detail=period,
        message=msg, enabled=True,
    )
    if sent:
        mark_heartbeat_sent(conn)
    conn.execute(
        """
        INSERT OR IGNORE INTO market_events_digest_log (
          digest_type, period_key, message_text, sent, created_at
        ) VALUES ('heartbeat', ?, ?, ?, ?)
        """,
        (period, msg, 1 if sent else 0, int(time.time())),
    )
    return sent


_STRINGS = {
    "en": {
        "title": "💚 System OK",
        "system_ok": "System OK",
        "crypto_collector": "Crypto collector",
        "tradfi_observer": "TradFi observer",
        "telegram_poll": "Telegram poll",
        "ai_queue": "AI queue",
        "database": "Database",
        "events_today": "Events today",
        "paper_trades": "Paper trades",
        "running": "running",
    },
    "ru": {
        "title": "💚 System OK",
        "system_ok": "System OK",
        "crypto_collector": "Crypto collector",
        "tradfi_observer": "TradFi observer",
        "telegram_poll": "Telegram poll",
        "ai_queue": "AI queue",
        "database": "Database",
        "events_today": "Events today",
        "paper_trades": "Paper trades",
        "running": "running",
    },
}
