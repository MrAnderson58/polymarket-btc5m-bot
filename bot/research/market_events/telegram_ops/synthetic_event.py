"""Phase E.5.3 — synthetic events for offline ops validation."""

from __future__ import annotations

import time
from typing import Any


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
