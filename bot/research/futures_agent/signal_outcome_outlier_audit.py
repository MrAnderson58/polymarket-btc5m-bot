"""Phase D.1.1 outlier attribution, return validation, and stability audit."""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from bot.research.futures_agent.historical_candles import CachedCandleProvider, required_end_ts
from bot.research.futures_agent.signal_outcome_constants import ENGINE_VERSION
from bot.research.futures_agent.signal_outcome_exit_policies import evaluate_exit_policy
from bot.research.futures_agent.signal_outcome_path import (
    PathEvaluation,
    TargetLevel,
    directional_return,
)
from bot.research.futures_agent.signal_outcome_report import (
    _compute_stats,
    _load_outcomes,
    _policy_return,
    _sample_label,
)
from bot.research.futures_agent.signal_outcome_robustness import (
    DEFAULT_COST_FEE_BPS,
    DEFAULT_COST_SLIPPAGE_BPS,
    RECENT_SIGNAL_WINDOWS,
    VerdictInputs,
    apply_round_trip_cost,
    compute_cost_scenarios,
    compute_delay_scenarios,
    compute_missed_entry_sensitivity,
    compute_research_verdict,
    compute_robust_stats,
    simulate_portfolio_equity,
)

ATTRIBUTION_POLICIES = ("P1", "P2", "P3")
RETURN_TOLERANCE_PCT = 0.05
IMPOSSIBLE_RETURN_BUFFER_PCT = 0.5


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


@dataclass
class TradeRecord:
    outcome_id: int
    thesis_id: int
    symbol: str
    direction: str
    decision_ts: int
    entry_mode: str
    stop_mode: str
    entry_price: float | None
    stop_price: float | None
    exit_price: float | None
    return_pct: float
    policy_id: str
    exit_reason: str
    holding_seconds: int | None
    exchange_symbol: str | None = None
    mfe_pct: float | None = None
    mae_pct: float | None = None
    ambiguous_intrabar: int = 0


@dataclass
class ValidationFinding:
    outcome_id: int
    severity: str
    category: str
    detail: str


@dataclass
class OutlierAuditReport:
    channel: str
    engine_version: str
    policy: str
    trade_count: int = 0
    robust_stats: dict[str, Any] = field(default_factory=dict)
    attribution: dict[str, Any] = field(default_factory=dict)
    validation_findings: list[ValidationFinding] = field(default_factory=list)
    stability: dict[str, Any] = field(default_factory=dict)
    economic_scenarios: dict[str, Any] = field(default_factory=dict)
    cohort_drilldowns: dict[str, Any] = field(default_factory=dict)
    verdict: str = "INSUFFICIENT_DATA"
    verdict_reasons: list[str] = field(default_factory=list)


def _iso_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _policy_meta(row: dict[str, Any], policy: str) -> tuple[str, float | None]:
    try:
        policies = json.loads(row["policy_results_json"] or "{}")
    except json.JSONDecodeError:
        return "PARSE_ERROR", None
    pr = policies.get(policy) or {}
    exit_event = str(pr.get("exit_event") or _row_get(row, "conservative_terminal") or "UNKNOWN")
    return exit_event, _policy_return(row, policy)


