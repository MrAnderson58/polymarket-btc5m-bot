"""Telegram / CLI response formatting for futures agent."""

from __future__ import annotations

import os
from typing import Any

import requests

from bot.research.futures_agent.config import get_telegram_chat_id, telegram_notify_enabled


def format_signal_received(conn: Any, input_id: int) -> str:
    row = conn.execute(
        """
        SELECT i.raw_text, s.symbol, s.direction, s.entry_low, s.entry_high,
               s.stop_loss, s.parse_status, s.passes_gate, s.taxonomy
        FROM futures_agent_inputs i
        LEFT JOIN futures_agent_signals s ON s.input_id = i.id
        WHERE i.id = ?
        """,
        (input_id,),
    ).fetchone()
    if not row:
        return "SIGNAL NOT FOUND"

    tps = conn.execute(
        """
        SELECT target_price FROM futures_agent_targets t
        JOIN futures_agent_signals s ON s.id = t.signal_id
        WHERE s.input_id = ?
        ORDER BY target_index
        """,
        (input_id,),
    ).fetchall()
    tp_str = " / ".join(f"{r['target_price']:.4g}" for r in tps) if tps else "—"

    lines = ["SIGNAL RECEIVED", ""]
    if row["symbol"]:
        lines.append(f"Symbol: {row['symbol']}")
    if row["direction"]:
        lines.append(f"Side: {row['direction']}")
    if row["entry_low"] is not None:
        hi = row["entry_high"]
        if hi is not None and hi != row["entry_low"]:
            lines.append(f"Entry: {row['entry_low']:.4g}–{hi:.4g}")
        else:
            lines.append(f"Entry: {row['entry_low']:.4g}")
    if row["stop_loss"] is not None:
        lines.append(f"SL: {row['stop_loss']:.4g}")
    lines.append(f"TP: {tp_str}")
    lines.extend([
        "",
        f"Taxonomy: {row['taxonomy'] or 'pending'}",
        f"Parse: {row['parse_status'] or 'pending'}",
        "",
        "Market snapshot: pending (run snapshot --signal-id)",
        "BTC context: pending (run snapshot --signal-id)",
        "LLM analysis: pending (Stage 3)",
        "",
        "No order is placed. Research mode.",
    ])
    return "\n".join(lines)


from bot.research.futures_agent.telegram_config import get_telegram_bot_token


def send_telegram_reply(chat_id: int | str, text: str) -> bool:
    """Send reply to a specific chat (inbound ack). Never logs token."""
    token = get_telegram_bot_token()
    if not token:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=15,
        )
        return resp.ok and resp.json().get("ok", False)
    except requests.RequestException:
        return False


def send_telegram_message(text: str) -> bool:
    if not telegram_notify_enabled():
        return False
    token = get_telegram_bot_token()
    chat_id = get_telegram_chat_id()
    if not token or not chat_id:
        return False
    return send_telegram_reply(chat_id, text)
