"""Phase E.5.3 — synthetic events for offline ops validation."""

from __future__ import annotations

import time
from typing import Any


def seed_demo_candles(conn: Any, *, symbol: str = "SOL", n: int = 60) -> None:
    """Seed deterministic candles so G1/F2 pipeline can run offline."""
    now = int(time.time())
    price = 100.0
    for i in range(n):
        ts = now - (n - i) * 300
        if i >= n - 8:
            o = price
            price *= 0.985
            c = price
            vol = 5000.0 + i * 100
        else:
            o = price
            price *= 0.999
            c = price
            vol = 1200.0
        h = max(o, c) * 1.002
        l = min(o, c) * 0.998
        conn.execute(
            """
            INSERT OR IGNORE INTO market_events_historical_candles (
              venue, symbol, timeframe, open_ts, open, high, low, close, volume, source, fetched_at
            ) VALUES ('binance_futures', ?, '5m', ?, ?, ?, ?, ?, ?, 'demo', ?)
            """,
            (symbol, ts, o, h, l, c, vol, now),
        )


def create_synthetic_shock_event(
    conn: Any,
    *,
    symbol: str = "SOL",
    run_tag: str = "e53",
    event_ts: int | None = None,
) -> int:
    """Insert a deterministic synthetic shock event (no market polling)."""
    now = event_ts if event_ts is not None else int(time.time())
    dedup = f"{run_tag}-synthetic-{now}-{symbol}"
    return int(conn.execute(
        """
        INSERT INTO market_events (
          event_ts, detected_ts, venue, symbol, direction, phase,
          trigger_window_seconds, return_pct, classification,
          btc_return_pct, relative_return_pct, volume_zscore,
          session_regime, asset_class, detector_version,
          detector_triggers_json, dedup_key, created_at
        ) VALUES (?, ?, 'binance_futures', ?, 'DOWN', 'SHOCK_DETECTED',
          60, -3.8, 'ASSET_SPECIFIC', 0.1, -3.9, 2.4,
          'US', 'CRYPTO', 'v1', '["SHOCK_A"]', ?, ?)
        """,
        (now, now, symbol, dedup, now),
    ).lastrowid)
