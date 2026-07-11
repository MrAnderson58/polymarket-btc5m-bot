"""Shared helpers for F.0 tests."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from bot.research.market_events.db import market_events_connection


def make_db() -> tuple[tempfile.TemporaryDirectory, Path]:
    tmp = tempfile.TemporaryDirectory()
    return tmp, Path(tmp.name) / "f0.db"


def seed_event(conn, *, symbol: str = "SUI", ret: float = -8.4) -> int:
    now = int(time.time())
    return int(conn.execute(
        """
        INSERT INTO market_events (
          event_ts, detected_ts, venue, symbol, direction, phase,
          trigger_window_seconds, return_pct, classification,
          detector_version, detector_triggers_json, dedup_key, created_at
        ) VALUES (?, ?, 'binance_futures', ?, 'DOWN', 'SHOCK_DETECTED',
          900, ?, 'ASSET_SPECIFIC', 'v1', '["SHOCK_A"]', ?, ?)
        """,
        (now, now, symbol, ret, f"dedup-f0-{now}-{symbol}", now),
    ).lastrowid)


def seed_candles(conn, *, symbol: str = "SUI", n: int = 60, trend: float = 0.02, shock: bool = True) -> None:
    now = int(time.time())
    price = 100.0
    for i in range(n):
        ts = now - (n - i) * 300
        if shock and i == n - 1:
            o = price
            c = price * 1.06
            h = c * 1.001
            l = o * 0.999
            vol = 8000.0
        else:
            o = price
            price *= 1.0 + (0.001 if shock else trend)
            c = price
            h = max(o, c) * 1.002
            l = min(o, c) * 0.998
            vol = 1000.0 + i * 50
        conn.execute(
            """
            INSERT OR IGNORE INTO market_events_historical_candles (
              venue, symbol, timeframe, open_ts, open, high, low, close, volume, source, fetched_at
            ) VALUES ('binance_futures', ?, '5m', ?, ?, ?, ?, ?, ?, 'test', ?)
            """,
            (symbol, ts, o, h, l, c, vol, now),
        )


def conn_ctx(db_path: Path):
    return market_events_connection(db_path=db_path)
