"""Deterministic path evaluation for EXPLICIT_SIGNAL historical outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bot.research.futures_agent.historical_candles import Candle
from bot.research.futures_agent.signal_level_extract import (
    ParsedSignalLevels,
    extract_levels_for_content_type,
)
from bot.research.futures_agent.signal_outcome_constants import (
    ENTRY_TTL_SEC,
    INTERVAL_1M_SEC,
    MARKOUT_HORIZONS_SEC,
)


@dataclass
class TargetLevel:
    price: float
    ordinal: int
    validity: str  # VALID | DIRECTION_INCONSISTENT | AMBIGUOUS


@dataclass
class SignalEvent:
    event_type: str
    event_ts: int
    event_price: float
    candle_open_ts: int
    target_index: int | None = None
    ambiguity_flag: int = 0


@dataclass
class MarkoutPoint:
    horizon: str
    horizon_seconds: int
    mark_ts: int
    mark_price: float
    directional_return_pct: float
    mfe_pct: float
    mae_pct: float


@dataclass
class PathEvaluation:
    decision_ts: int
    direction: str
    entry_mode: str
    entry_status: str
    entry_ts: int | None
    entry_price: float | None
    entry_fill_model: str
    stop_mode: str
    stop_price: float | None
    targets: list[TargetLevel]
    events: list[SignalEvent] = field(default_factory=list)
    markouts: list[MarkoutPoint] = field(default_factory=list)
    outcome_status: str = "PENDING"
    first_terminal_event: str | None = None
    first_terminal_ts: int | None = None
    max_target_reached: int = 0
    mfe_pct: float | None = None
    mae_pct: float | None = None
    ambiguous_intrabar: int = 0
    conservative_terminal: str | None = None
    optimistic_terminal: str | None = None
    data_quality_status: str = "UNKNOWN"
    raw_return_pct: float | None = None


def directional_return(direction: str, entry: float, px: float) -> float:
    if entry <= 0 or px <= 0:
        return 0.0
    if direction == "SHORT":
        return (entry / px) - 1.0
    return (px / entry) - 1.0


def _zone_touched(c: Candle, entry_low: float, entry_high: float) -> bool:
    lo, hi = min(entry_low, entry_high), max(entry_low, entry_high)
    return c.low <= hi and c.high >= lo


def _conservative_zone_fill(direction: str, entry_low: float, entry_high: float) -> float:
    lo, hi = min(entry_low, entry_high), max(entry_low, entry_high)
    return hi if direction == "LONG" else lo


def _classify_targets(
    direction: str,
    entry_price: float | None,
    targets: list[tuple[float, int]],
) -> list[TargetLevel]:
    out: list[TargetLevel] = []
    for price, ordinal in targets:
        validity = "VALID"
        if entry_price is not None:
            if direction == "LONG" and price <= entry_price:
                validity = "DIRECTION_INCONSISTENT"
            elif direction == "SHORT" and price >= entry_price:
                validity = "DIRECTION_INCONSISTENT"
        out.append(TargetLevel(price=price, ordinal=ordinal, validity=validity))
    return out


def _infer_entry_stop_modes(
    levels: dict[str, list[float]],
    parsed: ParsedSignalLevels,
) -> tuple[str, str, float | None, float | None, str]:
    entry_low = levels.get("ENTRY_LOW", [None])[0] if levels.get("ENTRY_LOW") else None
    entry_high = levels.get("ENTRY_HIGH", [None])[0] if levels.get("ENTRY_HIGH") else None
    stop = levels.get("STOP", [None])[0] if levels.get("STOP") else parsed.stop

    if entry_low is not None or entry_high is not None:
        if entry_low is None:
            entry_low = entry_high
        if entry_high is None:
            entry_high = entry_low
        entry_mode = "NUMERIC_ZONE"
    elif parsed.entry_status == "market":
        entry_mode = "MARKET_ENTRY"
    else:
        entry_mode = "NO_VALID_ENTRY"

    if stop is not None:
        stop_mode = "NUMERIC_STOP"
    elif parsed.stop_status == "deferred":
        stop_mode = "DEFERRED_STOP"
    else:
        stop_mode = "MISSING_STOP"
    return entry_mode, stop_mode, entry_low, entry_high, stop


def find_market_entry(candles: list[Candle], decision_ts: int) -> tuple[int | None, float | None]:
    """First full 1m candle open strictly after decision_ts."""
    for c in candles:
        if c.open_ts > decision_ts:
            return c.open_ts, c.open
    return None, None


def find_numeric_entry(
    candles: list[Candle],
    decision_ts: int,
    entry_low: float,
    entry_high: float,
    direction: str,
    *,
    ttl_sec: int = ENTRY_TTL_SEC,
) -> tuple[int | None, float | None, str]:
    deadline = decision_ts + ttl_sec
    for c in candles:
        if c.open_ts < decision_ts:
            continue
        if c.open_ts > deadline:
            break
        if _zone_touched(c, entry_low, entry_high):
            fill = _conservative_zone_fill(direction, entry_low, entry_high)
            return c.open_ts, fill, "conservative_boundary"
    return None, None, "conservative_boundary"


def _stop_hit(direction: str, c: Candle, stop: float) -> bool:
    if direction == "LONG":
        return c.low <= stop
    return c.high >= stop


def _target_hit(direction: str, c: Candle, target: float) -> bool:
    if direction == "LONG":
        return c.high >= target
    return c.low <= target


def _same_candle_stop_and_target(
    direction: str, c: Candle, stop: float | None, targets: list[TargetLevel],
) -> bool:
    if stop is None:
        return False
    stop_t = _stop_hit(direction, c, stop)
    if not stop_t:
        return False
    for t in targets:
        if t.validity == "VALID" and _target_hit(direction, c, t.price):
            return True
    return False


def evaluate_signal_path(
    *,
    decision_ts: int,
    direction: str,
    levels: dict[str, list[float]],
    raw_text: str,
    candles: list[Candle],
    data_quality: str = "COMPLETE",
) -> PathEvaluation:
    parsed = extract_levels_for_content_type(raw_text, "EXPLICIT_SIGNAL")
    entry_mode, stop_mode, entry_low, entry_high, stop = _infer_entry_stop_modes(levels, parsed)
    target_rows = sorted(
        ((float(p), int(i)) for i, p in enumerate(levels.get("TARGET", []), start=1)),
        key=lambda x: x[0],
    )

    result = PathEvaluation(
        decision_ts=decision_ts,
        direction=direction,
        entry_mode=entry_mode,
        entry_status="PENDING",
        entry_ts=None,
        entry_price=None,
        entry_fill_model="none",
        stop_mode=stop_mode,
        stop_price=float(stop) if stop is not None else None,
        targets=[],
        data_quality_status=data_quality,
    )

    if not candles:
        result.entry_status = "NO_DATA"
        result.outcome_status = "NO_DATA"
        return result

    entry_ts: int | None = None
    entry_price: float | None = None
    fill_model = "none"

    if entry_mode == "NUMERIC_ZONE":
        assert entry_low is not None and entry_high is not None
        entry_ts, entry_price, fill_model = find_numeric_entry(
            candles, decision_ts, float(entry_low), float(entry_high), direction,
        )
        if entry_ts is None:
            result.entry_status = "NOT_ENTERED"
            result.outcome_status = "NOT_ENTERED"
            result.entry_fill_model = fill_model
            return result
        result.entry_status = "ENTERED"
    elif entry_mode == "MARKET_ENTRY":
        entry_ts, entry_price = find_market_entry(candles, decision_ts)
        fill_model = "first_candle_open_after_decision"
        if entry_ts is None:
            result.entry_status = "NOT_ENTERED"
            result.outcome_status = "NOT_ENTERED"
            return result
        result.entry_status = "ENTERED"
    else:
        result.entry_status = "NOT_ENTERED"
        result.outcome_status = "NOT_ENTERED"
        return result

    result.entry_ts = entry_ts
    result.entry_price = entry_price
    result.entry_fill_model = fill_model
    result.targets = _classify_targets(direction, entry_price, target_rows)
    valid_targets = [t for t in result.targets if t.validity == "VALID"]

    events: list[SignalEvent] = [
        SignalEvent("ENTRY", entry_ts, entry_price, entry_ts, ambiguity_flag=0),
    ]

    active_candles = [c for c in candles if c.open_ts >= entry_ts]
    stop_hit_ts: int | None = None
    target_hits: dict[int, tuple[int, float]] = {}
    ambiguous = 0
    conservative_terminal: str | None = None
    optimistic_terminal: str | None = None

    for c in active_candles:
        if _same_candle_stop_and_target(direction, c, result.stop_price, valid_targets):
            ambiguous = 1
            if result.stop_price is not None and stop_hit_ts is None:
                stop_hit_ts = c.open_ts
                events.append(SignalEvent(
                    "STOP", c.open_ts, result.stop_price, c.open_ts, ambiguity_flag=1,
                ))
            for t in valid_targets:
                if t.ordinal not in target_hits and _target_hit(direction, c, t.price):
                    target_hits[t.ordinal] = (c.open_ts, t.price)
                    events.append(SignalEvent(
                        "TP", c.open_ts, t.price, c.open_ts,
                        target_index=t.ordinal, ambiguity_flag=1,
                    ))
            conservative_terminal = "STOP"
            optimistic_terminal = f"TP{min(target_hits)}" if target_hits else "STOP"
            break

        if result.stop_price is not None and stop_hit_ts is None and _stop_hit(direction, c, result.stop_price):
            stop_hit_ts = c.open_ts
            events.append(SignalEvent(
                "STOP", c.open_ts, result.stop_price, c.open_ts,
            ))
            if not target_hits:
                conservative_terminal = "STOP"
                optimistic_terminal = "STOP"
                break

        for t in valid_targets:
            if t.ordinal not in target_hits and _target_hit(direction, c, t.price):
                target_hits[t.ordinal] = (c.open_ts, t.price)
                events.append(SignalEvent(
                    "TP", c.open_ts, t.price, c.open_ts, target_index=t.ordinal,
                ))

        if stop_hit_ts is not None and target_hits:
            if stop_hit_ts <= min(ts for ts, _ in target_hits.values()):
                conservative_terminal = "STOP"
                optimistic_terminal = f"TP{min(target_hits)}"
            else:
                conservative_terminal = f"TP{min(target_hits)}"
                optimistic_terminal = conservative_terminal
            break
        if stop_hit_ts is not None:
            conservative_terminal = "STOP"
            optimistic_terminal = "STOP"
            break
        if target_hits:
            first_tp_idx = min(target_hits)
            first_tp_ts = target_hits[first_tp_idx][0]
            if stop_hit_ts is None or stop_hit_ts > first_tp_ts:
                conservative_terminal = f"TP{first_tp_idx}"
                optimistic_terminal = conservative_terminal
                break

    result.max_target_reached = max(target_hits) if target_hits else 0
    result.ambiguous_intrabar = ambiguous
    result.events = events

    if conservative_terminal is None:
        if active_candles:
            last = active_candles[-1]
            result.raw_return_pct = directional_return(direction, entry_price, last.close) * 100.0
        conservative_terminal = "TIMEOUT"
        optimistic_terminal = "TIMEOUT"
        events.append(SignalEvent(
            "TIMEOUT", active_candles[-1].open_ts if active_candles else entry_ts,
            active_candles[-1].close if active_candles else entry_price,
            active_candles[-1].open_ts if active_candles else entry_ts,
        ))

    result.conservative_terminal = conservative_terminal
    result.optimistic_terminal = optimistic_terminal
    result.first_terminal_event = conservative_terminal
    if conservative_terminal == "STOP" and stop_hit_ts is not None:
        result.first_terminal_ts = stop_hit_ts
    elif conservative_terminal.startswith("TP") and target_hits:
        idx = int(conservative_terminal[2:])
        result.first_terminal_ts = target_hits[idx][0]
    elif active_candles:
        result.first_terminal_ts = active_candles[-1].open_ts

    result.outcome_status = "ENTERED"
    if ambiguous:
        result.outcome_status = "AMBIGUOUS_INTRABAR"

    mfe = float("-inf")
    mae = float("inf")
    for c in active_candles:
        for px in (c.high, c.low, c.close):
            dr = directional_return(direction, entry_price, px)
            mfe = max(mfe, dr)
            mae = min(mae, dr)
    result.mfe_pct = mfe * 100.0 if mfe != float("-inf") else None
    result.mae_pct = mae * 100.0 if mae != float("inf") else None

    for hname, hsec in MARKOUT_HORIZONS_SEC.items():
        mark_ts = entry_ts + hsec
        path = [c for c in active_candles if c.open_ts <= mark_ts]
        if not path:
            continue
        mark_c = path[-1]
        sub_mfe = float("-inf")
        sub_mae = float("inf")
        for c in path:
            for px in (c.high, c.low):
                dr = directional_return(direction, entry_price, px)
                sub_mfe = max(sub_mfe, dr)
                sub_mae = min(sub_mae, dr)
        result.markouts.append(MarkoutPoint(
            horizon=hname,
            horizon_seconds=hsec,
            mark_ts=mark_c.open_ts,
            mark_price=mark_c.close,
            directional_return_pct=directional_return(direction, entry_price, mark_c.close) * 100.0,
            mfe_pct=sub_mfe * 100.0 if sub_mfe != float("-inf") else 0.0,
            mae_pct=sub_mae * 100.0 if sub_mae != float("inf") else 0.0,
        ))

    return result


def pre_entry_touch_ignored(
    candles: list[Candle],
    decision_ts: int,
    entry_ts: int,
    direction: str,
    stop: float | None,
    target: float,
) -> bool:
    """True when stop/target touched before numeric entry activation."""
    for c in candles:
        if c.open_ts < decision_ts:
            continue
        if c.open_ts >= entry_ts:
            break
        if stop is not None and _stop_hit(direction, c, stop):
            return True
        if _target_hit(direction, c, target):
            return True
    return False
