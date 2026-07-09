"""Phase D.1 outcome performance report and walk-forward analysis."""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from bot.research.futures_agent.signal_outcome_constants import (
    BOOTSTRAP_SAMPLES,
    BOOTSTRAP_SEED,
    DATA_QUALITY_COMPLETE_THRESHOLD,
    DEFAULT_FEE_BPS,
    DEFAULT_SLIPPAGE_BPS,
    ENGINE_VERSION,
    LEVERAGE_SCENARIOS,
    SAMPLE_INSUFFICIENT,
    SAMPLE_LOW,
    SAMPLE_MODERATE,
)
from bot.research.futures_agent.signal_outcome_robustness import (
    DEFAULT_COST_FEE_BPS,
    DEFAULT_COST_SLIPPAGE_BPS,
    VerdictInputs,
    apply_round_trip_cost,
    compute_research_verdict,
    compute_robust_stats,
    simulate_portfolio_equity,
)


@dataclass
class CohortStats:
    n: int = 0
    entered: int = 0
    entry_rate: float = 0.0
    win_rate: float = 0.0
    mean_return: float = 0.0
    median_return: float = 0.0
    profit_factor: float | None = None
    expectancy: float = 0.0
    std_dev: float = 0.0
    max_drawdown: float = 0.0
    sample_label: str = "INSUFFICIENT"
    bootstrap_mean_ci: tuple[float, float] | None = None
    bootstrap_win_ci: tuple[float, float] | None = None


@dataclass
class OutcomeReport:
    channel: str
    engine_version: str
    corpus: dict[str, int] = field(default_factory=dict)
    data_quality: dict[str, Any] = field(default_factory=dict)
    entry_conversion: dict[str, Any] = field(default_factory=dict)
    conservative_results: CohortStats = field(default_factory=CohortStats)
    cohorts: dict[str, dict[str, CohortStats]] = field(default_factory=dict)
    walk_forward: list[dict[str, Any]] = field(default_factory=list)
    rolling_quarters: list[dict[str, Any]] = field(default_factory=list)
    entry_status: dict[str, int] = field(default_factory=dict)
    outcome_status: dict[str, int] = field(default_factory=dict)
    policy_comparison: dict[str, CohortStats] = field(default_factory=dict)
    verdict: str = "INSUFFICIENT_DATA"
    verdict_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _sample_label(n: int) -> str:
    if n < SAMPLE_INSUFFICIENT:
        return "INSUFFICIENT"
    if n < SAMPLE_LOW:
        return "LOW CONFIDENCE"
    if n < SAMPLE_MODERATE:
        return "MODERATE"
    return "STRONG SAMPLE"


def _bootstrap_ci(values: list[float], *, seed: int = BOOTSTRAP_SEED) -> tuple[float, float] | None:
    if len(values) < 5:
        return None
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(BOOTSTRAP_SAMPLES):
        sample = [values[rng.randrange(len(values))] for _ in range(len(values))]
        means.append(statistics.mean(sample))
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means)) - 1]
    return lo, hi


def _compute_stats(returns: list[float]) -> CohortStats:
    cs = CohortStats(n=len(returns))
    if not returns:
        cs.sample_label = _sample_label(0)
        return cs
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    cs.win_rate = len(wins) / len(returns)
    cs.mean_return = statistics.mean(returns)
    cs.median_return = statistics.median(returns)
    cs.expectancy = cs.mean_return
    cs.std_dev = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    cs.profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
    cs.sample_label = _sample_label(len(returns))
    cs.bootstrap_mean_ci = _bootstrap_ci(returns)
    win_flags = [1.0 if r > 0 else 0.0 for r in returns]
    cs.bootstrap_win_ci = _bootstrap_ci(win_flags)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in returns:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    cs.max_drawdown = max_dd
    return cs


def _policy_return(row: dict[str, Any], policy: str = "P1") -> float | None:
    try:
        policies = json.loads(row["policy_results_json"] or "{}")
    except json.JSONDecodeError:
        return None
    pr = policies.get(policy)
    if not pr:
        return None
    val = pr.get("return_pct")
    return float(val) if val is not None else None


