"""Read-only production audit for Bidirectional Momentum V1.1 live shadow data.

Usage: python -m bot.research.bidirectional_live_audit

Does NOT modify the database or trading logic.
"""

from __future__ import annotations

import statistics
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from bot.strategy.bidirectional_momentum import EntryConfig, REGIME_EXIT_PROFILES
from bot.strategy.bidirectional_shadow import SHADOW_ENTRY_CONFIG

WINDOW_SECONDS = 300
REPLAY_OOS_BENCHMARK = {
    "pf": 2.253,
    "yes_pf": 2.20,
    "no_pf": 2.31,
    "max_cl": 5,
}

PROMOTION_CRITERIA = {
    "min_closed": 250,
    "pf_all_min": 1.50,
    "pf_last100_min": 1.30,
    "side_pf_min": 1.20,
    "max_cl_max": 10,
    "stress_b_pf_min": 1.20,
    "rolling_pf_windows": 5,
    "rolling_pf_pass_min": 4,
    "max_profit_concentration_pct": 60.0,
}

REPORT_WIDTH = 72


@dataclass
class LiveTrade:
    id: int
    market_slug: str
    window_start_ts: int | None
    side: str
    entry_price: float
    entry_ts: int
    entry_regime: str | None
    entry_confidence: float | None
    exit_price: float | None
    exit_reason: str | None
    pnl_pct: float | None
    holding_time_seconds: float | None
    max_price_seen: float | None
    btc_move_30s: float | None = None
    seconds_left: int | None = None

    @property
    def exit_ts(self) -> int | None:
        if self.holding_time_seconds is None:
            return None
        return int(self.entry_ts + self.holding_time_seconds)


def load_closed_trades(conn: sqlite3.Connection) -> list[LiveTrade]:
    rows = conn.execute(
        """
        SELECT
            t.id, t.market_slug, t.window_start_ts, t.side,
            t.entry_price, t.entry_ts, t.entry_regime, t.entry_confidence,
            t.exit_price, t.exit_reason, t.pnl_pct, t.holding_time_seconds,
            t.max_price_seen,
            o.btc_move_30s
        FROM bidirectional_shadow_trades t
        LEFT JOIN bidirectional_shadow_observations o
          ON o.market_slug = t.market_slug
         AND o.timestamp = t.entry_ts
         AND o.decision = t.side
        WHERE t.status = 'closed'
        ORDER BY t.entry_ts ASC
        """
    ).fetchall()

    trades: list[LiveTrade] = []
    for r in rows:
        ws = r["window_start_ts"]
        entry_ts = int(r["entry_ts"])
        seconds_left = None
        if ws is not None:
            seconds_left = max(0, WINDOW_SECONDS - (entry_ts - int(ws)))
        trades.append(
            LiveTrade(
                id=int(r["id"]),
                market_slug=str(r["market_slug"]),
                window_start_ts=int(ws) if ws is not None else None,
                side=str(r["side"]),
                entry_price=float(r["entry_price"]),
                entry_ts=entry_ts,
                entry_regime=r["entry_regime"],
                entry_confidence=r["entry_confidence"],
                exit_price=float(r["exit_price"]) if r["exit_price"] is not None else None,
                exit_reason=r["exit_reason"],
                pnl_pct=float(r["pnl_pct"]) if r["pnl_pct"] is not None else None,
                holding_time_seconds=float(r["holding_time_seconds"])
                if r["holding_time_seconds"] is not None
                else None,
                max_price_seen=float(r["max_price_seen"]) if r["max_price_seen"] is not None else None,
                btc_move_30s=float(r["btc_move_30s"]) if r["btc_move_30s"] is not None else None,
                seconds_left=seconds_left,
            )
        )
    return trades


