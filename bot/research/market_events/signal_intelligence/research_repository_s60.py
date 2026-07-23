"""S60 — Research storage separation (analytics DB ≠ live trading DB).

Live SQLite: signals, open trades, portfolio, execution, S55 gate features.
Research DB (PostgreSQL preferred, sibling SQLite fallback): S56–S61 analytics.

No DDL on the live trading path. Research schema via market-research-migrate only.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from bot.research.market_events.db import (
    MarketEventsDbError,
    PostgresBackend,
    _connect_postgres,
    _is_postgres_url,
    retry_on_db_locked,
)
from bot.research.market_events.db_config import resolve_market_events_db_config
from bot.research.market_events.sqlite_manager_g05 import connect_sqlite as _connect_sqlite_g05

logger = logging.getLogger(__name__)

RESEARCH_MIGRATIONS_TABLE = "market_events_research_migrations"
RESEARCH_SCHEMA_VERSION = 72


@dataclass(frozen=True)
class ResearchDbConfig:
    backend: str  # sqlite | postgresql
    url: str
    sqlite_path: Path | None
    config_source: str
    separated: bool  # True when research is not the live DB file/url


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


def resolve_research_db_config() -> ResearchDbConfig:
    """Resolve analytics/research DB.

    Priority:
      1. MARKET_EVENTS_RESEARCH_DB_URL (postgres or sqlite)
      2. If live is PostgreSQL → same URL (shared PG until dedicated URL set)
      3. Sibling SQLite: <live_stem>_research.db  (lock-isolated from live)
    """
    raw = os.getenv("MARKET_EVENTS_RESEARCH_DB_URL", "").strip()
    live = resolve_market_events_db_config()

    if raw:
        if _is_postgres_url(raw):
            return ResearchDbConfig(
                backend="postgresql",
                url=raw,
                sqlite_path=None,
                config_source="env_research_url",
                separated=raw != live.url,
            )
        if raw.startswith("sqlite:///"):
            p = Path(raw.replace("sqlite:///", ""))
            return ResearchDbConfig(
                backend="sqlite",
                url=raw,
                sqlite_path=p,
                config_source="env_research_url",
                separated=str(p.resolve()) != str(live.sqlite_path.resolve()),
            )
        raise MarketEventsDbError(
            "MARKET_EVENTS_RESEARCH_DB_URL must be postgresql:// or sqlite:///",
        )

    if live.backend == "postgresql":
        return ResearchDbConfig(
            backend="postgresql",
            url=live.url,
            sqlite_path=None,
            config_source="live_postgres_shared",
            separated=False,
        )

    research_path = live.sqlite_path.with_name(f"{live.sqlite_path.stem}_research.db")
    return ResearchDbConfig(
        backend="sqlite",
        url=_sqlite_url(research_path),
        sqlite_path=research_path,
        config_source="sibling_sqlite",
        separated=True,
    )


def configure_unit_test_research_isolation(research_sqlite_path: Path) -> None:
    os.environ["MARKET_EVENTS_RESEARCH_DB_URL"] = _sqlite_url(research_sqlite_path)


@contextmanager
def research_connection(*, readonly: bool = False) -> Iterator[Any]:
    """Open research/analytics DB (never runs live DDL)."""
    cfg = resolve_research_db_config()
    if cfg.backend == "postgresql":
        wrapper = _connect_postgres(cfg.url)
        try:
            yield wrapper
            if not readonly:
                wrapper.commit()
            else:
                try:
                    wrapper.rollback()
                except Exception:
                    pass
        except Exception:
            try:
                wrapper.rollback()
            except Exception:
                pass
            raise
        finally:
            wrapper._conn.close()
        return

    assert cfg.sqlite_path is not None
    cfg.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    if readonly:
        conn = _connect_sqlite_g05(cfg.sqlite_path, readonly=True, create_dirs=True)
        try:
            yield conn
        finally:
            conn.close()
    else:
        conn = _connect_sqlite_g05(cfg.sqlite_path, readonly=False, create_dirs=True)
        try:
            yield conn
            retry_on_db_locked(conn.commit)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()


def _exec_ddl(conn: Any, ddl: str) -> None:
    from bot.research.market_events.schema_pg import sqlite_ddl_to_pg

    if isinstance(conn, PostgresBackend):
        pg_ddl = sqlite_ddl_to_pg(ddl)
        for stmt in pg_ddl.split(";"):
            s = stmt.strip()
            if s:
                try:
                    conn.execute(s)
                except Exception as exc:
                    # IF NOT EXISTS / already exists — ignore
                    msg = str(exc).lower()
                    if "already exists" in msg:
                        continue
                    raise
    else:
        conn.executescript(ddl)


def apply_research_migrations(conn: Any) -> list[str]:
    """Create/upgrade research analytics schema only (S56–S61)."""
    from bot.research.market_events.event_schema import (
        S56_POSTMORTEM_DDL,
        S57_MARKET_REGIME_DDL,
        S58_DECISION_TRACE_DDL,
        S59_FEATURE_LAB_DDL,
        S61_STRATEGY_DISCOVERY_DDL,
        S62_ALPHA_DISCOVERY_DDL,
        _ensure_s56_postmortem,
        _ensure_s57_market_regime,
        _ensure_s58_decision_trace,
        _ensure_s59_feature_lab,
        _ensure_s61_strategy_discovery,
        _ensure_s62_alpha_discovery,
    )

    applied: list[str] = []
    now = int(time.time())
    _exec_ddl(
        conn,
        f"""
        CREATE TABLE IF NOT EXISTS {RESEARCH_MIGRATIONS_TABLE} (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL
        );
        """,
    )
    try:
        row = conn.execute(
            f"SELECT MAX(version) AS v FROM {RESEARCH_MIGRATIONS_TABLE}",
        ).fetchone()
        current = int((row["v"] if row and row["v"] is not None else 0) or 0)
    except Exception:
        current = 0

    steps: list[tuple[int, str, Any]] = [
        (66, "S56 trade postmortem snapshots and rule suggestions", S56_POSTMORTEM_DDL),
        (67, "S57 market regime intelligence", S57_MARKET_REGIME_DDL),
        (68, "S58 trade decision trace", S58_DECISION_TRACE_DDL),
        (69, "S59 feature laboratory", S59_FEATURE_LAB_DDL),
        (70, "S60 research storage separation", """
