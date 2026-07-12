"""Futures agent configuration diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from bot.research.futures_agent.env_bootstrap import (
    AGENT_ENV_KEYS,
    AgentDbConfig,
    bootstrap_config,
    env_file_path,
    fallback_reason,
    parse_env_file,
    project_root,
    resolve_agent_db_config,
    _resolve_env_var_source,
)
from bot.research.futures_agent.telegram_config import get_allowed_chat_ids, get_telegram_bot_token


def _redact_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.password:
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        user = parsed.username or ""
        db = (parsed.path or "").lstrip("/")
        return f"{parsed.scheme}://{user}:***@{host}{port}/{db}"
    if url.startswith("sqlite:"):
        return url
    return url


@dataclass(frozen=True)
class ConfigDiagnose:
    project_root: str
    env_file: str
    env_file_exists: bool
    database_backend: str
    database_url_source: str
    database_name: str | None
    database_url_redacted: str
    telegram_token_source: str
    telegram_token_configured: bool
    chat_ids: tuple[int, ...]
    chat_ids_source: str
    fallback_reason: str | None
    loaded_variables: tuple[tuple[str, str], ...]
    postgres_connect_ok: bool | None
    postgres_connect_error: str | None


def run_config_diagnose(*, test_connection: bool = True) -> ConfigDiagnose:
    before = bootstrap_config()
    dotenv = parse_env_file(env_file_path())
    cfg = resolve_agent_db_config()

    loaded: list[tuple[str, str]] = []
    for key in AGENT_ENV_KEYS:
        if key == "FUTURES_AGENT_PROJECT_ROOT":
            continue
        src = _resolve_env_var_source(key, before, dotenv)
        if src != "missing":
            loaded.append((key, src))

    pg_ok: bool | None = None
    pg_err: str | None = None
    if test_connection and cfg.is_postgres:
        try:
            from bot.research.futures_agent.db import agent_connection
            with agent_connection():
                pg_ok = True
        except Exception as exc:
            pg_ok = False
            pg_err = str(exc)

    token_src = _resolve_env_var_source("TELEGRAM_BOT_TOKEN", before, dotenv)
    chat_src = _resolve_env_var_source("TELEGRAM_AGENT_ALLOWED_CHAT_IDS", before, dotenv)

    url_source = cfg.config_source
    if cfg.config_source == "fallback_sqlite":
        url_source = "fallback_sqlite"

    return ConfigDiagnose(
        project_root=str(project_root()),
        env_file=str(env_file_path()),
        env_file_exists=env_file_path().is_file(),
        database_backend=cfg.backend,
        database_url_source=url_source,
        database_name=cfg.database_name,
        database_url_redacted=_redact_url(cfg.url),
        telegram_token_source=token_src,
        telegram_token_configured=bool(get_telegram_bot_token()),
        chat_ids=tuple(sorted(get_allowed_chat_ids())),
        chat_ids_source=chat_src,
        fallback_reason=fallback_reason(cfg, dotenv_vars=dotenv),
        loaded_variables=tuple(loaded),
        postgres_connect_ok=pg_ok,
        postgres_connect_error=pg_err,
    )


def format_config_diagnose(d: ConfigDiagnose) -> str:
    lines = [
        "FUTURES AGENT CONFIG DIAGNOSE",
        "",
        f"Project root: {d.project_root}",
        f"Loaded .env: {d.env_file}" + (" (found)" if d.env_file_exists else " (missing)"),
        "",
        "Database backend:",
        f"  {d.database_backend}",
        f"Database URL source: {d.database_url_source}",
    ]
    if d.database_name:
        lines.append(f"Database: {d.database_name}")
    lines.append(f"Database URL: {d.database_url_redacted}")
    if d.fallback_reason:
        lines.append(f"Reason for fallback: {d.fallback_reason}")
    if d.postgres_connect_ok is not None:
        if d.postgres_connect_ok:
            lines.append("PostgreSQL connection: ok")
        else:
            lines.append(f"PostgreSQL connection: FAILED — {d.postgres_connect_error}")

    lines.extend([
        "",
        f"Telegram token source: {d.telegram_token_source}",
        f"Telegram token configured: {'yes' if d.telegram_token_configured else 'no'}",
        f"Chat IDs source: {d.chat_ids_source}",
        f"Chat IDs: {', '.join(str(c) for c in d.chat_ids) or '(none)'}",
        "",
        "Loaded variables:",
    ])
    if d.loaded_variables:
        for key, src in d.loaded_variables:
            lines.append(f"  {key} ({src})")
    else:
        lines.append("  (none — check project .env)")
    return "\n".join(lines)


def format_startup_banner(cfg: AgentDbConfig | None = None) -> str:
    """Compact startup lines for telegram-poll."""
    d = run_config_diagnose(test_connection=False)
    if cfg is None:
        cfg = resolve_agent_db_config()
    lines = [
        "FUTURES AGENT TELEGRAM-POLL STARTUP",
        f"Backend: {cfg.backend}",
    ]
    if cfg.database_name:
        lines.append(f"Database: {cfg.database_name}")
    elif cfg.sqlite_path:
        lines.append(f"Database: {cfg.sqlite_path}")
    lines.append(f"Project .env: {d.env_file}" + (" (loaded)" if d.env_file_exists else " (missing)"))
    lines.append("Loaded variables:")
    if d.loaded_variables:
        for key, src in d.loaded_variables:
            lines.append(f"  {key} ({src})")
    else:
        lines.append("  (none)")
    if d.fallback_reason:
        lines.append(f"WARNING: fallback reason — {d.fallback_reason}")
    return "\n".join(lines)
