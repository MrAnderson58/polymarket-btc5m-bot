"""Advanced analytics: optimizer, significance, Monte Carlo, drift, equity."""

from __future__ import annotations

import math
import random
import sqlite3
import statistics
from datetime import datetime, timezone
from typing import Any

from bot.config import ER_V2_STOP_LOSS_PCT, TRAILING_ACTIVATION_PROFIT, TRAILING_OFFSET
from bot.er_btc_direction_stats import _exit_ts, _nearest_btc_price
from bot.no_c_btc_filter import compute_btc_move_30s
from bot.no_c_filter_shadow import _btc_price_at
from bot.report.analytics import (
    ALT_STOP_PCTS,
    ENTRY_PRICES,
    TRAIL_ACTIVATION_OPTS,
    TRAIL_DISTANCE_OPTS,
    _metrics,
    _pnl_pct,
    _simulate_trailing_exit,
    fetch_bid_series,
    trade_pnl,
)

MONTE_CARLO_ITERATIONS = 1000
ROLLING_WINDOW = 20
CONFIDENCE_HIGH_N = 100
CONFIDENCE_MEDIUM_N = 30
PERMUTATION_ITERATIONS = 2000


def confidence_label(n: int) -> str:
    if n >= CONFIDENCE_HIGH_N:
        return "High"
    if n >= CONFIDENCE_MEDIUM_N:
        return "Medium"
    return "Low"


def _pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 3 or len(xs) != len(ys):
        return 0.0
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def _permutation_pvalue(sample_a: list[float], sample_b: list[float]) -> float:
    if len(sample_a) < 3 or len(sample_b) < 3:
        return 1.0
    observed = abs(statistics.mean(sample_a) - statistics.mean(sample_b))
    combined = sample_a + sample_b
    n_a = len(sample_a)
    hits = 0
    for _ in range(PERMUTATION_ITERATIONS):
        random.shuffle(combined)
        diff = abs(statistics.mean(combined[:n_a]) - statistics.mean(combined[n_a:]))
        if diff >= observed:
            hits += 1
    return (hits + 1) / (PERMUTATION_ITERATIONS + 1)


def _max_drawdown(cumulative: list[float]) -> float:
    peak = cumulative[0]
    max_dd = 0.0
    for value in cumulative:
        peak = max(peak, value)
        max_dd = max(max_dd, peak - value)
    return max_dd


def build_trade_features(
    conn: sqlite3.Connection,
    closed: list[sqlite3.Row],
) -> list[dict[str, Any]]:
    features: list[dict[str, Any]] = []
    for trade in closed:
        entry_ts = int(trade["entry_ts"])
        exit_ts = _exit_ts(trade)
        slug = trade["market_slug"]
        side = trade["side"]
        entry = float(trade["entry_price"])
        pnl = trade_pnl(trade)

        btc_move = None
        current_btc = _btc_price_at(conn, entry_ts)
        if current_btc is not None:
            btc_move = compute_btc_move_30s(conn, current_btc=current_btc, now_ts=entry_ts)

        btc_trade_move = None
        btc_entry = _nearest_btc_price(conn, market_slug=slug, target_ts=entry_ts)
        btc_exit = _nearest_btc_price(conn, market_slug=slug, target_ts=exit_ts)
        if btc_entry is not None and btc_exit is not None:
            btc_trade_move = btc_exit - btc_entry

        spread = None
        prefix = "yes" if side == "YES" else "no"
        row = conn.execute(
            f"""
            SELECT {prefix}_bid AS bid, {prefix}_ask AS ask
            FROM market_checks
            WHERE market_slug = ?
            ORDER BY abs(cast(strftime('%s', checked_at) AS integer) - ?) ASC
            LIMIT 1
            """,
            (slug, entry_ts),
        ).fetchone()
        if row and row["bid"] is not None and row["ask"] is not None:
            spread = float(row["ask"]) - float(row["bid"])

        holding = float(trade["holding_time_seconds"] or 0)
        slippage = 0.0
        audit = conn.execute(
            """
            SELECT slippage FROM order_fill_audit
            WHERE idempotency_key LIKE ?
            LIMIT 1
            """,
            (f"%{slug}%",),
        ).fetchone()
        if audit and audit["slippage"] is not None:
            slippage = abs(float(audit["slippage"]))

        features.append(
            {
                "pnl": pnl,
                "btc_move_30s": btc_move,
                "btc_trade_move": btc_trade_move,
                "entry_price": entry,
                "holding_sec": holding,
                "volatility": abs(btc_trade_move) if btc_trade_move is not None else None,
                "spread": spread,
                "slippage": slippage,
            }
        )
    return features


def build_correlation_matrix(features: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "btc_move_30s",
        "entry_price",
        "holding_sec",
        "volatility",
        "spread",
        "slippage",
        "pnl",
    )
    labels = {
        "btc_move_30s": "BTC move 30s",
        "entry_price": "Entry price",
        "holding_sec": "Holding time",
        "volatility": "Volatility",
        "spread": "Spread",
        "slippage": "Slippage",
        "pnl": "Profit",
    }
    matrix: dict[str, dict[str, float]] = {}
    for k1 in keys:
        matrix[labels[k1]] = {}
        vals1 = [f[k1] for f in features if f.get(k1) is not None]
        for k2 in keys:
            pairs = [
                (f[k1], f[k2])
                for f in features
                if f.get(k1) is not None and f.get(k2) is not None
            ]
            if len(pairs) < 5:
                matrix[labels[k1]][labels[k2]] = 0.0
                continue
            xs, ys = zip(*pairs)
            matrix[labels[k1]][labels[k2]] = _pearson(list(xs), list(ys))
    return {"labels": list(labels.values()), "matrix": matrix}


def build_feature_importance(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    feature_keys = (
        ("btc_move_30s", "BTC move"),
        ("entry_price", "Entry price"),
        ("holding_sec", "Holding time"),
        ("volatility", "Volatility"),
        ("spread", "Spread"),
        ("slippage", "Slippage"),
    )
    pnls = [f["pnl"] for f in features]
    raw: list[tuple[str, float]] = []
    for key, label in feature_keys:
        pairs = [(f[key], f["pnl"]) for f in features if f.get(key) is not None]
        if len(pairs) < 5:
            raw.append((label, 0.0))
            continue
        xs, ys = zip(*pairs)
        raw.append((label, abs(_pearson(list(xs), list(ys)))))

    total = sum(v for _, v in raw) or 1.0
    return [
        {"feature": label, "importance_pct": round(100 * value / total, 1)}
        for label, value in sorted(raw, key=lambda x: x[1], reverse=True)
    ]


def _simulate_stop_pnl(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
    stop_pct: float,
) -> float:
    entry = float(trade["entry_price"])
    stop_price = entry * (1 + stop_pct / 100)
    series = fetch_bid_series(
        conn,
        market_slug=trade["market_slug"],
        side=trade["side"],
        start_ts=int(trade["entry_ts"]),
        end_ts=_exit_ts(trade),
    )
    if any(bid <= stop_price + 1e-9 for _, bid in series):
        return _pnl_pct(entry, stop_price)
    return trade_pnl(trade)


def _simulate_trailing_pnl(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
    activation: float,
    distance: float,
) -> float:
    entry = float(trade["entry_price"])
    series = fetch_bid_series(
        conn,
        market_slug=trade["market_slug"],
        side=trade["side"],
        start_ts=int(trade["entry_ts"]),
        end_ts=_exit_ts(trade),
    )
    if not series:
        return trade_pnl(trade)
    return _simulate_trailing_exit(series, entry=entry, activation=activation, distance=distance)


def build_parameter_optimizer(
    conn: sqlite3.Connection,
    closed: list[sqlite3.Row],
    *,
    current_entry: float = 0.40,
    current_stop_pct: float = ER_V2_STOP_LOSS_PCT,
    current_trailing: float = TRAILING_ACTIVATION_PROFIT,
) -> dict[str, Any]:
    current_pnls = [trade_pnl(t) for t in closed]
    current_m = _metrics(current_pnls)

    best_entry = current_entry
    best_entry_m = current_m
    for price in ENTRY_PRICES:
        subset = [t for t in closed if float(t["entry_price"]) <= price + 0.005]
        pnls = [trade_pnl(t) for t in subset]
        m = _metrics(pnls)
        if m["trades"] < 10:
            continue
        if m["net_profit"] > best_entry_m["net_profit"]:
            best_entry = price
            best_entry_m = m

    best_stop = current_stop_pct
    best_stop_m = current_m
    for stop_pct in ALT_STOP_PCTS:
        pnls = [_simulate_stop_pnl(conn, t, stop_pct) for t in closed]
        m = _metrics(pnls)
        if m["net_profit"] > best_stop_m["net_profit"]:
            best_stop = stop_pct
            best_stop_m = m

    best_trailing = current_trailing
    best_trail_m = current_m
    best_distance = TRAILING_OFFSET
    for activation in TRAIL_ACTIVATION_OPTS:
        for distance in TRAIL_DISTANCE_OPTS:
            pnls = [
                _simulate_trailing_pnl(conn, t, activation, distance) for t in closed
            ]
            m = _metrics(pnls)
            if m["net_profit"] > best_trail_m["net_profit"]:
                best_trailing = activation
                best_distance = distance
                best_trail_m = m

    optimal_pnls: list[float] = []
    for trade in closed:
        if float(trade["entry_price"]) > best_entry + 0.005:
            continue
        pnl = _simulate_stop_pnl(conn, trade, best_stop)
        trail_pnl = _simulate_trailing_pnl(conn, trade, best_trailing, best_distance)
        optimal_pnls.append(max(pnl, trail_pnl, key=lambda x: x))
    optimal_m = _metrics(optimal_pnls)

    if current_m["avg_pnl"] != 0:
        improvement = (
            (optimal_m["avg_pnl"] - current_m["avg_pnl"]) / abs(current_m["avg_pnl"]) * 100
        )
    else:
        improvement = optimal_m["avg_pnl"]

    entry_rows = []
    for price in ENTRY_PRICES:
        subset = [t for t in closed if abs(float(t["entry_price"]) - price) <= 0.005]
        pnls = [trade_pnl(t) for t in subset]
        m = _metrics(pnls)
        entry_rows.append(
            {
                "entry_price": price,
                **m,
                "confidence": confidence_label(m["trades"]),
                "p_value_vs_rest": _permutation_pvalue(
                    pnls,
                    [
                        trade_pnl(t)
                        for t in closed
                        if abs(float(t["entry_price"]) - price) > 0.005
                    ],
                ),
                "significant": None,
            }
        )
    for row in entry_rows:
        row["significant"] = row["p_value_vs_rest"] < 0.05 and row["trades"] >= 10

    return {
        "current": {
            "entry": current_entry,
            "stop_pct": current_stop_pct,
            "trailing_activation": current_trailing,
            "trailing_distance": TRAILING_OFFSET,
            **current_m,
            "confidence": confidence_label(current_m["trades"]),
        },
        "optimal": {
            "entry": best_entry,
            "stop_pct": best_stop,
            "trailing_activation": best_trailing,
            "trailing_distance": best_distance,
            **optimal_m,
            "confidence": confidence_label(optimal_m["trades"]),
        },
        "expected_improvement_pct": improvement,
        "entry_significance": entry_rows,
    }


def build_monte_carlo(closed: list[sqlite3.Row]) -> dict[str, Any]:
    pnls = [trade_pnl(t) for t in closed]
    if len(pnls) < 5:
        return {
            "iterations": 0,
            "expected_max_drawdown_pct": 0.0,
            "worst_month_pnl_pct": 0.0,
            "probability_of_ruin": 0.0,
            "expected_cagr_pct": 0.0,
        }

    drawdowns: list[float] = []
    ruin_hits = 0
    for _ in range(MONTE_CARLO_ITERATIONS):
        shuffled = pnls.copy()
        random.shuffle(shuffled)
        cumulative = [0.0]
        for p in shuffled:
            cumulative.append(cumulative[-1] + p)
        drawdowns.append(_max_drawdown(cumulative))
        if cumulative[-1] < -50:
            ruin_hits += 1

    by_day: dict[str, float] = {}
    for trade in closed:
        closed_at = trade["closed_at"]
        if not closed_at:
            continue
        day = str(closed_at)[:10]
        by_day[day] = by_day.get(day, 0.0) + trade_pnl(trade)
    monthly = list(by_day.values()) if by_day else [sum(pnls)]
    worst_month = min(monthly)

    dates: list[datetime] = []
    for trade in closed:
        if trade["closed_at"]:
            try:
                dates.append(
                    datetime.fromisoformat(str(trade["closed_at"]).replace(" ", "T"))
                )
            except ValueError:
                continue
    span_days = max(1, (max(dates) - min(dates)).days) if len(dates) >= 2 else 30
    total_pnl = sum(pnls)
    cagr = total_pnl / span_days * 365 / max(len(pnls), 1)

    return {
        "iterations": MONTE_CARLO_ITERATIONS,
        "expected_max_drawdown_pct": statistics.mean(drawdowns),
        "worst_month_pnl_pct": worst_month,
        "probability_of_ruin": ruin_hits / MONTE_CARLO_ITERATIONS,
        "expected_cagr_pct": cagr,
    }


def build_equity_curve(closed: list[sqlite3.Row]) -> dict[str, Any]:
    pnls = [trade_pnl(t) for t in closed]
    cumulative: list[float] = []
    total = 0.0
    for p in pnls:
        total += p
        cumulative.append(total)

    rolling_wr: list[float | None] = []
    rolling_pf: list[float | None] = []
    for i in range(len(pnls)):
        window = pnls[max(0, i - ROLLING_WINDOW + 1) : i + 1]
        if len(window) < 5:
            rolling_wr.append(None)
            rolling_pf.append(None)
            continue
        rolling_wr.append(sum(1 for p in window if p > 0) / len(window))
        wins = sum(p for p in window if p > 0)
        losses = abs(sum(p for p in window if p <= 0))
        rolling_pf.append(wins / losses if losses else float("inf"))

    if cumulative:
        mn, mx = min(cumulative), max(cumulative)
        span = mx - mn or 1.0
        blocks = 20
        idx = int((cumulative[-1] - mn) / span * (blocks - 1))
        spark = ["░"] * blocks
        spark[idx] = "█"
        sparkline = "".join(spark)
    else:
        sparkline = "░" * 20

    daily: dict[str, float] = {}
    for trade in closed:
        if trade["closed_at"]:
            day = str(trade["closed_at"])[:10]
            daily[day] = daily.get(day, 0.0) + trade_pnl(trade)

    return {
        "cumulative_pnl_pct": cumulative,
        "sparkline": sparkline,
        "final_pnl_pct": cumulative[-1] if cumulative else 0.0,
        "max_drawdown_pct": _max_drawdown(cumulative) if cumulative else 0.0,
        "rolling_wr": rolling_wr[-10:],
        "rolling_pf": rolling_pf[-10:],
        "daily_pnl": daily,
    }


def build_drift_detector(closed: list[sqlite3.Row]) -> dict[str, Any]:
    pnls = [trade_pnl(t) for t in closed]
    if len(pnls) < 70:
        return {
            "status": "insufficient_data",
            "recent_n": min(50, len(pnls)),
            "baseline_n": max(0, len(pnls) - 50),
            "degradation_pct": 0.0,
            "message": "Not enough trades for drift detection",
        }
    recent = pnls[-50:]
    baseline = pnls[-250:-50] if len(pnls) >= 250 else pnls[:-50]
    recent_avg = statistics.mean(recent)
    baseline_avg = statistics.mean(baseline) if baseline else recent_avg
    if baseline_avg != 0:
        degradation = (baseline_avg - recent_avg) / abs(baseline_avg) * 100
    else:
        degradation = 0.0
    if degradation > 10:
        status = "degrading"
        message = f"Последние 50 сделок хуже предыдущих {len(baseline)} на {degradation:.1f}%"
    elif degradation < -10:
        status = "improving"
        message = f"Последние 50 сделок лучше предыдущих {len(baseline)} на {abs(degradation):.1f}%"
    else:
        status = "stable"
        message = "Стратегия стабильна относительно базового окна"
    return {
        "status": status,
        "recent_n": 50,
        "baseline_n": len(baseline),
        "recent_avg_pnl": recent_avg,
        "baseline_avg_pnl": baseline_avg,
        "degradation_pct": degradation,
        "message": message,
        "p_value": _permutation_pvalue(recent, baseline) if baseline else 1.0,
        "significant": _permutation_pvalue(recent, baseline) < 0.05 if baseline else False,
    }


def build_live_vs_replay(
    live_metrics: dict[str, Any],
    optimizer: dict[str, Any],
) -> dict[str, Any]:
    replay = optimizer.get("optimal", {})
    live_pf = live_metrics.get("profit_factor", 0)
    replay_pf = replay.get("profit_factor", 0)
    delta = replay_pf - live_pf if live_pf not in (0, float("inf")) else 0
    gap_pct = 0.0
    if live_pf not in (0, float("inf")) and live_pf > 0:
        gap_pct = delta / live_pf * 100
    execution_gap = abs(delta) > 0.3

    reason_parts = []
    cur = optimizer.get("current", {})
    opt = optimizer.get("optimal", {})
    if opt.get("entry") != cur.get("entry"):
        reason_parts.append("Entry threshold")
    if opt.get("stop_pct") != cur.get("stop_pct"):
        reason_parts.append("Stop loss")
    if opt.get("trailing_activation") != cur.get("trailing_activation"):
        reason_parts.append("Trailing activation")
    reason = ", ".join(reason_parts) if reason_parts else "Execution / slippage"

    return {
        "live": {
            "profit_factor": live_pf,
            "avg_pnl": live_metrics.get("avg_pnl", 0),
            "win_rate": live_metrics.get("win_rate", 0),
        },
        "replay": {
            "profit_factor": replay_pf,
            "avg_pnl": replay.get("avg_pnl", 0),
            "win_rate": replay.get("win_rate", 0),
        },
        "pf_delta": delta,
        "gap_pct": round(gap_pct, 1),
        "reason": reason,
        "execution_issue_suspected": execution_gap,
        "note": (
            "Large live vs replay gap — check execution/slippage"
            if execution_gap
            else "Live and replay are aligned"
        ),
    }
