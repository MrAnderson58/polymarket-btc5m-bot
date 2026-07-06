"""Stage 2 market snapshot pipeline — separate transaction from Stage 1."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from bot.research.futures_agent.config import (
    CONTEXT_SYMBOL_BTC,
    CONTEXT_SYMBOL_ETH,
    DATA_QUALITY_API_UNAVAILABLE,
    DATA_QUALITY_SYMBOL_UNAVAILABLE,
    EXCHANGE_BINANCE,
    STATUS_COMPLETE,
    STATUS_PENDING_SNAPSHOT,
    include_eth_context,
)
from bot.research.futures_agent.db import _PgConnWrapper, insert_returning_id, validate_write_table
from bot.research.futures_agent.features import AssetSnapshot, build_asset_snapshot, correlation_beta
from bot.research.futures_agent.market_provider import BinanceMarketProvider, MarketDataProvider
from bot.research.futures_agent.regime import (
    alignment_label,
    btc_market_regime,
    btc_trend_fields,
    momentum_regime,
    preliminary_research_label,
    relative_strength_label,
    volatility_regime,
)


@dataclass
class SnapshotResult:
    signal_id: int
    success: bool
    skipped: bool = False
    data_quality: str | None = None
    research_label: str | None = None
    error: str | None = None
    alt_snapshot_id: int | None = None
    btc_context_id: int | None = None
    relative_strength_id: int | None = None


def _passes_gate_value(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    return bool(val)


def snapshot_signal(
    conn: Any,
    signal_id: int,
    *,
    provider: MarketDataProvider | None = None,
) -> SnapshotResult:
    """Collect synchronized market snapshot. Idempotent; never rolls back Stage 1 signal."""
    validate_write_table("futures_agent_market_snapshots")

    existing = conn.execute(
        "SELECT id FROM futures_agent_btc_context WHERE signal_id = ?",
        (signal_id,),
    ).fetchone()
    if existing:
        return SnapshotResult(signal_id=signal_id, success=True, skipped=True)

    row = conn.execute(
        """
        SELECT s.id, s.symbol, s.direction, s.passes_gate, s.input_id,
               i.received_at, i.processing_status
        FROM futures_agent_signals s
        JOIN futures_agent_inputs i ON i.id = s.input_id
        WHERE s.id = ?
        """,
        (signal_id,),
    ).fetchone()
    if not row:
        return SnapshotResult(signal_id=signal_id, success=False, error="signal not found")
    if not _passes_gate_value(row["passes_gate"]):
        return SnapshotResult(signal_id=signal_id, success=False, error="signal did not pass gate")
    if not row["symbol"]:
        return SnapshotResult(signal_id=signal_id, success=False, error="signal has no symbol")

    ts = int(row["received_at"])
    provider = provider or BinanceMarketProvider()

    try:
        alt_snap = build_asset_snapshot(provider, row["symbol"], ts)
        btc_snap = build_asset_snapshot(provider, CONTEXT_SYMBOL_BTC, ts, include_funding=False)

        if alt_snap.data_quality in (DATA_QUALITY_API_UNAVAILABLE, DATA_QUALITY_SYMBOL_UNAVAILABLE):
            conn.execute(
                "UPDATE futures_agent_inputs SET status_detail = ? WHERE id = ?",
                (f"snapshot_{alt_snap.data_quality.lower()}:{signal_id}", row["input_id"]),
            )
            return SnapshotResult(
                signal_id=signal_id,
                success=False,
                data_quality=alt_snap.data_quality,
                error=f"market data {alt_snap.data_quality.lower()}",
            )

        alt_id = _persist_market_snapshot(conn, signal_id, alt_snap)

        eth_id = None
        if include_eth_context():
            eth_snap = build_asset_snapshot(provider, CONTEXT_SYMBOL_ETH, ts, include_funding=False)
            if eth_snap.data_quality not in (DATA_QUALITY_API_UNAVAILABLE,):
                eth_id = _persist_market_snapshot(conn, signal_id, eth_snap)

        excess_5m = _excess(alt_snap.return_5m, btc_snap.return_5m)
        excess_15m = _excess(alt_snap.return_15m, btc_snap.return_15m)
        excess_1h = _excess(alt_snap.return_1h, btc_snap.return_1h)
        corr, beta = correlation_beta(alt_snap.minute_returns_1m, btc_snap.minute_returns_1m)
        align = alignment_label(
            signal_direction=row["direction"],
            alt_snap=alt_snap,
            btc_snap=btc_snap,
            excess_1h=excess_1h,
        )
        rs_label = relative_strength_label(excess_5m, excess_1h)
        btc_reg = btc_market_regime(btc_snap)
        vol_reg = volatility_regime(btc_snap)
        mom_reg = momentum_regime(btc_snap)
        trends = btc_trend_fields(btc_snap)

        btc_ctx_id = insert_returning_id(
            conn,
            """
            INSERT INTO futures_agent_btc_context (
                signal_id, snapshot_ts, btc_price,
                return_5m, return_15m, return_1h, return_4h, return_24h,
                trend_15m, trend_1h, trend_4h,
                volatility_regime, momentum_regime, market_regime
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id, ts, btc_snap.futures_price or btc_snap.spot_price,
                btc_snap.return_5m, btc_snap.return_15m, btc_snap.return_1h,
                btc_snap.return_4h, btc_snap.return_24h,
                trends["trend_15m"], trends["trend_1h"], trends["trend_4h"],
                vol_reg, mom_reg, btc_reg,
            ),
        )

        rs_id = insert_returning_id(
            conn,
            """
            INSERT INTO futures_agent_relative_strength (
                signal_id, symbol, snapshot_ts,
                alt_return_5m, alt_return_15m, alt_return_1h,
                btc_return_5m, btc_return_15m, btc_return_1h,
                excess_return_5m, excess_return_15m, excess_return_1h,
                correlation_to_btc, beta_to_btc, relative_strength_label
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id, row["symbol"].upper(), ts,
                alt_snap.return_5m, alt_snap.return_15m, alt_snap.return_1h,
                btc_snap.return_5m, btc_snap.return_15m, btc_snap.return_1h,
                excess_5m, excess_15m, excess_1h,
                corr, beta, rs_label,
            ),
        )

        research = preliminary_research_label(
            data_quality=alt_snap.data_quality,
            alignment=align,
            btc_regime=btc_reg,
            vol_regime=vol_reg,
            passes_gate=True,
        )

        conn.execute(
            """
            UPDATE futures_agent_inputs
            SET processing_status = ?, status_detail = ?
            WHERE id = ?
            """,
            (STATUS_COMPLETE, f"snapshot:{research};align:{align}", row["input_id"]),
        )

        meta = alt_snap.raw_metadata_json
        meta["alignment_label"] = align
        meta["research_label"] = research
        meta["eth_snapshot_id"] = eth_id
        if alt_id:
            conn.execute(
                "UPDATE futures_agent_market_snapshots SET raw_metadata_json = ? "
                "WHERE id = ?",
                (json.dumps(meta), alt_id),
            )

        return SnapshotResult(
            signal_id=signal_id,
            success=True,
            data_quality=alt_snap.data_quality,
            research_label=research,
            alt_snapshot_id=alt_id,
            btc_context_id=btc_ctx_id,
            relative_strength_id=rs_id,
        )
    except Exception as exc:
        conn.execute(
            "UPDATE futures_agent_inputs SET status_detail = ? WHERE id = ?",
            (f"snapshot_error:{exc}", row["input_id"]),
        )
        return SnapshotResult(signal_id=signal_id, success=False, error=str(exc))


def snapshot_pending(
    conn: Any,
    *,
    limit: int = 50,
    provider: MarketDataProvider | None = None,
) -> list[SnapshotResult]:
    gate_filter = "s.passes_gate IS TRUE" if isinstance(conn, _PgConnWrapper) else "s.passes_gate = 1"
    rows = conn.execute(
        f"""
        SELECT s.id
        FROM futures_agent_signals s
        JOIN futures_agent_inputs i ON i.id = s.input_id
        WHERE i.processing_status = ?
          AND {gate_filter}
        ORDER BY i.received_at ASC
        LIMIT ?
        """,
        (STATUS_PENDING_SNAPSHOT, limit),
    ).fetchall()
    return [snapshot_signal(conn, int(r["id"]), provider=provider) for r in rows]


def _excess(alt: float | None, btc: float | None) -> float | None:
    if alt is None or btc is None:
        return None
    return alt - btc


def _persist_market_snapshot(conn: Any, signal_id: int, snap: AssetSnapshot) -> int | None:
    if snap.data_quality in (DATA_QUALITY_API_UNAVAILABLE,):
        return None
    return insert_returning_id(
        conn,
        """
        INSERT INTO futures_agent_market_snapshots (
            signal_id, snapshot_ts, symbol, exchange,
            spot_price, futures_price,
            return_1m, return_5m, return_15m, return_30m, return_1h, return_4h, return_24h,
            ema_fast, ema_slow, ema_slope, atr, realized_vol,
            distance_from_local_high, distance_from_local_low, volume_ratio,
            funding_rate, open_interest, basis,
            data_quality, raw_metadata_json
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            signal_id, snap.snapshot_ts, snap.pair, snap.exchange,
            snap.spot_price, snap.futures_price,
            snap.return_1m, snap.return_5m, snap.return_15m, snap.return_30m,
            snap.return_1h, snap.return_4h, snap.return_24h,
            snap.ema_fast, snap.ema_slow, snap.ema_slope, snap.atr, snap.realized_vol,
            snap.distance_from_local_high, snap.distance_from_local_low, snap.volume_ratio,
            snap.funding_rate, snap.open_interest, snap.basis,
            snap.data_quality, json.dumps(snap.raw_metadata_json),
        ),
    )
