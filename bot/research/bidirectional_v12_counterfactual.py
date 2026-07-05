"""Bidirectional Momentum V1.2 counterfactual research (READ-ONLY).

Post-hoc filter simulation on corrected V1.1 shadow trades.
Does NOT modify execution, V1.1 shadow, or trading logic.

Usage:
  python -m bot.research.bidirectional_v12_counterfactual
  python -m bot.research.bidirectional_v12_counterfactual --top 15
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import statistics
from dataclasses import dataclass
from typing import Any

from bot.strategy.bidirectional_v12_config import CandidateSpec, passes_v12_filters

from bot.research.bidirectional_live_audit import (
    LiveTrade,
    WINDOW_SECONDS,
    _max_consecutive_losses,
    _max_drawdown,
    _pf,
    _pnl_from_prices,
    analyze_buckets,
    dedupe_first_trade_per_market,
    load_closed_trades,
    profit_concentration,
    rolling_windows,
)
from bot.research.bidirectional_quote_alignment import (
    TOLERANCE,
    quote_bidi_observation_at_entry,
    quote_market_check_at_or_before,
    quote_market_check_nearest,
    quote_v4_at_or_before,
    quote_v4_nearest,
)

REPORT_WIDTH = 72
BOOTSTRAP_SAMPLES = 1000
BOOTSTRAP_MIN_N = 30
WF_TRAIN_RATIO = 0.70

V12_GATES = {
    "min_n": 150,
    "pf_min": 1.50,
    "yes_pf_min": 1.20,
    "no_pf_min": 1.20,
    "stress_b_min": 1.10,
    "stress_b_target": 1.20,
    "fixed_2pct_min": 1.20,
    "max_cl_max": 10,
    "rolling_pass_min": 4,
    "rolling_windows": 5,
    "max_concentration_pct": 60.0,
    "bootstrap_pp_min": 0.85,
}


def _seconds_from_start(trade: LiveTrade) -> int | None:
    if trade.window_start_ts is None:
        return None
    return trade.entry_ts - int(trade.window_start_ts)


def _describe_spec(spec: CandidateSpec) -> str:
    parts = [f"time={spec.sfs_min}-{spec.sfs_max}"]
    if spec.exclude_sfs_lo is not None:
        parts.append(f"excl_sfs={spec.exclude_sfs_lo}-{spec.exclude_sfs_hi}")
    if spec.yes_exclude_035_040:
        parts.append("excl_YES_0.35-0.40")
    if spec.yes_exclude_below_025:
        parts.append("excl_YES<0.25")
    if spec.yes_min_ask is not None:
        parts.append(f"YES_min_ask={spec.yes_min_ask}")
    if spec.yes_allowed_lo is not None:
        parts.append(f"YES_only_{spec.yes_allowed_lo}-{spec.yes_allowed_hi}")
    if spec.yes_min_move != 5.0 or spec.no_min_move != 5.0:
        parts.append(f"move YES>={spec.yes_min_move} NO>={spec.no_min_move}")
    if spec.exclude_yes_move_lo is not None:
        parts.append(f"excl_YES_move_{spec.exclude_yes_move_lo}-{spec.exclude_yes_move_hi}")
    if spec.allowed_regimes:
        parts.append(f"regimes={','.join(spec.allowed_regimes)}")
    return " | ".join(parts)


def trade_passes_spec(trade: LiveTrade, spec: CandidateSpec) -> bool:
    sfs = _seconds_from_start(trade)
    if sfs is None:
        return False
    return passes_v12_filters(
        side=trade.side,
        entry_price=trade.entry_price,
        btc_move_30s=trade.btc_move_30s,
        seconds_from_start=sfs,
        regime=trade.entry_regime or "NORMAL",
        spec=spec,
    )


def filter_trades(trades: list[LiveTrade], spec: CandidateSpec) -> list[LiveTrade]:
    return [t for t in trades if trade_passes_spec(t, spec)]


def _side_pf(trades: list[LiveTrade], side: str) -> float:
    pnls = [t.pnl_pct or 0 for t in trades if t.side == side]
    return round(_pf(pnls), 3) if pnls else 0.0


def _stress_pnls(trades: list[LiveTrade]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {
        "B_entry+0.01_exit-0.01": [],
        "D_fixed_2pct_adverse": [],
    }
    for t in trades:
        if t.exit_price is None or t.pnl_pct is None:
            continue
        ep, xp = t.entry_price, t.exit_price
        out["B_entry+0.01_exit-0.01"].append(
            _pnl_from_prices(min(ep + 0.01, 0.99), max(xp - 0.01, 0.01))
        )
        out["D_fixed_2pct_adverse"].append(t.pnl_pct - 2.0)
    return out


def _max_bucket_concentration(trades: list[LiveTrade]) -> float:
    pnls = [t.pnl_pct or 0 for t in trades]
    total_profit = sum(p for p in pnls if p > 0)
    if total_profit <= 0:
        return 0.0
    max_pct = 0.0
    for side in ("YES", "NO"):
        buckets = analyze_buckets(trades, side)
        for _b, m in buckets.items():
            if m["total_pnl"] > 0:
                max_pct = max(max_pct, m["total_pnl"] / total_profit * 100)
    conc = profit_concentration(trades)
    max_pct = max(
        max_pct,
        conc.get("max_side_pct", 0),
        conc.get("max_regime_pct", 0),
    )
    return round(max_pct, 1)


def bootstrap_pp_pf_gt1(pnls: list[float], n_samples: int = BOOTSTRAP_SAMPLES) -> float | None:
    if len(pnls) < BOOTSTRAP_MIN_N:
        return None
    rng = random.Random(42)
    n = len(pnls)
    hits = 0
    for _ in range(n_samples):
        sample = [pnls[rng.randrange(n)] for _ in range(n)]
        if _pf(sample) > 1.0:
            hits += 1
    return round(hits / n_samples, 3)


def walk_forward_split(trades: list[LiveTrade]) -> dict[str, Any]:
    if len(trades) < 20:
        return {"train_n": 0, "test_n": 0, "train_pf": 0, "test_pf": 0, "ratio": 0}
    split = max(1, int(len(trades) * WF_TRAIN_RATIO))
    train = trades[:split]
    test = trades[split:]
    train_pnls = [t.pnl_pct or 0 for t in train]
    test_pnls = [t.pnl_pct or 0 for t in test]
    train_pf = _pf(train_pnls)
    test_pf = _pf(test_pnls)
    ratio = test_pf / train_pf if train_pf > 0 else 0.0
    return {
        "train_n": len(train),
        "test_n": len(test),
        "train_pf": round(train_pf, 3),
        "test_pf": round(test_pf, 3),
        "ratio": round(ratio, 3),
    }


def evaluate_candidate(trades: list[LiveTrade]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}

    pnls = [t.pnl_pct or 0 for t in trades]
    wins = [p for p in pnls if p > 0]
    stress = _stress_pnls(trades)
    stress_b = _pf(stress["B_entry+0.01_exit-0.01"]) if stress["B_entry+0.01_exit-0.01"] else 0
    fixed_2 = _pf(stress["D_fixed_2pct_adverse"]) if stress["D_fixed_2pct_adverse"] else 0

    rolling = rolling_windows(trades, 50)
    recent = rolling[-V12_GATES["rolling_windows"] :] if rolling else []
    rolling_pass = sum(1 for w in recent if w.get("pf", 0) > 1.0)

    last50 = trades[-50:] if len(trades) >= 50 else trades
    last100 = trades[-100:] if len(trades) >= 100 else trades
    last50_pnls = [t.pnl_pct or 0 for t in last50]
    last100_pnls = [t.pnl_pct or 0 for t in last100]

    wf = walk_forward_split(trades)
    bootstrap = bootstrap_pp_pf_gt1(pnls)
    max_bucket = _max_bucket_concentration(trades)

    return {
        "trades": len(trades),
        "wr": round(len(wins) / len(trades) * 100, 1),
        "pf": round(_pf(pnls), 3),
        "avg_pnl": round(statistics.mean(pnls), 2),
        "max_dd": round(_max_drawdown(pnls), 2),
        "max_cl": _max_consecutive_losses(pnls),
        "pf_last50": round(_pf(last50_pnls), 3) if last50_pnls else 0,
        "pf_last100": round(_pf(last100_pnls), 3) if last100_pnls else 0,
        "yes_pf": _side_pf(trades, "YES"),
        "no_pf": _side_pf(trades, "NO"),
        "stress_b_pf": round(stress_b, 3),
        "fixed_2pct_pf": round(fixed_2, 3),
        "rolling_4_of_5": rolling_pass,
        "rolling_windows_available": len(recent),
        "bootstrap_pp_gt1": bootstrap,
        "walk_forward": wf,
        "max_bucket_concentration_pct": max_bucket,
    }


def passes_v12_gates(metrics: dict[str, Any]) -> dict[str, bool]:
    g = V12_GATES
    bootstrap = metrics.get("bootstrap_pp_gt1")
    return {
        "n_ge_150": metrics.get("trades", 0) >= g["min_n"],
        "pf_ge_150": metrics.get("pf", 0) >= g["pf_min"],
        "yes_pf_ge_120": metrics.get("yes_pf", 0) >= g["yes_pf_min"],
        "no_pf_ge_120": metrics.get("no_pf", 0) >= g["no_pf_min"],
        "stress_b_ge_110": metrics.get("stress_b_pf", 0) >= g["stress_b_min"],
        "fixed_2pct_ge_120": metrics.get("fixed_2pct_pf", 0) >= g["fixed_2pct_min"],
        "max_cl_le_10": metrics.get("max_cl", 99) <= g["max_cl_max"],
        "rolling_4_of_5": metrics.get("rolling_4_of_5", 0) >= g["rolling_pass_min"],
        "no_concentration_gt_60": metrics.get("max_bucket_concentration_pct", 100) <= g["max_concentration_pct"],
        "bootstrap_pp_ge_085": bootstrap is not None and bootstrap >= g["bootstrap_pp_min"],
    }


def robustness_score(metrics: dict[str, Any]) -> float:
    """Rank by robustness, not raw PF."""
    if metrics.get("trades", 0) < 30:
        return -999.0
    score = 0.0
    score += min(metrics.get("stress_b_pf", 0), 2.5) * 35
    score += min(metrics.get("fixed_2pct_pf", 0), 2.5) * 30
    bpp = metrics.get("bootstrap_pp_gt1")
    if bpp is not None:
        score += bpp * 25
    else:
        score -= 15
    score += min(metrics.get("pf", 0), 2.5) * 10
    wf = metrics.get("walk_forward", {})
    if wf.get("train_pf", 0) > 0:
        score += min(wf.get("ratio", 0), 1.5) * 15
    score += metrics.get("rolling_4_of_5", 0) * 4
    if metrics.get("trades", 0) < V12_GATES["min_n"]:
        score -= (V12_GATES["min_n"] - metrics["trades"]) * 0.15
    if metrics.get("max_bucket_concentration_pct", 0) > V12_GATES["max_concentration_pct"]:
        score -= 25
    gates = passes_v12_gates(metrics)
    score += sum(10 for ok in gates.values() if ok)
    return round(score, 2)


def generate_candidates() -> list[CandidateSpec]:
    base = CandidateSpec(name="V1.1_baseline")
    cands: list[CandidateSpec] = [base]

    def add(name: str, **kwargs: Any) -> None:
        cands.append(CandidateSpec(name=name, **kwargs))

    # YES filters (each alone)
    add("yes_excl_035_040", yes_exclude_035_040=True)
    add("yes_excl_below_025", yes_exclude_below_025=True)
    add("yes_min_ask_025", yes_min_ask=0.25)
    add("yes_only_040_045", yes_allowed_lo=0.40, yes_allowed_hi=0.45)

    # Time filters
    add("time_15_180", sfs_max=180)
    add("time_15_120", sfs_max=120)
    add("time_120_250", sfs_min=120, sfs_max=250)
    add("time_180_250", sfs_min=180, sfs_max=250)
    add("time_excl_120_180", exclude_sfs_lo=120, exclude_sfs_hi=180)

    # Movement
    add("yes_move10_no5", yes_min_move=10.0, no_min_move=5.0)
    add("yes_move15_no5", yes_min_move=15.0, no_min_move=5.0)
    add("yes_excl_move15_30", exclude_yes_move_lo=15.0, exclude_yes_move_hi=30.0)

    # Regime
    add("regime_NORMAL_only", allowed_regimes=("NORMAL",))
    add("regime_NORMAL_MOMENTUM", allowed_regimes=("NORMAL", "MOMENTUM"))

    # Key combinations (robustness-oriented)
    add(
        "combo_time180_yes040",
        sfs_min=180, sfs_max=250,
        yes_allowed_lo=0.40, yes_allowed_hi=0.45,
    )
    add(
        "combo_excl035_time180",
        yes_exclude_035_040=True,
        sfs_min=180, sfs_max=250,
    )
    add(
        "combo_yesmove10_excl035",
        yes_min_move=10.0, no_min_move=5.0,
        yes_exclude_035_040=True,
    )
    add(
        "combo_time15_180_yesmove10",
        sfs_max=180,
        yes_min_move=10.0, no_min_move=5.0,
    )
    add(
        "combo_NORMAL_excl035",
        allowed_regimes=("NORMAL",),
        yes_exclude_035_040=True,
    )
    add(
        "combo_excl120_180_yes040",
        exclude_sfs_lo=120, exclude_sfs_hi=180,
        yes_allowed_lo=0.40, yes_allowed_hi=0.45,
    )
    add(
        "combo_yes040_regime_NM",
        yes_allowed_lo=0.40, yes_allowed_hi=0.45,
        allowed_regimes=("NORMAL", "MOMENTUM"),
    )
    add(
        "combo_robust_core",
        sfs_max=180,
        yes_exclude_035_040=True,
        yes_min_move=10.0, no_min_move=5.0,
        allowed_regimes=("NORMAL", "MOMENTUM"),
    )
    add(
        "combo_robust_strict",
        sfs_min=180, sfs_max=250,
        yes_allowed_lo=0.40, yes_allowed_hi=0.45,
        yes_min_move=10.0, no_min_move=5.0,
        allowed_regimes=("NORMAL",),
    )

    return cands


@dataclass
class CandidateResult:
    spec: CandidateSpec
    metrics: dict[str, Any]
    gates: dict[str, bool]
    robustness: float
    excluded: int


def run_counterfactual(trades: list[LiveTrade]) -> list[CandidateResult]:
    results: list[CandidateResult] = []
    for spec in generate_candidates():
        filtered = filter_trades(trades, spec)
        metrics = evaluate_candidate(filtered)
        gates = passes_v12_gates(metrics)
        results.append(
            CandidateResult(
                spec=spec,
                metrics=metrics,
                gates=gates,
                robustness=robustness_score(metrics),
                excluded=len(trades) - len(filtered),
            )
        )
    results.sort(key=lambda r: r.robustness, reverse=True)
    return results


def _within_tol(price: float | None, ref: float, tol: float = TOLERANCE) -> bool:
    return price is not None and price > 0 and abs(price - ref) <= tol


def classify_quote_violation(
    conn: sqlite3.Connection,
    trade: LiveTrade,
    violation_type: str,
) -> dict[str, Any]:
    """Classify ENTRY_NOT_ASK / EXIT_NOT_BID into A/B/C/D."""
    is_entry = violation_type == "ENTRY_NOT_ASK"
    ts = trade.entry_ts if is_entry else trade.exit_ts
    stored = trade.entry_price if is_entry else trade.exit_price
    if ts is None or stored is None:
        return {"class": "unknown", "detail": "missing ts/price"}

    side = trade.side

    def side_ask(snap) -> float | None:
        return snap.yes_ask if side == "YES" else snap.no_ask

    def side_bid(snap) -> float | None:
        return snap.yes_bid if side == "YES" else snap.no_bid

    price_fn = side_ask if is_entry else side_bid

    v4_before = quote_v4_at_or_before(conn, trade.market_slug, ts)
    v4_near = quote_v4_nearest(conn, trade.market_slug, ts)
    mc_before = quote_market_check_at_or_before(conn, trade.market_slug, ts)
    mc_near = quote_market_check_nearest(conn, trade.market_slug, ts)
    bidi = quote_bidi_observation_at_entry(conn, trade) if is_entry else None

    # D: audit false positive — shadow obs or market_checks match
    if is_entry and bidi and _within_tol(price_fn(bidi), stored):
        return {
            "class": "D",
            "detail": f"bidi_obs matches stored={stored:.3f}",
            "v4_lag": v4_before.lag_sec if v4_before else None,
        }
    if mc_near and _within_tol(price_fn(mc_near), stored):
        return {
            "class": "D",
            "detail": f"market_check_nearest matches lag={mc_near.lag_sec}s",
            "v4_lag": v4_before.lag_sec if v4_before else None,
        }
    if mc_before and _within_tol(price_fn(mc_before), stored):
        return {
            "class": "D",
            "detail": f"market_check_at_or_before matches lag={mc_before.lag_sec}s",
            "v4_lag": v4_before.lag_sec if v4_before else None,
        }

    # B: timestamp mismatch — v4 nearest matches, at-or-before does not
    if v4_near and _within_tol(price_fn(v4_near), stored):
        if not v4_before or not _within_tol(price_fn(v4_before), stored):
            return {
                "class": "B",
                "detail": (
                    f"v4_nearest Δ={abs((price_fn(v4_near) or 0) - stored):.3f} "
                    f"lag={v4_near.lag_sec}s; v4_before mismatch"
                ),
                "v4_lag": v4_before.lag_sec if v4_before else None,
            }

    # C: stale quote — v4 at-or-before lag > 15s
    if v4_before and v4_before.lag_sec is not None and v4_before.lag_sec > 15:
        return {
            "class": "C",
            "detail": f"v4 stale lag={v4_before.lag_sec}s v4_price={price_fn(v4_before)}",
            "v4_lag": v4_before.lag_sec,
        }

    # A: true execution/data bug — no source matches
    return {
        "class": "A",
        "detail": (
            f"no source within {TOLERANCE}; stored={stored:.3f} "
            f"v4={price_fn(v4_before) if v4_before else None}"
        ),
        "v4_lag": v4_before.lag_sec if v4_before else None,
    }


def analyze_quote_violations(conn: sqlite3.Connection, trades: list[LiveTrade]) -> dict[str, Any]:
    from bot.research.bidirectional_live_audit import _audit_quote_alignment

    violations: list = []
    suspects: list = []
    for t in trades:
        _audit_quote_alignment(conn, t, violations, suspects)

    by_type: dict[str, list] = {}
    for s in suspects:
        by_type.setdefault(s.violation_type, []).append(s)

    classified: list[dict[str, Any]] = []
    class_counts: dict[str, int] = {"A": 0, "B": 0, "C": 0, "D": 0}

    trade_by_id = {t.id: t for t in trades}
    for v in suspects:
        t = trade_by_id.get(v.trade_id) if v.trade_id else None
        if not t:
            continue
        c = classify_quote_violation(conn, t, v.violation_type)
        class_counts[c["class"]] = class_counts.get(c["class"], 0) + 1
        classified.append({
            "trade_id": v.trade_id,
            "type": v.violation_type,
            "market_slug": v.market_slug,
            "detail": v.detail,
            **c,
        })

    return {
        "total_suspects": len(suspects),
        "by_type": {k: len(v) for k, v in by_type.items()},
        "by_class": class_counts,
        "classified": classified,
    }


def pick_v12_winner(results: list[CandidateResult]) -> CandidateResult | None:
    """Best candidate passing all V12 gates with highest robustness score."""
    passing = [r for r in results if all(r.gates.values())]
    if passing:
        return passing[0]
    # Soft pick: most gates passed + high robustness
    scored = sorted(
        results,
        key=lambda r: (sum(r.gates.values()), r.robustness),
        reverse=True,
    )
    if scored and scored[0].robustness > 0:
        return scored[0]
    return None


def render_report(
    results: list[CandidateResult],
    quote_analysis: dict[str, Any],
    raw_n: int,
    corrected_n: int,
) -> str:
    lines: list[str] = []
    w = REPORT_WIDTH

    def h(title: str) -> None:
        lines.append("")
        lines.append("=" * w)
        lines.append(title)
        lines.append("=" * w)

    h("BIDIRECTIONAL V1.2 COUNTERFACTUAL RESEARCH")
    lines.append(f"Raw closed trades: {raw_n} | Corrected (1/market): {corrected_n}")
    lines.append("Ranking: robustness score (stress + bootstrap + WF), NOT max PF")

    h("1. QUOTE VIOLATION CLASSIFICATION")
    lines.append(f"  Total suspects: {quote_analysis['total_suspects']}")
    for t, n in quote_analysis.get("by_type", {}).items():
        lines.append(f"    {t}: {n}")
    lines.append("  By class:")
    for cls, label in [
        ("A", "true execution/data bug"),
        ("B", "timestamp mismatch"),
        ("C", "stale quote"),
        ("D", "audit false positive"),
    ]:
        lines.append(f"    {cls} ({label}): {quote_analysis['by_class'].get(cls, 0)}")

    h("2. TOP CANDIDATES (by robustness score)")
    lines.append(
        f"  {'Name':<28} {'N':>4} {'PF':>5} {'sB':>5} {'2%':>5} "
        f"{'Y':>5} {'N':>5} {'bpp':>5} {'WF':>5} {'R4':>3} {'Rob':>6} {'Pass':>4}"
    )
    for r in results[:20]:
        m = r.metrics
        bpp = m.get("bootstrap_pp_gt1")
        bpp_s = f"{bpp:.2f}" if bpp is not None else " n/a"
        wf = m.get("walk_forward", {})
        wf_s = f"{wf.get('test_pf', 0):.2f}" if wf.get("test_n") else " n/a"
        gates_pass = sum(r.gates.values())
        lines.append(
            f"  {r.spec.name:<28} {m.get('trades', 0):>4} {m.get('pf', 0):>5.2f} "
            f"{m.get('stress_b_pf', 0):>5.2f} {m.get('fixed_2pct_pf', 0):>5.2f} "
            f"{m.get('yes_pf', 0):>5.2f} {m.get('no_pf', 0):>5.2f} "
            f"{bpp_s:>5} {wf_s:>5} {m.get('rolling_4_of_5', 0):>3} "
            f"{r.robustness:>6.1f} {gates_pass:>2}/{len(r.gates)}"
        )
        lines.append(f"      {_describe_spec(r.spec)}")

    winner = pick_v12_winner(results)
    h("3. V1.2 RECOMMENDATION")
    if winner:
        m = winner.metrics
        all_pass = all(winner.gates.values())
        lines.append(f"  Candidate: {winner.spec.name}")
        lines.append(f"  Filters: {_describe_spec(winner.spec)}")
        lines.append(f"  Robustness: {winner.robustness} | Gates: {sum(winner.gates.values())}/{len(winner.gates)}")
        lines.append(
            f"  N={m.get('trades')} PF={m.get('pf')} stress_B={m.get('stress_b_pf')} "
            f"fixed_2%={m.get('fixed_2pct_pf')} YES={m.get('yes_pf')} NO={m.get('no_pf')}"
        )
        if all_pass:
            lines.append("  VERDICT: READY for V1.2 parallel shadow deployment")
        else:
            lines.append("  VERDICT: BEST AVAILABLE — does not pass all gates yet")
            for k, ok in winner.gates.items():
                if not ok:
                    lines.append(f"    FAIL: {k}")
    else:
        lines.append("  No viable V1.2 candidate — continue V1.1 shadow collection")

    h("4. V1.1 BASELINE")
    baseline = next((r for r in results if r.spec.name == "V1.1_baseline"), None)
    if baseline:
        m = baseline.metrics
        lines.append(
            f"  N={m.get('trades')} PF={m.get('pf')} stress_B={m.get('stress_b_pf')} "
            f"fixed_2%={m.get('fixed_2pct_pf')} robustness={baseline.robustness}"
        )

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    from bot.database import connect, init_db
    from bot.strategy.bidirectional_shadow import ensure_tables

    parser = argparse.ArgumentParser(description="V1.2 counterfactual research (read-only)")
    parser.add_argument("--top", type=int, default=20, help="Show top N candidates")
    args = parser.parse_args()

    init_db()
    with connect() as conn:
        ensure_tables(conn)
        raw = load_closed_trades(conn)
        trades, dedup = dedupe_first_trade_per_market(raw)
        quote_analysis = analyze_quote_violations(conn, trades)
        results = run_counterfactual(trades)
        print(render_report(results, quote_analysis, len(raw), len(trades)))

        winner = pick_v12_winner(results)
        if winner and all(winner.gates.values()):
            print(f"\n>>> Deploy V1.2 parallel shadow with spec: {winner.spec.name}")
            print(f">>> {_describe_spec(winner.spec)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
