"""Deterministic market price access from production source DB.

No APIs. Uses only existing source DB tables (e.g. market_prices).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Iterator

from bot.research.futures.source_reader import SourceReader


@dataclass(frozen=True)
class MarketPriceColumnMap:
    table: str
    ts_col: str
    symbol_col: str
    price_col: str

    def select_sql(self) -> str:
        return f"{self.ts_col} AS ts, {self.symbol_col} AS symbol, {self.price_col} AS price"


_TS_COLS = ("ts", "timestamp", "time", "created_at", "collected_at")
_SYMBOL_COLS = ("symbol", "ticker", "asset", "base_symbol")
_PRICE_COLS = ("price", "close", "mark_price", "spot_price", "mid_price")


def _pick(cols: list[str], candidates: tuple[str, ...]) -> str | None:
    lower = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


def resolve_market_price_columns(reader: SourceReader, table: str = "market_prices") -> MarketPriceColumnMap | None:
    tables = reader.list_source_tables()
    info = tables.get(table)
    if not info or not info.get("exists"):
        return None
    cols: list[str] = info.get("columns") or []
    ts_col = _pick(cols, _TS_COLS)
    symbol_col = _pick(cols, _SYMBOL_COLS)
    price_col = _pick(cols, _PRICE_COLS)
    if not ts_col or not symbol_col or not price_col:
        return None
    return MarketPriceColumnMap(table=table, ts_col=ts_col, symbol_col=symbol_col, price_col=price_col)


def iter_market_prices(
    reader: SourceReader,
    *,
    symbol: str,
    start_ts: int,
    end_ts: int,
    table: str = "market_prices",
) -> Iterator[dict[str, Any]]:
    mapping = resolve_market_price_columns(reader, table=table)
    if mapping is None:
        return

    # We purposely only rely on simple >= / <= for portability.
    if getattr(reader, "info", None) is not None and reader.info.backend == "postgres":
        cur = reader._conn.cursor()  # type: ignore[attr-defined]
        cur.execute(
            f"""
            SELECT {mapping.select_sql()}
            FROM {mapping.table}
            WHERE {mapping.symbol_col} = %s
              AND EXTRACT(EPOCH FROM {mapping.ts_col}::timestamptz) >= %s
              AND EXTRACT(EPOCH FROM {mapping.ts_col}::timestamptz) <= %s
            ORDER BY {mapping.ts_col} ASC
            """,
            (symbol, start_ts, end_ts),
        )
        for row in cur:
            d = dict(row)
            yield {"ts": int(d["ts"]), "symbol": str(d["symbol"]), "price": float(d["price"])}
        return

    conn = reader._conn  # type: ignore[attr-defined]
    prev_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        for row in conn.execute(
            f"""
            SELECT {mapping.select_sql()}
            FROM {mapping.table}
            WHERE {mapping.symbol_col} = ?
              AND CAST({mapping.ts_col} AS INTEGER) >= ?
              AND CAST({mapping.ts_col} AS INTEGER) <= ?
            ORDER BY {mapping.ts_col} ASC
            """,
            (symbol, int(start_ts), int(end_ts)),
        ):
            d = dict(row)
            yield {"ts": int(d["ts"]), "symbol": str(d["symbol"]), "price": float(d["price"])}
    finally:
        conn.row_factory = prev_factory

