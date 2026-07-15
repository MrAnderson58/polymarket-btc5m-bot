"""FIX-5 — Telegram runtime audit (startup / getMe / exit diagnostics). No new product features."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from typing import Any

import requests

from bot.research.futures_agent.env_bootstrap import env_file_path, project_root
from bot.research.futures_agent.telegram_config import (
    get_allowed_chat_ids,
    get_telegram_bot_token,
)

API_HOST = "https://api.telegram.org"
GETME_TIMEOUT_SEC = 10


def _mask_chat(chat_id: Any) -> str:
    if chat_id is None or chat_id == "":
        return "(none)"
    s = str(chat_id)
    if len(s) <= 4:
        return "***"
    return f"{s[:2]}…{s[-2:]}"


def print_telegram_startup_audit() -> None:
    """TASK 1 — process start diagnostics (cwd / python / .env / token / chat)."""
    from bot.research.futures_agent.env_bootstrap import bootstrap_config

    bootstrap_config()
    env_path = env_file_path()
    token = get_telegram_bot_token()
    allowed = get_allowed_chat_ids()
    agent_chat = os.getenv("TELEGRAM_AGENT_CHAT_ID", "").strip()
    legacy_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    me_alert = os.getenv("ME_ALERT_CHAT_ID", "").strip()

    print("TELEGRAM RUNTIME AUDIT", flush=True)
    print(f"cwd: {Path.cwd()}", flush=True)
    print(f"python executable: {sys.executable}", flush=True)
    print(f".env path: {env_path}", flush=True)
    print(f".env exists: {'yes' if env_path.is_file() else 'no'}", flush=True)
    print(f"project_root: {project_root()}", flush=True)
    print(f"TELEGRAM_BOT_TOKEN present: {'yes' if token else 'no'}", flush=True)
    print(f"token length: {len(token)}", flush=True)
    print(
        f"chat id (allowed): "
        f"{', '.join(_mask_chat(c) for c in sorted(allowed)) or '(none)'}",
        flush=True,
    )
    print(f"TELEGRAM_AGENT_CHAT_ID: {_mask_chat(agent_chat) if agent_chat else '(none)'}", flush=True)
    print(f"TELEGRAM_CHAT_ID: {_mask_chat(legacy_chat) if legacy_chat else '(none)'}", flush=True)
    print(f"ME_ALERT_CHAT_ID: {_mask_chat(me_alert) if me_alert else '(none)'}", flush=True)
    if not token:
        print(
            "DIAGNOSIS: TELEGRAM_BOT_TOKEN missing in process env after loading "
            f"{env_path}. Add it to that file (see .env.example).",
            flush=True,
        )
    if token and not allowed:
        print(
            "DIAGNOSIS: TELEGRAM_AGENT_ALLOWED_CHAT_IDS is empty — "
            "polling requires comma-separated chat IDs.",
            flush=True,
        )


def print_pre_api_audit(*, token: str | None, timeout: float = GETME_TIMEOUT_SEC) -> str:
    """TASK 2 — before first Telegram API call (this stack uses requests, not Bot SDK)."""
    token = (token or "").strip()
    redacted = f"{token[:6]}…{token[-4:]}" if len(token) > 12 else ("(empty)" if not token else "***")
    api_url = f"{API_HOST}/bot{redacted}/getMe"
    session = requests.Session()
    proxy = {
        "http": os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or "(none)",
        "https": os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or "(none)",
        "ALL_PROXY": os.getenv("ALL_PROXY") or os.getenv("all_proxy") or "(none)",
    }
    print("PRE-API (getMe)", flush=True)
    print(f"api url: {api_url}", flush=True)
    print(f"requests session: {type(session).__module__}.{type(session).__name__}", flush=True)
    print(f"proxy: {proxy}", flush=True)
    print(f"timeout: {timeout}s", flush=True)
    return api_url


def probe_get_me(
    token: str | None = None,
    *,
    timeout: float = GETME_TIMEOUT_SEC,
) -> dict[str, Any]:
    """TASK 3 — execute getMe; return HTTP code / response / exception (never raises)."""
    token = (token if token is not None else get_telegram_bot_token()).strip()
    result: dict[str, Any] = {
        "ok": False,
        "http_code": None,
        "response": None,
        "exception": None,
        "username": None,
        "bot_id": None,
    }
    if not token:
        result["exception"] = "no_token"
        return result

    url = f"{API_HOST}/bot{token}/getMe"
    try:
        resp = requests.get(url, timeout=timeout)
        result["http_code"] = resp.status_code
        try:
            body = resp.json()
        except ValueError:
            body = {"raw": (resp.text or "")[:500]}
        result["response"] = body
        if resp.ok and isinstance(body, dict) and body.get("ok"):
            info = body.get("result") or {}
            result["ok"] = True
            result["username"] = info.get("username")
            result["bot_id"] = info.get("id")
    except Exception as exc:
        result["exception"] = f"{type(exc).__name__}: {exc}"
    return result


def print_get_me_probe(result: dict[str, Any]) -> None:
    print("getMe probe", flush=True)
    print(f"HTTP code: {result.get('http_code') if result.get('http_code') is not None else '—'}", flush=True)
    print(f"response: {result.get('response')!r}"[:2000], flush=True)
    print(f"exception: {result.get('exception') or '—'}", flush=True)
    if result.get("ok"):
        print(f"getMe OK", flush=True)
        print(f"Bot username: @{result.get('username') or '—'}", flush=True)
        print(f"Bot ID: {result.get('bot_id') or '—'}", flush=True)
    else:
        print("getMe FAILED", flush=True)


def print_polling_exit_traceback(exc: BaseException | None = None) -> None:
    """TASK 4 — full traceback when polling terminates unexpectedly."""
    print("TELEGRAM POLLING EXIT", flush=True)
    if exc is not None:
        print(f"exception type: {type(exc).__name__}", flush=True)
        print(f"exception: {exc}", flush=True)
    print("traceback:", flush=True)
    if exc is not None:
        traceback.print_exception(type(exc), exc, exc.__traceback__)
    else:
        traceback.print_exc()
