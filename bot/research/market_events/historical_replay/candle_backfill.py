"""Task B — isolated historical candle store and backfill."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.historical_replay.constants import REPLAY_DATA_SOURCE


def historical_candle_coverage(conn: Any) -> str:
    rows = conn.execute(
        """
        SELECT venue, symbol, timeframe, source,
               COUNT(*) AS n, MIN(open_ts) AS t0, MAX(open_ts) AS t1
        FROM market_events_historical_candles
        GROUP BY venue, symbol, timeframe, source
        ORDER BY symbol
        """,
    ).fetchall()
    checkpoints = conn.execute(
        """
        SELECT venue, symbol, timeframe, source, status, rows_fetched, gaps_found,
               start_ts, end_ts, last_cursor_ts
        FROM market_events_candle_backfill_checkpoints
        ORDER BY updated_at DESC LIMIT 20
        """,
    ).fetchall()
    lines = [
        "HISTORICAL CANDLE COVERAGE",
        f"mode: {REPLAY_DATA_SOURCE}",
        "",
    ]
    if not rows:
        lines.append("No historical candles stored yet.")
    else:
        for r in rows:
            span = int(r["t1"]) - int(r["t0"]) if r["t1"] and r["t0"] else 0
            lines.append(
                f"  {r['symbol']} venue={r['venue']} tf={r['timeframe']} source={r['source']} "
                f"rows={r['n']} span_sec={span}",
            )
    lines.append("")
    lines.append("Recent backfill checkpoints:")
    if not checkpoints:
        lines.append("  (none)")
    for c in checkpoints:
        lines.append(
            f"  {c['symbol']} {c['status']} rows={c['rows_fetched']} gaps={c['gaps_found']} "
            f"cursor={c['last_cursor_ts']}",
        )
    return "\n".join(lines)


def run_candle_backfill(
    conn: Any,
    *,
    symbols: list[str],
    asset_class: str | None = None,
    start_ts: int,
    end_ts: int,
    timeframe: str = "1m",
    venue: str = "binance_futures",
) -> dict[str, int]:
    """Idempotent Binance futures kline backfill into market_events_historical_candles."""
    from bot.research.futures_agent.historical_candles import (
        BinanceCandleProvider,
        candle_from_binance,
        count_gaps,
    )

    if end_ts <= start_ts:
        raise ValueError("end must be after start")

    stats = {"symbols": 0, "rows": 0, "gaps": 0, "errors": 0}
    provider = BinanceCandleProvider()

    target_symbols = symbols
    if not target_symbols and asset_class:
        rows = conn.execute(
            """
            SELECT DISTINCT canonical_asset FROM market_events_instruments
            WHERE asset_class = ? AND active = 1
            """,
            (asset_class,),
        ).fetchall()
        target_symbols = [r["canonical_asset"] for r in rows]

    for sym in target_symbols:
        stats["symbols"] += 1
        pair = f"{sym.upper()}USDT" if not sym.upper().endswith("USDT") else sym.upper()
        source = "binance_futures"
        cp = conn.execute(
            """
            SELECT id, last_cursor_ts, rows_fetched FROM market_events_candle_backfill_checkpoints
            WHERE venue = ? AND symbol = ? AND timeframe = ? AND start_ts = ? AND end_ts = ? AND source = ?
            """,
            (venue, sym.upper(), timeframe, start_ts, end_ts, source),
        ).fetchone()
        now = int(time.time())
        if cp:
            checkpoint_id = int(cp["id"])
            cursor = int(cp["last_cursor_ts"] or end_ts)
            rows_before = int(cp["rows_fetched"] or 0)
        else:
            checkpoint_id = insert_returning_id(
                conn,
                """
                INSERT INTO market_events_candle_backfill_checkpoints (
                  venue, symbol, timeframe, source, start_ts, end_ts,
                  last_cursor_ts, status, rows_fetched, gaps_found, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', 0, 0, ?, ?)
                """,
                (venue, sym.upper(), timeframe, source, start_ts, end_ts, end_ts, now, now),
            )
            cursor = end_ts
            rows_before = 0

        try:
            candles, meta = provider.fetch_range(pair, start_ts, cursor, interval=timeframe)
            locked_source = meta.get("data_source") or source
            inserted = 0
            from bot.research.market_events.historical_replay.candle_backfill import insert_candle_ignore
            for c in candles:
                if c.open_ts < start_ts or c.open_ts > end_ts:
                    continue
                if insert_candle_ignore(
                    conn, venue=venue, symbol=sym.upper(), timeframe=timeframe,
                    open_ts=c.open_ts, o=c.open, h=c.high, l=c.low, c=c.close,
                    source=locked_source,
                    volume=getattr(c, "volume", None),
                ):
                    inserted += 1
            gaps = count_gaps(candles)
            stats["rows"] += inserted
            stats["gaps"] += gaps
            conn.execute(
                """
                UPDATE market_events_candle_backfill_checkpoints SET
                  last_cursor_ts = ?, status = 'complete', rows_fetched = ?,
                  gaps_found = ?, updated_at = ?
                WHERE id = ?
                """,
                (start_ts, rows_before + inserted, gaps, now, checkpoint_id),
            )
        except Exception as exc:
            stats["errors"] += 1
            conn.execute(
                """
                UPDATE market_events_candle_backfill_checkpoints SET
                  status = 'failed', error = ?, updated_at = ?
                WHERE id = ?
                """,
                (str(exc)[:500], int(time.time()), checkpoint_id),
            )
    return stats


def insert_candle_ignore(conn: Any, *, venue: str, symbol: str, timeframe: str,
                         open_ts: int, o: float, h: float, l: float, c: float,
                         source: str, volume: float | None = None) -> bool:
    """Backend-neutral candle insert for tests."""
    from bot.research.market_events.db import connection_is_postgres

    now = int(time.time())
    params = (venue, symbol, timeframe, open_ts, o, h, l, c, volume, source, now)
    if connection_is_postgres(conn):
        row = conn.execute(
            """
            INSERT INTO market_events_historical_candles (
              venue, symbol, timeframe, open_ts, open, high, low, close, volume,
              source, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (venue, symbol, timeframe, open_ts) DO NOTHING
            RETURNING id
            """,
            params,
        ).fetchone()
        return row is not None
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO market_events_historical_candles (
          venue, symbol, timeframe, open_ts, open, high, low, close, volume,
          source, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        params,
    )
    return cur.rowcount > 0
