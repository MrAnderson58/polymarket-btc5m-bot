"""Phase E.1 shock detection — parallel fixed detector variants."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.config import SHOCK_DEDUP_WINDOW_SEC, SHOCK_THRESHOLDS
from bot.research.market_events.event_types import (
    SHOCK_DIRECTION_DOWN,
    SHOCK_DIRECTION_UP,
)
from bot.research.market_events.price_feed import BinanceFuturesPriceFeed, SymbolPriceState


@dataclass
class ShockTrigger:
    detector_id: str
    window_sec: int
    return_pct: float
    volume_zscore: float | None = None
    relative_return_pct: float | None = None


@dataclass
class ShockCandidate:
    symbol: str
    direction: str
    event_ts: int
    detected_ts: int
    triggers: list[ShockTrigger] = field(default_factory=list)
    return_pct: float = 0.0
    velocity: float = 0.0
    acceleration: float = 0.0
    volume_zscore: float | None = None
    market_return_pct: float | None = None
    btc_return_pct: float | None = None
    relative_return_pct: float | None = None
    confidence: float = 0.5
    raw_metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        bucket = self.event_ts // SHOCK_DEDUP_WINDOW_SEC
        raw = f"{self.symbol}:{self.direction}:{bucket}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def max_abs_return(self) -> float:
        if not self.triggers:
            return abs(self.return_pct)
        return max(abs(t.return_pct) for t in self.triggers)


def _direction_from_return(ret: float) -> str | None:
    if ret >= 0:
        return SHOCK_DIRECTION_UP if ret > 0 else None
    return SHOCK_DIRECTION_DOWN


def detect_shock_triggers(
    state: SymbolPriceState,
    *,
    now_ts: int,
    btc_state: SymbolPriceState | None = None,
    median_market_return: float | None = None,
) -> list[ShockTrigger]:
    triggers: list[ShockTrigger] = []
    for det_id, cfg in SHOCK_THRESHOLDS.items():
        window = int(cfg["window_sec"])
        ret = state.return_over(window, now_ts)
        if ret is None:
            continue
        vol_z = state.volume_zscore(window, now_ts)
        btc_ret = btc_state.return_over(window, now_ts) if btc_state else None
        rel_ret = (ret - btc_ret) if btc_ret is not None else None

        fired = False
        if det_id in ("SHOCK_A", "SHOCK_B", "SHOCK_C"):
            fired = abs(ret) >= float(cfg["min_abs_return_pct"])
        elif det_id == "SHOCK_D":
            fired = (
                abs(ret) >= float(cfg["min_abs_return_pct"])
                and vol_z is not None
                and vol_z >= float(cfg["min_volume_zscore"])
            )
        elif det_id == "SHOCK_E":
            if rel_ret is not None:
                fired = abs(rel_ret) >= float(cfg["min_relative_return_pct"])
            elif median_market_return is not None:
                fired = abs(ret - median_market_return) >= float(cfg["min_relative_return_pct"])

        if fired:
            direction = _direction_from_return(ret)
            if direction is None:
                continue
            triggers.append(ShockTrigger(
                detector_id=det_id,
                window_sec=window,
                return_pct=ret,
                volume_zscore=vol_z,
                relative_return_pct=rel_ret,
            ))
    return triggers


def merge_triggers_to_candidate(
    symbol: str,
    triggers: list[ShockTrigger],
    *,
    now_ts: int,
    state: SymbolPriceState,
    btc_state: SymbolPriceState | None,
    median_market_return: float | None,
) -> ShockCandidate | None:
    if not triggers:
        return None
    primary = max(triggers, key=lambda t: abs(t.return_pct))
    direction = _direction_from_return(primary.return_pct)
    if direction is None:
        return None
    short_ret = state.return_over(30, now_ts) or primary.return_pct
    long_ret = state.return_over(180, now_ts) or primary.return_pct
    velocity = short_ret / 30.0 if short_ret else 0.0
    acceleration = (short_ret - long_ret) / 150.0
    btc_ret = btc_state.return_over(primary.window_sec, now_ts) if btc_state else None
    rel = (primary.return_pct - btc_ret) if btc_ret is not None else None
    vol_z = primary.volume_zscore
    conf = min(0.95, 0.4 + 0.1 * len(triggers) + min(abs(primary.return_pct) / 10.0, 0.3))
    return ShockCandidate(
        symbol=symbol,
        direction=direction,
        event_ts=now_ts,
        detected_ts=now_ts,
        triggers=triggers,
        return_pct=primary.return_pct,
        velocity=velocity,
        acceleration=acceleration,
        volume_zscore=vol_z,
        market_return_pct=median_market_return,
        btc_return_pct=btc_ret,
        relative_return_pct=rel,
        confidence=conf,
        raw_metrics={
            "trigger_ids": [t.detector_id for t in triggers],
            "trigger_returns": {t.detector_id: t.return_pct for t in triggers},
        },
    )


def scan_universe_for_shocks(
    feed: BinanceFuturesPriceFeed,
    symbols: list[str],
    *,
    now_ts: int | None = None,
) -> list[ShockCandidate]:
    now = now_ts or int(__import__("time").time())
    returns: list[float] = []
    for sym in symbols:
        st = feed.get_state(sym)
        if st:
            r = st.return_over(60, now)
            if r is not None:
                returns.append(r)
    median_ret = sorted(returns)[len(returns) // 2] if returns else None
    btc = feed.get_state("BTC")

    candidates: list[ShockCandidate] = []
    for sym in symbols:
        state = feed.get_state(sym)
        if not state:
            continue
        triggers = detect_shock_triggers(
            state, now_ts=now, btc_state=btc, median_market_return=median_ret,
        )
        cand = merge_triggers_to_candidate(
            sym, triggers, now_ts=now, state=state,
            btc_state=btc, median_market_return=median_ret,
        )
        if cand:
            candidates.append(cand)
    return candidates
