"""Generic HTF research engine — walk-forward, separate entry/exit selection."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.research.mtf.htf_research.framework import (
    FamilyResult,
    HtfResearchSpec,
    concentration_analysis,
    coverage_audit,
    list_markets_chronological,
    load_market_paths,
)
from bot.research.mtf.htf_research.metrics import pf, regime_slices, summarize_trades, temporal_halves_stable
from bot.research.mtf.snapshots import ensure_tables


def _simulate_family(
    spec: HtfResearchSpec,
    family_name: str,
    paths: dict,
    market_slugs: list[str],
    thresholds: dict[str, float],
    exit_mode: str,
) -> list:
    fn = spec.families[family_name]
    trades = []
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
            base = spec.try_entry(path, i, side)
            if base is None:
                continue
            base.family = family_name
            trades.append(spec.simulate_exit(base, path, i, exit_mode))
            entered = True
    return trades


def _pick_exit(spec: HtfResearchSpec, by_exit: dict[str, list]) -> str:
    best, best_pf = "settlement", 0.0
    for mode, trades in by_exit.items():
        pnls = [t.pnl_pct for t in trades if t.pnl_pct is not None]
        if len(pnls) < spec.min_trades_family:
            continue
        p = pf(pnls)
        if p > best_pf:
            best_pf, best = p, mode
    return best


def run_htf_research(conn: sqlite3.Connection, spec: HtfResearchSpec) -> dict[str, Any]:
    ensure_tables(conn)
    paths = load_market_paths(conn, spec)
    markets = list_markets_chronological(paths)
    audit = coverage_audit(conn, spec, paths)

    if len(markets) < spec.min_markets:
        return {
            "status": "insufficient_data",
            "data_audit": audit,
            "verdict": "COLLECT_MORE_DATA",
        }

    split = max(1, int(len(markets) * spec.wf_train_ratio))
    train_m, test_m = markets[:split], markets[split:]
    train_obs = [o for s in train_m for o in paths[s]]
    thresholds = spec.calibrate(train_obs)

    family_results: list[FamilyResult] = []
    yes_no_asymmetry: dict[str, dict[str, float]] = {}
    all_train_trades: list = []

    for fname in spec.families:
        train_by_exit = {
            m: _simulate_family(spec, fname, paths, train_m, thresholds, m)
            for m in spec.exit_modes
        }
        chosen = _pick_exit(spec, train_by_exit)
        train_trades = train_by_exit[chosen]
        test_trades = _simulate_family(spec, fname, paths, test_m, thresholds, chosen)
        all_train_trades.extend(train_trades)

        for side in ("YES", "NO"):
            st = [t for t in train_trades if t.side == side]
            if len(st) < 5:
                continue
            summary = summarize_trades(st)
            fr = FamilyResult(
                family=fname, side=side, exit_mode=chosen,
                n=summary.get("n", 0), pf=summary.get("pf", 0),
                wr=summary.get("wr", 0), avg_pnl=summary.get("avg_pnl", 0),
                max_dd=summary.get("max_dd", 0), max_cl=summary.get("max_cl", 0),
                stress_pp_01=summary.get("stress_pp_01", {}),
                bootstrap_p_pf_gt1=summary.get("bootstrap_p_pf_gt1"),
            )
            tp = [t.pnl_pct for t in test_trades if t.side == side and t.pnl_pct is not None]
            fr.oos_pf = round(pf(tp), 3) if tp else None
            tr_pnls = [t.pnl_pct for t in st if t.pnl_pct is not None]
            fr.temporal_stable = temporal_halves_stable(tr_pnls)
            fr.regime_slice = {
                "temporal_thirds": summary.get("temporal_thirds", {}),
                "regimes": regime_slices(st, spec.regime_fn) if spec.regime_fn else {},
            }
            family_results.append(fr)

        yes_no_asymmetry[fname] = {
            "yes_pf": pf([t.pnl_pct for t in train_trades if t.side == "YES" and t.pnl_pct is not None]),
            "no_pf": pf([t.pnl_pct for t in train_trades if t.side == "NO" and t.pnl_pct is not None]),
        }

    verdict = _verdict(spec, family_results, audit, len(test_m))
    return {
        "status": "ok",
        "data_audit": audit,
        "train_markets": len(train_m),
        "test_markets": len(test_m),
        "thresholds": thresholds,
        "family_results": [r.to_dict() for r in family_results],
        "yes_no_asymmetry": yes_no_asymmetry,
        "concentration": concentration_analysis(all_train_trades),
        "verdict": verdict,
    }


def _verdict(spec: HtfResearchSpec, results: list[FamilyResult], audit: dict, test_n: int) -> str:
    if audit["markets"] < spec.min_markets:
        return "COLLECT_MORE_DATA"
    strong = [
        r for r in results
        if r.n >= spec.min_trades_family
        and r.pf >= spec.pf_strong
        and (r.oos_pf or 0) >= spec.oos_min
        and (r.bootstrap_p_pf_gt1 or 0) >= spec.bootstrap_min
        and r.temporal_stable
    ]
    total = sum(r.n for r in results)
    if not strong:
        if total < spec.min_trades_verdict:
            return "COLLECT_MORE_DATA"
        mod = [r for r in results if r.n >= spec.min_trades_family and r.pf >= 1.1]
        return "CONTINUE_RESEARCH" if mod else "NO_EDGE"
    if test_n >= 5 and len(strong) >= 2 and total >= spec.min_trades_verdict:
        return spec.ready_verdict
    return "CONTINUE_RESEARCH"
