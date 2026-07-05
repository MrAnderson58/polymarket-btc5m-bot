"""Immutable MTF market metadata cache — research/collector only."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

TABLE = "mtf_market_metadata"


@dataclass
class MarketMetadata:
    market_slug: str
    timeframe: str
    window_start_ts: int | None
    window_end_ts: int | None
    strike: float | None
    strike_source: str
    strike_source_timestamp: int | None
    strike_confidence: float
    discovered_at: int
    raw_metadata_json: dict[str, Any] | None = None


def ensure_metadata_table(conn: sqlite3.Connection) -> None:
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            market_slug TEXT PRIMARY KEY,
            timeframe TEXT NOT NULL,
            window_start_ts INTEGER,
            window_end_ts INTEGER,
            strike REAL,
            strike_source TEXT NOT NULL DEFAULT 'UNKNOWN',
            strike_source_timestamp INTEGER,
            strike_confidence REAL NOT NULL DEFAULT 0.0,
            discovered_at INTEGER NOT NULL,
            raw_metadata_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_mtf_metadata_tf
            ON {TABLE}(timeframe);
        CREATE INDEX IF NOT EXISTS idx_mtf_metadata_ws
            ON {TABLE}(window_start_ts);
    """)


def get_cached_metadata(conn: sqlite3.Connection, market_slug: str) -> MarketMetadata | None:
    row = conn.execute(
        f"""
        SELECT market_slug, timeframe, window_start_ts, window_end_ts,
               strike, strike_source, strike_source_timestamp, strike_confidence,
               discovered_at, raw_metadata_json
        FROM {TABLE}
        WHERE market_slug = ?
        """,
        (market_slug,),
    ).fetchone()
    if not row:
        return None
    raw = row["raw_metadata_json"]
    return MarketMetadata(
        market_slug=row["market_slug"],
        timeframe=row["timeframe"],
        window_start_ts=row["window_start_ts"],
        window_end_ts=row["window_end_ts"],
        strike=row["strike"],
        strike_source=row["strike_source"],
        strike_source_timestamp=row["strike_source_timestamp"],
        strike_confidence=float(row["strike_confidence"]),
        discovered_at=row["discovered_at"],
        raw_metadata_json=json.loads(raw) if raw else None,
    )


def upsert_metadata(conn: sqlite3.Connection, meta: MarketMetadata) -> None:
    existing = get_cached_metadata(conn, meta.market_slug)
    if existing and existing.strike is not None and existing.strike_confidence >= meta.strike_confidence:
        if meta.strike is None or meta.strike_confidence <= existing.strike_confidence:
            return
    conn.execute(
        f"""
        INSERT INTO {TABLE} (
            market_slug, timeframe, window_start_ts, window_end_ts,
            strike, strike_source, strike_source_timestamp, strike_confidence,
            discovered_at, raw_metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(market_slug) DO UPDATE SET
            timeframe = excluded.timeframe,
            window_start_ts = COALESCE(excluded.window_start_ts, {TABLE}.window_start_ts),
            window_end_ts = COALESCE(excluded.window_end_ts, {TABLE}.window_end_ts),
            strike = CASE
                WHEN excluded.strike IS NOT NULL
                     AND (excluded.strike_confidence >= {TABLE}.strike_confidence
                          OR {TABLE}.strike IS NULL)
                THEN excluded.strike
                ELSE {TABLE}.strike
            END,
            strike_source = CASE
                WHEN excluded.strike IS NOT NULL
                     AND (excluded.strike_confidence >= {TABLE}.strike_confidence
                          OR {TABLE}.strike IS NULL)
                THEN excluded.strike_source
                ELSE {TABLE}.strike_source
            END,
            strike_source_timestamp = CASE
                WHEN excluded.strike IS NOT NULL
                     AND (excluded.strike_confidence >= {TABLE}.strike_confidence
                          OR {TABLE}.strike IS NULL)
                THEN excluded.strike_source_timestamp
                ELSE {TABLE}.strike_source_timestamp
            END,
            strike_confidence = CASE
                WHEN excluded.strike IS NOT NULL
                     AND (excluded.strike_confidence >= {TABLE}.strike_confidence
                          OR {TABLE}.strike IS NULL)
                THEN excluded.strike_confidence
                ELSE {TABLE}.strike_confidence
            END,
            discovered_at = MIN({TABLE}.discovered_at, excluded.discovered_at),
            raw_metadata_json = COALESCE(excluded.raw_metadata_json, {TABLE}.raw_metadata_json)
        """,
        (
            meta.market_slug,
            meta.timeframe,
            meta.window_start_ts,
            meta.window_end_ts,
            meta.strike,
            meta.strike_source,
            meta.strike_source_timestamp,
            meta.strike_confidence,
            meta.discovered_at,
            json.dumps(meta.raw_metadata_json) if meta.raw_metadata_json else None,
        ),
    )


def list_metadata_by_source(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        f"""
        SELECT timeframe, strike_source, COUNT(*) AS n
        FROM {TABLE}
        GROUP BY timeframe, strike_source
        ORDER BY timeframe, strike_source
        """
    ).fetchall()
