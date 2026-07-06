"""Canonical environment bootstrap for futures_agent CLI commands."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

_ENV_SNAPSHOT: dict[str, str] | None = None
_BOOTSTRAPPED = False


def project_root() -> Path:
    """Repository root: bot/research/futures_agent -> parents[3]."""
    return Path(__file__).resolve().parents[3]


def bootstrap_config() -> dict[str, str]:
    """Load project-root .env once. Never overwrite existing process env."""
    global _BOOTSTRAPPED, _ENV_SNAPSHOT
    if _BOOTSTRAPPED and _ENV_SNAPSHOT is not None:
        return _ENV_SNAPSHOT
    _ENV_SNAPSHOT = dict(os.environ)
    env_path = project_root() / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)
    _BOOTSTRAPPED = True
    return _ENV_SNAPSHOT


def _is_postgres_url(url: str) -> bool:
    return url.startswith(("postgres://", "postgresql://"))


def _agent_url_env_source(before: dict[str, str]) -> str | None:
    if before.get("FUTURES_AGENT_DATABASE_URL"):
        return "shell_env"
    if os.getenv("FUTURES_AGENT_DATABASE_URL"):
        return "project_dotenv"
    return None


def _sqlite_path_env_source(before: dict[str, str]) -> str | None:
    if before.get("FUTURES_AGENT_SQLITE_PATH"):
        return "shell_env"
    if os.getenv("FUTURES_AGENT_SQLITE_PATH"):
        return "project_dotenv"
    return None


@dataclass(frozen=True)
class AgentDbConfig:
    backend: str  # postgresql | sqlite
    url: str
    database_name: str | None
    sqlite_path: str | None
    config_source: str  # shell_env | project_dotenv | fallback_sqlite | shell_env_sqlite | project_dotenv_sqlite
    postgres_url_configured: bool

    @property
    def is_postgres(self) -> bool:
        return self.backend == "postgresql"


def resolve_agent_db_config() -> AgentDbConfig:
    """Resolve agent DB backend after bootstrap. No silent PG->SQLite fallback."""
    before = bootstrap_config()

    agent_url = os.getenv("FUTURES_AGENT_DATABASE_URL", "").strip()
    if agent_url:
        source = _agent_url_env_source(before) or "project_dotenv"
        if _is_postgres_url(agent_url):
            parsed = urlparse(agent_url)
            dbname = (parsed.path or "/").lstrip("/") or "trading_ai"
            return AgentDbConfig(
                backend="postgresql",
                url=agent_url,
                database_name=dbname,
                sqlite_path=None,
                config_source=source,
                postgres_url_configured=True,
            )
        if agent_url.startswith("sqlite:"):
            path = agent_url.replace("sqlite:///", "")
            return AgentDbConfig(
                backend="sqlite",
                url=agent_url,
                database_name=None,
                sqlite_path=path,
                config_source=source,
                postgres_url_configured=False,
            )

    sqlite_path = os.getenv("FUTURES_AGENT_SQLITE_PATH", "").strip()
    if sqlite_path:
        src = _sqlite_path_env_source(before) or "project_dotenv_sqlite"
        return AgentDbConfig(
            backend="sqlite",
            url=f"sqlite:///{sqlite_path}",
            database_name=None,
            sqlite_path=sqlite_path,
            config_source=src,
            postgres_url_configured=False,
        )

    fallback = project_root() / "data" / "futures_agent.db"
    return AgentDbConfig(
        backend="sqlite",
        url=f"sqlite:///{fallback}",
        database_name=None,
        sqlite_path=str(fallback),
        config_source="fallback_sqlite",
        postgres_url_configured=False,
    )


def reset_bootstrap_for_tests() -> None:
    """Clear bootstrap cache — test isolation only."""
    global _BOOTSTRAPPED, _ENV_SNAPSHOT
    _BOOTSTRAPPED = False
    _ENV_SNAPSHOT = None


def db_config_diagnostics(cfg: AgentDbConfig) -> dict[str, str | bool | None]:
    """Safe diagnostics — no passwords."""
    return {
        "backend": cfg.backend,
        "database_name": cfg.database_name,
        "sqlite_path": cfg.sqlite_path,
        "config_source": cfg.config_source,
        "postgres_url_configured": cfg.postgres_url_configured,
        "url_scheme": urlparse(cfg.url).scheme if cfg.is_postgres else "sqlite",
    }