def load_observation_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT decision, regime, COUNT(*) AS n
        FROM bidirectional_shadow_observations
        GROUP BY decision, regime
        """
    ).fetchall()
    by_decision: dict[str, int] = {}
    by_regime: dict[str, dict[str, int]] = {}
    total = 0
    for r in rows:
        d = r["decision"]
        reg = r["regime"] or "UNKNOWN"
        n = int(r["n"])
        total += n
        by_decision[d] = by_decision.get(d, 0) + n
        by_regime.setdefault(reg, {"observations": 0, "entries": 0})
        by_regime[reg]["observations"] += n
        if d in ("YES", "NO"):
            by_regime[reg]["entries"] += n
    return {"total": total, "by_decision": by_decision, "by_regime": by_regime}


def _pf(pnls: list[float]) -> float:
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p <= 0))
    return gp / gl if gl else (99.0 if gp > 0 else 0.0)


def _max_drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _max_consecutive_losses(pnls: list[float]) -> int:
    streak = 0
    best = 0
    for p in pnls:
        if p <= 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def compute_metrics(trades: list[LiveTrade]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}

    pnls = [t.pnl_pct or 0.0 for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    holdings = [t.holding_time_seconds or 0 for t in trades]

    return {
        "trades": len(trades),
        "wr": round(len(wins) / len(trades) * 100, 1),
        "pf": round(_pf(pnls), 3),
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "avg_pnl": round(statistics.mean(pnls), 2),
        "median_pnl": round(statistics.median(pnls), 2),
        "max_dd": round(_max_drawdown(pnls), 2),
        "max_consecutive_losses": _max_consecutive_losses(pnls),
        "expectancy": round(statistics.mean(pnls), 2),
        "avg_holding_seconds": round(statistics.mean(holdings), 1) if holdings else 0,
    }


def _quote_at_ts(
    conn: sqlite3.Connection,
    market_slug: str,
    ts: int,
) -> dict[str, float | None] | None:
    row = conn.execute(
        """
        SELECT yes_bid, yes_ask, no_bid, no_ask, timestamp
        FROM v4_shadow_observations
        WHERE market_slug = ? AND timestamp <= ?
        ORDER BY timestamp DESC LIMIT 1
        """,
        (market_slug, ts),
    ).fetchone()
    if not row:
        return None
    return {
        "yes_bid": row["yes_bid"],
        "yes_ask": row["yes_ask"],
        "no_bid": row["no_bid"],
        "no_ask": row["no_ask"],
        "timestamp": row["timestamp"],
    }


def _quote_after_delay(
    conn: sqlite3.Connection,
    market_slug: str,
    entry_ts: int,
    delay_sec: int,
) -> dict[str, float | None] | None:
    target = entry_ts + delay_sec
    row = conn.execute(
        """
        SELECT yes_bid, yes_ask, no_bid, no_ask, timestamp
        FROM v4_shadow_observations
        WHERE market_slug = ?
          AND timestamp >= ?
          AND timestamp <= ?
        ORDER BY timestamp ASC LIMIT 1
        """,
        (market_slug, target, target + 5),
    ).fetchone()
    if not row:
        return None
    return {
        "yes_bid": row["yes_bid"],
        "yes_ask": row["yes_ask"],
        "no_bid": row["no_bid"],
        "no_ask": row["no_ask"],
        "timestamp": row["timestamp"],
    }


@dataclass
class IntegrityViolation:
    trade_id: int | None
    market_slug: str
    violation_type: str
    detail: str


def audit_integrity(conn: sqlite3.Connection, trades: list[LiveTrade]) -> dict[str, Any]:
    violations: list[IntegrityViolation] = []
    cfg = SHADOW_ENTRY_CONFIG

    # One trade per market
    by_market: dict[str, list[LiveTrade]] = {}
    for t in trades:
        by_market.setdefault(t.market_slug, []).append(t)
    for slug, group in by_market.items():
        if len(group) > 1:
            violations.append(
                IntegrityViolation(
                    None,
                    slug,
                    "DUPLICATE_MARKET",
                    f"{len(group)} closed trades in same market",
                )
            )

    open_dupes = conn.execute(
        """
        SELECT market_slug, COUNT(*) AS n
        FROM bidirectional_shadow_trades
        WHERE status = 'open'
        GROUP BY market_slug HAVING n > 1
        """
    ).fetchall()
    for r in open_dupes:
        violations.append(
            IntegrityViolation(
                None,
                r["market_slug"],
                "DUPLICATE_OPEN",
                f"{r['n']} open trades",
            )
        )

    exit_reasons: dict[str, int] = {}
    for t in trades:
        exit_reasons[t.exit_reason or "UNKNOWN"] = exit_reasons.get(t.exit_reason or "UNKNOWN", 0) + 1

        if not (0 < t.entry_price < 1):
            violations.append(
                IntegrityViolation(t.id, t.market_slug, "IMPOSSIBLE_ENTRY", f"entry={t.entry_price}")
            )
        if t.exit_price is not None and not (0 < t.exit_price <= 1):
            violations.append(
                IntegrityViolation(t.id, t.market_slug, "IMPOSSIBLE_EXIT", f"exit={t.exit_price}")
            )

        if t.pnl_pct is not None and t.exit_price is not None:
            expected = ((t.exit_price - t.entry_price) / t.entry_price) * 100
            if abs(expected - t.pnl_pct) > 0.15:
                violations.append(
                    IntegrityViolation(
                        t.id,
                        t.market_slug,
                        "PNL_MISMATCH",
                        f"stored={t.pnl_pct:.2f}% calc={expected:.2f}%",
                    )
                )

        if t.holding_time_seconds is not None and t.holding_time_seconds < 0:
            violations.append(
                IntegrityViolation(t.id, t.market_slug, "NEGATIVE_HOLD", str(t.holding_time_seconds))
            )

        if t.entry_regime in cfg.skip_regimes:
            violations.append(
                IntegrityViolation(
                    t.id,
                    t.market_slug,
                    "SKIP_REGIME_ENTRY",
                    f"regime={t.entry_regime}",
                )
            )

        if t.side == "NO" and cfg.no_avoid_zone_lo <= t.entry_price < cfg.no_avoid_zone_hi:
            violations.append(
                IntegrityViolation(
                    t.id,
                    t.market_slug,
                    "NO_AVOID_ZONE",
                    f"entry={t.entry_price:.3f} in [{cfg.no_avoid_zone_lo},{cfg.no_avoid_zone_hi})",
                )
            )

        if t.exit_price is not None and t.exit_price >= 0.98:
            violations.append(
                IntegrityViolation(
                    t.id,
                    t.market_slug,
                    "SETTLEMENT_LEAKAGE",
                    f"exit={t.exit_price:.3f} near settlement",
                )
            )

        # Price source validation via v4 observations (when available)
        q_entry = _quote_at_ts(conn, t.market_slug, t.entry_ts)
        if q_entry:
            ask = q_entry["yes_ask"] if t.side == "YES" else q_entry["no_ask"]
            if ask and abs(ask - t.entry_price) > 0.03:
                violations.append(
                    IntegrityViolation(
                        t.id,
                        t.market_slug,
                        "ENTRY_NOT_ASK",
                        f"trade={t.entry_price:.3f} v4_ask={ask:.3f} Δ={abs(ask-t.entry_price):.3f}",
                    )
                )
            lag = t.entry_ts - int(q_entry["timestamp"] or t.entry_ts)
            if lag > 15:
                violations.append(
                    IntegrityViolation(
                        t.id,
                        t.market_slug,
                        "STALE_ENTRY_QUOTE",
                        f"quote lag {lag}s",
                    )
                )

        if t.exit_ts:
            q_exit = _quote_at_ts(conn, t.market_slug, t.exit_ts)
            if q_exit and t.exit_price is not None:
                bid = q_exit["yes_bid"] if t.side == "YES" else q_exit["no_bid"]
                if bid and abs(bid - t.exit_price) > 0.03:
                    violations.append(
                        IntegrityViolation(
                            t.id,
                            t.market_slug,
                            "EXIT_NOT_BID",
                            f"trade={t.exit_price:.3f} v4_bid={bid:.3f}",
                        )
                    )

    # NO avoid zone: verify no YES/NO observations led to entry in zone for NO
    zone_obs = conn.execute(
        """
        SELECT COUNT(*) AS n FROM bidirectional_shadow_observations
        WHERE decision = 'NO' AND entry_price >= ? AND entry_price < ?
        """,
        (cfg.no_avoid_zone_lo, cfg.no_avoid_zone_hi),
    ).fetchone()["n"]
    zone_entries = sum(
        1
        for t in trades
        if t.side == "NO" and cfg.no_avoid_zone_lo <= t.entry_price < cfg.no_avoid_zone_hi
    )

    return {
        "violations": violations,
        "violation_count": len(violations),
        "exit_reasons": exit_reasons,
        "markets_with_trades": len(by_market),
        "one_trade_per_market": all(len(g) == 1 for g in by_market.values()),
        "no_avoid_zone_observations": int(zone_obs or 0),
        "no_avoid_zone_entries": zone_entries,
        "pass": len(violations) == 0,
    }


def bucket_entry_price(price: float) -> str:
    if price < 0.25:
        return "<0.25"
    if price < 0.30:
        return "0.25-0.30"
    if price < 0.35:
        return "0.30-0.35"
    if price < 0.40:
        return "0.35-0.40"
    return "0.40-0.45"


def bucket_seconds_left(sec: int | None) -> str:
    if sec is None:
        return "unknown"
    if sec <= 30:
        return "15-30"
    if sec <= 60:
        return "30-60"
    if sec <= 120:
        return "60-120"
    if sec <= 180:
        return "120-180"
    return "180-250"


def bucket_btc_move(move: float | None) -> str:
    if move is None:
        return "unknown"
    a = abs(move)
    if a < 15:
        return "5-15$"
    if a < 30:
        return "15-30$"
    if a < 50:
        return "30-50$"
    if a < 80:
        return "50-80$"
    if a < 150:
        return "80-150$"
    return "150+$"


def analyze_buckets(trades: list[LiveTrade], side: str) -> dict[str, dict]:
    subset = [t for t in trades if t.side == side]
    buckets: dict[str, list[float]] = {}
    for t in subset:
        b = bucket_entry_price(t.entry_price)
        buckets.setdefault(b, []).append(t.pnl_pct or 0)
    out: dict[str, dict] = {}
    for b, pnls in sorted(buckets.items()):
        wins = sum(1 for p in pnls if p > 0)
        out[b] = {
            "n": len(pnls),
            "wr": round(wins / len(pnls) * 100, 1) if pnls else 0,
            "pf": round(_pf(pnls), 3),
            "avg_pnl": round(statistics.mean(pnls), 2) if pnls else 0,
            "total_pnl": round(sum(pnls), 2),
        }
    return out


def analyze_regimes(
    trades: list[LiveTrade],
    obs_stats: dict[str, Any],
) -> dict[str, dict]:
    regimes = ["NORMAL", "MOMENTUM", "STRONG_MOMENTUM", "NEWS_SPIKE", "REVERSAL", "CHOP"]
    out: dict[str, dict] = {}
    obs_by = obs_stats.get("by_regime", {})
    for reg in regimes:
        reg_trades = [t for t in trades if (t.entry_regime or "") == reg]
        pnls = [t.pnl_pct or 0 for t in reg_trades]
        obs_n = obs_by.get(reg, {}).get("observations", 0)
        entry_obs = obs_by.get(reg, {}).get("entries", 0)
        out[reg] = {
            "observations": obs_n,
            "entry_observations": entry_obs,
            "entry_rate_pct": round(entry_obs / obs_n * 100, 1) if obs_n else 0,
            "closed_trades": len(reg_trades),
            "pf": round(_pf(pnls), 3) if pnls else 0,
            "wr": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1) if pnls else 0,
            "avg_pnl": round(statistics.mean(pnls), 2) if pnls else 0,
            "max_dd": round(_max_drawdown(pnls), 2) if pnls else 0,
        }
    return out


def analyze_time_buckets(trades: list[LiveTrade]) -> dict[str, dict]:
    buckets: dict[str, list[float]] = {}
    for t in trades:
        b = bucket_seconds_left(t.seconds_left)
        buckets.setdefault(b, []).append(t.pnl_pct or 0)
    return {
        b: {
            "n": len(pnls),
            "wr": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1),
            "pf": round(_pf(pnls), 3),
            "avg_pnl": round(statistics.mean(pnls), 2),
        }
        for b, pnls in sorted(buckets.items())
    }


def analyze_btc_moves(trades: list[LiveTrade], side: str) -> dict[str, dict]:
    subset = [t for t in trades if t.side == side]
    buckets: dict[str, list[LiveTrade]] = {}
    for t in subset:
        b = bucket_btc_move(t.btc_move_30s)
        buckets.setdefault(b, []).append(t)
    out: dict[str, dict] = {}
    for b, group in sorted(buckets.items()):
        pnls = [t.pnl_pct or 0 for t in group]
        wins = sum(1 for p in pnls if p > 0)
        out[b] = {
            "n": len(group),
            "wr": round(wins / len(pnls) * 100, 1) if pnls else 0,
            "pf": round(_pf(pnls), 3) if pnls else 0,
            "avg_pnl": round(statistics.mean(pnls), 2) if pnls else 0,
        }
    return out


def temporal_quarters(trades: list[LiveTrade]) -> dict[str, dict]:
    if not trades:
        return {}
    n = len(trades)
    q_size = max(1, n // 4)
    labels = ["Q1", "Q2", "Q3", "Q4"]
    out: dict[str, dict] = {}
    for i, label in enumerate(labels):
        start = i * q_size
        end = n if i == 3 else (i + 1) * q_size
        chunk = trades[start:end]
        if chunk:
            out[label] = compute_metrics(chunk)
    return out


def rolling_windows(trades: list[LiveTrade], size: int = 50) -> list[dict]:
    if len(trades) < size:
        return []
    windows: list[dict] = []
    for i in range(len(trades) - size + 1):
        chunk = trades[i : i + size]
        m = compute_metrics(chunk)
        m["window_start_idx"] = i + 1
        m["window_end_idx"] = i + size
        windows.append(m)
    return windows


def stress_test(conn: sqlite3.Connection, trades: list[LiveTrade]) -> dict[str, Any]:
    scenarios: dict[str, list[float]] = {
        "A_current": [],
        "B_entry+0.01_exit-0.01": [],
        "C_entry+0.02_exit-0.02": [],
        "D_fixed_2pct_adverse": [],
    }
    delay_results: dict[str, list[float]] = {}
    delay_available = 0

    for t in trades:
        if t.exit_price is None or t.pnl_pct is None:
            continue
        ep, xp = t.entry_price, t.exit_price

        scenarios["A_current"].append(t.pnl_pct)
        scenarios["B_entry+0.01_exit-0.01"].append(
            _pnl_from_prices(min(ep + 0.01, 0.99), max(xp - 0.01, 0.01))
        )
        scenarios["C_entry+0.02_exit-0.02"].append(
            _pnl_from_prices(min(ep + 0.02, 0.99), max(xp - 0.02, 0.01))
        )
        scenarios["D_fixed_2pct_adverse"].append(t.pnl_pct - 2.0)

        for delay in (1, 3, 5):
            key = f"delay_{delay}s"
            q = _quote_after_delay(conn, t.market_slug, t.entry_ts, delay)
            if q:
                ask = q["yes_ask"] if t.side == "YES" else q["no_ask"]
                if ask and ask > 0:
                    delay_available += 1
                    delay_results.setdefault(key, []).append(
                        _pnl_from_prices(ask, xp)
                    )

    out: dict[str, Any] = {"scenarios": {}, "delay_note": ""}
    for name, pnls in scenarios.items():
        if pnls:
            out["scenarios"][name] = compute_metrics_from_pnls(pnls)

    if delay_available >= len(trades) * 0.5:
        for name, pnls in delay_results.items():
            out["scenarios"][name] = compute_metrics_from_pnls(pnls)
        out["delay_note"] = f"Delay sim available for {delay_available}/{len(trades)} trades (v4_shadow_observations)"
    else:
        out["delay_note"] = (
            f"Delay sim SKIPPED — v4 observations available for only "
            f"{delay_available}/{len(trades)} trades (<50%)"
        )

    return out


def _pnl_from_prices(entry: float, exit_p: float) -> float:
    if entry <= 0:
        return 0.0
    return ((exit_p - entry) / entry) * 100


def compute_metrics_from_pnls(pnls: list[float]) -> dict[str, Any]:
    wins = [p for p in pnls if p > 0]
    gp = sum(wins)
    gl = abs(sum(p for p in pnls if p <= 0))
    return {
        "trades": len(pnls),
        "wr": round(len(wins) / len(pnls) * 100, 1),
        "pf": round(_pf(pnls), 3),
        "avg_pnl": round(statistics.mean(pnls), 2),
        "max_dd": round(_max_drawdown(pnls), 2),
    }


def replay_vs_live_consistency() -> dict[str, Any]:
    """Static comparison of replay vs live shadow implementation (not replay metrics)."""
    cfg = SHADOW_ENTRY_CONFIG
    diffs: list[str] = []

    # Live uses bot.main quotes + v4 history enrichment; replay uses v4 tick stream
    diffs.append(
        "DATA SOURCE: Live shadow enriches BTC moves from v4_shadow_observations history "
        "at bot.main cycle tick; replay walks full v4 observation stream tick-by-tick."
    )
    diffs.append(
        "TIMING: Live entry_ts = int(time.time()) at observe cycle; replay entry_ts = observation timestamp."
    )
    diffs.append(
        "FEATURE GAPS: Live initializes btc_move_* to 0 then fills from history with age windows "
        "(5s/10s/30s); replay computes moves from contiguous observation series — may differ early-window."
    )
    diffs.append(
        "EXIT ORDER: Both check STOP_LOSS → TIME_STOP → TRAILING_STOP in same priority."
    )
    diffs.append(
        "ONE TRADE/MARKET: Live checks get_open_shadow_trade before entry; replay uses trade_completed flag."
    )

    if cfg != EntryConfig():
        diffs.append("WARNING: SHADOW_ENTRY_CONFIG differs from EntryConfig() defaults")
    else:
        diffs.append("ENTRY CONFIG: SHADOW_ENTRY_CONFIG matches EntryConfig() V1.1 defaults.")

    replay_window = (15, 180)  # historical replay default in run_replay
    live_window = (cfg.min_seconds_from_start, cfg.max_seconds_from_start)
    if replay_window != live_window:
        diffs.append(
            f"TIME WINDOW MISMATCH: replay default {replay_window} vs live {live_window} "
            "— live shadow allows entries up to 250s; replay research used 180s max."
        )

    diffs.append(
        "PF INFLATION RISK: Live PF > replay OOS may reflect: (1) smaller sample variance, "
        "(2) wider entry window 250s vs 180s, (3) live feature enrichment differences, "
        "(4) favorable recent regime mix, (5) no execution slippage in shadow."
    )

    return {
        "entry_config_match": cfg == EntryConfig(),
        "exit_profiles": {k: asdict_exit(v) for k, v in REGIME_EXIT_PROFILES.items()},
        "differences": diffs,
        "replay_benchmark": REPLAY_OOS_BENCHMARK,
    }


def asdict_exit(cfg) -> dict:
    return {
        "sl": cfg.stop_loss_pct,
        "ta": cfg.trailing_activation_pct,
        "td": cfg.trailing_distance_pct,
        "ts": cfg.time_stop_seconds,
    }


def profit_concentration(trades: list[LiveTrade]) -> dict[str, float]:
    pnls = [t.pnl_pct or 0 for t in trades]
    total = sum(p for p in pnls if p > 0)
    if total <= 0:
        return {"side": 0, "regime": 0, "max_side_pct": 0, "max_regime_pct": 0}

    by_side: dict[str, float] = {}
    by_regime: dict[str, float] = {}
    for t in trades:
        p = t.pnl_pct or 0
        if p > 0:
            by_side[t.side] = by_side.get(t.side, 0) + p
            r = t.entry_regime or "UNKNOWN"
            by_regime[r] = by_regime.get(r, 0) + p

    return {
        "max_side_pct": round(max(by_side.values()) / total * 100, 1) if by_side else 0,
        "max_regime_pct": round(max(by_regime.values()) / total * 100, 1) if by_regime else 0,
        "by_side": {k: round(v, 1) for k, v in by_side.items()},
        "by_regime": {k: round(v, 1) for k, v in by_regime.items()},
    }


def evaluate_promotion(report: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    c = PROMOTION_CRITERIA
    all_m = report["performance"]["all"]
    last100_m = report["performance"].get("last_100", {})
    yes_m = report["side_analysis"].get("YES", {})
    no_m = report["side_analysis"].get("NO", {})
    integrity = report["integrity"]
    stress_b = report["stress"]["scenarios"].get("B_entry+0.01_exit-0.01", {})
    rolling = report["temporal"].get("rolling_50", [])
    concentration = report["profit_concentration"]

    checks = {
        "closed_ge_250": all_m.get("trades", 0) >= c["min_closed"],
        "pf_all_ge_150": all_m.get("pf", 0) >= c["pf_all_min"],
        "pf_last100_ge_130": last100_m.get("pf", 0) >= c["pf_last100_min"],
        "yes_pf_ge_120": yes_m.get("pf", 0) >= c["side_pf_min"],
        "no_pf_ge_120": no_m.get("pf", 0) >= c["side_pf_min"],
        "max_cl_le_10": all_m.get("max_consecutive_losses", 99) <= c["max_cl_max"],
        "no_integrity_violations": integrity.get("pass", False),
        "stress_b_pf_ge_120": stress_b.get("pf", 0) >= c["stress_b_pf_min"],
    }

    recent_windows = rolling[-c["rolling_pf_windows"] :] if rolling else []
    pf_pass = sum(1 for w in recent_windows if w.get("pf", 0) > 1.0)
    checks["rolling_4_of_5_pf_gt_1"] = pf_pass >= c["rolling_pf_pass_min"]

    checks["no_concentration_gt_60"] = (
        concentration.get("max_side_pct", 100) <= c["max_profit_concentration_pct"]
        and concentration.get("max_regime_pct", 100) <= c["max_profit_concentration_pct"]
    )

    labels = {
        "closed_ge_250": f"closed trades {all_m.get('trades',0)} >= {c['min_closed']}",
        "pf_all_ge_150": f"PF all {all_m.get('pf',0):.3f} >= {c['pf_all_min']}",
        "pf_last100_ge_130": f"PF last100 {last100_m.get('pf',0):.3f} >= {c['pf_last100_min']}",
        "yes_pf_ge_120": f"YES PF {yes_m.get('pf',0):.3f} >= {c['side_pf_min']}",
        "no_pf_ge_120": f"NO PF {no_m.get('pf',0):.3f} >= {c['side_pf_min']}",
        "max_cl_le_10": f"MaxCL {all_m.get('max_consecutive_losses',0)} <= {c['max_cl_max']}",
        "no_integrity_violations": f"integrity violations {integrity.get('violation_count',0)}",
        "stress_b_pf_ge_120": f"stress B PF {stress_b.get('pf',0):.3f} >= {c['stress_b_pf_min']}",
        "rolling_4_of_5_pf_gt_1": f"rolling PF>1 in {pf_pass}/{len(recent_windows)} recent windows",
        "no_concentration_gt_60": (
            f"max side/regime profit share {concentration.get('max_side_pct',0)}%/"
            f"{concentration.get('max_regime_pct',0)}%"
        ),
    }

    for key, ok in checks.items():
        if not ok:
            reasons.append(f"FAIL: {labels[key]}")

    all_pass = all(checks.values())
    return {
        "checks": checks,
        "reasons": reasons,
        "verdict": "READY_FOR_PAPER_MICRO" if all_pass else "CONTINUE_SHADOW",
    }


def run_live_audit(conn: sqlite3.Connection) -> dict[str, Any]:
    trades = load_closed_trades(conn)
    obs_stats = load_observation_stats(conn)
    integrity = audit_integrity(conn, trades)

    performance = {
        "all": compute_metrics(trades),
        "last_50": compute_metrics(trades[-50:]) if len(trades) >= 50 else compute_metrics(trades),
        "last_100": compute_metrics(trades[-100:]) if len(trades) >= 100 else compute_metrics(trades),
        "last_200": compute_metrics(trades[-200:]) if len(trades) >= 200 else compute_metrics(trades),
    }

    side_analysis = {
        side: compute_metrics([t for t in trades if t.side == side])
        for side in ("YES", "NO")
    }

    return {
        "trade_count": len(trades),
        "observations": obs_stats,
        "integrity": integrity,
        "performance": performance,
        "side_analysis": side_analysis,
        "entry_buckets": {
            "YES": analyze_buckets(trades, "YES"),
            "NO": analyze_buckets(trades, "NO"),
        },
        "regime_analysis": analyze_regimes(trades, obs_stats),
        "time_analysis": analyze_time_buckets(trades),
        "btc_move_analysis": {
            "YES": analyze_btc_moves(trades, "YES"),
            "NO": analyze_btc_moves(trades, "NO"),
        },
        "temporal": {
            "quarters": temporal_quarters(trades),
            "rolling_50": rolling_windows(trades, 50),
        },
        "stress": stress_test(conn, trades),
        "replay_vs_live": replay_vs_live_consistency(),
        "profit_concentration": profit_concentration(trades),
        "promotion": {},  # filled after report assembled
    }


def render_report(report: dict[str, Any]) -> str:
    report["promotion"] = evaluate_promotion(report)
    lines: list[str] = []
    report_width = REPORT_WIDTH

    def h(title: str) -> None:
        lines.append("")
        lines.append("=" * report_width)
        lines.append(title)
        lines.append("=" * report_width)

    def row(label: str, metrics: dict) -> None:
        if not metrics or metrics.get("trades", 0) == 0:
            lines.append(f"  {label}: no trades")
            return
        lines.append(
            f"  {label}: N={metrics['trades']} WR={metrics['wr']}% PF={metrics['pf']} "
            f"avg={metrics['avg_pnl']}% med={metrics.get('median_pnl', '?')}% "
            f"DD={metrics['max_dd']}% MaxCL={metrics['max_consecutive_losses']} "
            f"E={metrics['expectancy']}% hold={metrics.get('avg_holding_seconds', 0)}s"
        )

    h("BIDIRECTIONAL MOMENTUM V1.1 — LIVE SHADOW AUDIT")
    lines.append(f"Closed trades: {report['trade_count']}")
    lines.append(f"Observations: {report['observations']['total']}")

    h("1. DATA INTEGRITY")
    ig = report["integrity"]
    lines.append(f"  Verdict: {'PASS' if ig['pass'] else 'FAIL'} ({ig['violation_count']} violations)")
    lines.append(f"  One trade/market: {ig['one_trade_per_market']}")
    lines.append(f"  Exit reasons: {ig['exit_reasons']}")
    lines.append(f"  NO avoid zone entries: {ig['no_avoid_zone_entries']} (must be 0)")
    lines.append(f"  NO avoid zone NO observations: {ig['no_avoid_zone_observations']}")
    if ig["violations"]:
        lines.append("  Violations:")
        lines.append(f"  {'ID':>5} {'Market':<28} {'Type':<22} Detail")
        for violation in ig["violations"][:30]:
            tid = violation.trade_id if violation.trade_id else "-"
            slug = violation.market_slug[:28]
            lines.append(
                f"  {str(tid):>5} {slug:<28} {violation.violation_type:<22} {violation.detail}"
            )
        if len(ig["violations"]) > 30:
            lines.append(f"  ... +{len(ig['violations']) - 30} more")

    h("2. LIVE SHADOW PERFORMANCE")
    for label in ("all", "last_50", "last_100", "last_200"):
        row(label.upper(), report["performance"].get(label, {}))

    h("3. SIDE ANALYSIS")
    for side in ("YES", "NO"):
        row(side, report["side_analysis"].get(side, {}))

    h("4. ENTRY PRICE BUCKETS")
    for side in ("YES", "NO"):
        lines.append(f"  --- {side} ---")
        for bucket, bucket_metrics in report["entry_buckets"][side].items():
            lines.append(
                f"    {bucket:<10} N={bucket_metrics['n']:>3} WR={bucket_metrics['wr']:>5.1f}% "
                f"PF={bucket_metrics['pf']:>5.3f} avg={bucket_metrics['avg_pnl']:>6.2f}% "
                f"total={bucket_metrics['total_pnl']:>7.1f}%"
            )

    h("5. REGIME ANALYSIS")
    lines.append(
        f"  {'Regime':<18} {'Obs':>6} {'Entries':>8} {'Rate%':>6} "
        f"{'Closed':>7} {'PF':>6} {'WR':>6} {'avgPnL':>7}"
    )
    for regime, regime_metrics in report["regime_analysis"].items():
        lines.append(
            f"  {regime:<18} {regime_metrics['observations']:>6} "
            f"{regime_metrics['entry_observations']:>8} "
            f"{regime_metrics['entry_rate_pct']:>5.1f}% {regime_metrics['closed_trades']:>7} "
            f"{regime_metrics['pf']:>6.3f} {regime_metrics['wr']:>5.1f}% "
            f"{regime_metrics['avg_pnl']:>6.2f}%"
        )
    chop_rev = report["regime_analysis"].get("CHOP", {}).get("closed_trades", 0)
    chop_rev += report["regime_analysis"].get("REVERSAL", {}).get("closed_trades", 0)
    lines.append(f"  CHOP+REVERSAL closed trades (must be 0): {chop_rev}")

    h("6. TIME ANALYSIS (seconds_left at entry)")
    for bucket, bucket_metrics in report["time_analysis"].items():
        lines.append(
            f"  {bucket:<10} N={bucket_metrics['n']:>3} WR={bucket_metrics['wr']:>5.1f}% "
            f"PF={bucket_metrics['pf']:>5.3f} avg={bucket_metrics['avg_pnl']:>6.2f}%"
        )

    h("7. BTC MOVE MAGNITUDE (abs btc_move_30s at entry)")
    for side in ("YES", "NO"):
        lines.append(f"  --- {side} ---")
        for bucket, bucket_metrics in report["btc_move_analysis"][side].items():
            lines.append(
                f"    {bucket:<10} N={bucket_metrics['n']:>3} WR={bucket_metrics['wr']:>5.1f}% "
                f"PF={bucket_metrics['pf']:>5.3f} avg={bucket_metrics['avg_pnl']:>6.2f}%"
            )

    h("8. TEMPORAL STABILITY")
    lines.append("  Quarters:")
    for quarter, quarter_metrics in report["temporal"]["quarters"].items():
        row(f"    {quarter}", quarter_metrics)
    rolling_windows = report["temporal"]["rolling_50"]
    if rolling_windows:
        lines.append(f"  Rolling windows (50): {len(rolling_windows)} windows")
        for window in rolling_windows[-5:]:
            lines.append(
                f"    [{window['window_start_idx']}-{window['window_end_idx']}] "
                f"PF={window['pf']} WR={window['wr']}% MaxCL={window['max_consecutive_losses']}"
            )

    h("9. REPLAY VS LIVE SHADOW")
    rl = report["replay_vs_live"]
    lines.append(
        f"  Replay OOS benchmark: PF={rl['replay_benchmark']['pf']} "
        f"YES={rl['replay_benchmark']['yes_pf']} NO={rl['replay_benchmark']['no_pf']}"
    )
    all_m = report["performance"]["all"]
    lines.append(
        f"  Live shadow:          PF={all_m.get('pf', 0)} "
        f"YES={report['side_analysis'].get('YES', {}).get('pf', 0)} "
        f"NO={report['side_analysis'].get('NO', {}).get('pf', 0)} "
        f"MaxCL={all_m.get('max_consecutive_losses', 0)}"
    )
    for diff in rl["differences"]:
        lines.append(f"  • {diff}")

    h("10. COST / STRESS TEST")
    lines.append(f"  {report['stress']['delay_note']}")
    for scenario, scenario_metrics in report["stress"]["scenarios"].items():
        lines.append(
            f"  {scenario:<28} PF={scenario_metrics['pf']:>5.3f} WR={scenario_metrics['wr']:>5.1f}% "
            f"avg={scenario_metrics['avg_pnl']:>6.2f}% DD={scenario_metrics['max_dd']:>6.1f}%"
        )

    h("11. PROMOTION GATE")
    promo = report["promotion"]
    lines.append(f"  VERDICT: {promo['verdict']}")
    for check_key, passed in promo["checks"].items():
        lines.append(f"    {'PASS' if passed else 'FAIL'}: {check_key}")
    if promo["reasons"]:
        lines.append("  Reasons:")
        for reason in promo["reasons"]:
            lines.append(f"    • {reason}")

    h("12. SUMMARY VERDICTS")
    lines.append(f"  Integrity:    {'PASS' if ig['pass'] else 'FAIL'}")
    pf_stable = sum(
        1
        for quarter_metrics in report["temporal"]["quarters"].values()
        if quarter_metrics.get("pf", 0) >= 1.2
    )
    lines.append(
        f"  Performance:  PF={all_m.get('pf', 0):.3f} "
        f"({'stable' if pf_stable >= 3 else 'concentrated'})"
    )
    stress_b = report["stress"]["scenarios"].get("B_entry+0.01_exit-0.01", {})
    lines.append(
        f"  Robustness:   stress-B PF={stress_b.get('pf', 0):.3f} "
        f"MaxCL={all_m.get('max_consecutive_losses', 0)}"
    )
    lines.append("  Replay/Live:  live PF exceeds replay OOS — see section 9 for causes")
    lines.append(f"  Promotion:    {promo['verdict']}")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    from bot.database import connect, init_db
    from bot.strategy.bidirectional_shadow import ensure_tables

    init_db()
    with connect() as conn:
        ensure_tables(conn)
        report = run_live_audit(conn)
        print(render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
