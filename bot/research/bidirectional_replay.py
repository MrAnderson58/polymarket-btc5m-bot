"""Deterministic replay engine for Bidirectional Momentum Strategy V1.

Replays historical v4_shadow_observations to simulate strategy decisions,
entries, exits, and compute comprehensive performance metrics.

No look-ahead bias: at each decision point, only past data is used.
"""

from __future__ import annotations

import math
import random
import sqlite3
from dataclasses import dataclass, field

from bot.research.features import (
    MovementFeatures,
    build_features_for_market,
    compute_btc_moves,
    compute_direction_consistency,
    classify_regime,
    get_research_markets,
    load_market_observations,
)
from bot.strategy.bidirectional_momentum import (
    DirectionDecision,
    EntryConfig,
    ExitConfig,
    evaluate_direction,
    get_exit_config,
)


@dataclass
class ReplayTrade:
    market_slug: str
    side: str
    entry_price: float
    entry_ts: int
    entry_regime: str
    entry_confidence: float
    entry_reason: str

    exit_price: float | None = None
    exit_ts: int | None = None
    exit_reason: str | None = None
    pnl_pct: float | None = None

    max_price: float | None = None
    min_price: float | None = None
    max_profit_pct: float = 0.0
    max_loss_pct: float = 0.0


@dataclass
class ReplayResult:
    trades: list[ReplayTrade] = field(default_factory=list)
    decisions: list[DirectionDecision] = field(default_factory=list)
    markets_processed: int = 0
    observations_processed: int = 0

    @property
    def closed_trades(self) -> list[ReplayTrade]:
        return [t for t in self.trades if t.exit_price is not None]

    def metrics(self) -> dict:
        closed = self.closed_trades
        if not closed:
            return {"trades": 0}

        wins = [t for t in closed if (t.pnl_pct or 0) > 0]
        losses = [t for t in closed if (t.pnl_pct or 0) <= 0]
        yes_trades = [t for t in closed if t.side == "YES"]
        no_trades = [t for t in closed if t.side == "NO"]

        gross_profit = sum(t.pnl_pct for t in wins)
        gross_loss = abs(sum(t.pnl_pct for t in losses))
        pf = gross_profit / gross_loss if gross_loss else 0.0

        pnls = [t.pnl_pct or 0 for t in closed]
        total_pnl = sum(pnls)
        avg_pnl = total_pnl / len(pnls)
        max_dd = _max_drawdown(pnls)

        yes_wins = [t for t in yes_trades if (t.pnl_pct or 0) > 0]
        no_wins = [t for t in no_trades if (t.pnl_pct or 0) > 0]
        yes_pf = _pf([t.pnl_pct or 0 for t in yes_trades])
        no_pf = _pf([t.pnl_pct or 0 for t in no_trades])

        max_consec_loss = _max_consecutive_losses(closed)

        exit_reasons: dict[str, int] = {}
        for t in closed:
            exit_reasons[t.exit_reason or "UNKNOWN"] = exit_reasons.get(t.exit_reason or "UNKNOWN", 0) + 1

        regime_stats: dict[str, dict] = {}
        for t in closed:
            r = t.entry_regime
            if r not in regime_stats:
                regime_stats[r] = {"n": 0, "wins": 0, "pnl": 0.0}
            regime_stats[r]["n"] += 1
            regime_stats[r]["pnl"] += t.pnl_pct or 0
            if (t.pnl_pct or 0) > 0:
                regime_stats[r]["wins"] += 1

        return {
            "trades": len(closed),
            "yes_trades": len(yes_trades),
            "no_trades": len(no_trades),
            "wins": len(wins),
            "losses": len(losses),
            "wr": len(wins) / len(closed) * 100,
            "pf": round(pf, 3),
            "yes_pf": round(yes_pf, 3),
            "no_pf": round(no_pf, 3),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(avg_pnl, 2),
            "max_dd": round(max_dd, 2),
            "max_consecutive_losses": max_consec_loss,
            "exit_reasons": exit_reasons,
            "regime_stats": regime_stats,
            "markets_processed": self.markets_processed,
            "observations_processed": self.observations_processed,
        }


