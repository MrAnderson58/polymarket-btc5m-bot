"""Telegram configuration report and API probes (getMe is the reachability source of truth)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bot.research.market_events.alert_config import ChatIdResolution, resolve_alert_chat_id


def env_file_loaded() -> tuple[bool, str | None]:
    from bot.research.futures_agent.env_bootstrap import env_file_path

    path = env_file_path()
    if path.is_file():
        return True, str(path)
    # Fallback: cwd dotenv for legacy reports
    try:
        from dotenv import find_dotenv
        path_s = find_dotenv(usecwd=True)
        if path_s and Path(path_s).is_file():
            return True, path_s
    except Exception:
        pass
    return False, str(path)


def probe_telegram_get_me(*, timeout: float = 10.0) -> dict[str, Any]:
    """Explicit getMe probe used by telegram-config reachability."""
    from bot.research.futures_agent.telegram_runtime_audit import probe_get_me

    return probe_get_me(timeout=timeout)


def fetch_bot_info() -> dict[str, Any] | None:
    """Legacy helper — returns getMe result dict or None. Reachability = getMe only."""
    probe = probe_telegram_get_me()
    if not probe.get("ok"):
        return None
    body = probe.get("response") or {}
    if isinstance(body, dict):
        return body.get("result")
    return None


def telegram_api_reachable() -> bool:
    """Telegram API reachable iff getMe succeeds."""
    return bool(probe_telegram_get_me().get("ok"))


def format_telegram_config_report() -> str:
    from bot.research.futures_agent.env_bootstrap import bootstrap_config, env_file_path
    from bot.research.futures_agent.telegram_config import get_allowed_chat_ids, get_telegram_bot_token

    bootstrap_config()
    token = get_telegram_bot_token()
    resolution: ChatIdResolution = resolve_alert_chat_id()
    loaded, env_path = env_file_loaded()
    probe = probe_telegram_get_me() if token else {
        "ok": False, "http_code": None, "response": None, "exception": "no_token",
        "username": None, "bot_id": None,
    }
    allowed = get_allowed_chat_ids()

    lines = [
        "TELEGRAM CONFIG",
        "",
        "Bot Token:",
        "configured" if token else "missing",
        f"token length: {len(token)}",
        "",
        "Chat ID:",
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
        "Allowed chats (polling):",
        ", ".join(str(c) for c in sorted(allowed)) or "(none)",
        "",
        "Telegram API:",
        "reachable yes" if probe.get("ok") else "reachable no",
        "(checked via getMe)",
        "",
        "getMe:",
        f"HTTP code: {probe.get('http_code') if probe.get('http_code') is not None else '—'}",
        f"exception: {probe.get('exception') or '—'}",
        f"ok: {'yes' if probe.get('ok') else 'no'}",
        "",
        "Environment file:",
        f"loaded yes ({env_path})" if loaded else f"loaded no ({env_file_path()})",
    ])

    if probe.get("ok"):
        lines.extend([
            "",
            f"Bot username: @{probe.get('username') or '—'}",
            f"Bot ID: {probe.get('bot_id') or '—'}",
            "getMe OK",
        ])
    elif not token:
        lines.extend([
            "",
            f"DIAGNOSIS: set TELEGRAM_BOT_TOKEN in {env_file_path()}",
            "Also set TELEGRAM_AGENT_ALLOWED_CHAT_IDS for polling.",
        ])

    return "\n".join(lines)
