"""Numbered pipeline trace logs for Early Reversion entry path."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from bot.er_stats import (
    BLOCK_REASON_DUPLICATE_ENTRY,
    BLOCK_REASON_EXISTING_POSITION,
    BLOCK_REASON_LIVE_MODE,
    BLOCK_REASON_MAX_DAILY_LOSS,
    BLOCK_REASON_MAX_OPEN_POSITIONS,
    BLOCK_REASON_OTHER,
    BLOCK_REASON_UNKNOWN,
    TRACKED_VERSIONS,
    record_entry_blocked,
)

logger = logging.getLogger(__name__)

PIPELINE_LOG_WINDOW_SEC = 30

_context_window_start: int | None = None
_seconds_from_start: float | None = None
_early_window_logged: set[tuple[int, str]] = set()

# Trace from already_open_ok toward attempt_entry_open():
#  1 eval: already_open_ok
#  2 eval: price_ok
#  3 eval: risk check
#  4 eval: signal evaluation done
#  5 process: entry window open -> _try_open_signals
#  6 process: outside entry window -> skip _try_open_signals
#  7 try_open: signal loop iteration
#  8 try_open: no active signals (return)
#  9 try_open: continue (has_trade)
# 10 try_open: continue (ask fail)
# 11 try_open: immediately before attempt_entry_open()
# 12 execution: attempt_entry_open() entered

STOP_MAX_OPEN = "max_open_positions"
STOP_DAILY_LOSS = "daily_loss"
STOP_DUPLICATE = "duplicate_entry"
STOP_HAS_TRADE = "has_trade"
STOP_ASK_CHANGED = "ask_changed"
STOP_ASK_MISSING = "ask_missing"
STOP_NO_ACTIVE_SIGNALS = "no_active_signals"
STOP_OUTSIDE_WINDOW = "outside_window"
STOP_NO_SIGNAL = "no_signal"
STOP_LIVE_MODE = "live_mode"
STOP_OTHER = "other"
STOP_UNKNOWN = "unknown"
STOP_ENTRY_FAILED = "entry_failed"

_PIPELINE_STOP_TO_BLOCK = {
    STOP_MAX_OPEN: BLOCK_REASON_MAX_OPEN_POSITIONS,
    STOP_DAILY_LOSS: BLOCK_REASON_MAX_DAILY_LOSS,
    STOP_DUPLICATE: BLOCK_REASON_DUPLICATE_ENTRY,
    STOP_HAS_TRADE: BLOCK_REASON_EXISTING_POSITION,
    STOP_ASK_CHANGED: BLOCK_REASON_OTHER,
    STOP_ASK_MISSING: BLOCK_REASON_OTHER,
    STOP_NO_ACTIVE_SIGNALS: BLOCK_REASON_UNKNOWN,
    STOP_OUTSIDE_WINDOW: BLOCK_REASON_UNKNOWN,
    STOP_NO_SIGNAL: BLOCK_REASON_UNKNOWN,
    STOP_LIVE_MODE: BLOCK_REASON_LIVE_MODE,
    STOP_OTHER: BLOCK_REASON_OTHER,
    STOP_UNKNOWN: BLOCK_REASON_UNKNOWN,
    STOP_ENTRY_FAILED: BLOCK_REASON_UNKNOWN,
}


def set_pipeline_log_context(*, window_start_ts: int, seconds_from_start: float) -> None:
    global _context_window_start, _seconds_from_start
    if _context_window_start != window_start_ts:
        _early_window_logged.clear()
        _context_window_start = window_start_ts
    _seconds_from_start = seconds_from_start


def pipeline_logs_enabled() -> bool:
    if _seconds_from_start is None:
        return False
    return _seconds_from_start <= PIPELINE_LOG_WINDOW_SEC


def log_early_window_once(
    *,
    strategy_name: str,
    ask: float | None,
    seconds_from_start: float,
) -> None:
    if not pipeline_logs_enabled() or _context_window_start is None:
        return
    key = (_context_window_start, strategy_name)
    if key in _early_window_logged:
        return
    _early_window_logged.add(key)
    ask_label = "-" if ask is None else f"{ask:.4f}"
    logger.info(
        "EARLY WINDOW | seconds=%.0f strategy=%s ask=%s",
        seconds_from_start,
        strategy_name,
        ask_label,
    )


def log_pipeline(
    step: int,
    *,
    strategy: str | None = None,
    detail: str | None = None,
) -> None:
    if not pipeline_logs_enabled():
        return
    parts = [f"PIPELINE {step}"]
    if strategy is not None:
        parts.append(f"strategy={strategy}")
    if detail is not None:
        parts.append(detail)
    logger.info(" ".join(parts))


def log_pipeline_stop(
    reason: str,
    *,
    strategy: str | None = None,
    detail: str | None = None,
) -> None:
    if not pipeline_logs_enabled():
        return
    lines = ["PIPELINE STOP", "", f"reason={reason}"]
    if strategy is not None:
        lines.extend(["", f"strategy={strategy}"])
    if detail is not None:
        lines.extend(["", detail])
    logger.info("\n".join(lines))


def diagnostic_passed_pre_risk(diagnostic: Any) -> bool:
    labels = {condition.label: condition.passed for condition in diagnostic.conditions}
    return (
        labels.get("seconds_from_start", False)
        and labels.get("already_open", False)
        and labels.get("ask", False)
    )


def diagnostic_risk_failed(diagnostic: Any) -> bool:
    for condition in diagnostic.conditions:
        if condition.label == "risk":
            return not condition.passed
    return False


def should_increment_pipeline_stop(diagnostic: Any | None) -> bool:
    """Skip counter when eval already recorded a risk block for this cycle."""
    if diagnostic is None:
        return True
    if not diagnostic_passed_pre_risk(diagnostic):
        return False
    return not diagnostic_risk_failed(diagnostic)


def pipeline_stop(
    conn: sqlite3.Connection | None,
    *,
    strategy_version: str,
    strategy_name: str | None,
    reason: str,
    detail: str | None = None,
    increment_counter: bool = True,
) -> None:
    log_pipeline_stop(reason, strategy=strategy_name, detail=detail)
    if not increment_counter or conn is None or strategy_name is None:
        return
    if strategy_version not in TRACKED_VERSIONS:
        return
    block_code = _PIPELINE_STOP_TO_BLOCK.get(reason, BLOCK_REASON_UNKNOWN)
    record_entry_blocked(
        conn,
        strategy_version,
        strategy_name,
        block_code,
        increment_counter=True,
        log_event=False,
    )


def stop_reason_for_block(block_code: str) -> str:
    for stop_reason, mapped in _PIPELINE_STOP_TO_BLOCK.items():
        if mapped == block_code:
            return stop_reason
    return STOP_UNKNOWN


def pipeline_stop_for_diagnostic(
    conn: sqlite3.Connection,
    *,
    strategy_version: str,
    diagnostic: Any | None,
    strategy_name: str,
    reason: str,
    detail: str | None = None,
) -> None:
    pipeline_stop(
        conn,
        strategy_version=strategy_version,
        strategy_name=strategy_name,
        reason=reason,
        detail=detail,
        increment_counter=should_increment_pipeline_stop(diagnostic),
    )


def log_try_open_skipped_outside_window(
    conn: sqlite3.Connection,
    *,
    strategy_version: str,
    diagnostics: list[Any],
) -> None:
    for diagnostic in diagnostics:
        if not diagnostic_passed_pre_risk(diagnostic):
            continue
        pipeline_stop_for_diagnostic(
            conn,
            strategy_version=strategy_version,
            diagnostic=diagnostic,
            strategy_name=diagnostic.strategy_name,
            reason=STOP_OUTSIDE_WINDOW,
        )
