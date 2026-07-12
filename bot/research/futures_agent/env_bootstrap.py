"""Canonical environment bootstrap for futures_agent CLI commands."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

_ENV_SNAPSHOT: dict[str, str] | None = None
_BOOTSTRAPPED = False

AGENT_ENV_KEYS = (
    "FUTURES_AGENT_DATABASE_URL",
    "FUTURES_AGENT_SQLITE_PATH",
    "FUTURES_AGENT_PROJECT_ROOT",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_AGENT_ALLOWED_CHAT_IDS",
    "TELEGRAM_AGENT_CHAT_ID",
    "TELEGRAM_CHAT_ID",
)


class AgentDbConfigError(RuntimeError):
    """Misconfigured agent database — never silently fall back to SQLite."""


def project_root() -> Path:
    """Repository root; override with FUTURES_AGENT_PROJECT_ROOT when set."""
    override = os.getenv("FUTURES_AGENT_PROJECT_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[3]


def env_file_path() -> Path:
    return project_root() / ".env"


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines from a dotenv file (no variable expansion)."""
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        out[key] = val
    return out


def bootstrap_config() -> dict[str, str]:
    """Load project-root .env once. Never overwrite existing process env."""
    global _BOOTSTRAPPED, _ENV_SNAPSHOT
    if _BOOTSTRAPPED and _ENV_SNAPSHOT is not None:
        return _ENV_SNAPSHOT
    _ENV_SNAPSHOT = dict(os.environ)
    path = env_file_path()
    if path.is_file():
        load_dotenv(path, override=False)
    _BOOTSTRAPPED = True
    return _ENV_SNAPSHOT


def _is_postgres_url(url: str) -> bool:
    return url.startswith(("postgres://", "postgresql://"))


def _agent_url_env_source(before: dict[str, str], *, from_dotenv_file: bool) -> str:
    if before.get("FUTURES_AGENT_DATABASE_URL"):
        return "shell_env"
    if from_dotenv_file:
        return "project_dotenv"
    if os.getenv("FUTURES_AGENT_DATABASE_URL"):
        return "project_dotenv"
    return "project_dotenv"


def _sqlite_path_env_source(before: dict[str, str]) -> str | None:
    if before.get("FUTURES_AGENT_SQLITE_PATH"):
        return "shell_env"
    if os.getenv("FUTURES_AGENT_SQLITE_PATH"):
        return "project_dotenv"
    return None


def _postgres_url_configured(
    before: dict[str, str],
    dotenv_vars: dict[str, str],
) -> bool:
    for raw in (
        before.get("FUTURES_AGENT_DATABASE_URL"),
        os.getenv("FUTURES_AGENT_DATABASE_URL"),
        dotenv_vars.get("FUTURES_AGENT_DATABASE_URL"),
    ):
        if raw and _is_postgres_url(str(raw).strip()):
            return True
    return False


def _resolve_env_var_source(key: str, before: dict[str, str], dotenv_vars: dict[str, str]) -> str:
    if before.get(key):
        return "shell_env"
    if os.getenv(key):
        return "project_dotenv"
    if key in dotenv_vars and dotenv_vars[key]:
        return "project_dotenv_file"
    return "missing"


@dataclass(frozen=True)
class AgentDbConfig:
    backend: str  # postgresql | sqlite
    url: str
    database_name: str | None
    sqlite_path: str | None
    config_source: str  # shell_env | project_dotenv | fallback_sqlite | ...
    postgres_url_configured: bool

    @property
    def is_postgres(self) -> bool:
        return self.backend == "postgresql"


def resolve_agent_db_config() -> AgentDbConfig:
    """Resolve agent DB backend after bootstrap. No silent PG->SQLite fallback."""
    before = bootstrap_config()
    dotenv_vars = parse_env_file(env_file_path())

    agent_url = os.getenv("FUTURES_AGENT_DATABASE_URL", "").strip()
    from_dotenv_file = False
    if not agent_url:
        file_url = dotenv_vars.get("FUTURES_AGENT_DATABASE_URL", "").strip()
        if file_url:
            agent_url = file_url
            from_dotenv_file = True

    if agent_url:
        source = _agent_url_env_source(before, from_dotenv_file=from_dotenv_file)
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
        if _postgres_url_configured(before, dotenv_vars):
            raise AgentDbConfigError(
                f"FUTURES_AGENT_DATABASE_URL is set but not a valid PostgreSQL URL: {agent_url!r}"
            )

    sqlite_path = os.getenv("FUTURES_AGENT_SQLITE_PATH", "").strip()
    if not sqlite_path:
        sqlite_path = dotenv_vars.get("FUTURES_AGENT_SQLITE_PATH", "").strip()
    if sqlite_path:
        src = _sqlite_path_env_source(before) or (
            "project_dotenv" if dotenv_vars.get("FUTURES_AGENT_SQLITE_PATH") else "project_dotenv_sqlite"
        )
        if _postgres_url_configured(before, dotenv_vars):
            raise AgentDbConfigError(
                "FUTURES_AGENT_DATABASE_URL is configured for PostgreSQL — "
                "SQLite path cannot be used as fallback."
            )
        return AgentDbConfig(
            backend="sqlite",
            url=f"sqlite:///{sqlite_path}",
            database_name=None,
            sqlite_path=sqlite_path,
            config_source=src,
            postgres_url_configured=False,
        )

    if _postgres_url_configured(before, dotenv_vars):
        raise AgentDbConfigError(
            "Cannot connect to PostgreSQL: FUTURES_AGENT_DATABASE_URL is configured "
            f"in project .env ({env_file_path()}) but could not be resolved."
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


def fallback_reason(cfg: AgentDbConfig, *, dotenv_vars: dict[str, str] | None = None) -> str | None:
    """Human-readable reason when backend is fallback_sqlite."""
    if cfg.config_source != "fallback_sqlite":
        return None
    path = env_file_path()
    if not path.is_file():
        return f"no project .env at {path}"
    vars_ = dotenv_vars if dotenv_vars is not None else parse_env_file(path)
    if not vars_.get("FUTURES_AGENT_DATABASE_URL"):
        return "FUTURES_AGENT_DATABASE_URL not set in project .env or shell environment"
    url = vars_["FUTURES_AGENT_DATABASE_URL"].strip()
    if url.startswith("#") or not url:
        return "FUTURES_AGENT_DATABASE_URL empty or commented in project .env"
    if not _is_postgres_url(url):
        return f"FUTURES_AGENT_DATABASE_URL in .env is not PostgreSQL: {url!r}"
    return "unknown — PostgreSQL URL present in .env but not applied"


def reset_bootstrap_for_tests() -> None:
    """Clear bootstrap cache — test isolation only."""
    global _BOOTSTRAPPED, _ENV_SNAPSHOT
    _BOOTSTRAPPED = False
    _ENV_SNAPSHOT = None


def configure_unit_test_db_isolation(sqlite_path: str) -> None:
    """Force agent DB resolution to an isolated SQLite file for unit tests."""
    path = str(sqlite_path)
    os.environ["FUTURES_AGENT_SQLITE_PATH"] = path
    os.environ["FUTURES_AGENT_DATABASE_URL"] = f"sqlite:///{path}"
    reset_bootstrap_for_tests()


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