def run_replay(
    conn: sqlite3.Connection,
    *,
    entry_config: EntryConfig | None = None,
    exit_overrides: dict[str, ExitConfig] | None = None,
    market_slugs: list[str] | None = None,
    max_markets: int | None = None,
    entry_window: tuple[int, int] = (15, 180),
    one_trade_per_market: bool = True,
) -> ReplayResult:
    """Run deterministic replay over historical v4 observations.

    Args:
        conn: Database connection.
        entry_config: Entry configuration for direction engine.
        exit_overrides: Override exit configs by regime.
        market_slugs: Specific markets to replay (None = all eligible).
        max_markets: Limit number of markets processed.
        entry_window: (min_seconds, max_seconds) from start for entry.
        one_trade_per_market: Only one trade per 5-minute market.
    """
    cfg = entry_config or EntryConfig()
    result = ReplayResult()

    markets = market_slugs or get_research_markets(conn, min_obs=30)
    if max_markets:
        markets = markets[:max_markets]

    for slug in markets:
        observations = load_market_observations(conn, slug)
        if len(observations) < 30:
            continue

        features = build_features_for_market(observations)
        result.markets_processed += 1
        result.observations_processed += len(features)

        open_trade: ReplayTrade | None = None
        trade_completed = False

        for i, feat in enumerate(features):
            # Process exit for open trade
            if open_trade is not None:
                exit_result = _check_exit(open_trade, feat, exit_overrides)
                if exit_result:
                    open_trade.exit_price = exit_result[0]
                    open_trade.exit_ts = feat.timestamp
                    open_trade.exit_reason = exit_result[1]
                    open_trade.pnl_pct = _calc_pnl(
                        open_trade.side, open_trade.entry_price, exit_result[0]
                    )
                    result.trades.append(open_trade)
                    open_trade = None
                    trade_completed = True
                    if one_trade_per_market:
                        break
                else:
                    _update_trade_tracking(open_trade, feat)
                continue

            if one_trade_per_market and trade_completed:
                break

            # Decision point — only if no open trade
            if feat.seconds_from_start < entry_window[0]:
                continue
            if feat.seconds_from_start > entry_window[1]:
                continue

            decision = evaluate_direction(feat, cfg)
            result.decisions.append(decision)

            if decision.decision in ("YES", "NO"):
                entry_price = feat.yes_ask if decision.decision == "YES" else feat.no_ask
                if entry_price <= 0 or entry_price >= 1:
                    continue

                open_trade = ReplayTrade(
                    market_slug=slug,
                    side=decision.decision,
                    entry_price=entry_price,
                    entry_ts=feat.timestamp,
                    entry_regime=decision.regime,
                    entry_confidence=decision.confidence,
                    entry_reason=decision.reason,
                    max_price=entry_price,
                    min_price=entry_price,
                )

                if one_trade_per_market:
                    # After entry, only process exits
                    pass

        # Force-close any still-open trade at market end (TIME_STOP)
        if open_trade is not None and features:
            last = features[-1]
            bid = last.yes_bid if open_trade.side == "YES" else last.no_bid
            if bid and bid > 0:
                open_trade.exit_price = bid
                open_trade.exit_ts = last.timestamp
                open_trade.exit_reason = "TIME_STOP"
                open_trade.pnl_pct = _calc_pnl(
                    open_trade.side, open_trade.entry_price, bid
                )
                result.trades.append(open_trade)

    return result


def walk_forward_split(
    markets: list[str], train_ratio: float = 0.7
) -> tuple[list[str], list[str]]:
    """Split markets chronologically (by timestamp in slug) into train/test."""
    sorted_markets = sorted(markets)
    split_idx = int(len(sorted_markets) * train_ratio)
    return sorted_markets[:split_idx], sorted_markets[split_idx:]


def bootstrap_pf(
    trades: list[ReplayTrade],
    n_samples: int = 1000,
    sample_size: int | None = None,
) -> dict[str, float]:
    """Bootstrap confidence interval for Profit Factor."""
    if not trades:
        return {"pf_mean": 0, "pf_lower": 0, "pf_upper": 0, "p_pf_gt_1": 0}

    pnls = [t.pnl_pct or 0 for t in trades]
    n = sample_size or len(pnls)
    pfs = []

    for _ in range(n_samples):
        sample = random.choices(pnls, k=n)
        pf = _pf(sample)
        pfs.append(pf)

    pfs.sort()
    lower_idx = int(n_samples * 0.025)
    upper_idx = int(n_samples * 0.975)

    return {
        "pf_mean": round(sum(pfs) / len(pfs), 3),
        "pf_lower": round(pfs[lower_idx], 3),
        "pf_upper": round(pfs[upper_idx], 3),
        "p_pf_gt_1": round(sum(1 for p in pfs if p > 1.0) / n_samples, 3),
    }


