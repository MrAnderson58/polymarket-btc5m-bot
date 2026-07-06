"""V1.3 execution-aware research engine."""

from __future__ import annotations

import sqlite3
from typing import Any

from bot.research.bidirectional_live_audit import dedupe_first_trade_per_market, load_closed_trades
from bot.research.bidirectional_v12_counterfactual import filter_trades
from bot.research.bidirectional_v13_execution.config import BASE_CANDIDATE_SPEC_NAME
from bot.research.bidirectional_v13_execution.entry_models import ENTRY_MODELS, PASSIVE_MODELS
from bot.research.bidirectional_v13_execution.exit_models import EXIT_MODELS, apply_exit_model
from bot.research.bidirectional_v13_execution.metrics import (
    ExecutableTrade,
    determine_verdict,
    evaluate_executable_trades,
    passes_promotion_gates,
    walk_forward_oos,
)
from bot.research.bidirectional_v13_execution.quote_path import load_quote_paths
from bot.research.bidirectional_v13_execution.side_filters import ALL_SIDE_FILTERS, apply_side_filter
from bot.strategy.bidirectional_v12_config import CandidateSpec


def _base_spec() -> CandidateSpec:
    from bot.research.bidirectional_v12_counterfactual import generate_candidates
    for spec in generate_candidates():
        if spec.name == BASE_CANDIDATE_SPEC_NAME:
            return spec
    return CandidateSpec(name=BASE_CANDIDATE_SPEC_NAME, yes_min_move=15.0, no_min_move=5.0)


def _simulate_entry_exit(
    conn: sqlite3.Connection,
    candidates: list,
    paths: dict,
    entry_model: str,
    exit_model: str,
) -> tuple[list[ExecutableTrade], int, int]:
    entry_fn = ENTRY_MODELS[entry_model]
    filled: list[ExecutableTrade] = []
    seen_markets: set[str] = set()
    signals = len(candidates)
    fill_count = 0

    for trade in candidates:
        if trade.market_slug in seen_markets:
            continue
        ctx = paths.get(trade.id)
        if ctx is None:
            continue
        entry_fill = entry_fn(ctx, trade)
        if not entry_fill.filled or entry_fill.entry_price is None:
            continue
        fill_count += 1
        seen_markets.add(trade.market_slug)
        exit_result = apply_exit_model(conn, trade, entry_fill.entry_price, exit_model)
        if exit_result.pnl_pct is None or exit_result.exit_price is None:
            continue
        filled.append(ExecutableTrade(
            trade=trade,
            entry_price=entry_fill.entry_price,
            exit_price=exit_result.exit_price,
            pnl_pct=exit_result.pnl_pct,
            entry_model=entry_model,
            exit_model=exit_model,
            fill_ts=entry_fill.fill_ts,
        ))
    return filled, signals, fill_count


def run_v13_execution_research(conn: sqlite3.Connection) -> dict[str, Any]:
    all_trades = load_closed_trades(conn)
    deduped, dedup_stats = dedupe_first_trade_per_market(all_trades)
    base_spec = _base_spec()
    candidates = filter_trades(deduped, base_spec)
    paths = load_quote_paths(conn, candidates)

    coverage_ok = sum(1 for p in paths.values() if p.coverage_ok)
    coverage = {
        "total_candidates": len(candidates),
        "quote_path_coverage": coverage_ok,
        "quote_path_coverage_pct": round(100 * coverage_ok / len(candidates), 1) if candidates else 0,
        "dedup": dedup_stats,
        "base_spec": base_spec.name,
    }

    if not candidates:
        return {
            "coverage": coverage,
            "entry_models": [],
            "exit_models": [],
            "side_filters": [],
            "best_entry": None,
            "best_exit": None,
            "best_combo": {},
            "combo_gates": {},
            "verdict": "COLLECT_MORE_DATA",
            "quote_paths_sample": [],
        }

    default_exit = "immediate_bid"
    entry_results: list[dict[str, Any]] = []
    for name in ENTRY_MODELS:
        filled, signals, fills = _simulate_entry_exit(conn, candidates, paths, name, default_exit)
        m = evaluate_executable_trades(filled, signals=signals, fills=fills, entry_model=name, exit_model=default_exit)
        m["walk_forward"] = walk_forward_oos(filled)
        m["gates"] = passes_promotion_gates(m, is_passive=name in PASSIVE_MODELS)
        m["gates"]["oos_pf"] = (m["walk_forward"].get("oos_pf") or 0) >= 1.20
        entry_results.append(m)

    default_entry = "A_immediate_taker"
    exit_results: list[dict[str, Any]] = []
    for name in EXIT_MODELS:
        filled, signals, fills = _simulate_entry_exit(conn, candidates, paths, default_entry, name)
        m = evaluate_executable_trades(filled, signals=signals, fills=fills, entry_model=default_entry, exit_model=name)
        m["walk_forward"] = walk_forward_oos(filled)
        exit_results.append(m)

    side_results: list[dict[str, Any]] = []
    for filt in ALL_SIDE_FILTERS:
        filtered = apply_side_filter(candidates, filt)
        fpaths = {t.id: paths[t.id] for t in filtered if t.id in paths}
        filled, signals, fills = _simulate_entry_exit(
            conn, filtered, fpaths, default_entry, default_exit,
        )
        m = evaluate_executable_trades(
            filled, signals=signals, fills=fills,
            entry_model=default_entry, exit_model=default_exit,
        )
        m["filter"] = filt.name
        m["side"] = filt.side
        side_results.append(m)

    entry_ranked = sorted(entry_results, key=lambda x: (-(x.get("pf") or 0), -(x.get("fill_rate") or 0)))
    exit_ranked = sorted(exit_results, key=lambda x: -(x.get("pf") or 0))

    best_entry = entry_ranked[0] if entry_ranked and entry_ranked[0].get("closed_trades") else None
    best_exit = exit_ranked[0] if exit_ranked and exit_ranked[0].get("closed_trades") else None

    combo_entry = (best_entry or {}).get("entry_model") or default_entry
    combo_exit = (best_exit or {}).get("exit_model") or default_exit
    combo_filled, combo_signals, combo_fills = _simulate_entry_exit(
        conn, candidates, paths, combo_entry, combo_exit,
    )
    combo_metrics = evaluate_executable_trades(
        combo_filled, signals=combo_signals, fills=combo_fills,
        entry_model=combo_entry, exit_model=combo_exit,
    )
    combo_wf = walk_forward_oos(combo_filled)
    combo_metrics["walk_forward"] = combo_wf
    is_passive = combo_entry in PASSIVE_MODELS
    combo_gates = passes_promotion_gates(combo_metrics, is_passive=is_passive)
    combo_gates["oos_pf"] = (combo_wf.get("oos_pf") or 0) >= 1.20

    verdict = determine_verdict(combo_metrics, combo_wf, combo_gates)

    return {
        "coverage": coverage,
        "entry_models": entry_results,
        "exit_models": exit_results,
        "side_filters": side_results,
        "best_entry": best_entry,
        "best_exit": best_exit,
        "best_combo": combo_metrics,
        "combo_gates": combo_gates,
        "verdict": verdict,
        "quote_paths_sample": [
            {
                "trade_id": p.trade_id,
                "market_slug": p.market_slug,
                "signal_ask": p.signal_ask,
                "spread": p.spread,
                "coverage_ok": p.coverage_ok,
                "ask_reavailable": p.signal_ask_reavailable,
            }
            for p in list(paths.values())[:5]
        ],
    }