CREATE TABLE IF NOT EXISTS market_events_research_ops_s60 (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""),
        (71, "S61 strategy discovery engine", S61_STRATEGY_DISCOVERY_DDL),
        (72, "S62 alpha discovery engine", S62_ALPHA_DISCOVERY_DDL),
    ]

    for ver, desc, ddl in steps:
        if current < ver:
            _exec_ddl(conn, ddl)
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {RESEARCH_MIGRATIONS_TABLE}
                  (version, applied_at, description)
                VALUES (?, ?, ?)
                """,
                (ver, str(now), desc),
            )
            applied.append(f"v{ver}")
            current = ver

    # Idempotent ensure (SQLite-friendly helpers)
    try:
        _ensure_s56_postmortem(conn)
        _ensure_s57_market_regime(conn)
        _ensure_s58_decision_trace(conn)
        _ensure_s59_feature_lab(conn)
        _ensure_s61_strategy_discovery(conn)
        _ensure_s62_alpha_discovery(conn)
    except Exception as exc:
        logger.warning("s60 research ensure helpers: %s", exc)

    try:
        conn.commit()
    except Exception:
        pass
    return applied


class ResearchRepository:
    """Facade for analytics/research storage (S56–S59)."""

    def __init__(self, cfg: ResearchDbConfig | None = None) -> None:
        self.cfg = cfg or resolve_research_db_config()

    def connection(self, *, readonly: bool = False):
        return research_connection(readonly=readonly)

    def migrate(self) -> dict[str, Any]:
        with research_connection() as conn:
            applied = apply_research_migrations(conn)
            try:
                conn.commit()
            except Exception:
                pass
        return {
            "ok": True,
            "backend": self.cfg.backend,
            "url_source": self.cfg.config_source,
            "separated": self.cfg.separated,
            "applied": applied,
            "schema_version": RESEARCH_SCHEMA_VERSION,
        }

    def status(self) -> dict[str, Any]:
        cfg = resolve_research_db_config()
        live = resolve_market_events_db_config()
        info: dict[str, Any] = {
            "research_backend": cfg.backend,
            "research_source": cfg.config_source,
            "separated": cfg.separated,
            "live_backend": live.backend,
            "schema_target": RESEARCH_SCHEMA_VERSION,
        }
        try:
            with research_connection(readonly=True) as conn:
                row = conn.execute(
                    f"SELECT MAX(version) AS v FROM {RESEARCH_MIGRATIONS_TABLE}",
                ).fetchone()
                info["research_version"] = int(row["v"] or 0) if row else 0
        except Exception as exc:
            info["research_version"] = None
            info["error"] = str(exc)[:120]
        return info


def get_research_repository() -> ResearchRepository:
    return ResearchRepository()


def write_close_analytics(live_conn: Any, *, trade_row: Any, now: int) -> None:
    """S56 snapshot + S58 finalize → research DB only (never blocks live DDL)."""
    from bot.research.market_events.signal_intelligence.decision_trace_s58 import (
        finalize_decision_on_close,
    )
    from bot.research.market_events.signal_intelligence.trade_postmortem_s56 import (
        record_close_snapshot,
    )

    t = dict(trade_row) if not isinstance(trade_row, dict) else dict(trade_row)
    try:
        t = {k: trade_row[k] for k in trade_row.keys()}  # type: ignore[attr-defined]
    except Exception:
        pass

    features: dict[str, Any] = {}
    try:
        fr = live_conn.execute(
            """
            SELECT * FROM market_events_trade_features_s55
            WHERE s40_signal_type = ? AND s40_signal_id = ?
            """,
            (str(t.get("s40_signal_type") or ""), int(t.get("s40_signal_id") or 0)),
        ).fetchone()
        if fr:
            features = dict(fr)
    except Exception:
        features = {}

    try:
        with research_connection() as rconn:
            record_close_snapshot(
                rconn,
                trade_row=t,
                now=now,
                trigger_postmortem=True,
                features=features,
            )
            finalize_decision_on_close(
                rconn,
                paper_trade_id=int(t.get("id") or 0),
                exit_reason=t.get("exit_reason"),
                duration_sec=int(t.get("holding_seconds") or 0) or None,
                max_profit_pct=t.get("mfe_pct"),
                max_drawdown_pct=t.get("mae_pct"),
                final_pnl_usd=t.get("pnl_usd"),
                final_pnl_pct=t.get("pnl_pct"),
                closed_at=now,
                now=now,
            )
            try:
                rconn.commit()
            except Exception:
                pass
    except Exception as exc:
        logger.warning("s60 write_close_analytics failed: %s", exc)


def write_open_decision(
    live_conn: Any,
    *,
    paper_trade_id: int,
    s40_signal_type: str,
    s40_signal_id: int,
    features: dict[str, Any],
    estimate: dict[str, Any],
    gate_decision: str,
    entry_price: float | None,
    opened_at: int,
    open_count: int | None,
    now: int,
) -> None:
    """S58 decision trace → research DB only."""
    from bot.research.market_events.signal_intelligence.decision_trace_s58 import (
        _candles_emas,
        record_decision_on_open,
    )

    emas = _candles_emas(live_conn, str(features.get("symbol") or ""))
    try:
        with research_connection() as rconn:
            record_decision_on_open(
                rconn,
                paper_trade_id=paper_trade_id,
                s40_signal_type=s40_signal_type,
                s40_signal_id=s40_signal_id,
                features=features,
                estimate=estimate,
                gate_decision=gate_decision,
                entry_price=entry_price,
                opened_at=opened_at,
                open_count=open_count,
                now=now,
                emas=emas,
            )
            try:
                rconn.commit()
            except Exception:
                pass
    except Exception as exc:
        logger.warning("s60 write_open_decision failed: %s", exc)


__all__ = [
    "RESEARCH_SCHEMA_VERSION",
    "ResearchDbConfig",
    "ResearchRepository",
    "apply_research_migrations",
    "configure_unit_test_research_isolation",
    "get_research_repository",
    "research_connection",
    "resolve_research_db_config",
    "write_close_analytics",
    "write_open_decision",
]
