"""Phase G.0 — market_events database backend configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from bot.research.market_events.config import BASE_DIR, DEFAULT_DB_PATH

load_dotenv()


@dataclass(frozen=True)
class MarketEventsDbConfig:
    backend: str  # "sqlite" | "postgresql"
    url: str
    sqlite_path: Path
    config_source: str
    postgres_url_configured: bool

    @property
    def database_name(self) -> str:
        if self.backend == "postgresql":
            from urllib.parse import urlparse
            name = (urlparse(self.url).path or "/trading_ai").lstrip("/")
            return name or "trading_ai"
        return self.sqlite_path.name


def _is_postgres_url(url: str) -> bool:
    return url.startswith(("postgres://", "postgresql://"))


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


def resolve_market_events_db_config() -> MarketEventsDbConfig:
    """Resolve backend from MARKET_EVENTS_DB_BACKEND / MARKET_EVENTS_DB_URL."""
    explicit_backend = os.getenv("MARKET_EVENTS_DB_BACKEND", "").strip().lower()
    db_url = os.getenv("MARKET_EVENTS_DB_URL", "").strip()
    legacy_path = os.getenv("MARKET_EVENTS_DATABASE_PATH", "").strip()

    sqlite_path = Path(legacy_path) if legacy_path else DEFAULT_DB_PATH

    if db_url:
        if _is_postgres_url(db_url):
            return MarketEventsDbConfig(
                backend="postgresql",
                url=db_url,
                sqlite_path=sqlite_path,
                config_source="env_url",
                postgres_url_configured=True,
            )
        if db_url.startswith("sqlite:///"):
            p = Path(db_url.replace("sqlite:///", ""))
            return MarketEventsDbConfig(
                backend="sqlite",
                url=db_url,
                sqlite_path=p,
                config_source="env_url",
                postgres_url_configured=False,
            )

    if explicit_backend in ("postgresql", "postgres"):
        if not db_url:
            raise RuntimeError(
                "MARKET_EVENTS_DB_BACKEND=postgresql requires MARKET_EVENTS_DB_URL",
            )
        return MarketEventsDbConfig(
            backend="postgresql",
            url=db_url,
            sqlite_path=sqlite_path,
            config_source="env_backend",
            postgres_url_configured=True,
        )

    return MarketEventsDbConfig(
        backend="sqlite",
        url=_sqlite_url(sqlite_path),
        sqlite_path=sqlite_path,
        config_source="default_sqlite",
        postgres_url_configured=False,
    )


def configure_unit_test_db_isolation(sqlite_path: Path) -> None:
    """Force SQLite for unit tests."""
    os.environ["MARKET_EVENTS_DB_BACKEND"] = "sqlite"
    os.environ["MARKET_EVENTS_DB_URL"] = _sqlite_url(sqlite_path)
    os.environ["MARKET_EVENTS_DATABASE_PATH"] = str(sqlite_path)


def reset_db_config_for_tests() -> None:
    for key in (
        "MARKET_EVENTS_DB_BACKEND",
        "MARKET_EVENTS_DB_URL",
        "MARKET_EVENTS_DATABASE_PATH",
    ):
        os.environ.pop(key, None)
