"""Persist market behavior research results."""

from __future__ import annotations

import sqlite3

from bot.research.market_behavior.models import (
    AnalysisReport,
    LateWindowSnapshot,
    MarketSummary,
    TpAggregate,
)
from bot.research.market_behavior.schema import (
    LATE_WINDOW_TABLE,
    SUMMARY_TABLE,
    TP_PROBABILITY_TABLE,
)


def store_report(conn: sqlite3.Connection, report: AnalysisReport) -> None:
    for summary in report.summaries:
        _upsert_summary(conn, summary)
    for lw in report.late_windows:
        _upsert_late_window(conn, lw)
    conn.execute(f"DELETE FROM {TP_PROBABILITY_TABLE}")
    for agg in report.tp_aggregates:
        _insert_tp_aggregate(conn, agg)
    conn.commit()


def _upsert_summary(conn: sqlite3.Connection, s: MarketSummary) -> None:
    conn.execute(
        f"""
        INSERT INTO {SUMMARY_TABLE} (
            market_slug, window_start_ts, strike, final_btc, btc_delta_final,
            winning_side, yes_bid_min, yes_bid_max, yes_ask_min, yes_ask_max,
            no_bid_min, no_bid_max, no_ask_min, no_ask_max, observation_count,
            analyzed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(market_slug) DO UPDATE SET
            window_start_ts=excluded.window_start_ts,
            strike=excluded.strike,
            final_btc=excluded.final_btc,
            btc_delta_final=excluded.btc_delta_final,
            winning_side=excluded.winning_side,
            yes_bid_min=excluded.yes_bid_min,
            yes_bid_max=excluded.yes_bid_max,
            yes_ask_min=excluded.yes_ask_min,
            yes_ask_max=excluded.yes_ask_max,
            no_bid_min=excluded.no_bid_min,
            no_bid_max=excluded.no_bid_max,
            no_ask_min=excluded.no_ask_min,
            no_ask_max=excluded.no_ask_max,
            observation_count=excluded.observation_count,
            analyzed_at=datetime('now')
        """,
        (
            s.market_slug, s.window_start_ts, s.strike, s.final_btc, s.btc_delta_final,
            s.winning_side, s.yes_bid_min, s.yes_bid_max, s.yes_ask_min, s.yes_ask_max,
            s.no_bid_min, s.no_bid_max, s.no_ask_min, s.no_ask_max, s.observation_count,
        ),
    )


def _upsert_late_window(conn: sqlite3.Connection, lw: LateWindowSnapshot) -> None:
    conn.execute(
        f"""
        INSERT INTO {LATE_WINDOW_TABLE} (
            market_slug, seconds_bucket, obs_timestamp, seconds_left_actual,
            btc_price, btc_delta_vs_strike, yes_bid, yes_ask, no_bid, no_ask,
            btc_move_to_close, yes_ask_move_to_close, no_ask_move_to_close
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(market_slug, seconds_bucket) DO UPDATE SET
            obs_timestamp=excluded.obs_timestamp,
            seconds_left_actual=excluded.seconds_left_actual,
            btc_price=excluded.btc_price,
            btc_delta_vs_strike=excluded.btc_delta_vs_strike,
            yes_bid=excluded.yes_bid,
            yes_ask=excluded.yes_ask,
            no_bid=excluded.no_bid,
            no_ask=excluded.no_ask,
            btc_move_to_close=excluded.btc_move_to_close,
            yes_ask_move_to_close=excluded.yes_ask_move_to_close,
            no_ask_move_to_close=excluded.no_ask_move_to_close
        """,
        (
            lw.market_slug, lw.seconds_bucket, lw.obs_timestamp, lw.seconds_left_actual,
            lw.btc_price, lw.btc_delta_vs_strike, lw.yes_bid, lw.yes_ask,
            lw.no_bid, lw.no_ask, lw.btc_move_to_close,
            lw.yes_ask_move_to_close, lw.no_ask_move_to_close,
        ),
    )


def _insert_tp_aggregate(conn: sqlite3.Connection, agg: TpAggregate) -> None:
    conn.execute(
        f"""
        INSERT INTO {TP_PROBABILITY_TABLE} (
            side, entry_bucket, entry_price_mid, tp_level,
            sample_count, reach_count, reach_probability, computed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            agg.side, agg.entry_bucket, agg.entry_price_mid, agg.tp_level,
            agg.sample_count, agg.reach_count, agg.reach_probability,
        ),
    )
