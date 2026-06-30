"""Entry diagnostics for Early Reversion v2 / v2.5 / v3."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

from bot.config import effective_entry_threshold
from bot.early_reversion import EarlyReversionSignal
from bot.er_pipeline import log_early_window_once, log_pipeline, set_pipeline_log_context
from bot.er_stats import record_entry_evaluation
from bot.risk import RiskCheckResult, check_can_open_position

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EntryCondition:
    label: str
    actual: str
    limit: str
    passed: bool


@dataclass(frozen=True)
class SignalEntryDiagnostic:
    strategy_name: str
    side: str
    conditions: tuple[EntryCondition, ...]
    would_enter: bool
    blocked_by: str | None


def _side_ask(quotes: dict[str, float | None], side: str) -> float | None:
    return quotes.get(f"{side.lower()}_ask")


def evaluate_signal_entry(
    signal: EarlyReversionSignal,
    *,
    seconds_open: float,
    entry_window_sec: int,
    quotes: dict[str, float | None],
    has_trade: bool,
    risk: RiskCheckResult | None = None,
    entry_threshold: float | None = None,
) -> SignalEntryDiagnostic:
    ask = _side_ask(quotes, signal.side)
    threshold = (
        entry_threshold
        if entry_threshold is not None
        else effective_entry_threshold(signal.entry_threshold)
    )
    conditions: list[EntryCondition] = [
        EntryCondition(
            label="seconds_from_start",
            actual=f"{seconds_open:.0f}",
            limit=f"allowed<={entry_window_sec}",
            passed=seconds_open <= entry_window_sec,
        ),
        EntryCondition(
            label="already_open",
            actual="no" if not has_trade else "yes",
            limit="no",
            passed=not has_trade,
        ),
        EntryCondition(
            label="ask",
            actual="-" if ask is None else f"{ask:.2f}",
            limit=f"max_price={threshold:.2f}",
            passed=ask is not None and ask <= threshold,
        ),
    ]

    if risk is not None and all(condition.passed for condition in conditions):
        conditions.append(
            EntryCondition(
                label="risk",
                actual=risk.reason or "ok",
                limit="allowed",
                passed=risk.allowed,
            )
        )

    blocked_by = next(
        (
            f"{condition.label} ({condition.actual} vs {condition.limit})"
            for condition in conditions
            if not condition.passed
        ),
        None,
    )

    return SignalEntryDiagnostic(
        strategy_name=signal.strategy_name,
        side=signal.side,
        conditions=tuple(conditions),
        would_enter=blocked_by is None,
        blocked_by=blocked_by,
    )


def evaluate_version_entries(
    conn: sqlite3.Connection,
    *,
    strategy_version: str,
    active_signals: tuple[EarlyReversionSignal, ...],
    market_slug: str,
    seconds_open: float,
    entry_window_sec: int,
    quotes: dict[str, float | None],
    has_trade_fn,
    btc_price: float | None = None,
    now_ts: int | None = None,
) -> list[SignalEntryDiagnostic]:
    from bot.no_c_btc_filter import resolve_no_c_entry_threshold

    diagnostics: list[SignalEntryDiagnostic] = []

    for signal in active_signals:
        has_trade = has_trade_fn(conn, market_slug, signal.strategy_name)
        ask = _side_ask(quotes, signal.side)
        threshold = resolve_no_c_entry_threshold(
            conn,
            signal.strategy_name,
            signal.entry_threshold,
            btc_price=btc_price,
            now_ts=now_ts,
        )
        precheck = evaluate_signal_entry(
            signal,
            seconds_open=seconds_open,
            entry_window_sec=entry_window_sec,
            quotes=quotes,
            has_trade=has_trade,
            entry_threshold=threshold,
        )
        window_ok = precheck.conditions[0].passed
        already_open_ok = precheck.conditions[1].passed
        price_ok = precheck.conditions[2].passed

        risk = None
        if window_ok and already_open_ok:
            log_early_window_once(
                strategy_name=signal.strategy_name,
                ask=ask,
                seconds_from_start=seconds_open,
            )
            log_pipeline(1, strategy=signal.strategy_name)
            if price_ok:
                log_pipeline(2, strategy=signal.strategy_name)
                log_pipeline(3, strategy=signal.strategy_name)
                risk = check_can_open_position(conn)
        diagnostic = evaluate_signal_entry(
            signal,
            seconds_open=seconds_open,
            entry_window_sec=entry_window_sec,
            quotes=quotes,
            has_trade=has_trade,
            risk=risk,
            entry_threshold=threshold,
        )
        record_entry_evaluation(
            conn,
            strategy_version=strategy_version,
            strategy_name=signal.strategy_name,
            ask=ask,
            entry_threshold=threshold,
            seconds_open=seconds_open,
            entry_window_sec=entry_window_sec,
            has_trade=has_trade,
            risk=risk,
            would_enter=diagnostic.would_enter,
            market_slug=market_slug,
        )
        if window_ok and already_open_ok:
            log_pipeline(4, strategy=signal.strategy_name)
        diagnostics.append(diagnostic)

    return diagnostics


def _format_condition(condition: EntryCondition) -> str:
    mark = "✓" if condition.passed else "✗"
    if condition.label == "ask":
        return f"ask={condition.actual}\n{condition.limit} {mark}"
    if condition.label == "seconds_from_start":
        return f"seconds_from_start={condition.actual}\n{condition.limit} {mark}"
    if condition.label == "already_open":
        return f"already_open={condition.actual} (need {condition.limit}) {mark}"
    if condition.label == "risk":
        return f"risk={condition.actual} {mark}"
    return f"{condition.label}={condition.actual} {mark}"


def log_version_entry_check(
    version_label: str,
    diagnostics: list[SignalEntryDiagnostic],
) -> str | None:
    """Log entry diagnostics for one ER version. Returns active signal name if any."""
    if not diagnostics:
        return None

    active_signal: str | None = None
    for diagnostic in diagnostics:
        lines = [
            f"{version_label} CHECK",
            f"strategy={diagnostic.strategy_name}",
            f"side={diagnostic.side}",
        ]
        for condition in diagnostic.conditions:
            lines.append(_format_condition(condition))
        if diagnostic.would_enter:
            active_signal = diagnostic.strategy_name
            lines.append(f"RESULT = {diagnostic.strategy_name}")
        else:
            lines.append("RESULT = NO SIGNAL")
        logger.info("\n".join(lines))

    return active_signal


def format_cycle_signal_status(signal_name: str | None) -> str:
    return signal_name if signal_name else "-"
