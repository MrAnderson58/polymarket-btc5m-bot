"""BTC movement feature engineering for bidirectional momentum research.

Computes symmetric features from v4_shadow_observations time series:
- Multi-timeframe BTC velocity and acceleration
- Direction consistency
- Regime classification from empirical distributions
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class MovementFeatures:
    timestamp: int
    market_slug: str
    seconds_from_start: int
    seconds_left: int

    btc_price: float
    strike: float
    delta: float

    yes_bid: float
    yes_ask: float
    no_bid: float
    no_ask: float
    spread: float

    btc_move_5s: float = 0.0
    btc_move_10s: float = 0.0
    btc_move_15s: float = 0.0
    btc_move_30s: float = 0.0
    btc_move_60s: float = 0.0
    btc_move_90s: float = 0.0

    btc_velocity_10s: float = 0.0
    btc_velocity_30s: float = 0.0
    btc_acceleration: float = 0.0

    distance_from_strike_usd: float = 0.0
    distance_from_strike_pct: float = 0.0

    direction_consistency: float = 0.0
    regime: str = "NORMAL"

    trend_score: float | None = None
    trend_side: str | None = None


def compute_btc_moves(observations: list[dict], idx: int) -> dict[str, float]:
    """Compute BTC movement features at observation index using only past data."""
    current = observations[idx]
    ts = current["timestamp"]
    btc = current["btc_price"]

    moves = {}
    windows = [5, 10, 15, 30, 60, 90]

    for w in windows:
        target_ts = ts - w
        past_price = _find_price_at(observations, idx, target_ts)
        moves[f"btc_move_{w}s"] = (btc - past_price) if past_price else 0.0

    vel_10 = moves["btc_move_10s"] / 10.0 if moves["btc_move_10s"] else 0.0
    vel_30 = moves["btc_move_30s"] / 30.0 if moves["btc_move_30s"] else 0.0
    moves["btc_velocity_10s"] = vel_10
    moves["btc_velocity_30s"] = vel_30
    moves["btc_acceleration"] = (vel_10 - vel_30) / 20.0 if vel_30 != 0 else 0.0

    return moves


def compute_direction_consistency(moves: dict[str, float]) -> float:
    """Score 0-1 indicating how consistently BTC moves in one direction."""
    signs = []
    for k in ["btc_move_10s", "btc_move_30s", "btc_move_60s"]:
        v = moves.get(k, 0.0)
        if v > 0:
            signs.append(1)
        elif v < 0:
            signs.append(-1)
        else:
            signs.append(0)

    if not signs:
        return 0.0

    dominant = max(abs(sum(signs)), 1)
    return dominant / len(signs)


def classify_regime(moves: dict[str, float], *, quantiles: dict | None = None) -> str:
    """Classify market regime from movement features.

    Uses empirical quantiles when provided; otherwise uses reasonable defaults
    derived from data analysis.
    """
    q = quantiles or {
        "strong_move_30s": 40.0,
        "news_spike_30s": 80.0,
        "chop_velocity": 0.3,
    }

    move_30 = abs(moves.get("btc_move_30s", 0.0))
    move_60 = abs(moves.get("btc_move_60s", 0.0))
    raw_vel_10 = moves.get("btc_velocity_10s", 0.0)
    raw_vel_30 = moves.get("btc_velocity_30s", 0.0)
    vel_10 = abs(raw_vel_10)
    vel_30 = abs(raw_vel_30)
    accel = moves.get("btc_acceleration", 0.0)
    consistency = moves.get("direction_consistency", 0.0)

    if move_30 >= q["news_spike_30s"]:
        return "NEWS_SPIKE"

    if move_30 >= q["strong_move_30s"] and consistency >= 0.8:
        return "STRONG_MOMENTUM"

    if move_30 >= q["strong_move_30s"] * 0.5 and consistency >= 0.6:
        return "MOMENTUM"

    if raw_vel_10 != 0 and raw_vel_30 != 0 and (raw_vel_10 * raw_vel_30) < 0:
        return "REVERSAL"

    if move_30 < q["strong_move_30s"] * 0.2 and vel_10 < q["chop_velocity"]:
        return "CHOP"

    return "NORMAL"


def build_features_for_market(
    observations: list[dict],
) -> list[MovementFeatures]:
    """Build full feature set for all observations in a market.

    Only uses data available at or before each observation timestamp.
    """
    results = []
    for idx in range(len(observations)):
        obs = observations[idx]
        moves = compute_btc_moves(observations, idx)
        consistency = compute_direction_consistency(moves)
        moves["direction_consistency"] = consistency
        regime = classify_regime(moves)

        strike = obs.get("strike") or obs.get("btc_price", 0)
        btc = obs["btc_price"]
        dist_usd = btc - strike
        dist_pct = (dist_usd / strike * 100) if strike else 0.0

        feat = MovementFeatures(
            timestamp=obs["timestamp"],
            market_slug=obs["market_slug"],
            seconds_from_start=obs["seconds_from_start"],
            seconds_left=obs["seconds_left"],
            btc_price=btc,
            strike=strike,
            delta=obs.get("delta", 0.0),
            yes_bid=obs.get("yes_bid", 0.0) or 0.0,
            yes_ask=obs.get("yes_ask", 0.0) or 0.0,
            no_bid=obs.get("no_bid", 0.0) or 0.0,
            no_ask=obs.get("no_ask", 0.0) or 0.0,
            spread=obs.get("spread", 0.0) or 0.0,
            btc_move_5s=moves["btc_move_5s"],
            btc_move_10s=moves["btc_move_10s"],
            btc_move_15s=moves["btc_move_15s"],
            btc_move_30s=moves["btc_move_30s"],
            btc_move_60s=moves["btc_move_60s"],
            btc_move_90s=moves["btc_move_90s"],
            btc_velocity_10s=moves["btc_velocity_10s"],
            btc_velocity_30s=moves["btc_velocity_30s"],
            btc_acceleration=moves["btc_acceleration"],
            distance_from_strike_usd=dist_usd,
            distance_from_strike_pct=dist_pct,
            direction_consistency=consistency,
            regime=regime,
            trend_score=obs.get("trend_score"),
            trend_side=obs.get("trend_side"),
        )
        results.append(feat)

    return results


def _find_price_at(observations: list[dict], current_idx: int, target_ts: int) -> float | None:
    """Find BTC price closest to target_ts, using only past observations."""
    best = None
    best_diff = float("inf")
    for i in range(current_idx, -1, -1):
        ts = observations[i]["timestamp"]
        if ts > target_ts + 2:
            continue
        diff = abs(ts - target_ts)
        if diff < best_diff:
            best_diff = diff
            best = observations[i]["btc_price"]
        if ts < target_ts - 5:
            break
    return best


def load_market_observations(
    conn: sqlite3.Connection,
    market_slug: str,
    *,
    normalize_quotes: bool = True,
) -> list[dict]:
    """Load all v4 observations for a market, ordered by timestamp."""
    rows = conn.execute(
        "SELECT * FROM v4_shadow_observations WHERE market_slug = ? ORDER BY timestamp ASC",
        (market_slug,),
    ).fetchall()
    out = [dict(r) for r in rows]
    if normalize_quotes:
        from bot.research.quote_semantics import normalize_observation_path

        return normalize_observation_path(out)
    return out


def get_research_markets(conn: sqlite3.Connection, min_obs: int = 30) -> list[str]:
    """Get markets with enough observations for feature computation."""
    rows = conn.execute(
        """SELECT market_slug, COUNT(*) as n
           FROM v4_shadow_observations
           GROUP BY market_slug HAVING n >= ?
           ORDER BY market_slug""",
        (min_obs,),
    ).fetchall()
    return [r["market_slug"] for r in rows]