def _exit_price_from_row(row: dict[str, Any], policy: str, exit_event: str) -> float | None:
    if exit_event == "STOP" and _row_get(row,"stop_price") is not None:
        return float(row["stop_price"])
    if exit_event.startswith("TP"):
        try:
            meta = json.loads(_row_get(row,"candle_meta_json") or "{}")
            idx = int(exit_event[2:])
            for t in meta.get("targets") or []:
                if int(t.get("ordinal", -1)) == idx:
                    return float(t["price"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass
    if _row_get(row,"entry_price") is not None:
        return float(row["entry_price"])
    return None


def _holding_seconds(row: dict[str, Any], exit_event: str) -> int | None:
    entry_ts = _row_get(row,"entry_ts")
    terminal_ts = _row_get(row,"first_terminal_ts")
    if entry_ts and terminal_ts:
        return int(terminal_ts) - int(entry_ts)
    return None


def build_trade_record(row: dict[str, Any], policy: str) -> TradeRecord | None:
    ret = _policy_return(row, policy)
    if ret is None:
        return None
    exit_event, _ = _policy_meta(row, policy)
    return TradeRecord(
        outcome_id=int(row["id"]),
        thesis_id=int(row["thesis_id"]),
        symbol=str(row["symbol"] or ""),
        direction=str(row["direction"] or ""),
        decision_ts=int(row["decision_ts"]),
        entry_mode=str(row["entry_mode"] or ""),
        stop_mode=str(row["stop_mode"] or ""),
        entry_price=float(row["entry_price"]) if _row_get(row,"entry_price") is not None else None,
        stop_price=float(row["stop_price"]) if _row_get(row,"stop_price") is not None else None,
        exit_price=_exit_price_from_row(row, policy, exit_event),
        return_pct=ret,
        policy_id=policy,
        exit_reason=exit_event,
        holding_seconds=_holding_seconds(row, exit_event),
        exchange_symbol=_row_get(row,"exchange_symbol"),
        mfe_pct=float(row["mfe_pct"]) if _row_get(row,"mfe_pct") is not None else None,
        mae_pct=float(row["mae_pct"]) if _row_get(row,"mae_pct") is not None else None,
        ambiguous_intrabar=int(_row_get(row,"ambiguous_intrabar") or 0),
    )


def _trade_to_dict(t: TradeRecord) -> dict[str, Any]:
    return {
        "outcome_id": t.outcome_id,
        "thesis_id": t.thesis_id,
        "symbol": t.symbol,
        "direction": t.direction,
        "decision_ts": t.decision_ts,
        "entry_mode": t.entry_mode,
        "stop_mode": t.stop_mode,
        "entry_price": t.entry_price,
        "stop_price": t.stop_price,
        "return_pct": t.return_pct,
        "entry_ts": t.decision_ts,
    }


def _load_events(conn: Any, outcome_id: int) -> list[dict[str, Any]]:
    return conn.execute(
        """
        SELECT event_type, target_index, event_ts, event_price, ambiguity_flag, ordinal
        FROM futures_agent_research_signal_events
        WHERE outcome_id = ?
        ORDER BY ordinal
        """,
        (outcome_id,),
    ).fetchall()


def _load_levels(conn: Any, thesis_id: int) -> dict[str, list[float]]:
    rows = conn.execute(
        """
        SELECT level_type, price, ordinal
        FROM futures_agent_trader_levels
        WHERE thesis_id = ?
        ORDER BY ordinal
        """,
        (thesis_id,),
    ).fetchall()
    out: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        out[str(r["level_type"])].append(float(r["price"]))
    return dict(out)


def _path_from_stored(row: dict[str, Any], events: list[dict[str, Any]]) -> PathEvaluation:
    targets: list[TargetLevel] = []
    try:
        meta = json.loads(_row_get(row,"candle_meta_json") or "{}")
        for t in meta.get("targets") or []:
            targets.append(TargetLevel(
                price=float(t["price"]),
                ordinal=int(t["ordinal"]),
                validity=str(t.get("validity") or "VALID"),
            ))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        pass

    from bot.research.futures_agent.signal_outcome_path import MarkoutPoint, SignalEvent

    ev = PathEvaluation(
        decision_ts=int(row["decision_ts"]),
        direction=str(row["direction"]),
        entry_mode=str(row["entry_mode"]),
        entry_status=str(row["entry_status"]),
        entry_ts=int(row["entry_ts"]) if _row_get(row,"entry_ts") is not None else None,
        entry_price=float(row["entry_price"]) if _row_get(row,"entry_price") is not None else None,
        entry_fill_model=str(_row_get(row,"entry_fill_model") or ""),
        stop_mode=str(row["stop_mode"]),
        stop_price=float(row["stop_price"]) if _row_get(row,"stop_price") is not None else None,
        targets=targets,
        outcome_status=str(_row_get(row,"outcome_status") or ""),
        conservative_terminal=_row_get(row,"conservative_terminal"),
        optimistic_terminal=_row_get(row,"optimistic_terminal"),
        max_target_reached=int(_row_get(row,"max_target_reached") or 0),
        mfe_pct=float(row["mfe_pct"]) if _row_get(row,"mfe_pct") is not None else None,
        mae_pct=float(row["mae_pct"]) if _row_get(row,"mae_pct") is not None else None,
        ambiguous_intrabar=int(_row_get(row,"ambiguous_intrabar") or 0),
        data_quality_status=str(_row_get(row,"data_quality_status") or ""),
    )
    ev.events = [
        SignalEvent(
            event_type=str(e["event_type"]),
            event_ts=int(e["event_ts"]),
            event_price=float(e["event_price"]),
            candle_open_ts=int(e["event_ts"]),
            target_index=int(e["target_index"]) if _row_get(e, "target_index") is not None else None,
            ambiguity_flag=int(_row_get(e, "ambiguity_flag") or 0),
        )
        for e in events
    ]
    return ev


def _load_markouts(conn: Any, outcome_id: int) -> list[dict[str, Any]]:
    return conn.execute(
        """
        SELECT horizon, horizon_seconds, mark_ts, mark_price,
               directional_return_pct, mfe_pct, mae_pct
        FROM futures_agent_research_signal_markouts
        WHERE outcome_id = ?
        ORDER BY horizon_seconds
        """,
        (outcome_id,),
    ).fetchall()


def _attach_markouts(ev: PathEvaluation, markout_rows: list[dict[str, Any]]) -> None:
    from bot.research.futures_agent.signal_outcome_path import MarkoutPoint

    ev.markouts = [
        MarkoutPoint(
            horizon=str(m["horizon"]),
            horizon_seconds=int(m["horizon_seconds"]),
            mark_ts=int(m["mark_ts"]),
            mark_price=float(m["mark_price"]),
            directional_return_pct=float(m["directional_return_pct"]),
            mfe_pct=float(m["mfe_pct"]),
            mae_pct=float(m["mae_pct"]),
        )
        for m in markout_rows
    ]


def validate_trade_math(
    conn: Any,
    row: dict[str, Any],
    *,
    policy: str,
    candle_provider: Any | None = None,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    oid = int(row["id"])
    events = _load_events(conn, oid)
    markouts = _load_markouts(conn, oid)
    ev = _path_from_stored(row, events)
    _attach_markouts(ev, markouts)

    stored_ret = _policy_return(row, policy)
    if stored_ret is None:
        return findings

    recomputed = evaluate_exit_policy(ev, policy)
    if recomputed.return_pct is None:
        findings.append(ValidationFinding(
            oid, "WARN", "policy_recompute",
            f"{policy} could not be recomputed from stored path ({recomputed.exit_event})",
        ))
    elif abs(recomputed.return_pct - stored_ret) > RETURN_TOLERANCE_PCT:
        findings.append(ValidationFinding(
            oid, "ERROR", "policy_mismatch",
            f"{policy} stored={stored_ret:.4f}% recomputed={recomputed.return_pct:.4f}%",
        ))

    direction = str(row["direction"])
    entry = _row_get(row,"entry_price")
    if entry is not None and float(entry) <= 0:
        findings.append(ValidationFinding(oid, "ERROR", "bad_entry_price", f"entry={entry}"))

    if direction == "SHORT" and entry and ev.markouts:
        m = ev.markouts[0]
        manual = directional_return("SHORT", float(entry), m.mark_price) * 100.0
        if abs(manual - m.directional_return_pct) > RETURN_TOLERANCE_PCT:
            findings.append(ValidationFinding(
                oid, "ERROR", "short_formula",
                f"markout SHORT formula mismatch manual={manual:.4f}% stored={m.directional_return_pct:.4f}%",
            ))

    if _row_get(row,"mfe_pct") is not None and stored_ret > float(row["mfe_pct"]) + IMPOSSIBLE_RETURN_BUFFER_PCT:
        findings.append(ValidationFinding(
            oid, "WARN", "impossible_vs_mfe",
            f"return {stored_ret:.3f}% > mfe {float(row['mfe_pct']):.3f}% + buffer",
        ))

    if _row_get(row,"symbol_resolve_status") != "resolved":
        findings.append(ValidationFinding(
            oid, "WARN", "symbol_mapping", f"symbol_status={_row_get(row,'symbol_resolve_status')}",
        ))

    levels = _load_levels(conn, int(row["thesis_id"]))
    for tp in levels.get("TARGET", []):
        if entry and float(entry) > 0:
            ratio = tp / float(entry)
            if ratio > 50 or ratio < 0.02:
                findings.append(ValidationFinding(
                    oid, "WARN", "target_units",
                    f"target/entry ratio {ratio:.4f} suggests unit error (target={tp}, entry={entry})",
                ))
                break

    if _row_get(row,"entry_mode") == "MARKET_ENTRY" and _row_get(row,"entry_ts") and _row_get(row,"decision_ts"):
        lag = int(row["entry_ts"]) - int(row["decision_ts"])
        if lag < 0:
            findings.append(ValidationFinding(
                oid, "ERROR", "market_entry_leakage", f"entry_ts before decision_ts lag={lag}s",
            ))

    if candle_provider and _row_get(row,"exchange_symbol"):
        try:
            start_ts = int(row["decision_ts"])
            end_ts = required_end_ts(start_ts)
            candles, meta = candle_provider.fetch_range(
                str(row["exchange_symbol"]), start_ts, end_ts,
            )
            if meta.get("gap_count", 0) > 10:
                findings.append(ValidationFinding(
                    oid, "WARN", "bad_candles", f"gap_count={meta.get('gap_count')}",
                ))
            if candles and _row_get(row,"entry_ts"):
                entry_ts = int(row["entry_ts"])
                post = [c for c in candles if c.open_ts >= entry_ts]
                if post and _row_get(row,"stop_price") is not None:
                    max_move = max(
                        directional_return(direction, float(entry), c.high if direction == "LONG" else c.low)
                        for c in post[:60]
                    ) * 100.0
                    if stored_ret > max_move + 5.0 and policy == "P1":
                        findings.append(ValidationFinding(
                            oid, "WARN", "candle_path_bounds",
                            f"return {stored_ret:.2f}% exceeds 60m path max {max_move:.2f}%",
                        ))
        except Exception as exc:
            findings.append(ValidationFinding(oid, "INFO", "candle_check_skipped", str(exc)))

    return findings


def _format_trade_line(t: TradeRecord) -> str:
    hold = f"{t.holding_seconds}s" if t.holding_seconds is not None else "n/a"
    return (
        f"id={t.outcome_id} {t.symbol} {t.direction} ts={_iso_ts(t.decision_ts)} "
        f"entry_mode={t.entry_mode} stop_mode={t.stop_mode} "
        f"entry={t.entry_price} exit={t.exit_price} ret={t.return_pct:+.3f}% "
        f"hold={hold} exit={t.exit_reason}"
    )


def _cohort_stats(trades: list[TradeRecord]) -> dict[str, Any]:
    rets = [t.return_pct for t in trades]
    if not rets:
        return {"n": 0}
    cs = _compute_stats(rets)
    return {
        "n": cs.n,
        "mean_return": cs.mean_return,
        "median_return": cs.median_return,
        "win_rate": cs.win_rate,
        "sample_label": cs.sample_label,
    }


def _rolling_6m_cohorts(trades: list[TradeRecord]) -> list[dict[str, Any]]:
    if not trades:
        return []
    sorted_t = sorted(trades, key=lambda t: t.decision_ts)
    buckets: dict[str, list[float]] = defaultdict(list)
    for t in sorted_t:
        dt = datetime.fromtimestamp(t.decision_ts, tz=timezone.utc)
        half = 1 if dt.month <= 6 else 2
        key = f"{dt.year}-H{half}"
        buckets[key].append(t.return_pct)
    return [
        {
            "cohort": key,
            "n": len(rets),
            "mean_return": statistics.mean(rets),
            "win_rate": sum(1 for r in rets if r > 0) / len(rets),
            "sample_label": _sample_label(len(rets)),
        }
        for key, rets in sorted(buckets.items())
    ]


def _rolling_3m_cohorts(trades: list[TradeRecord]) -> list[dict[str, Any]]:
    if not trades:
        return []
    sorted_t = sorted(trades, key=lambda t: t.decision_ts)
    buckets: dict[str, list[float]] = defaultdict(list)
    for t in sorted_t:
        dt = datetime.fromtimestamp(t.decision_ts, tz=timezone.utc)
        key = f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"
        buckets[key].append(t.return_pct)
    return [
        {
            "cohort": key,
            "n": len(rets),
            "mean_return": statistics.mean(rets),
            "win_rate": sum(1 for r in rets if r > 0) / len(rets),
            "sample_label": _sample_label(len(rets)),
        }
        for key, rets in sorted(buckets.items())
    ]


def run_outlier_audit(
    conn: Any,
    *,
    channel: str = "signalyp",
    engine_version: str = ENGINE_VERSION,
    policy: str = "P1",
    validate_top_n: int = 25,
    candle_provider: Any | None = None,
) -> OutlierAuditReport:
    report = OutlierAuditReport(
        channel=channel,
        engine_version=engine_version,
        policy=policy,
    )
    rows = _load_outcomes(conn, channel=channel, engine_version=engine_version)
    entered = [r for r in rows if r["entry_status"] == "ENTERED"]
    trades = [t for r in entered if (t := build_trade_record(r, policy)) is not None]
    report.trade_count = len(trades)
    if not trades:
        report.verdict = "INSUFFICIENT_DATA"
        report.verdict_reasons.append("No entered trades with policy returns")
        return report

    returns = [t.return_pct for t in trades]
    rs = compute_robust_stats(returns)
    report.robust_stats = {
        "arithmetic_mean": rs.arithmetic_mean,
        "median": rs.median,
        "geometric_mean": rs.geometric_mean,
        "trimmed_means": rs.trimmed_means,
        "winsorized_means": rs.winsorized_means,
        "mean_after_top_trim": rs.mean_after_top_trim,
        "top_contribution_pct": rs.top_contribution_pct,
        "bootstrap_mean_ci": rs.bootstrap_mean_ci,
    }

    sorted_trades = sorted(trades, key=lambda t: t.return_pct, reverse=True)
    bottom_trades = sorted(trades, key=lambda t: t.return_pct)

    attribution: dict[str, Any] = {"policies": {}}
    for pol in ATTRIBUTION_POLICIES:
        pol_trades = [t for r in entered if (t := build_trade_record(r, pol)) is not None]
        pol_returns = [t.return_pct for t in pol_trades]
        pol_rs = compute_robust_stats(pol_returns)
        pol_sorted = sorted(pol_trades, key=lambda t: t.return_pct, reverse=True)
        pol_bottom = sorted(pol_trades, key=lambda t: t.return_pct)
        attribution["policies"][pol] = {
            "n": len(pol_trades),
            "robust": {
                "arithmetic_mean": pol_rs.arithmetic_mean,
                "median": pol_rs.median,
                "geometric_mean": pol_rs.geometric_mean,
                "trimmed_means": pol_rs.trimmed_means,
                "winsorized_means": pol_rs.winsorized_means,
                "mean_after_top_trim": pol_rs.mean_after_top_trim,
                "top_contribution_pct": pol_rs.top_contribution_pct,
            },
            "top_10": [_format_trade_line(t) for t in pol_sorted[:10]],
            "top_25": [_format_trade_line(t) for t in pol_sorted[:25]],
            "bottom_10": [_format_trade_line(t) for t in pol_bottom[:10]],
            "bottom_25": [_format_trade_line(t) for t in pol_bottom[:25]],
        }
    report.attribution = attribution

    provider = candle_provider or CachedCandleProvider(conn)
    outliers = sorted_trades[:validate_top_n] + bottom_trades[:validate_top_n]
    seen: set[int] = set()
    for t in outliers:
        if t.outcome_id in seen:
            continue
        seen.add(t.outcome_id)
        row = next(r for r in entered if int(r["id"]) == t.outcome_id)
        report.validation_findings.extend(
            validate_trade_math(conn, row, policy=policy, candle_provider=provider),
        )

    trade_dicts = [_trade_to_dict(t) for t in trades]
    report.economic_scenarios = {
        "cost_grid": [
            {
                "fee_bps": c.fee_bps,
                "slippage_bps": c.slippage_bps,
                "mean_return": c.mean_return,
                "median_return": c.median_return,
                "win_rate": c.win_rate,
            }
            for c in compute_cost_scenarios(returns)
        ],
        "execution_delay_haircut": compute_delay_scenarios(returns),
        "missed_entry_sensitivity": {
            f"delay_{d}m": compute_missed_entry_sensitivity(trade_dicts, delay_min=d)
            for d in (1, 3, 5)
        },
        "portfolio_fixed_risk": {
            "final_equity": simulate_portfolio_equity(trade_dicts, sizing="fixed_risk").final_equity,
            "max_drawdown_pct": simulate_portfolio_equity(trade_dicts, sizing="fixed_risk").max_drawdown_pct,
        },
        "portfolio_equal_weight": {
            "final_equity": simulate_portfolio_equity(trade_dicts, sizing="equal_weight").final_equity,
            "max_drawdown_pct": simulate_portfolio_equity(trade_dicts, sizing="equal_weight").max_drawdown_pct,
        },
        "default_cost_mean": statistics.mean([
            apply_round_trip_cost(r, fee_bps=DEFAULT_COST_FEE_BPS, slippage_bps=DEFAULT_COST_SLIPPAGE_BPS)
            for r in returns
        ]),
    }

    def _by(predicate):
        return _cohort_stats([t for t in trades if predicate(t)])

    april_2025 = [t for t in trades if _iso_ts(t.decision_ts).startswith("2025-04")]
    q3_trades = [
        t for t in trades
        if datetime.fromtimestamp(t.decision_ts, tz=timezone.utc).year == 2025
        and (datetime.fromtimestamp(t.decision_ts, tz=timezone.utc).month - 1) // 3 + 1 == 3
    ]

    def _exclude_top_pct(pct: float) -> dict[str, Any]:
        k = max(1, int(math.ceil(len(trades) * pct)))
        excluded = {t.outcome_id for t in sorted_trades[:k]}
        remaining = [t for t in trades if t.outcome_id not in excluded]
        return _cohort_stats(remaining)

    stability: dict[str, Any] = {
        "LONG": _by(lambda t: t.direction == "LONG"),
        "SHORT": _by(lambda t: t.direction == "SHORT"),
        "MARKET_ENTRY": _by(lambda t: t.entry_mode == "MARKET_ENTRY"),
        "NUMERIC_ZONE": _by(lambda t: t.entry_mode == "NUMERIC_ZONE"),
        "NUMERIC_STOP": _by(lambda t: t.stop_mode == "NUMERIC_STOP"),
        "DEFERRED_STOP_EXCLUDED": _by(lambda t: t.stop_mode != "DEFERRED_STOP"),
        "year_2024": _by(lambda t: datetime.fromtimestamp(t.decision_ts, tz=timezone.utc).year == 2024),
        "year_2025": _by(lambda t: datetime.fromtimestamp(t.decision_ts, tz=timezone.utc).year == 2025),
        "year_2026": _by(lambda t: datetime.fromtimestamp(t.decision_ts, tz=timezone.utc).year == 2026),
        "exclude_april_2025": _by(lambda t: not _iso_ts(t.decision_ts).startswith("2025-04")),
        "exclude_top_1pct": _exclude_top_pct(0.01),
        "exclude_top_2pct": _exclude_top_pct(0.02),
        "exclude_top_5pct": _exclude_top_pct(0.05),
        "rolling_3m": _rolling_3m_cohorts(trades),
        "rolling_6m": _rolling_6m_cohorts(trades),
    }
    for n in RECENT_SIGNAL_WINDOWS:
        recent = sorted(trades, key=lambda t: t.decision_ts)[-n:]
        stability[f"last_{n}"] = _cohort_stats(recent)
    report.stability = stability

    report.cohort_drilldowns = {
        "april_2025": {
            "summary": _cohort_stats(april_2025),
            "trades": [_format_trade_line(t) for t in sorted(april_2025, key=lambda t: -t.return_pct)],
        },
        "q3_2025": {
            "summary": _cohort_stats(q3_trades),
            "trades": [_format_trade_line(t) for t in sorted(q3_trades, key=lambda t: -t.return_pct)],
        },
    }

    complete = sum(1 for r in entered if r["data_quality_status"] == "COMPLETE")
    complete_pct = complete / len(entered) if entered else 0.0
    rolling_3m = stability.get("rolling_3m", [])
    rolling_3m_pos = sum(1 for w in rolling_3m if w.get("mean_return", 0) > 0 and w.get("n", 0) >= 10)

    years: dict[int, list[float]] = defaultdict(list)
    for t in trades:
        years[datetime.fromtimestamp(t.decision_ts, tz=timezone.utc).year].append(t.return_pct)
    oos_years_total = sum(1 for y, rets in years.items() if len(rets) >= 10)
    oos_years_pos = sum(1 for y, rets in years.items() if len(rets) >= 10 and statistics.mean(rets) > 0)

    n = len(trades)
    quarter = max(1, n // 4)
    sorted_t = sorted(trades, key=lambda t: t.decision_ts)
    rolling_blocks_pos = 0
    rolling_blocks_total = 0
    for i in range(4):
        chunk = sorted_t[i * quarter:(i + 1) * quarter if i < 3 else n]
        if len(chunk) < 10:
            continue
        rolling_blocks_total += 1
        if statistics.mean([t.return_pct for t in chunk]) > 0:
            rolling_blocks_pos += 1

    recent_250 = stability.get("last_250", {})
    port = simulate_portfolio_equity(trade_dicts, sizing="fixed_risk")

    verdict_inp = VerdictInputs(
        entered_n=len(trades),
        complete_data_pct=complete_pct,
        headline_mean=rs.arithmetic_mean,
        bootstrap_mean_ci=rs.bootstrap_mean_ci,
        winsorized_5pct_mean=rs.winsorized_means.get("5pct"),
        top10_concentration=rs.top_contribution_pct.get("top_10"),
        top25_concentration=rs.top_contribution_pct.get("top_25"),
        cost_adjusted_mean=report.economic_scenarios.get("default_cost_mean"),
        risk_sized_max_dd=port.max_drawdown_pct,
        oos_years_positive=oos_years_pos,
        oos_years_total=oos_years_total,
        rolling_blocks_positive=rolling_blocks_pos,
        rolling_blocks_total=rolling_blocks_total,
        rolling_3m_positive=rolling_3m_pos,
        rolling_3m_total=sum(1 for w in rolling_3m if w.get("n", 0) >= 10),
        recent_250_mean=recent_250.get("mean_return"),
        p4_mean=_compute_stats([r for r in (_policy_return(row, "P4") for row in entered) if r is not None]).mean_return,
        p5_mean=_compute_stats([r for r in (_policy_return(row, "P5") for row in entered) if r is not None]).mean_return,
        p6_mean=_compute_stats([r for r in (_policy_return(row, "P6") for row in entered) if r is not None]).mean_return,
    )
    report.verdict, report.verdict_reasons = compute_research_verdict(verdict_inp)
    return report


def render_outlier_audit(report: OutlierAuditReport) -> str:
    lines = [
        "PHASE D.1.1 OUTLIER & ROBUSTNESS AUDIT (read-only)",
        f"channel: {report.channel}",
        f"engine_version: {report.engine_version}",
        f"headline_policy: {report.policy}",
        f"entered_trades: {report.trade_count:,}",
        "",
        "=== A. ROBUST STATISTICS ===",
    ]
    rs = report.robust_stats
    lines.append(f"  arithmetic_mean: {rs.get('arithmetic_mean', 0):.3f}%")
    lines.append(f"  median: {rs.get('median', 0):.3f}%")
    if rs.get("geometric_mean") is not None:
        lines.append(f"  geometric_mean: {rs['geometric_mean']:.3f}%")
    if rs.get("bootstrap_mean_ci"):
        lo, hi = rs["bootstrap_mean_ci"]
        lines.append(f"  bootstrap_mean_ci: [{lo:.3f}%, {hi:.3f}%]")
    for label, data in (
        ("top_contribution", rs.get("top_contribution_pct", {})),
        ("winsorized_mean", rs.get("winsorized_means", {})),
        ("trimmed_mean", rs.get("trimmed_means", {})),
        ("mean_after_removing_top", rs.get("mean_after_top_trim", {})),
    ):
        if data:
            lines.append(f"  {label}:")
            for k, v in data.items():
                lines.append(f"    {k}: {v:.3f}%" if isinstance(v, float) else f"    {k}: {v}")

    lines.extend(["", "=== A. OUTLIER ATTRIBUTION (P1/P2/P3) ==="])
    for pol, data in report.attribution.get("policies", {}).items():
        lines.append(f"  --- {pol} N={data.get('n', 0)} mean={data['robust']['arithmetic_mean']:.3f}% ---")
        for section in ("top_10", "bottom_10"):
            rows = data.get(section, [])
            if rows:
                lines.append(f"    {section}:")
                for row in rows[:10]:
                    lines.append(f"      {row}")

    lines.extend(["", "=== B. RETURN MATH VALIDATION (top/bottom outliers) ==="])
    if not report.validation_findings:
        lines.append("  No validation issues detected in audited outliers.")
    else:
        by_sev: dict[str, list[ValidationFinding]] = defaultdict(list)
        for f in report.validation_findings:
            by_sev[f.severity].append(f)
        for sev in ("ERROR", "WARN", "INFO"):
            for f in by_sev.get(sev, []):
                lines.append(f"  [{sev}] outcome={f.outcome_id} {f.category}: {f.detail}")

    lines.extend(["", "=== C. ECONOMIC REALISM (scenarios, outcomes unchanged) ==="])
    for row in report.economic_scenarios.get("cost_grid", []):
        lines.append(
            f"  fee={row['fee_bps']}bps slip={row['slippage_bps']}bps "
            f"mean={row['mean_return']:.3f}% median={row['median_return']:.3f}% win={row['win_rate']:.1%}",
        )
    pf = report.economic_scenarios.get("portfolio_fixed_risk", {})
    lines.append(
        f"  fixed_risk portfolio: equity={pf.get('final_equity', 0):.4f} "
        f"max_dd={pf.get('max_drawdown_pct', 0):.2f}%",
    )
    lines.append(
        f"  default_cost_mean ({DEFAULT_COST_FEE_BPS}+{DEFAULT_COST_SLIPPAGE_BPS}bps): "
        f"{report.economic_scenarios.get('default_cost_mean', 0):.3f}%",
    )
    delay = report.economic_scenarios.get("execution_delay_haircut", {})
    if delay:
        lines.append("  execution_delay_haircut (conservative, outcomes unchanged):")
        for k, v in delay.items():
            lines.append(f"    {k}: mean={v:.3f}%")
    missed = report.economic_scenarios.get("missed_entry_sensitivity", {})
    if missed:
        lines.append("  missed_entry_sensitivity (market-entry cohort):")
        for k, v in missed.items():
            lines.append(
                f"    {k}: market_n={v.get('market_entry_n', 0)} "
                f"miss_rate={v.get('miss_rate', 0):.1%}",
            )

    lines.extend(["", "=== D. STABILITY COHORTS ==="])
    for key, stats in report.stability.items():
        if key in ("rolling_3m", "rolling_6m"):
            lines.append(f"  {key}:")
            for w in stats:
                lines.append(
                    f"    {w['cohort']}: N={w['n']} mean={w['mean_return']:.3f}% win={w['win_rate']:.1%}",
                )
            continue
        if isinstance(stats, dict) and stats.get("n"):
            lines.append(
                f"  {key}: N={stats['n']} mean={stats.get('mean_return', 0):.3f}% "
                f"median={stats.get('median_return', 0):.3f}% [{stats.get('sample_label', '')}]",
            )

    for cohort_name in ("april_2025", "q3_2025"):
        drill = report.cohort_drilldowns.get(cohort_name, {})
        summary = drill.get("summary", {})
        lines.extend(["", f"=== D. DRILLDOWN {cohort_name.upper()} ==="])
        lines.append(
            f"  N={summary.get('n', 0)} mean={summary.get('mean_return', 0):.3f}% "
            f"median={summary.get('median_return', 0):.3f}%",
        )
        for row in drill.get("trades", [])[:30]:
            lines.append(f"    {row}")

    lines.extend([
        "",
        "=== E. ROBUSTNESS VERDICT ===",
        f"  {report.verdict}",
    ])
    for r in report.verdict_reasons:
        lines.append(f"    - {r}")
    lines.extend([
        "",
        "Read-only audit. Historical outcomes not modified.",
        "Do not promote to live or paper trading automatically.",
    ])
    return "\n".join(lines)
