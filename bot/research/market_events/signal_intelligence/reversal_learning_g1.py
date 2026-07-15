"""Phase G.1 — reversal learning after paper trades."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.signal_intelligence.candles import load_recent_candles
from bot.research.market_events.signal_intelligence.trend_windows_g1 import (
    analyze_consecutive_pattern,
)


def _pattern_key(
    *,
    symbol: str,
    consecutive: int,
    direction: str,
    window_minutes: int,
    signal_type: str,
) -> str:
    return f"{symbol}|{signal_type}|{window_minutes}m|{consecutive}_{direction}"


def _candles_to_reversal(
    conn: Any,
    *,
    symbol: str,
    entry_ts: int,
    shock_direction: str,
    venue: str = "binance_futures",
) -> tuple[int | None, float | None]:
    """How many 5m candles until price reverses against shock direction."""
    bars = load_recent_candles(conn, symbol=symbol, venue=venue, timeframe="5m", limit=120)
    if not bars:
        return None, None

    entry_idx = None
    for i, b in enumerate(bars):
        if b.open_ts >= entry_ts:
            entry_idx = i
            break
    if entry_idx is None:
        return None, None

    entry_price = bars[entry_idx].close
    if entry_price <= 0:
        return None, None

    expect_up = shock_direction == "DOWN"  # reversal after down shock = up move
    for j in range(entry_idx + 1, len(bars)):
        move_pct = (bars[j].close / entry_price - 1.0) * 100.0
        if expect_up and move_pct >= 0.5:
            return j - entry_idx, move_pct
        if not expect_up and move_pct <= -0.5:
            return j - entry_idx, move_pct
    return None, None


def record_reversal_learning_g1(
    conn: Any,
    *,
    event_id: int,
    symbol: str,
    entry_ts: int,
    shock_direction: str,
    net_return: float,
) -> None:
    """Called after paper close — record candles-to-reversal stats."""
    from bot.research.market_events.signal_intelligence.config import G1_ENABLED

    if not G1_ENABLED:
        return

    g1 = conn.execute(
        "SELECT signal_type, consecutive_pattern_json FROM market_events_liquidity_trend_g1 WHERE event_id = ?",
        (event_id,),
    ).fetchone()

    signal_type = str(g1["signal_type"]) if g1 else "UNKNOWN"
    consecutive = 0
    window_minutes = 15
    direction = shock_direction

    if g1 and g1["consecutive_pattern_json"]:
        import json
        try:
            patterns = json.loads(g1["consecutive_pattern_json"])
            if patterns:
                best = max(patterns, key=lambda p: p.get("max_streak", 0))
                consecutive = int(best.get("max_streak", 0))
                window_minutes = int(best.get("window_minutes", 15))
                direction = "DOWN" if best.get("max_streak_color") == "red" else "UP"
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    candles, rev_pct = _candles_to_reversal(
        conn, symbol=symbol, entry_ts=entry_ts, shock_direction=shock_direction,
    )
    if candles is None:
        return

    pattern_key = _pattern_key(
        symbol=symbol,
        consecutive=consecutive,
        direction=direction,
        window_minutes=window_minutes,
        signal_type=signal_type,
    )
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO market_events_reversal_learning_g1 (
          event_id, symbol, pattern_key, consecutive_red, consecutive_green,
          window_minutes, candles_to_reversal, reversal_pct, signal_type, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, symbol, pattern_key,
            consecutive if direction == "DOWN" else 0,
            consecutive if direction == "UP" else 0,
            window_minutes, candles, rev_pct, signal_type, now,
        ),
    )

    row = conn.execute(
        "SELECT samples, avg_candles_to_reversal, reversal_rate FROM market_events_g1_pattern_stats WHERE pattern_key = ?",
        (pattern_key,),
    ).fetchone()
    won = net_return > 0
    if row:
        n = int(row["samples"]) + 1
        avg_c = (float(row["avg_candles_to_reversal"]) * int(row["samples"]) + candles) / n
        wr = (float(row["reversal_rate"]) * int(row["samples"]) + (1 if won else 0)) / n
        conn.execute(
            """
            UPDATE market_events_g1_pattern_stats
            SET samples = ?, avg_candles_to_reversal = ?, reversal_rate = ?, updated_at = ?
            WHERE pattern_key = ?
            """,
            (n, round(avg_c, 2), round(wr, 3), now, pattern_key),
        )
    else:
        conn.execute(
            """
            INSERT INTO market_events_g1_pattern_stats (
              pattern_key, samples, avg_candles_to_reversal, reversal_rate, updated_at
            ) VALUES (?, 1, ?, ?, ?)
            """,
            (pattern_key, float(candles), 1.0 if won else 0.0, now),
        )


def lookup_historical_reversal_rate(conn: Any, pattern_key: str) -> float | None:
    """Lookup WR for a pattern key with S3.1 alias compatibility (read-only)."""
    row = conn.execute(
        "SELECT reversal_rate, samples FROM market_events_g1_pattern_stats WHERE pattern_key = ?",
        (pattern_key,),
    ).fetchone()
    if row and int(row["samples"]) >= 3:
        return float(row["reversal_rate"])

    # Compatibility: try canonical + legacy aliases without rewriting stored keys.
    try:
        from bot.research.market_events.signal_intelligence.pattern_keys_s31 import (
            expand_pattern_key_aliases_s31,
            parse_pattern_key_s31,
            pattern_key_match_s31,
        )
        aliases = expand_pattern_key_aliases_s31(pattern_key)
        for aka in aliases:
            if aka == pattern_key:
                continue
            row = conn.execute(
                "SELECT reversal_rate, samples FROM market_events_g1_pattern_stats WHERE pattern_key = ?",
                (aka,),
            ).fetchone()
            if row and int(row["samples"]) >= 3:
                return float(row["reversal_rate"])

        parsed = parse_pattern_key_s31(pattern_key)
        rows = conn.execute(
            "SELECT pattern_key, reversal_rate, samples FROM market_events_g1_pattern_stats "
            "WHERE pattern_key LIKE ?",
            (f"{parsed['symbol']}|%",),
        ).fetchall()
        best = None
        for r in rows:
            if int(r["samples"] or 0) < 3:
                continue
            if pattern_key_match_s31(
                str(r["pattern_key"]),
                want_symbol=parsed["symbol"],
                want_family=parsed["family"],
                want_tf=parsed["timeframe"],
            ):
                if best is None or int(r["samples"]) > int(best["samples"]):
                    best = r
        if best is not None:
            return float(best["reversal_rate"])
    except Exception:
        pass
    return None
