"""trade_features cache + batch enriched feature loading."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import bot.config as config
from bot.ai_agent.features import (
    enrich_for_similarity,
    outcome_label,
    regime_label_from_features,
)
from bot.config import (
    ER_V2_STOP_LOSS_PCT,
    ER_V2_TIME_STOP_SEC,
    TRAILING_ACTIVATION_PROFIT,
    TRAILING_OFFSET,
    effective_entry_threshold,
)
from bot.optimizer.constants import SOURCE_TABLE
from bot.perf.market_cache import MarketDataCache
from bot.report.analytics import trade_pnl

FEATURE_COLUMNS = (
    "trade_id",
    "source_table",
    "market_slug",
    "strategy_name",
    "side",
    "entry_ts",
    "entry_price",
    "exit_price",
    "pnl",
    "pnl_usdc",
    "btc_move_5s",
    "btc_move_10s",
    "btc_move_15s",
    "btc_move_20s",
    "btc_move_30s",
    "btc_move_45s",
    "btc_move_60s",
    "btc_move_90s",
    "seconds_open",
    "spread",
    "ask",
    "bid",
    "distance_to_strike",
    "volatility_15s",
    "volatility_30s",
    "volatility_60s",
    "stop_loss_pct",
    "trailing_activation",
    "trailing_distance",
    "holding_time",
    "mfe",
    "mae",
    "is_win",
    "is_loss",
    "is_stop",
    "is_time_stop",
    "is_trailing",
    "exit_reason",
    "regime_label",
)


def build_feature_row_cached(trade: Any, cache: MarketDataCache) -> dict[str, Any]:
    entry_ts = int(trade["entry_ts"])
    exit_reason = trade["exit_reason"] or ""
    pnl = trade_pnl(trade)
    mfe, mae = cache.mfe_mae(trade)
    bid, ask, dist_strike, _sec_rem = cache.quote_at_entry(
        market_slug=str(trade["market_slug"]),
        side=str(trade["side"]),
        entry_ts=entry_ts,
    )
    spread = (ask - bid) if bid is not None and ask is not None else None
    window_start = int(trade["window_start_ts"])
    return {
        "trade_id": int(trade["id"]),
        "source_table": SOURCE_TABLE,
        "market_slug": trade["market_slug"],
        "strategy_name": trade["strategy_name"],
        "side": trade["side"],
        "entry_ts": entry_ts,
        "entry_price": float(trade["entry_price"]),
        "exit_price": float(trade["exit_price"]) if trade["exit_price"] is not None else None,
        "pnl": pnl,
        "pnl_usdc": float(trade["pnl_usdc"]) if trade["pnl_usdc"] is not None else None,
        "btc_move_5s": cache.btc_move_at(entry_ts, 5),
        "btc_move_10s": cache.btc_move_at(entry_ts, 10),
        "btc_move_15s": cache.btc_move_at(entry_ts, 15),
        "btc_move_20s": cache.btc_move_at(entry_ts, 20),
        "btc_move_30s": cache.btc_move_at(entry_ts, 30),
        "btc_move_45s": cache.btc_move_at(entry_ts, 45),
        "btc_move_60s": cache.btc_move_at(entry_ts, 60),
        "btc_move_90s": cache.btc_move_at(entry_ts, 90),
        "seconds_open": float(entry_ts - window_start),
        "spread": spread,
        "ask": ask,
        "bid": bid,
        "distance_to_strike": dist_strike,
        "volatility_15s": cache.btc_volatility(entry_ts, 15),
        "volatility_30s": cache.btc_volatility(entry_ts, 30),
        "volatility_60s": cache.btc_volatility(entry_ts, 60),
        "stop_loss_pct": ER_V2_STOP_LOSS_PCT,
        "trailing_activation": TRAILING_ACTIVATION_PROFIT,
        "trailing_distance": TRAILING_OFFSET,
        "holding_time": float(trade["holding_time_seconds"] or 0),
        "mfe": mfe,
        "mae": mae,
        "is_win": int(pnl > 0),
        "is_loss": int(pnl <= 0),
        "is_stop": int(exit_reason == "STOP_LOSS"),
        "is_time_stop": int(exit_reason == "TIME_STOP"),
        "is_trailing": int(exit_reason == "TRAILING_STOP"),
        "exit_reason": exit_reason,
        "regime_label": regime_label_from_features(
            cache.btc_move_at(entry_ts, 30), spread
        ),
    }


def _upsert_feature_row(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    cols = list(FEATURE_COLUMNS)
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"""
        INSERT OR REPLACE INTO trade_features ({", ".join(cols)})
        VALUES ({placeholders})
        """,
        tuple(row[c] for c in cols),
    )


def sync_trade_features_incremental(
    conn: sqlite3.Connection,
    *,
    cache: MarketDataCache | None = None,
) -> tuple[int, MarketDataCache | None]:
    """Insert features only for closed trades missing from trade_features."""
    missing = conn.execute(
        f"""
        SELECT t.*
        FROM {SOURCE_TABLE} t
        LEFT JOIN trade_features f
          ON f.trade_id = t.id AND f.source_table = ?
        WHERE t.status = 'closed' AND f.trade_id IS NULL
        ORDER BY t.entry_ts ASC
        """,
        (SOURCE_TABLE,),
    ).fetchall()
    if not missing:
        return 0, cache

    if cache is None:
        cache = MarketDataCache.build(conn, missing)

    for trade in missing:
        _upsert_feature_row(conn, build_feature_row_cached(trade, cache))
        from bot.evolution.shadow import evaluate_trade_for_shadow
        from bot.evolution.shadow_db import get_running_shadow

        if get_running_shadow(conn) is not None:
            evaluate_trade_for_shadow(conn, trade)
    return len(missing), cache


def load_feature_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM trade_features
        WHERE source_table = ?
        ORDER BY entry_ts ASC
        """,
        (SOURCE_TABLE,),
    ).fetchall()
    return [dict(r) for r in rows]


def feature_dict_to_signal(row: dict[str, Any], trade: Any | None = None) -> dict[str, Any]:
    pnl = float(row.get("pnl") or 0)
    regime = regime_label_from_features(row.get("btc_move_30s"), row.get("spread"))
    features_for_json = {
        k: row.get(k)
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
    exit_reason = row.get("exit_reason")
    if trade is not None:
        exit_reason = trade["exit_reason"]
    return {
        **row,
        "regime_label": regime,
        "time_stop_sec": float(ER_V2_TIME_STOP_SEC),
        "entry_threshold": effective_entry_threshold(0.40),
        "position_size_usdc": config.EARLY_REVERSION_POSITION_SIZE_USDC,
        "trading_mode": config.TRADING_MODE,
        "features_json": json.dumps(features_for_json, ensure_ascii=False),
        "outcome": outcome_label(trade, pnl) if trade is not None else row.get("outcome"),
        "observe_mode": 1,
    }


def load_enriched_features(conn: sqlite3.Connection, *, sync: bool = True) -> list[dict[str, Any]]:
    """Load all enriched trade features from cache table (no per-trade SQL)."""
    if sync:
        sync_trade_features_incremental(conn)
    out: list[dict[str, Any]] = []
    for row in load_feature_rows(conn):
        signal = feature_dict_to_signal(row)
        out.append(enrich_for_similarity(signal))
    return out


def enriched_by_trade_id(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    return {int(r["trade_id"]): r for r in load_enriched_features(conn)}