def run_ablation(
    conn: sqlite3.Connection,
    markets: list[str],
) -> dict[str, dict]:
    """Run ablation test with incremental features.

    A. BTC movement only
    B. + acceleration
    C. + distance from strike
    D. + trend_score (consistency)
    E. + token price
    F. + spread
    G. full model
    """
    results = {}

    # A: movement only
    cfg_a = EntryConfig(min_move_30s=5.0, min_confidence=0.52, max_spread=1.0, min_consistency=0.0)
    r = run_replay(conn, entry_config=cfg_a, market_slugs=markets)
    results["A_movement_only"] = r.metrics()

    # B: + acceleration (default already uses it, just lower threshold)
    cfg_b = EntryConfig(min_move_30s=5.0, min_confidence=0.53, max_spread=1.0, min_consistency=0.0)
    r = run_replay(conn, entry_config=cfg_b, market_slugs=markets)
    results["B_plus_acceleration"] = r.metrics()

    # C: + distance from strike (already in scoring, reduce noise threshold)
    cfg_c = EntryConfig(min_move_30s=5.0, min_confidence=0.54, max_spread=1.0, min_consistency=0.0)
    r = run_replay(conn, entry_config=cfg_c, market_slugs=markets)
    results["C_plus_distance"] = r.metrics()

    # D: + consistency
    cfg_d = EntryConfig(min_move_30s=5.0, min_confidence=0.54, max_spread=1.0, min_consistency=0.5)
    r = run_replay(conn, entry_config=cfg_d, market_slugs=markets)
    results["D_plus_consistency"] = r.metrics()

    # E: + token price
    cfg_e = EntryConfig(min_move_30s=5.0, min_confidence=0.54, max_spread=1.0, min_consistency=0.5,
                        yes_max_ask=0.45, no_max_ask=0.45)
    r = run_replay(conn, entry_config=cfg_e, market_slugs=markets)
    results["E_plus_price_filter"] = r.metrics()

    # F: + spread
    cfg_f = EntryConfig(min_move_30s=5.0, min_confidence=0.54, max_spread=0.06, min_consistency=0.5,
                        yes_max_ask=0.45, no_max_ask=0.45)
    r = run_replay(conn, entry_config=cfg_f, market_slugs=markets)
    results["F_plus_spread"] = r.metrics()

    # G: full model (default)
    r = run_replay(conn, entry_config=EntryConfig(), market_slugs=markets)
    results["G_full_model"] = r.metrics()

    return results


def _check_exit(
    trade: ReplayTrade,
    feat: MovementFeatures,
    exit_overrides: dict[str, ExitConfig] | None,
) -> tuple[float, str] | None:
    """Check if trade should exit at current observation."""
    exit_cfg = (exit_overrides or {}).get(trade.entry_regime) or get_exit_config(trade.entry_regime)

    # Current bid for this side
    bid = feat.yes_bid if trade.side == "YES" else feat.no_bid
    if not bid or bid <= 0:
        return None

    current_pnl = _calc_pnl(trade.side, trade.entry_price, bid)
    holding_time = feat.timestamp - trade.entry_ts

    # Stop loss
    if current_pnl <= exit_cfg.stop_loss_pct:
        return (bid, "STOP_LOSS")

    # Time stop
    if holding_time >= exit_cfg.time_stop_seconds:
        return (bid, "TIME_STOP")

    # Trailing stop
    if trade.max_profit_pct >= exit_cfg.trailing_activation_pct:
        drawdown_from_peak = trade.max_profit_pct - current_pnl
        if drawdown_from_peak >= exit_cfg.trailing_distance_pct:
            return (bid, "TRAILING_STOP")

    # Reversal detection: if we entered on momentum and it reversed
    if trade.entry_regime in ("MOMENTUM", "STRONG_MOMENTUM", "NEWS_SPIKE"):
        if trade.side == "YES" and feat.btc_velocity_10s < -3.0 and feat.btc_acceleration < -0.1:
            if current_pnl > 0:
                return (bid, "EXIT_REVERSAL")
        elif trade.side == "NO" and feat.btc_velocity_10s > 3.0 and feat.btc_acceleration > 0.1:
            if current_pnl > 0:
                return (bid, "EXIT_REVERSAL")

    return None


def _update_trade_tracking(trade: ReplayTrade, feat: MovementFeatures) -> None:
    """Update max/min price tracking for trailing stop."""
    bid = feat.yes_bid if trade.side == "YES" else feat.no_bid
    if bid and bid > 0:
        if trade.max_price is None or bid > trade.max_price:
            trade.max_price = bid
        if trade.min_price is None or bid < trade.min_price:
            trade.min_price = bid

        current_pnl = _calc_pnl(trade.side, trade.entry_price, bid)
        if current_pnl > trade.max_profit_pct:
            trade.max_profit_pct = current_pnl
        if current_pnl < trade.max_loss_pct:
            trade.max_loss_pct = current_pnl


def _calc_pnl(side: str, entry_price: float, exit_price: float) -> float:
    """Calculate PnL percentage for a trade."""
    if entry_price == 0:
        return 0.0
    return ((exit_price - entry_price) / entry_price) * 100


def _pf(pnls: list[float]) -> float:
    """Calculate profit factor from PnL list."""
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p <= 0))
    if gross_loss == 0:
        return 0.0 if gross_profit == 0 else 99.0
    return gross_profit / gross_loss


def _max_drawdown(pnls: list[float]) -> float:
    """Calculate max drawdown from sequential PnL list."""
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _max_consecutive_losses(trades: list[ReplayTrade]) -> int:
    """Count maximum consecutive losses."""
    max_streak = 0
    current = 0
    for t in trades:
        if (t.pnl_pct or 0) <= 0:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak
