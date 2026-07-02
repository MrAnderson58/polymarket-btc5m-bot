"""Report analytics helpers — read-only computations over trades.db."""

from __future__ import annotations

import sqlite3
import statistics
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bot.perf.market_cache import MarketDataCache

from bot.config import (
    POLL_INTERVAL_SEC,
    effective_entry_threshold,
)
from bot.er_btc_direction_stats import (
    BTC_FLAT_THRESHOLD_USD,
    _exit_ts,
)
from bot.er_stats import (
    STRATEGY_NAMES,
    _enabled_strategy_names,
    _realized_pnl_percent,
    fetch_aggregated_funnel_stats,
    fetch_funnel_window_stats,
)
from bot.no_c_btc_filter import (
    BTC_MOVE_STRICT_USD,
    NO_C_NORMAL_THRESHOLD,
    NO_C_STRICT_THRESHOLD,
)

V2_TABLE = "early_reversion_v2_trades"
ENTRY_PRICES = [round(x * 0.01, 2) for x in range(34, 41)]
HOLDING_BUCKETS_SEC = (5, 10, 20, 30, 40, 50, 60, 90, 120)
ALT_STOP_PCTS = (-15.0, -18.0, -20.0, -22.0, -25.0)
TRAIL_ACTIVATION_OPTS = (0.02, 0.025, 0.03, 0.04)
TRAIL_DISTANCE_OPTS = (0.01, 0.015, 0.02, 0.03)
WALK_FORWARD_SIZES = (20, 50, 100, 200)
SETTLEMENT_BID = 0.99

BTC_BUCKETS = [
    ("<=+5", float("-inf"), 5.0),
    ("+5..+10", 5.0, 10.0),
    ("+10..+15", 10.0, 15.0),
    ("+15..+20", 15.0, 20.0),
    ("+20..+30", 20.0, 30.0),
    (">+30", 30.0, float("inf")),
]
RECOMMENDED_NO_C_THRESHOLDS = {
    "<=+5": 0.40,
    "+5..+10": 0.39,
    "+10..+15": 0.38,
    "+15..+20": 0.38,
    "+20..+30": 0.37,
    ">+30": 0.40,
}


def _pnl_pct(entry: float, exit_price: float) -> float:
    return (exit_price - entry) / entry * 100


def _metrics(pnls: list[float]) -> dict[str, Any]:
    if not pnls:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
            "profit_factor": 0.0,
            "net_profit": 0.0,
            "wins": 0,
            "losses": 0,
        }
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_profit = sum(wins)
    total_loss = abs(sum(losses))
    return {
        "trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(pnls),
        "avg_pnl": sum(pnls) / len(pnls),
        "profit_factor": total_profit / total_loss if total_loss else float("inf"),
        "net_profit": total_profit - total_loss,
    }


def fetch_v2_trades(conn: sqlite3.Connection, *, closed_only: bool = False) -> list[sqlite3.Row]:
    where = "WHERE status = 'closed'" if closed_only else ""
    return conn.execute(
        f"""
        SELECT *
        FROM {V2_TABLE}
        {where}
        ORDER BY entry_ts ASC
        """
    ).fetchall()


def trade_pnl(trade: sqlite3.Row) -> float:
    return _realized_pnl_percent(trade)


def _bid_column(side: str) -> str:
    return "yes_bid" if side == "YES" else "no_bid"


def fetch_bid_series(
    conn: sqlite3.Connection,
    *,
    market_slug: str,
    side: str,
    start_ts: int,
    end_ts: int,
) -> list[tuple[int, float]]:
    column = _bid_column(side)
    rows = conn.execute(
        f"""
        SELECT cast(strftime('%s', checked_at) AS integer) AS ts,
               {column} AS bid
        FROM market_checks
        WHERE market_slug = ?
          AND cast(strftime('%s', checked_at) AS integer) >= ?
          AND cast(strftime('%s', checked_at) AS integer) <= ?
        ORDER BY ts ASC
        """,
        (market_slug, start_ts, end_ts),
    ).fetchall()
    series: list[tuple[int, float]] = []
    for row in rows:
        if row["bid"] is None:
            continue
        bid = float(row["bid"])
        if bid + 1e-9 >= SETTLEMENT_BID:
            continue
        series.append((int(row["ts"]), bid))
    return series


def _btc_bucket(move: float) -> str:
    for label, lo, hi in BTC_BUCKETS:
        if lo < move <= hi:
            return label
    return "<=+5"


def build_entry_price_analysis(closed: list[sqlite3.Row]) -> dict[str, Any]:
    by_price: dict[float, list[float]] = {p: [] for p in ENTRY_PRICES}
    for trade in closed:
        price = round(float(trade["entry_price"]), 2)
        nearest = min(ENTRY_PRICES, key=lambda p: abs(p - price))
        if abs(nearest - price) <= 0.005:
            by_price[nearest].append(trade_pnl(trade))

    rows = []
    for price in ENTRY_PRICES:
        pnls = by_price[price]
        m = _metrics(pnls)
        rows.append({"entry_price": price, **m})

    ranked = [r for r in rows if r["trades"] > 0]
    best = max(ranked, key=lambda r: (r["profit_factor"], r["net_profit"])) if ranked else None
    worst = min(ranked, key=lambda r: (r["profit_factor"], r["net_profit"])) if ranked else None
    return {
        "rows": rows,
        "best_entry_price": best["entry_price"] if best else None,
        "worst_entry_price": worst["entry_price"] if worst else None,
    }


def build_exit_analysis(closed: list[sqlite3.Row]) -> dict[str, Any]:
    groups: dict[str, list[float]] = {
        "TRAILING_STOP": [],
        "STOP_LOSS": [],
        "TIME_STOP": [],
        "RECOVERY_NO_POSITION": [],
        "OTHER": [],
    }
    for trade in closed:
        reason = trade["exit_reason"] or "OTHER"
        key = reason if reason in groups else "OTHER"
        groups[key].append(trade_pnl(trade))
    return {name: _metrics(pnls) for name, pnls in groups.items()}


def build_stop_loss_analysis(closed: list[sqlite3.Row]) -> dict[str, Any]:
    stops = [t for t in closed if t["exit_reason"] == "STOP_LOSS"]
    pnls = [trade_pnl(t) for t in stops]
    hold_times = [float(t["holding_time_seconds"]) for t in stops if t["holding_time_seconds"]]
    return {
        "count": len(stops),
        "avg_loss": sum(pnls) / len(pnls) if pnls else 0.0,
        "max_loss": min(pnls) if pnls else 0.0,
        "min_loss": max(pnls) if pnls else 0.0,
        "avg_time_to_stop_sec": sum(hold_times) / len(hold_times) if hold_times else 0.0,
    }


def build_alternative_stop_test(
    conn: sqlite3.Connection,
    closed: list[sqlite3.Row],
) -> dict[str, Any]:
    results: dict[str, list[float]] = {str(p): [] for p in ALT_STOP_PCTS}
    for trade in closed:
        entry = float(trade["entry_price"])
        exit_ts = _exit_ts(trade)
        series = fetch_bid_series(
            conn,
            market_slug=trade["market_slug"],
            side=trade["side"],
            start_ts=int(trade["entry_ts"]),
            end_ts=exit_ts,
        )
        actual_pnl = trade_pnl(trade)
        for stop_pct in ALT_STOP_PCTS:
            stop_price = entry * (1 + stop_pct / 100)
            triggered = any(bid <= stop_price + 1e-9 for _, bid in series)
            if triggered:
                results[str(stop_pct)].append(_pnl_pct(entry, stop_price))
            else:
                results[str(stop_pct)].append(actual_pnl)
    return {label: _metrics(pnls) for label, pnls in results.items()}


def _simulate_trailing_exit(
    series: list[tuple[int, float]],
    *,
    entry: float,
    activation: float,
    distance: float,
) -> float:
    active = False
    high_water = entry
    last_bid = entry
    for _, bid in series:
        last_bid = bid
        if not active and bid - entry + 1e-9 >= activation:
            active = True
            high_water = bid
        if active:
            high_water = max(high_water, bid)
            if bid <= high_water - distance + 1e-9:
                return _pnl_pct(entry, bid)
    return _pnl_pct(entry, last_bid)


def build_trailing_simulation(
    conn: sqlite3.Connection,
    closed: list[sqlite3.Row],
) -> dict[str, Any]:
    combos: list[dict[str, Any]] = []
    for activation in TRAIL_ACTIVATION_OPTS:
        for distance in TRAIL_DISTANCE_OPTS:
            pnls: list[float] = []
            for trade in closed:
                entry = float(trade["entry_price"])
                exit_ts = _exit_ts(trade)
                series = fetch_bid_series(
                    conn,
                    market_slug=trade["market_slug"],
                    side=trade["side"],
                    start_ts=int(trade["entry_ts"]),
                    end_ts=exit_ts,
                )
                if not series:
                    pnls.append(trade_pnl(trade))
                    continue
                pnls.append(
                    _simulate_trailing_exit(
                        series,
                        entry=entry,
                        activation=activation,
                        distance=distance,
                    )
                )
            combos.append(
                {
                    "activation": activation,
                    "distance": distance,
                    **_metrics(pnls),
                }
            )
    best = max(combos, key=lambda c: (c["net_profit"], c["profit_factor"])) if combos else None
    return {"combos": combos, "best": best}


def build_btc_filter_analysis(
    closed: list[sqlite3.Row],
    *,
    cache: MarketDataCache,
    feature_by_trade_id: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    no_c = [t for t in closed if t["strategy_name"] == "NO_C"]
    bucket_pnls: dict[str, list[float]] = {b[0]: [] for b in BTC_BUCKETS}
    for trade in no_c:
        move = None
        if feature_by_trade_id is not None:
            feat = feature_by_trade_id.get(int(trade["id"]))
            if feat and feat.get("btc_move_30s") is not None:
                move = float(feat["btc_move_30s"])
        if move is None:
            move = cache.btc_move_at(int(trade["entry_ts"]), 30)
        if move is None:
            continue
        bucket_pnls[_btc_bucket(move)].append(trade_pnl(trade))

    rows = []
    for label, _, _ in BTC_BUCKETS:
        rows.append({"bucket": label, **_metrics(bucket_pnls[label])})

    return {
        "rows": rows,
        "current_thresholds": {
            "normal": effective_entry_threshold(NO_C_NORMAL_THRESHOLD),
            "strict": effective_entry_threshold(NO_C_STRICT_THRESHOLD),
            "btc_move_strict_usd": BTC_MOVE_STRICT_USD,
        },
        "recommended_thresholds": RECOMMENDED_NO_C_THRESHOLDS,
    }


def build_holding_time_analysis(closed: list[sqlite3.Row]) -> dict[str, Any]:
    rows = []
    for upper in HOLDING_BUCKETS_SEC:
        pnls = [
            trade_pnl(t)
            for t in closed
            if t["holding_time_seconds"] is not None
            and float(t["holding_time_seconds"]) <= upper
        ]
        rows.append({"max_holding_sec": upper, **_metrics(pnls)})
    return {"rows": rows}


def build_mae_mfe(
    closed: list[sqlite3.Row],
    *,
    cache: MarketDataCache,
) -> dict[str, Any]:
    maes: list[float] = []
    mfes: list[float] = []
    for trade in closed:
        mfe, mae = cache.mfe_mae(trade)
        if mfe is None or mae is None:
            continue
        mfes.append(mfe)
        maes.append(mae)

    def _dist(values: list[float]) -> dict[str, float]:
        if not values:
            return {"avg": 0.0, "median": 0.0, "p95": 0.0}
        sorted_v = sorted(values)
        p95_idx = min(len(sorted_v) - 1, int(len(sorted_v) * 0.95))
        return {
            "avg": statistics.mean(values),
            "median": statistics.median(values),
            "p95": sorted_v[p95_idx],
        }

    return {"mae": _dist(maes), "mfe": _dist(mfes), "mae_samples": len(maes)}


def build_market_regime(
    closed: list[sqlite3.Row],
    *,
    cache: MarketDataCache,
) -> dict[str, Any]:
    regimes: dict[str, list[float]] = {
        "Trend Up": [],
        "Trend Down": [],
        "Range": [],
        "High Volatility": [],
        "Low Volatility": [],
    }
    for trade in closed:
        entry_ts = int(trade["entry_ts"])
        exit_ts = _exit_ts(trade)
        slug = str(trade["market_slug"])
        btc_entry = cache.slug_btc_price_at(slug, entry_ts)
        btc_exit = cache.slug_btc_price_at(slug, exit_ts)
        if btc_entry is None or btc_exit is None:
            continue
        move = btc_exit - btc_entry
        pnl = trade_pnl(trade)
        if abs(move) < BTC_FLAT_THRESHOLD_USD:
            regimes["Range"].append(pnl)
        elif move > 0:
            regimes["Trend Up"].append(pnl)
        else:
            regimes["Trend Down"].append(pnl)
        if abs(move) >= BTC_FLAT_THRESHOLD_USD * 2:
            regimes["High Volatility"].append(pnl)
        else:
            regimes["Low Volatility"].append(pnl)
    return {name: _metrics(pnls) for name, pnls in regimes.items()}


def build_walk_forward(closed: list[sqlite3.Row]) -> dict[str, Any]:
    pnls_all = [trade_pnl(t) for t in closed]
    rows = [{"label": "All", **_metrics(pnls_all)}]
    for size in WALK_FORWARD_SIZES:
        subset = closed[-size:] if len(closed) >= size else closed
        rows.append({"label": f"Last {size}", **_metrics([trade_pnl(t) for t in subset])})

    if len(rows) >= 3:
        recent = rows[1]
        older = rows[-1]
        if recent["avg_pnl"] > older["avg_pnl"] + 1.0:
            trend = "improving"
        elif recent["avg_pnl"] < older["avg_pnl"] - 1.0:
            trend = "degrading"
        else:
            trend = "stable"
    else:
        trend = "insufficient_data"
    return {"rows": rows, "trend": trend}


def build_btc_direction_extended(
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    from bot.er_btc_direction_stats import fetch_strategy_btc_direction_stats

    result: dict[str, Any] = {}
    for strategy in _enabled_strategy_names() or STRATEGY_NAMES[:2]:
        stats = fetch_strategy_btc_direction_stats(conn, strategy)
        result[strategy] = [
            {
                "direction": b.direction,
                "trades": b.trades,
                "win_rate": b.win_rate,
                "avg_pnl": b.avg_pnl,
            }
            for b in stats.buckets
        ]
    return result


def build_execution_quality(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT clob_side, slippage, price_difference, fill_status, submitted_shares, fill_shares
        FROM order_fill_audit
        WHERE fill_status IN ('filled', 'partial')
        """
    ).fetchall()
    buy_slip: list[float] = []
    sell_slip: list[float] = []
    fill_diff: list[float] = []
    partial = 0
    for row in rows:
        slip = row["slippage"]
        if slip is not None:
            if row["clob_side"] == "BUY":
                buy_slip.append(float(slip))
            else:
                sell_slip.append(float(slip))
        if row["price_difference"] is not None:
            fill_diff.append(float(row["price_difference"]))
        if row["fill_status"] == "partial":
            partial += 1
    rejected = conn.execute(
        "SELECT COUNT(*) AS c FROM order_intents WHERE status = 'failed'"
    ).fetchone()
    recovery = conn.execute(
        """
        SELECT COUNT(*) AS c FROM early_reversion_v2_trades
        WHERE exit_reason = 'RECOVERY_NO_POSITION'
        """
    ).fetchone()
    return {
        "avg_buy_slippage": statistics.mean(buy_slip) if buy_slip else 0.0,
        "avg_sell_slippage": statistics.mean(sell_slip) if sell_slip else 0.0,
        "avg_fill_difference": statistics.mean(fill_diff) if fill_diff else 0.0,
        "rejected_intents": int(rejected["c"]) if rejected else 0,
        "partial_fills": partial,
        "recovery_exits": int(recovery["c"]) if recovery else 0,
        "filled_orders": len(rows),
    }


def build_funnel_extended(conn: sqlite3.Connection) -> dict[str, Any]:
    now = int(time.time())
    windows = [
        ("Last 1 hour", 3600),
        ("Last 6 hours", 21600),
        ("Last 24 hours", 86400),
        ("All time", 0),
    ]
    strategies = _enabled_strategy_names() or ("NO_C", "YES_B")
    sections: list[dict[str, Any]] = []
    for label, secs in windows:
        since = 0 if secs == 0 else now - secs
        for strategy in strategies:
            if secs == 0:
                agg = fetch_aggregated_funnel_stats(conn)
                row = next((r for r in agg if r.strategy_name == strategy), None)
                if row is None:
                    continue
                blocked_detail = {
                    "window": row.blocked_by_window,
                    "price": row.blocked_by_price,
                    "already_open": row.blocked_by_already_open,
                    "risk": row.blocked_by_risk,
                    "max_open_positions": row.blocked_max_open_positions,
                }
                sections.append(
                    {
                        "window": label,
                        "strategy": strategy,
                        "checks": row.checks,
                        "window_ok": row.window_ok,
                        "price_ok": row.price_ok,
                        "ready": row.already_open_ok,
                        "entries": row.entry_success,
                        "blocked": max(row.checks - row.entry_success, 0),
                        "blocked_reasons": blocked_detail,
                    }
                )
            else:
                stats = fetch_funnel_window_stats(
                    conn,
                    strategy_name=strategy,
                    since_ts=since,
                    label=label,
                )
                sections.append(
                    {
                        "window": label,
                        "strategy": strategy,
                        "checks": stats.checks,
                        "window_ok": stats.window_ok,
                        "price_ok": stats.price_ok,
                        "ready": stats.ready,
                        "entries": stats.entries,
                        "blocked": max(stats.checks - stats.entries, 0),
                    }
                )
    return {"sections": sections}


def build_open_positions(conn: sqlite3.Connection, *, now_ts: int) -> dict[str, Any]:
    from bot.database import get_open_early_reversion_v2_trades

    open_trades = get_open_early_reversion_v2_trades(conn)
    positions: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for trade in open_trades:
        slug = trade["market_slug"]
        side = trade["side"]
        entry = float(trade["entry_price"])
        entry_ts = int(trade["entry_ts"])
        end_ts = int(trade["end_ts"])
        holding = now_ts - entry_ts
        column = _bid_column(side)
        quote = conn.execute(
            f"""
            SELECT {column} AS bid
            FROM market_checks
            WHERE market_slug = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (slug,),
        ).fetchone()
        current_bid = float(quote["bid"]) if quote and quote["bid"] is not None else None
        peak = float(trade["max_price_seen"]) if trade["max_price_seen"] is not None else None
        pnl = _pnl_pct(entry, current_bid) if current_bid is not None else None
        drawdown = None
        if current_bid is not None and peak is not None:
            drawdown = (current_bid - peak) / entry * 100
        expired = end_ts < now_ts
        intent = conn.execute(
            """
            SELECT status, error_message FROM order_intents
            WHERE market_slug = ? AND strategy_name = ?
            ORDER BY id DESC LIMIT 1
            """,
            (slug, trade["strategy_name"]),
        ).fetchone()
        pos = {
            "trade_id": int(trade["id"]),
            "market": slug,
            "strategy": trade["strategy_name"],
            "entry": entry,
            "current_bid": current_bid,
            "current_pnl_pct": pnl,
            "holding_sec": holding,
            "peak": peak,
            "drawdown_from_peak_pct": drawdown,
            "trailing_active": bool(trade["trailing_active"]),
            "trailing_activation_price": trade["trailing_activation_price"],
            "trailing_stop_price": trade["trailing_stop_price"],
            "expired": expired,
            "last_intent_status": intent["status"] if intent else None,
        }
        positions.append(pos)
        if expired:
            warnings.append(
                {
                    "level": "WARNING",
                    "trade_id": int(trade["id"]),
                    "message": "Expired, still open — blocks new entries, recovery recommended",
                }
            )
    failed_exits = conn.execute(
        """
        SELECT COUNT(*) AS c FROM order_intents
        WHERE status = 'failed' AND idempotency_key LIKE '%:exit'
        """
    ).fetchone()
    orphan = conn.execute(
        """
        SELECT COUNT(*) AS c FROM order_intents o
        LEFT JOIN early_reversion_v2_trades t
          ON t.market_slug = o.market_slug AND t.strategy_name = o.strategy_name
        WHERE o.status IN ('pending', 'submitted')
          AND (t.id IS NULL OR t.status = 'closed')
        """
    ).fetchone()
    return {
        "positions": positions,
        "warnings": warnings,
        "failed_exit_intents": int(failed_exits["c"]) if failed_exits else 0,
        "orphan_intents": int(orphan["c"]) if orphan else 0,
    }


def build_bot_health(conn: sqlite3.Connection, *, now_ts: int) -> dict[str, Any]:
    since_1h = now_ts - 3600
    checks_1h = conn.execute(
        """
        SELECT COUNT(*) AS c FROM market_checks
        WHERE cast(strftime('%s', checked_at) AS integer) >= ?
        """,
        (since_1h,),
    ).fetchone()
    last_check = conn.execute(
        "SELECT MAX(checked_at) AS ts FROM market_checks"
    ).fetchone()
    failed = conn.execute(
        "SELECT COUNT(*) AS c FROM order_intents WHERE status = 'failed'"
    ).fetchone()
    recovery_events = conn.execute(
        """
        SELECT COUNT(*) AS c FROM er_health_events
        WHERE event_type = 'recovery_action' AND event_ts >= ?
        """,
        (since_1h,),
    ).fetchone()
    return {
        "market_checks_1h": int(checks_1h["c"]) if checks_1h else 0,
        "estimated_cycles_1h": int(checks_1h["c"]) if checks_1h else 0,
        "last_market_check_at": last_check["ts"] if last_check else None,
        "failed_intents_total": int(failed["c"]) if failed else 0,
        "recovery_actions_1h": int(recovery_events["c"]) if recovery_events else 0,
        "poll_interval_sec": POLL_INTERVAL_SEC,
        "memory_mb": None,
        "note": "Cycle/error counts inferred from market_checks and er_health_events",
    }
