"""15m bidirectional research engine — walk-forward, no joint entry+exit optimization."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.research.mtf.research_15m.config import (
    MIN_MARKETS,
    MIN_TRADES_FAMILY,
    MIN_TRADES_VERDICT,
    WF_TRAIN_RATIO,
)
from bot.research.mtf.research_15m.exits import EXIT_MODES, simulate_exit, try_entry
from bot.research.mtf.research_15m.families import FAMILIES, calibrate_thresholds
from bot.research.mtf.research_15m.metrics import pf, summarize_trades, temporal_halves_stable
from bot.research.mtf.research_15m.models import FamilyResult, Obs15m, SimTrade
from bot.research.mtf.research_15m.observations import list_markets_chronological, load_15m_market_paths
from bot.research.mtf.snapshots import ensure_tables


def _simulate_family_on_paths(
    family_name: str,
    paths: dict[str, list[Obs15m]],
    market_slugs: list[str],
    thresholds: dict[str, float],
    exit_mode: str,
) -> list[SimTrade]:
    fn = FAMILIES[family_name]
    trades: list[SimTrade] = []
    for slug in market_slugs:
        path = paths.get(slug)
        if not path:
            continue
        entered = False
        for i, obs in enumerate(path):
            if entered:
                break
            side = fn(obs, path, i, thresholds)
            if side is None:
                continue
            base = try_entry(path, i, side)
            if base is None:
                continue
            base.family = family_name
            trade = simulate_exit(base, path, i, exit_mode)
            trades.append(trade)
            entered = True
    return trades


def _pick_exit_on_train(train_trades_by_exit: dict[str, list[SimTrade]]) -> str:
    best_mode = "settlement"
    best_pf = 0.0
    for mode, trades in train_trades_by_exit.items():
        pnls = [t.pnl_pct for t in trades if t.pnl_pct is not None]
        if len(pnls) < MIN_TRADES_FAMILY:
            continue
        p = pf(pnls)
        if p > best_pf:
            best_pf = p
            best_mode = mode
    return best_mode


def run_15m_research(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_tables(conn)
    paths = load_15m_market_paths(conn)
    markets = list_markets_chronological(paths)

    data_audit = {
        "markets": len(markets),
        "observations": sum(len(p) for p in paths.values()),
        "snapshot_rows": conn.execute(
            "SELECT COUNT(*) FROM multi_timeframe_snapshots WHERE market_15m_slug IS NOT NULL"
        ).fetchone()[0],
    }

    if len(markets) < MIN_MARKETS:
        return {
            "status": "insufficient_data",
            "data_audit": data_audit,
            "verdict": "COLLECT_MORE_DATA",
        }

    split = max(1, int(len(markets) * WF_TRAIN_RATIO))
    train_markets = markets[:split]
    test_markets = markets[split:]

    train_obs = [o for s in train_markets for o in paths[s]]
    thresholds = calibrate_thresholds(train_obs)

    family_results: list[FamilyResult] = []
    yes_no_asymmetry: dict[str, dict[str, float]] = {}

    for family_name in FAMILIES:
        train_by_exit = {
            mode: _simulate_family_on_paths(family_name, paths, train_markets, thresholds, mode)
            for mode in EXIT_MODES
        }
        chosen_exit = _pick_exit_on_train(train_by_exit)
        train_trades = train_by_exit[chosen_exit]
        test_trades = _simulate_family_on_paths(
            family_name, paths, test_markets, thresholds, chosen_exit,
        )

        for side in ("YES", "NO"):
            side_train = [t for t in train_trades if t.side == side]
            side_test = [t for t in test_trades if t.side == side]
            if len(side_train) < 5:
                continue
            summary = summarize_trades(side_train)
            fr = FamilyResult(
                family=family_name,
                side=side,
                exit_mode=chosen_exit,
                n=summary.get("n", 0),
                pf=summary.get("pf", 0),
                wr=summary.get("wr", 0),
                avg_pnl=summary.get("avg_pnl", 0),
                max_dd=summary.get("max_dd", 0),
                max_cl=summary.get("max_cl", 0),
                stress_pp_01=summary.get("stress_pp_01", {}),
                bootstrap_p_pf_gt1=summary.get("bootstrap_p_pf_gt1"),
            )
            test_pnls = [t.pnl_pct for t in side_test if t.pnl_pct is not None]
            fr.oos_pf = round(pf(test_pnls), 3) if test_pnls else None
            train_pnls = [t.pnl_pct for t in side_train if t.pnl_pct is not None]
            fr.temporal_stable = temporal_halves_stable(train_pnls)
            family_results.append(fr)

        yes_pf = pf([t.pnl_pct for t in train_trades if t.side == "YES" and t.pnl_pct is not None])
        no_pf = pf([t.pnl_pct for t in train_trades if t.side == "NO" and t.pnl_pct is not None])
        yes_no_asymmetry[family_name] = {"yes_pf": yes_pf, "no_pf": no_pf}

    verdict = _determine_verdict(family_results, data_audit, len(test_markets))

    return {
        "status": "ok",
        "data_audit": data_audit,
        "train_markets": len(train_markets),
        "test_markets": len(test_markets),
        "thresholds": thresholds,
        "family_results": [r.to_dict() for r in family_results],
        "yes_no_asymmetry": yes_no_asymmetry,
        "verdict": verdict,
    }


def _determine_verdict(
    results: list[FamilyResult],
    audit: dict[str, Any],
    test_n: int,
) -> str:
    if audit["markets"] < MIN_MARKETS:
        return "COLLECT_MORE_DATA"

    strong = [
        r for r in results
        if r.n >= MIN_TRADES_FAMILY
        and r.pf >= 1.25
        and (r.oos_pf or 0) >= 1.1
        and (r.bootstrap_p_pf_gt1 or 0) >= 0.6
        and r.temporal_stable
    ]
    total_trades = sum(r.n for r in results)

    if not strong:
        if total_trades < MIN_TRADES_VERDICT:
            return "COLLECT_MORE_DATA"
        moderate = [r for r in results if r.n >= MIN_TRADES_FAMILY and r.pf >= 1.1]
        return "CONTINUE_RESEARCH" if moderate else "NO_EDGE"

    if test_n >= 5 and len(strong) >= 2 and total_trades >= MIN_TRADES_VERDICT:
        return "READY_FOR_15M_SHADOW"
    return "CONTINUE_RESEARCH"
