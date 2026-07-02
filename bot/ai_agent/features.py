"""Feature extraction for AI Agent signals."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import bot.config as config
from bot.config import (
    ER_V2_STOP_LOSS_PCT,
    ER_V2_TIME_STOP_SEC,
    TRAILING_ACTIVATION_PROFIT,
    TRAILING_OFFSET,
    effective_entry_threshold,
)
from bot.no_c_btc_filter import compute_btc_move_30s
from bot.no_c_filter_shadow import _btc_price_at
from bot.report.analytics import trade_pnl

SOURCE_TABLE = "early_reversion_v2_trades"


def regime_label_from_features(
    move_30: float | None,
    spread: float | None,
) -> str:
    spread = float(spread or 0)
    if spread > 0.025:
        return "Low Liquidity"
    if move_30 is not None and abs(move_30) > 30:
        return "News Spike"
    if move_30 is not None and move_30 > 15:
        return "Strong Uptrend"
    if move_30 is not None and move_30 < -15:
        return "Panic"
    if move_30 is not None and abs(move_30) <= 5:
        return "Mean Reversion"
    return "Range"


def _regime_label(conn: sqlite3.Connection, trade: sqlite3.Row) -> str:
    entry_ts = int(trade["entry_ts"])
    current = _btc_price_at(conn, entry_ts)
    move_30 = None
    if current is not None:
        move_30 = compute_btc_move_30s(conn, current_btc=current, now_ts=entry_ts)

    prefix = "yes" if trade["side"] == "YES" else "no"
    row = conn.execute(
        f"""
        SELECT {prefix}_bid AS bid, {prefix}_ask AS ask
        FROM market_checks
        WHERE market_slug = ?
        ORDER BY abs(cast(strftime('%s', checked_at) AS integer) - ?) ASC
        LIMIT 1
        """,
        (trade["market_slug"], entry_ts),
    ).fetchone()
    spread = 0.0
    if row and row["bid"] is not None and row["ask"] is not None:
        spread = float(row["ask"]) - float(row["bid"])

    return regime_label_from_features(move_30, spread)


def outcome_label(trade: sqlite3.Row, pnl: float) -> str:
    reason = (trade["exit_reason"] or "").upper()
    mapping = {
        "STOP_LOSS": "STOP_LOSS",
        "TIME_STOP": "TIME_STOP",
        "TRAILING_STOP": "TRAILING_STOP",
    }
    if reason in mapping:
        return mapping[reason]
    return "WIN" if pnl > 0 else "LOSS"


def build_signal_features(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
    *,
    cache: Any | None = None,
    cached_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect market, strategy, and computed features for one trade/signal."""
    if cached_row is not None:
        from bot.perf.feature_store import feature_dict_to_signal

        return feature_dict_to_signal(cached_row, trade)

    if cache is not None:
        from bot.perf.feature_store import build_feature_row_cached, feature_dict_to_signal

        base = build_feature_row_cached(trade, cache)
        return feature_dict_to_signal(base, trade)

    from bot.optimizer.dataset import build_feature_row

    base = build_feature_row(conn, trade)
    pnl = trade_pnl(trade)
    regime = _regime_label(conn, trade)

    extra = {
        "regime_label": regime,
        "time_stop_sec": float(ER_V2_TIME_STOP_SEC),
        "entry_threshold": effective_entry_threshold(0.40),
        "position_size_usdc": config.EARLY_REVERSION_POSITION_SIZE_USDC,
        "trading_mode": config.TRADING_MODE,
    }

    features_for_json = {
        k: base.get(k)
        for k in (
            "btc_move_30s",
            "volatility_30s",
            "spread",
            "seconds_open",
            "mfe",
            "mae",
        )
    }
    features_for_json["regime_label"] = regime

    row = {
        "trade_id": int(trade["id"]),
        "source_table": SOURCE_TABLE,
        "market_slug": trade["market_slug"],
        "strategy_name": trade["strategy_name"],
        "side": trade["side"],
        "entry_ts": int(trade["entry_ts"]),
        "entry_price": float(trade["entry_price"]),
        "exit_price": float(trade["exit_price"]) if trade["exit_price"] is not None else None,
        "seconds_open": base.get("seconds_open"),
        "spread": base.get("spread"),
        "ask": base.get("ask"),
        "bid": base.get("bid"),
        "distance_to_strike": base.get("distance_to_strike"),
        "btc_move_5s": base.get("btc_move_5s"),
        "btc_move_10s": base.get("btc_move_10s"),
        "btc_move_15s": base.get("btc_move_15s"),
        "btc_move_20s": base.get("btc_move_20s"),
        "btc_move_30s": base.get("btc_move_30s"),
        "btc_move_45s": base.get("btc_move_45s"),
        "btc_move_60s": base.get("btc_move_60s"),
        "btc_move_90s": base.get("btc_move_90s"),
        "volatility_15s": base.get("volatility_15s"),
        "volatility_30s": base.get("volatility_30s"),
        "volatility_60s": base.get("volatility_60s"),
        "regime_label": regime,
        "stop_loss_pct": ER_V2_STOP_LOSS_PCT,
        "trailing_activation": TRAILING_ACTIVATION_PROFIT,
        "trailing_distance": TRAILING_OFFSET,
        "time_stop_sec": extra["time_stop_sec"],
        "entry_threshold": extra["entry_threshold"],
        "position_size_usdc": extra["position_size_usdc"],
        "mfe": base.get("mfe"),
        "mae": base.get("mae"),
        "holding_time": base.get("holding_time"),
        "features_json": json.dumps(features_for_json, ensure_ascii=False),
        "outcome": outcome_label(trade, pnl),
        "pnl": pnl,
        "pnl_usdc": float(trade["pnl_usdc"]) if trade["pnl_usdc"] is not None else None,
        "exit_reason": trade["exit_reason"],
        "observe_mode": 1,
    }
    return row


