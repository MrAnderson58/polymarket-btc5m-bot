"""Deterministic golden S55 trade corpus for Research QA (immutable by seed)."""

from __future__ import annotations

import json
import random
from typing import Any

GOLDEN_SEED = 42
GOLDEN_N_DEFAULT = 200
GOLDEN_N_MIN = 100
GOLDEN_N_MAX = 300


def generate_golden_trades(
    n: int = GOLDEN_N_DEFAULT,
    *,
    seed: int = GOLDEN_SEED,
) -> list[dict[str, Any]]:
    """
    Synthetic closed S55-shaped trades. Same seed → identical corpus forever.
    Designed so Feature Validation / Pattern / Hypothesis / Experiment have signal.
    """
    n = max(GOLDEN_N_MIN, min(GOLDEN_N_MAX, int(n)))
    rng = random.Random(seed)
    base_ts = 1_700_000_000
    trades: list[dict[str, Any]] = []
    for i in range(n):
        regime_roll = rng.random()
        if regime_roll < 0.35:
            regime = "WEAK_BULL"
            trend = rng.uniform(0.4, 1.5)
            funding = rng.uniform(0.01, 0.03)
            edge = 0.9
        elif regime_roll < 0.65:
            regime = "RANGE"
            trend = rng.uniform(-0.3, 0.3)
            funding = rng.uniform(-0.005, 0.01)
            edge = 0.0
        else:
            regime = "WEAK_BEAR"
            trend = rng.uniform(-1.5, -0.4)
            funding = rng.uniform(-0.02, 0.0)
            edge = -0.7

        direction = "LONG" if rng.random() < 0.55 else "SHORT"
        align = 0.0
        if direction == "LONG" and trend > 0.2:
            align = 0.6
        elif direction == "SHORT" and trend < -0.2:
            align = 0.6
        elif direction == "LONG" and trend < -0.5:
            align = -0.8
        elif direction == "SHORT" and trend > 0.5:
            align = -0.8

        oi = rng.uniform(-5, 5) + (2.0 if funding > 0.01 else -1.0)
        vol = rng.uniform(0.3, 2.0)
        if vol > 1.4:
            edge -= 0.35
        fg = rng.uniform(10, 70)
        ai = 50 + trend * 15 + rng.uniform(-10, 10)
        neighbor = edge * 0.3 + rng.uniform(-0.2, 0.2)

        noise = rng.gauss(0, 0.55)
        pnl = edge + align + (0.25 if funding > 0.015 else 0.0) + noise
        pnl = max(-4.0, min(4.0, pnl))

        trades.append(
            {
                "s40_signal_type": "g3",
                "s40_signal_id": i + 1,
                "symbol": "BTCUSDT" if i % 5 else "ETHUSDT",
                "direction": direction,
                "funding": round(funding, 6),
                "trend": round(trend, 6),
                "fear_greed": round(fg, 4),
                "oi_delta": round(oi, 4),
                "volatility": round(vol, 4),
                "ai_score": round(ai, 4),
                "features_json": json.dumps(
                    {
                        "funding": funding,
                        "trend": trend,
                        "fear_greed": fg,
                        "oi_delta": oi,
                        "volatility": vol,
                        "ai_score": ai,
                    }
                ),
                "gate_decision": "ALLOWED",
                "gate_expected_pnl_pct": round(neighbor, 4),
                "created_at": base_ts + i * 300,
                "closed_at": base_ts + i * 300 + 180 + (i % 40),
                "pnl_pct": round(pnl, 4),
                "result": "WIN" if pnl > 0 else "LOSS",
                "mfe_pct": round(abs(pnl) + rng.uniform(0.1, 0.8), 4),
                "mae_pct": round(-abs(pnl) * rng.uniform(0.2, 0.7), 4),
                "duration_sec": 120 + (i % 90),
                "market_regime": regime,
            }
        )
    return trades


def seed_golden_s55(conn: Any, trades: list[dict[str, Any]] | None = None) -> int:
    """Insert golden trades into market_events_trade_features_s55. Returns n."""
    rows = trades if trades is not None else generate_golden_trades()
    for t in rows:
        conn.execute(
            """
            INSERT INTO market_events_trade_features_s55 (
              s40_signal_type, s40_signal_id, symbol, direction,
              funding, trend, fear_greed, oi_delta, volatility, ai_score,
              features_json, gate_decision, gate_expected_pnl_pct,
              created_at, closed_at, pnl_pct, result, mfe_pct, mae_pct,
              duration_sec, market_regime
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                t["s40_signal_type"],
                t["s40_signal_id"],
                t["symbol"],
                t["direction"],
                t["funding"],
                t["trend"],
                t["fear_greed"],
                t["oi_delta"],
                t["volatility"],
                t["ai_score"],
                t["features_json"],
                t["gate_decision"],
                t["gate_expected_pnl_pct"],
                t["created_at"],
                t["closed_at"],
                t["pnl_pct"],
                t["result"],
                t["mfe_pct"],
                t["mae_pct"],
                t["duration_sec"],
                t["market_regime"],
            ),
        )
    try:
        conn.commit()
    except Exception:
        pass
    return len(rows)
