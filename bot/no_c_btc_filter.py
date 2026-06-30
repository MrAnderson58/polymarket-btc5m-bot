"""Adaptive BTC-up entry threshold for NO_C live trading."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from bot.config import effective_entry_threshold
from bot.no_c_filter_shadow import NO_C_STRATEGY, _btc_price_at

logger = logging.getLogger(__name__)

LOOKBACK_SEC = 30
BTC_MOVE_STRICT_USD = 5.0
NO_C_NORMAL_THRESHOLD = 0.40
NO_C_STRICT_THRESHOLD = 0.36
SKIP_REASON_STRICT_PRICE = "STRICT_PRICE"


@dataclass(frozen=True)
class NoCFilterDecision:
    mode: str
    btc_move: float | None
    threshold: float


def compute_btc_move_30s(
    conn: sqlite3.Connection,
    *,
    current_btc: float,
    now_ts: int,
) -> float | None:
    past_btc = _btc_price_at(conn, now_ts - LOOKBACK_SEC)
    if past_btc is None:
        return None
    return current_btc - past_btc


def resolve_no_c_filter(
    conn: sqlite3.Connection,
    *,
    current_btc: float,
    now_ts: int,
) -> NoCFilterDecision:
    btc_move = compute_btc_move_30s(conn, current_btc=current_btc, now_ts=now_ts)
    normal_threshold = effective_entry_threshold(NO_C_NORMAL_THRESHOLD)
    if btc_move is None or btc_move <= BTC_MOVE_STRICT_USD:
        return NoCFilterDecision(
            mode="NORMAL",
            btc_move=btc_move,
            threshold=normal_threshold,
        )
    return NoCFilterDecision(
        mode="STRICT",
        btc_move=btc_move,
        threshold=effective_entry_threshold(NO_C_STRICT_THRESHOLD),
    )


def resolve_no_c_entry_threshold(
    conn: sqlite3.Connection,
    signal_name: str,
    base_threshold: float,
    *,
    btc_price: float | None,
    now_ts: int | None,
) -> float:
    if signal_name != NO_C_STRATEGY or btc_price is None or now_ts is None:
        return effective_entry_threshold(base_threshold)
    return resolve_no_c_filter(conn, current_btc=btc_price, now_ts=now_ts).threshold


def log_no_c_filter_mode(decision: NoCFilterDecision) -> None:
    move_text = "n/a" if decision.btc_move is None else f"{decision.btc_move:+.1f}"
    logger.info(
        "\n".join(
            [
                "NO_C FILTER",
                "",
                f"mode={decision.mode}",
                "",
                f"btc_move={move_text}",
                "",
                f"threshold={decision.threshold:.2f}",
            ]
        )
    )


def log_no_c_filter_skipped(
    decision: NoCFilterDecision,
    *,
    ask: float,
) -> None:
    move_text = "n/a" if decision.btc_move is None else f"{decision.btc_move:+.1f}"
    logger.info(
        "\n".join(
            [
                "NO_C FILTER",
                "",
                "SKIPPED",
                "",
                f"reason={SKIP_REASON_STRICT_PRICE}",
                "",
                f"btc_move={move_text}",
                "",
                f"ask={ask:.2f}",
                "",
                f"required<={decision.threshold:.2f}",
            ]
        )
    )


def _ensure_live_counters_row(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO no_c_filter_live_counters (id)
        VALUES (1)
        ON CONFLICT(id) DO NOTHING
        """
    )


def record_no_c_filter_normal_entry(conn: sqlite3.Connection) -> None:
    _ensure_live_counters_row(conn)
    conn.execute(
        """
        UPDATE no_c_filter_live_counters
        SET normal_entries = normal_entries + 1
        WHERE id = 1
        """
    )


def record_no_c_filter_strict_entry(conn: sqlite3.Connection) -> None:
    _ensure_live_counters_row(conn)
    conn.execute(
        """
        UPDATE no_c_filter_live_counters
        SET strict_entries = strict_entries + 1
        WHERE id = 1
        """
    )


def record_no_c_filter_skipped_strict_price(conn: sqlite3.Connection) -> None:
    _ensure_live_counters_row(conn)
    conn.execute(
        """
        UPDATE no_c_filter_live_counters
        SET skipped_strict_price = skipped_strict_price + 1
        WHERE id = 1
        """
    )


def fetch_no_c_filter_live_stats(conn: sqlite3.Connection) -> tuple[int, int, int]:
    row = conn.execute(
        """
        SELECT normal_entries, strict_entries, skipped_strict_price
        FROM no_c_filter_live_counters
        WHERE id = 1
        """
    ).fetchone()
    if row is None:
        return 0, 0, 0
    return (
        int(row["normal_entries"]),
        int(row["strict_entries"]),
        int(row["skipped_strict_price"]),
    )


def format_no_c_filter_live_block(conn: sqlite3.Connection) -> str:
    normal, strict, skipped = fetch_no_c_filter_live_stats(conn)
    return "\n".join(
        [
            "NO_C FILTER LIVE",
            "",
            f"Normal entries: {normal}",
            f"Strict entries: {strict}",
            f"Skipped by strict price: {skipped}",
        ]
    )


def log_no_c_filter_live_config() -> None:
    logger.info(
        "NO_C adaptive filter | lookback=%ss | strict_if_move>%.0f USD | "
        "normal<=%.2f | strict<=%.2f",
        LOOKBACK_SEC,
        BTC_MOVE_STRICT_USD,
        effective_entry_threshold(NO_C_NORMAL_THRESHOLD),
        effective_entry_threshold(NO_C_STRICT_THRESHOLD),
    )