def _load_outcomes(
    conn: Any,
    *,
    channel: str,
    engine_version: str = ENGINE_VERSION,
    symbol: str | None = None,
    direction: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> list[dict[str, Any]]:
    q = """
    SELECT o.*, p.message_ts
    FROM futures_agent_research_signal_outcomes o
    JOIN futures_agent_trader_posts p ON p.id = o.post_id
    WHERE o.channel = ? AND o.engine_version = ?
    """
    params: list[Any] = [channel, engine_version]
    if symbol:
        q += " AND o.symbol = ?"
        params.append(symbol)
    if direction:
        q += " AND o.direction = ?"
        params.append(direction)
    if start_ts is not None:
        q += " AND o.decision_ts >= ?"
        params.append(int(start_ts))
    if end_ts is not None:
        q += " AND o.decision_ts <= ?"
        params.append(int(end_ts))
    q += " ORDER BY o.decision_ts ASC"
    return conn.execute(q, params).fetchall()


def run_outcome_report(
    conn: Any,
    *,
    channel: str = "signalyp",
    engine_version: str = ENGINE_VERSION,
    policy: str = "P1",
    symbol: str | None = None,
    direction: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> OutcomeReport:
    report = OutcomeReport(channel=channel, engine_version=engine_version)
    rows = _load_outcomes(
        conn, channel=channel, engine_version=engine_version,
        symbol=symbol, direction=direction, start_ts=start_ts, end_ts=end_ts,
    )

    expected = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_theses t
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        WHERE p.channel_name = ? AND p.content_type = 'EXPLICIT_SIGNAL'
        """,
        (channel,),
    ).fetchone()["n"]

    report.corpus = {
        "expected_explicit_signals": expected,
        "outcomes_built": len(rows),
    }

    entered_rows = [r for r in rows if r["entry_status"] == "ENTERED"]
    not_entered = sum(1 for r in rows if r["entry_status"] == "NOT_ENTERED")
    no_data = sum(1 for r in rows if r["data_quality_status"] in ("NO_DATA", "NO_SYMBOL"))
    ambiguous = sum(1 for r in rows if r["ambiguous_intrabar"])
    complete = sum(
        1 for r in entered_rows
        if r["data_quality_status"] == "COMPLETE"
    )

    report.entry_status = {
        "ENTERED": sum(1 for r in rows if r["entry_status"] == "ENTERED"),
        "NOT_ENTERED": sum(1 for r in rows if r["entry_status"] == "NOT_ENTERED"),
        "SYMBOL_UNRESOLVED": sum(1 for r in rows if r["entry_status"] == "SYMBOL_UNRESOLVED"),
        "NO_DATA": sum(1 for r in rows if r["entry_status"] == "NO_DATA"),
    }
    report.outcome_status = {}
    for row in rows:
        st = str(row["outcome_status"] or "UNKNOWN")
        report.outcome_status[st] = report.outcome_status.get(st, 0) + 1

    report.data_quality = {
        "signals_total": len(rows),
        "symbols_total": len({r["symbol"] for r in rows if r["symbol"]}),
        "symbols_resolved": sum(1 for r in rows if r["exchange_symbol"]),
        "symbols_unresolved": sum(1 for r in rows if r["symbol_resolve_status"] != "resolved"),
        "entered": len(entered_rows),
        "not_entered": not_entered,
        "no_data": no_data,
        "ambiguous_intrabar_count": ambiguous,
        "ambiguous_intrabar_pct": (ambiguous / len(entered_rows) if entered_rows else 0.0),
        "complete_data_pct": (complete / len(entered_rows) if entered_rows else 0.0),
        "numeric_entry_count": sum(1 for r in rows if r["entry_mode"] == "NUMERIC_ZONE"),
        "market_entry_count": sum(1 for r in rows if r["entry_mode"] == "MARKET_ENTRY"),
        "numeric_stop_count": sum(1 for r in rows if r["stop_mode"] == "NUMERIC_STOP"),
        "deferred_stop_count": sum(1 for r in rows if r["stop_mode"] == "DEFERRED_STOP"),
        "missing_stop_count": sum(1 for r in rows if r["stop_mode"] == "MISSING_STOP"),
    }

    report.entry_conversion = {
        "signals_total": len(rows),
        "entered": len(entered_rows),
        "not_entered": not_entered,
        "entry_rate": len(entered_rows) / len(rows) if rows else 0.0,
    }

    conservative_returns = [
        r for r in (_policy_return(row, policy) for row in entered_rows)
        if r is not None
    ]
    report.conservative_results = _compute_stats(conservative_returns)

    for pol in ("P1", "P2", "P3", "P4", "P5", "P6"):
        rets = [r for r in (_policy_return(row, pol) for row in entered_rows) if r is not None]
        report.policy_comparison[pol] = _compute_stats(rets)

    cohort_keys = {
        "direction": lambda r: str(r["direction"]),
        "entry_mode": lambda r: str(r["entry_mode"]),
        "stop_mode": lambda r: str(r["stop_mode"]),
        "year": lambda r: str(datetime.fromtimestamp(int(r["decision_ts"]), tz=timezone.utc).year),
        "month": lambda r: datetime.fromtimestamp(
            int(r["decision_ts"]), tz=timezone.utc,
        ).strftime("%Y-%m"),
        "symbol": lambda r: str(r["symbol"]),
    }
    report.cohorts = {}
    for name, fn in cohort_keys.items():
        buckets: dict[str, list[float]] = defaultdict(list)
        for row in entered_rows:
            ret = _policy_return(row, policy)
            if ret is not None:
                buckets[fn(row)].append(ret)
        report.cohorts[name] = {k: _compute_stats(v) for k, v in sorted(buckets.items())}

    if entered_rows:
        sorted_rows = sorted(entered_rows, key=lambda r: int(r["decision_ts"]))
        n = len(sorted_rows)
        quarter = max(1, n // 4)
        for i, label in enumerate(("Q1_first_25pct", "Q2", "Q3", "Q4_last_25pct")):
            chunk = sorted_rows[i * quarter:(i + 1) * quarter if i < 3 else n]
            rets = [r for r in (_policy_return(row, policy) for row in chunk) if r is not None]
            cs = _compute_stats(rets)
            report.rolling_quarters.append({
                "block": label,
                "n": cs.n,
                "mean_return": cs.mean_return,
                "win_rate": cs.win_rate,
                "sample_label": cs.sample_label,
            })

        years = sorted({datetime.fromtimestamp(int(r["decision_ts"]), tz=timezone.utc).year for r in sorted_rows})
        for yr in years:
            train = [r for r in sorted_rows if datetime.fromtimestamp(
                int(r["decision_ts"]), tz=timezone.utc,
            ).year < yr]
            test = [r for r in sorted_rows if datetime.fromtimestamp(
                int(r["decision_ts"]), tz=timezone.utc,
            ).year == yr]
            if not test:
                continue
            train_rets = [r for r in (_policy_return(row, policy) for row in train) if r is not None]
            test_rets = [r for r in (_policy_return(row, policy) for row in test) if r is not None]
            report.walk_forward.append({
                "oos_year": yr,
                "train_n": len(train_rets),
                "oos_n": len(test_rets),
                "train_mean": statistics.mean(train_rets) if train_rets else None,
                "oos_mean": statistics.mean(test_rets) if test_rets else None,
                "oos_win_rate": (sum(1 for r in test_rets if r > 0) / len(test_rets)) if test_rets else None,
                "oos_sample_label": _sample_label(len(test_rets)),
            })

    complete_pct = report.data_quality.get("complete_data_pct", 0.0)
    if len(rows) < expected:
        report.warnings.append(
            f"Only {len(rows)}/{expected} EXPLICIT_SIGNAL outcomes built; run outcome-build first.",
        )

    robust = compute_robust_stats(conservative_returns)
    trade_dicts = [
            {
                "return_pct": _policy_return(row, policy),
                "entry_price": row["entry_price"],
                "stop_price": row["stop_price"],
                "direction": row["direction"],
            }
        for row in entered_rows
        if _policy_return(row, policy) is not None
    ]
    port = simulate_portfolio_equity(trade_dicts, sizing="fixed_risk")
    cost_mean = statistics.mean([
        apply_round_trip_cost(r, fee_bps=DEFAULT_COST_FEE_BPS, slippage_bps=DEFAULT_COST_SLIPPAGE_BPS)
        for r in conservative_returns
    ]) if conservative_returns else None

    oos_years_pos = sum(
        1 for w in report.walk_forward
        if w.get("oos_mean") is not None and w["oos_mean"] > 0 and w.get("oos_n", 0) >= 10
    )
    oos_years_total = sum(1 for w in report.walk_forward if w.get("oos_n", 0) >= 10)
    rolling_blocks_pos = sum(1 for q in report.rolling_quarters if q["mean_return"] > 0 and q["n"] >= 10)
    rolling_blocks_total = sum(1 for q in report.rolling_quarters if q["n"] >= 10)

    recent_rows = sorted(entered_rows, key=lambda r: int(r["decision_ts"]))[-250:]
    recent_rets = [r for r in (_policy_return(row, policy) for row in recent_rows) if r is not None]
    recent_250_mean = statistics.mean(recent_rets) if recent_rets else None

    verdict_inp = VerdictInputs(
        entered_n=report.conservative_results.n,
        complete_data_pct=complete_pct,
        headline_mean=report.conservative_results.mean_return,
        bootstrap_mean_ci=robust.bootstrap_mean_ci,
        winsorized_5pct_mean=robust.winsorized_means.get("5pct"),
        top10_concentration=robust.top_contribution_pct.get("top_10"),
        top25_concentration=robust.top_contribution_pct.get("top_25"),
        cost_adjusted_mean=cost_mean,
        risk_sized_max_dd=port.max_drawdown_pct,
        oos_years_positive=oos_years_pos,
        oos_years_total=oos_years_total,
        rolling_blocks_positive=rolling_blocks_pos,
        rolling_blocks_total=rolling_blocks_total,
        rolling_3m_positive=0,
        rolling_3m_total=0,
        recent_250_mean=recent_250_mean,
        p4_mean=report.policy_comparison.get("P4", CohortStats()).mean_return,
        p5_mean=report.policy_comparison.get("P5", CohortStats()).mean_return,
        p6_mean=report.policy_comparison.get("P6", CohortStats()).mean_return,
    )
    report.verdict, report.verdict_reasons = compute_research_verdict(verdict_inp)

    return report


def render_outcome_report(report: OutcomeReport, *, policy: str = "P1") -> str:
    lines = [
        "SIGNALYP EXPLICIT_SIGNAL OUTCOME REPORT",
        f"channel: {report.channel}",
        f"engine_version: {report.engine_version}",
        f"policy: {policy} (conservative headline)",
        "",
        "=== 1. CORPUS ===",
    ]
    for k, v in report.corpus.items():
        lines.append(f"  {k}: {v:,}" if isinstance(v, int) else f"  {k}: {v}")

    lines.extend(["", "=== 2. UNIVERSE & ENTRY STATUS ==="])
    lines.append(f"  EXPLICIT_SIGNAL universe (expected): {report.corpus.get('expected_explicit_signals', 0):,}")
    lines.append(f"  outcomes built: {report.corpus.get('outcomes_built', 0):,}")
    for k, v in report.entry_status.items():
        lines.append(f"  {k}: {v:,}")
    if report.outcome_status:
        lines.append("  outcome_status:")
        for k, v in report.outcome_status.items():
            lines.append(f"    {k}: {v:,}")
    ambiguous_n = report.data_quality.get("ambiguous_intrabar_count", 0)
    lines.append(f"  AMBIGUOUS_INTRABAR (entered subset): {ambiguous_n:,}")
    lines.append(f"  deferred_stop cohort: {report.data_quality.get('deferred_stop_count', 0):,}")

    lines.extend(["", "=== 3. DATA QUALITY ==="])
    for k, v in report.data_quality.items():
        if isinstance(v, float):
            lines.append(f"  {k}: {v:.1%}" if "pct" in k or "rate" in k else f"  {k}: {v:.4f}")
        else:
            lines.append(f"  {k}: {v:,}" if isinstance(v, int) else f"  {k}: {v}")

    lines.extend([
        "",
        "=== 4. ENTRY CONVERSION ===",
        f"  entry_rate: {report.entry_conversion.get('entry_rate', 0):.1%}",
        "",
        "=== 5. RETURN MODEL ASSUMPTIONS ===",
        "  MODEL A (headline): unleveraged underlying move, conservative execution (P1).",
        f"  Fees: {DEFAULT_FEE_BPS} bps (headline not adjusted; see outcome-outlier-audit for scenarios).",
        f"  Slippage: {DEFAULT_SLIPPAGE_BPS} bps (headline not adjusted).",
        f"  Robustness cost check uses {DEFAULT_COST_FEE_BPS}+{DEFAULT_COST_SLIPPAGE_BPS} bps round-trip.",
        "  Leverage scenarios below are research-only, not exchange simulation.",
        "",
        "=== 6. CONSERVATIVE UNLEVERAGED (MODEL A / P1) ===",
        f"  N: {report.conservative_results.n} ({report.conservative_results.sample_label})",
        f"  mean_return: {report.conservative_results.mean_return:.3f}%",
        f"  median_return: {report.conservative_results.median_return:.3f}%",
        f"  win_rate: {report.conservative_results.win_rate:.1%}",
        f"  profit_factor: {report.conservative_results.profit_factor}",
        f"  expectancy: {report.conservative_results.expectancy:.3f}%",
        f"  max_drawdown (sequential): {report.conservative_results.max_drawdown:.3f}%",
    ])
    if report.conservative_results.bootstrap_mean_ci:
        lo, hi = report.conservative_results.bootstrap_mean_ci
        lines.append(f"  bootstrap mean CI: [{lo:.3f}%, {hi:.3f}%]")

    lines.extend(["", "=== 8. EXIT POLICY COMPARISON (P1-P6, fixed, not optimized) ==="])
    for pol, cs in report.policy_comparison.items():
        lines.append(
            f"  {pol}: N={cs.n} mean={cs.mean_return:.3f}% win={cs.win_rate:.1%} [{cs.sample_label}]",
        )

    for section, key in (
        ("8. LONG vs SHORT", "direction"),
        ("9. ENTRY MODE", "entry_mode"),
        ("10. STOP MODE", "stop_mode"),
        ("11. BY YEAR", "year"),
        ("12. BY MONTH (top)", "month"),
        ("13. BY SYMBOL (top)", "symbol"),
    ):
        lines.extend(["", f"=== {section} ==="])
        cohort = report.cohorts.get(key, {})
        items = sorted(cohort.items(), key=lambda x: -x[1].n)[:15]
        for label, cs in items:
            if cs.sample_label == "INSUFFICIENT":
                continue
            lines.append(
                f"  {label}: N={cs.n} mean={cs.mean_return:.3f}% win={cs.win_rate:.1%} [{cs.sample_label}]",
            )
        insufficient = [label for label, cs in cohort.items() if cs.sample_label == "INSUFFICIENT"]
        if insufficient:
            lines.append(f"  (insufficient N omitted: {', '.join(insufficient[:10])})")

    lines.extend(["", "=== 15. WALK-FORWARD (OOS BY YEAR) ==="])
    for w in report.walk_forward:
        lines.append(
            f"  year={w['oos_year']} train_n={w['train_n']} oos_n={w['oos_n']} "
            f"oos_mean={w['oos_mean']:.3f}% oos_win={w['oos_win_rate']:.1%} "
            f"[{w['oos_sample_label']}]" if w.get("oos_mean") is not None else f"  year={w['oos_year']} insufficient",
        )

    lines.extend(["", "=== ROLLING 25% BLOCKS ==="])
    for q in report.rolling_quarters:
        lines.append(
            f"  {q['block']}: N={q['n']} mean={q['mean_return']:.3f}% win={q['win_rate']:.1%} [{q['sample_label']}]",
        )

    lines.extend([
        "",
        "=== LEVERAGE SCENARIOS (NOT EXCHANGE SIMULATION) ===",
        "  Liquidation/margin not modeled. Research-only scaled returns.",
    ])
    for lev in LEVERAGE_SCENARIOS:
        scaled = report.conservative_results.mean_return * lev
        lines.append(f"  {lev}x mean_return: {scaled:.3f}%")

    if report.warnings:
        lines.extend(["", "WARNINGS:"])
        for w in report.warnings:
            lines.append(f"  - {w}")

    lines.extend([
        "",
        "=== 16. RESEARCH VERDICT (D.1.1 robustness bar) ===",
        f"  {report.verdict}",
        "  Run outcome-outlier-audit for full attribution, validation, and stability drilldown.",
    ])
    for r in report.verdict_reasons:
        lines.append(f"    - {r}")
    lines.append("")
    lines.append("Do not promote to live trading automatically.")
    return "\n".join(lines)
