"""Central research SQLite write path — serialized, no manual BEGIN IMMEDIATE."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

from bot.research.market_events.db import retry_on_db_locked
from bot.research.market_events.research_db_session import research_write_lock

T = TypeVar("T")


@contextmanager
def research_write_transaction(conn: Any) -> Iterator[None]:
    """
    Exclusive research writer critical section with commit/rollback.
    Caller must NOT use BEGIN IMMEDIATE — lock + single commit only.
    """
    with research_write_lock():
        try:
            yield
            retry_on_db_locked(conn.commit)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise


def research_write_batch(conn: Any, fn: Callable[[Any], T]) -> T:
    """Run write callback under research_write_transaction."""
    with research_write_transaction(conn):
        return fn(conn)


def research_replace_table(
    conn: Any,
    *,
    delete_sql: str,
    insert_sql: str,
    rows: list[tuple[Any, ...]],
    delete_params: tuple[Any, ...] = (),
) -> int:
    """DELETE + executemany INSERT in one transaction."""

    def _run(c: Any) -> int:
        c.execute(delete_sql, delete_params)
        if rows:
            c.executemany(insert_sql, rows)
        return len(rows)

    return research_write_batch(conn, _run)


def research_executemany(conn: Any, sql: str, rows: list[tuple[Any, ...]]) -> int:
    """Executemany under write manager."""

    def _run(c: Any) -> int:
        if rows:
            c.executemany(sql, rows)
        return len(rows)

    return research_write_batch(conn, _run)


__all__ = [
    "research_executemany",
    "research_replace_table",
    "research_write_batch",
    "research_write_transaction",
]
