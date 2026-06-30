"""NO_C BTC-up filter shadow — log WOULD_BLOCK stats without blocking entries."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

logger = logging.getLogger(__name__)

NO_C_STRATEGY = "NO_C"
LOOKBACK_SECS: tuple[int, ...] = (30, 60, 90)
THRESHOLDS_USD: tuple[float, ...] = (5.0, 10.0, 15.0, 20.0)
MAX_BTC_LOOKUP_DELTA_SEC = 10


@dataclass(frozen=True)
class BtcMoveReading:
    lookback_sec: int
    move_usd: float
    current_btc: float
    past_btc: float


@dataclass(frozen=True)
class FilterShadowHit:
    lookback_sec: int
    threshold_usd: float
    move_usd: float


def _btc_price_at(conn: sqlite3.Connection, target_ts: int) -> float | None:
    row = conn.execute(
        """
        SELECT btc_price,
               abs(cast(strftime('%s', checked_at) AS integer) - ?) AS delta
        FROM market_checks
        ORDER BY delta ASC
        LIMIT 1
        """,
        (target_ts,),
    ).fetchone()
    if row is None or int(row["delta"]) > MAX_BTC_LOOKUP_DELTA_SEC:
        return None
    return float(row["btc_price"])


def compute_btc_moves(
    conn: sqlite3.Connection,
    *,
    current_btc: float,
    now_ts: int,
) -> list[BtcMoveReading]:
    readings: list[BtcMoveReading] = []
    for lookback_sec in LOOKBACK_SECS:
        past_btc = _btc_price_at(conn, now_ts - lookback_sec)
        if past_btc is None:
            continue
        readings.append(
            BtcMoveReading(
                lookback_sec=lookback_sec,
                move_usd=current_btc - past_btc,
                current_btc=current_btc,
                past_btc=past_btc,
            )
        )
    return readings


def _would_block_upward_move(move_usd: float, threshold_usd: float) -> bool:
    return move_usd > threshold_usd


def _evaluate_hits(readings: list[BtcMoveReading]) -> list[FilterShadowHit]:
    hits: list[FilterShadowHit] = []
    for reading in readings:
        for threshold_usd in THRESHOLDS_USD:
            if _would_block_upward_move(reading.move_usd, threshold_usd):
                hits.append(
                    FilterShadowHit(
                        lookback_sec=reading.lookback_sec,
                        threshold_usd=threshold_usd,
                        move_usd=reading.move_usd,
                    )
                )
    return hits


def _upsert_counter(
    conn: sqlite3.Connection,
    *,
    lookback_sec: int,
    threshold_usd: float,
    would_block: bool,
) -> None:
    conn.execute(
        """
        INSERT INTO no_c_filter_shadow_counters (
            lookback_sec, threshold_usd, check_count, would_block_count
        ) VALUES (?, ?, 1, ?)
        ON CONFLICT(lookback_sec, threshold_usd) DO UPDATE SET
            check_count = check_count + 1,
            would_block_count = would_block_count + excluded.would_block_count
        """,
        (lookback_sec, threshold_usd, int(would_block)),
    )


def _record_entry_snapshot(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    entry_ts: int,
    entry_price: float,
    current_btc: float,
    readings: list[BtcMoveReading],
) -> None:
    moves = {reading.lookback_sec: reading.move_usd for reading in readings}
    conn.execute(
        """
        INSERT INTO no_c_filter_shadow_entries (
            market_slug, entry_ts, entry_price, current_btc,
            btc_move_30s, btc_move_60s, btc_move_90s
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            market_slug,
            entry_ts,
            entry_price,
            current_btc,
            moves.get(30),
            moves.get(60),
            moves.get(90),
        ),
    )


def record_no_c_filter_shadow(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    entry_ts: int,
    entry_price: float,
    current_btc: float,
) -> list[FilterShadowHit]:
    """
    Evaluate BTC-up filter shadow rules before a NO_C entry.

    Always returns hits for logging; never blocks the caller.
    """
    readings = compute_btc_moves(conn, current_btc=current_btc, now_ts=entry_ts)
    hits = _evaluate_hits(readings)
    hits_by_key = {(hit.lookback_sec, hit.threshold_usd): hit for hit in hits}

    for lookback_sec in LOOKBACK_SECS:
        reading = next((item for item in readings if item.lookback_sec == lookback_sec), None)
        for threshold_usd in THRESHOLDS_USD:
            hit = hits_by_key.get((lookback_sec, threshold_usd))
            would_block = hit is not None
            if reading is None:
                continue
            _upsert_counter(
                conn,
                lookback_sec=lookback_sec,
                threshold_usd=threshold_usd,
                would_block=would_block,
            )
            if would_block and hit is not None:
                logger.info(
                    "NO_C_FILTER_SHADOW | WOULD_BLOCK | market=%s | lookback=%ss | "
                    "btc_move=+%.2f | threshold=%.0f | entry_price=%.3f",
                    market_slug,
                    lookback_sec,
                    hit.move_usd,
                    threshold_usd,
                    entry_price,
                )

    if readings:
        _record_entry_snapshot(
            conn,
            market_slug=market_slug,
            entry_ts=entry_ts,
            entry_price=entry_price,
            current_btc=current_btc,
            readings=readings,
        )
    else:
        logger.info(
            "NO_C_FILTER_SHADOW | SKIP | market=%s | insufficient BTC history",
            market_slug,
        )

    return hits


def fetch_filter_shadow_counters(
    conn: sqlite3.Connection,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT lookback_sec, threshold_usd, check_count, would_block_count
        FROM no_c_filter_shadow_counters
        ORDER BY lookback_sec ASC, threshold_usd ASC
        """
    ).fetchall()


def fetch_filter_shadow_entry_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS count FROM no_c_filter_shadow_entries").fetchone()
    return int(row["count"]) if row is not None else 0


def format_no_c_filter_shadow_report(conn: sqlite3.Connection) -> str:
    entries = fetch_filter_shadow_entry_count(conn)
    rows = fetch_filter_shadow_counters(conn)

    lines = [
        "NO_C FILTER SHADOW",
        "",
        f"Entries evaluated: {entries}",
        "",
        f"{'Lookback':<10} {'Threshold':>10} {'Checks':>8} {'WOULD_BLOCK':>12} {'Block %':>8}",
    ]

    if not rows:
        lines.append("(no data yet)")
        return "\n".join(lines)

    for row in rows:
        checks = int(row["check_count"])
        blocks = int(row["would_block_count"])
        block_pct = blocks / checks if checks else 0.0
        lines.append(
            f"{int(row['lookback_sec'])}s{'':<7} "
            f"${row['threshold_usd']:>8.0f} "
            f"{checks:>8} "
            f"{blocks:>12} "
            f"{block_pct:>7.0%}"
        )

    lines.extend(
        [
            "",
            "Rule: WOULD_BLOCK when BTC move up exceeds threshold over lookback window.",
            "Entries are never blocked — statistics only.",
        ]
    )
    return "\n".join(lines)


def log_no_c_filter_shadow_report(conn: sqlite3.Connection) -> None:
    logger.info("\n%s", format_no_c_filter_shadow_report(conn))
