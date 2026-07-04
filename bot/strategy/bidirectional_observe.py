"""Observe-only integration for bidirectional momentum shadow strategy.

Called from bot.main cycle. Records observations and virtual trades.
Cannot affect order decisions — wrapped in try/except.
"""

from __future__ import annotations

import logging
import sqlite3
import time

logger = logging.getLogger(__name__)


def observe_market(
    conn: sqlite3.Connection,
    market_slug: str,
    window_start_ts: int,
    btc_price: float,
    strike: float,
    yes_bid: float,
    yes_ask: float,
    no_bid: float,
    no_ask: float,
    seconds_from_start: int,
    seconds_left: int,
) -> None:
    """Observe a market tick for the bidirectional shadow strategy.

    Safe to call every cycle. Only enters if conditions are met.
    Only affects bidirectional_shadow_* tables.
    """
    from bot.strategy.bidirectional_shadow import (
        ensure_tables,
        get_open_shadow_trade,
        open_shadow_trade,
        close_shadow_trade,
        record_observation,
        SHADOW_ENTRY_CONFIG,
    )
    from bot.strategy.bidirectional_momentum import (
        evaluate_direction,
        get_exit_config,
    )
    from bot.research.features import MovementFeatures

    ensure_tables(conn)
    now_ts = int(time.time())

    spread = abs(yes_ask - yes_bid) if yes_ask and yes_bid else 0.0
    delta = btc_price - strike if strike else 0.0

    feat = MovementFeatures(
        timestamp=now_ts,
        market_slug=market_slug,
        seconds_from_start=seconds_from_start,
        seconds_left=seconds_left,
        btc_price=btc_price,
        strike=strike,
        delta=delta,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=no_bid,
        no_ask=no_ask,
        spread=spread,
        btc_move_5s=0.0,
        btc_move_10s=0.0,
        btc_move_15s=0.0,
        btc_move_30s=0.0,
        btc_move_60s=0.0,
        btc_move_90s=0.0,
        btc_velocity_10s=0.0,
        btc_velocity_30s=0.0,
        btc_acceleration=0.0,
        distance_from_strike_usd=delta,
        distance_from_strike_pct=(delta / strike * 100) if strike else 0.0,
        direction_consistency=0.0,
        regime="NORMAL",
    )

    # Compute BTC moves from recent observations
    _enrich_from_history(conn, feat, market_slug, now_ts)

    # Check for open trade — process exit
    open_trade = get_open_shadow_trade(conn, market_slug)
    if open_trade:
        _process_exit(conn, open_trade, feat)
        return

    # Evaluate entry
    decision = evaluate_direction(feat, SHADOW_ENTRY_CONFIG)
    entry_price = feat.yes_ask if decision.decision == "YES" else feat.no_ask

    record_observation(conn, decision, market_slug, now_ts, entry_price)

    if decision.decision in ("YES", "NO"):
        if entry_price and 0 < entry_price < 1:
            open_shadow_trade(conn, decision, market_slug, window_start_ts, entry_price, now_ts)
            logger.debug(
                "BIDI_SHADOW | %s %s @ %.3f | %s | conf=%.2f",
                decision.decision, market_slug, entry_price, decision.reason, decision.confidence,
            )


def _process_exit(conn: sqlite3.Connection, trade: dict, feat: MovementFeatures) -> None:
    """Check and process exit for open shadow trade."""
    from bot.strategy.bidirectional_shadow import close_shadow_trade
    from bot.strategy.bidirectional_momentum import get_exit_config

    side = trade["side"]
    entry_price = trade["entry_price"]
    entry_ts = trade["entry_ts"]
    regime = trade.get("entry_regime", "NORMAL")

    bid = feat.yes_bid if side == "YES" else feat.no_bid
    if not bid or bid <= 0:
        return

    current_pnl = ((bid - entry_price) / entry_price) * 100 if entry_price else 0
    holding_time = feat.timestamp - entry_ts

    # Update max price
    max_seen = trade.get("max_price_seen") or entry_price
    if bid > max_seen:
        conn.execute(
            "UPDATE bidirectional_shadow_trades SET max_price_seen=? WHERE id=?",
            (bid, trade["id"]),
        )
        max_seen = bid

    max_profit = ((max_seen - entry_price) / entry_price) * 100 if entry_price else 0
    exit_cfg = get_exit_config(regime)

    exit_reason = None

    if current_pnl <= exit_cfg.stop_loss_pct:
        exit_reason = "STOP_LOSS"
    elif holding_time >= exit_cfg.time_stop_seconds:
        exit_reason = "TIME_STOP"
    elif max_profit >= exit_cfg.trailing_activation_pct:
        drawdown_from_peak = max_profit - current_pnl
        if drawdown_from_peak >= exit_cfg.trailing_distance_pct:
            exit_reason = "TRAILING_STOP"

    if exit_reason:
        close_shadow_trade(conn, trade["id"], bid, exit_reason, current_pnl, holding_time)
        logger.debug(
            "BIDI_SHADOW | CLOSE %s %s | %s | pnl=%.1f%%",
            side, trade["market_slug"], exit_reason, current_pnl,
        )


def _enrich_from_history(
    conn: sqlite3.Connection,
    feat: MovementFeatures,
    market_slug: str,
    now_ts: int,
) -> None:
    """Enrich features from recent v4_shadow_observations if available."""
    rows = conn.execute(
        """SELECT btc_price, timestamp FROM v4_shadow_observations
           WHERE market_slug = ? AND timestamp <= ?
           ORDER BY timestamp DESC LIMIT 30""",
        (market_slug, now_ts),
    ).fetchall()

    if not rows:
        return

    current_btc = feat.btc_price
    for row in rows:
        age = now_ts - row["timestamp"]
        move = current_btc - row["btc_price"]

        if age <= 7 and feat.btc_move_5s == 0:
            feat.btc_move_5s = move
        if age <= 12 and feat.btc_move_10s == 0:
            feat.btc_move_10s = move
        if age <= 17 and feat.btc_move_15s == 0:
            feat.btc_move_15s = move
        if age <= 35 and feat.btc_move_30s == 0:
            feat.btc_move_30s = move
        if age <= 65 and feat.btc_move_60s == 0:
            feat.btc_move_60s = move
        if age <= 95 and feat.btc_move_90s == 0:
            feat.btc_move_90s = move

    if feat.btc_move_10s:
        feat.btc_velocity_10s = feat.btc_move_10s / 10.0
    if feat.btc_move_30s:
        feat.btc_velocity_30s = feat.btc_move_30s / 30.0
    if feat.btc_velocity_10s and feat.btc_velocity_30s:
        feat.btc_acceleration = (feat.btc_velocity_10s - feat.btc_velocity_30s) / 20.0

    from bot.research.features import compute_direction_consistency, classify_regime
    moves = {
        "btc_move_10s": feat.btc_move_10s,
        "btc_move_30s": feat.btc_move_30s,
        "btc_move_60s": feat.btc_move_60s,
        "btc_velocity_10s": feat.btc_velocity_10s,
        "btc_velocity_30s": feat.btc_velocity_30s,
        "btc_acceleration": feat.btc_acceleration,
    }
    feat.direction_consistency = compute_direction_consistency(moves)
    moves["direction_consistency"] = feat.direction_consistency
    feat.regime = classify_regime(moves)
