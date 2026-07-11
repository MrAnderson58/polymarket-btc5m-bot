"""Telegram configuration report and API probes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from bot.research.market_events.alert_config import ChatIdResolution, resolve_alert_chat_id


def env_file_loaded() -> tuple[bool, str | None]:
    from dotenv import find_dotenv

    path = find_dotenv(usecwd=True)
    if path and Path(path).is_file():
        return True, path
    return False, None


def fetch_bot_info() -> dict[str, Any] | None:
    from bot.research.futures_agent.telegram_config import get_telegram_bot_token

    token = get_telegram_bot_token()
    if not token:
        return None
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=10,
        )
        body = resp.json()
        if resp.ok and body.get("ok"):
            return body.get("result")
    except (requests.RequestException, ValueError):
        return None
    return None


def telegram_api_reachable() -> bool:
    return fetch_bot_info() is not None


def format_telegram_config_report() -> str:
    from bot.research.futures_agent.telegram_config import get_telegram_bot_token

    token = get_telegram_bot_token()
    resolution: ChatIdResolution = resolve_alert_chat_id()
    loaded, env_path = env_file_loaded()
    bot_info = fetch_bot_info() if token else None

    lines = [
        "TELEGRAM CONFIG",
        "",
        f"Bot Token:",
        "configured" if token else "missing",
        "",
        f"Chat ID:",
        resolution.chat_id if resolution.chat_id else "not resolved",
        "",
        "Resolved from:",
    ]
    if resolution.source:
        lines.append(resolution.source)
    elif resolution.error:
        lines.append(resolution.error)
    else:
        lines.append("none")

    lines.extend([
        "",
        "Telegram API:",
        "reachable yes" if bot_info else "reachable no",
        "",
        "Environment file:",
        f"loaded yes ({env_path})" if loaded else "loaded no",
    ])

    if bot_info:
        lines.extend([
            "",
            f"Bot username: @{bot_info.get('username', '—')}",
            f"Bot ID: {bot_info.get('id', '—')}",
        ])

    return "\n".join(lines)
