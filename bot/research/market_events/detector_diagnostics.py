"""Production SHOCK_A-E detector rejection diagnostics — additive, thresholds unchanged."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.config import SHOCK_THRESHOLDS
from bot.research.market_events.event_types import DETECTOR_IDS
from bot.research.market_events.price_feed import SymbolPriceState

logger = logging.getLogger(__name__)


def _log_reject(
    *,
    symbol: str,
    detector_id: str,
    reason: str,
    return_pct: float | None,
    threshold_pct: float,
    window_sec: int,
) -> None:
    """Log actual move vs threshold for a rejected detector evaluation."""
    ret_s = "n/a" if return_pct is None else f"{return_pct:+.4f}"
    logger.debug(
        "shock reject %s %s reason=%s return_pct=%s threshold_pct=%.4f window=%ss",
        symbol,
        detector_id,
        reason,
        ret_s,
        threshold_pct,
        window_sec,
    )

REJECTION_INSUFFICIENT_HISTORY = "insufficient_history"
REJECTION_BELOW_RETURN = "below_return_threshold"
REJECTION_VOLUME_FAILED = "volume_condition_failed"
REJECTION_RELATIVE_FAILED = "relative_move_failed"
REJECTION_STALE_QUOTE = "stale_quote"
REJECTION_SESSION_BLOCKED = "session_blocked"
REJECTION_COOLDOWN = "cooldown"
REJECTION_DUPLICATE = "duplicate_event"
REJECTION_FIRED = "fired"

ALL_REJECTION_REASONS = (
    REJECTION_INSUFFICIENT_HISTORY,
    REJECTION_BELOW_RETURN,
    REJECTION_VOLUME_FAILED,
    REJECTION_RELATIVE_FAILED,
    REJECTION_STALE_QUOTE,
    REJECTION_SESSION_BLOCKED,
    REJECTION_COOLDOWN,
    REJECTION_DUPLICATE,
    REJECTION_FIRED,
)


@dataclass
class DetectorDiagnostics:
    """Aggregated per-detector rejection counters."""

    counts: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int)),
    )
    symbol_counts: dict[str, dict[str, dict[str, int]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int))),
    )

    def record(self, detector_id: str, reason: str, *, symbol: str | None = None, n: int = 1) -> None:
        self.counts[detector_id][reason] += n
        if symbol:
            self.symbol_counts[symbol][detector_id][reason] += n

    def merge(self, other: DetectorDiagnostics) -> None:
        for det, reasons in other.counts.items():
            for reason, n in reasons.items():
                self.counts[det][reason] += n
        for sym, dets in other.symbol_counts.items():
            for det, reasons in dets.items():
                for reason, n in reasons.items():
                    self.symbol_counts[sym][det][reason] += n

    def total_evaluations(self) -> int:
        return sum(sum(r.values()) for r in self.counts.values())

    def format_summary(self) -> list[str]:
        lines: list[str] = []
        for det_id in DETECTOR_IDS:
            reasons = self.counts.get(det_id, {})
            if not reasons:
                continue
            parts = ", ".join(f"{k}={v}" for k, v in sorted(reasons.items()))
            lines.append(f"  {det_id}: {parts}")
        return lines


def diagnose_detectors_for_state(
    state: SymbolPriceState,
    *,
    now_ts: int,
    btc_state: SymbolPriceState | None = None,
    median_market_return: float | None = None,
    symbol: str | None = None,
) -> DetectorDiagnostics:
    """Mirror production detector logic; record rejection reasons without firing events."""
    diag = DetectorDiagnostics()
    sym = symbol or state.symbol

    for det_id, cfg in SHOCK_THRESHOLDS.items():
        window = int(cfg["window_sec"])
        ret = state.return_over(window, now_ts)
        if ret is None:
            diag.record(det_id, REJECTION_INSUFFICIENT_HISTORY, symbol=sym)
            continue

        vol_z = state.volume_zscore(window, now_ts)
        btc_ret = btc_state.return_over(window, now_ts) if btc_state else None
        rel_ret = (ret - btc_ret) if btc_ret is not None else None

        if det_id in ("SHOCK_A", "SHOCK_B", "SHOCK_C"):
            thr = float(cfg["min_abs_return_pct"])
            if abs(ret) >= thr:
                diag.record(det_id, REJECTION_FIRED, symbol=sym)
            else:
                diag.record(det_id, REJECTION_BELOW_RETURN, symbol=sym)
                _log_reject(
                    symbol=sym,
                    detector_id=det_id,
                    reason=REJECTION_BELOW_RETURN,
                    return_pct=ret,
                    threshold_pct=thr,
                    window_sec=window,
                )
        elif det_id == "SHOCK_D":
            thr = float(cfg["min_abs_return_pct"])
            if abs(ret) < thr:
                diag.record(det_id, REJECTION_BELOW_RETURN, symbol=sym)
                _log_reject(
                    symbol=sym,
                    detector_id=det_id,
                    reason=REJECTION_BELOW_RETURN,
                    return_pct=ret,
                    threshold_pct=thr,
                    window_sec=window,
                )
            elif vol_z is None or vol_z < float(cfg["min_volume_zscore"]):
                diag.record(det_id, REJECTION_VOLUME_FAILED, symbol=sym)
            else:
                diag.record(det_id, REJECTION_FIRED, symbol=sym)
        elif det_id == "SHOCK_E":
            thr = float(cfg["min_relative_return_pct"])
            compare_ret: float | None = None
            fired = False
            if rel_ret is not None:
                compare_ret = rel_ret
                fired = abs(rel_ret) >= thr
            elif median_market_return is not None:
                compare_ret = ret - median_market_return
                fired = abs(compare_ret) >= thr
            if fired:
                diag.record(det_id, REJECTION_FIRED, symbol=sym)
            else:
                diag.record(det_id, REJECTION_RELATIVE_FAILED, symbol=sym)
                _log_reject(
                    symbol=sym,
                    detector_id=det_id,
                    reason=REJECTION_RELATIVE_FAILED,
                    return_pct=compare_ret,
                    threshold_pct=thr,
                    window_sec=window,
                )

    return diag


def diagnose_universe(
    feed: Any,
    symbols: list[str],
    *,
    now_ts: int,
    shock_allowed_fn: Any | None = None,
) -> DetectorDiagnostics:
    """Run detector diagnostics across universe; optional shock_allowed_fn(symbol, now)->(bool, reason)."""
    returns: list[float] = []
    for sym in symbols:
        st = feed.get_state(sym)
        if st:
            r = st.return_over(60, now_ts)
            if r is not None:
                returns.append(r)
    median_ret = sorted(returns)[len(returns) // 2] if returns else None
    btc = feed.get_state("BTC")

    diag = DetectorDiagnostics()
    for sym in symbols:
        state = feed.get_state(sym)
        if not state:
            for det_id in DETECTOR_IDS:
                diag.record(det_id, REJECTION_INSUFFICIENT_HISTORY, symbol=sym)
            continue

        if shock_allowed_fn:
            allowed, reason = shock_allowed_fn(sym, now_ts)
            if not allowed:
                block_reason = REJECTION_SESSION_BLOCKED
                if reason and "quote" in (reason or "").lower():
                    block_reason = REJECTION_STALE_QUOTE
                elif reason and "session" in (reason or "").lower():
                    block_reason = REJECTION_SESSION_BLOCKED
                for det_id in DETECTOR_IDS:
                    diag.record(det_id, block_reason, symbol=sym)
                continue

        sym_diag = diagnose_detectors_for_state(
            state, now_ts=now_ts, btc_state=btc,
            median_market_return=median_ret, symbol=sym,
        )
        diag.merge(sym_diag)

    return diag