def btc_acceleration(row: dict[str, Any]) -> float | None:
    m15 = row.get("btc_move_15s")
    m30 = row.get("btc_move_30s")
    if m15 is not None and m30 is not None:
        return float(m30) - float(m15)
    return None


def btc_direction_label(row: dict[str, Any]) -> str:
    move = row.get("btc_move_30s")
    if move is None:
        return "flat"
    if move > 5:
        return "up"
    if move < -5:
        return "down"
    return "flat"


def entry_bucket(price: float) -> float:
    return round(price, 2)


def holding_bucket(holding_sec: float | None) -> str:
    if holding_sec is None:
        return "unknown"
    h = float(holding_sec)
    if h <= 30:
        return "<=30s"
    if h <= 60:
        return "31-60s"
    if h <= 90:
        return "61-90s"
    return ">90s"


def enrich_for_similarity(row: dict[str, Any]) -> dict[str, Any]:
    """Add derived fields used by Similar Trades Engine."""
    return {
        **row,
        "btc_acceleration": btc_acceleration(row),
        "btc_direction": btc_direction_label(row),
        "entry_bucket": entry_bucket(float(row.get("entry_price") or 0)),
        "holding_bucket": holding_bucket(row.get("holding_time")),
        "market_regime": row.get("regime_label") or "Range",
        "volatility": row.get("volatility_30s"),
        "seconds_from_start": row.get("seconds_open"),
    }


def feature_vector(row: dict[str, Any]) -> dict[str, float | None]:
    """Flat numeric vector for ML models."""
    keys = (
        "entry_price",
        "seconds_open",
        "spread",
        "distance_to_strike",
        "btc_move_5s",
        "btc_move_10s",
        "btc_move_15s",
        "btc_move_20s",
        "btc_move_30s",
        "btc_move_45s",
        "btc_move_60s",
        "btc_move_90s",
        "volatility_15s",
        "volatility_30s",
        "volatility_60s",
        "mfe",
        "mae",
        "holding_time",
    )
    return {k: row.get(k) for k in keys}
