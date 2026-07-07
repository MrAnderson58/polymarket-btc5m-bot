"""V4 Shadow state machine — virtual trades only, no CLOB execution."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from typing import Literal

from bot.config import (
    TRAILING_ACTIVATION_PROFIT,
    TRAILING_OFFSET,
    V4_MIN_PROBABILITY,
    V4_MIN_SCORE,
    V4_OBSERVE_SECONDS,
    V4_PULLBACK,
)
from bot.database import (
    close_v4_shadow_trade,
    get_open_v4_shadow_trade,
    has_v4_shadow_trade,
    insert_v4_shadow_observation,
    insert_v4_shadow_trade,
    update_v4_shadow_trade_tracking,
)
from bot.er_trailing_stop import (
    TrailingSnapshot,
    TrailingTickResult,
    activation_reached,
    compute_trailing_close_stats,
    compute_trailing_stop_price,
)
from bot.market_scanner import Btc5mMarket
from bot.v4.observe import ObservationSnapshot, build_observation, in_observe_phase, log_observe
from bot.v4.pullback import log_wait_pullback, pullback_reached, side_ask, update_peak_ask
from bot.v4.score import compute_trend_score, meets_entry_threshold
from bot.v4.state_log import blocked_signature, log_blocked, log_state_transition
from bot.v4.statistics import (
    log_shadow_buy,
    log_shadow_exit,
    log_trailing,
    log_trend_found,
    log_v4_summary,
)
from bot.v4.trend_detector import TrendSignal, detect_trend


V4Phase = Literal[
    "OBSERVE",
    "TREND_FOUND",
    "WAIT_PULLBACK",
    "SHADOW_BUY",
    "TRAILING",
    "EXIT",
]

_EXIT_REASON = Literal["TRAILING_STOP", "TIME_STOP"]


@dataclass
class V4WindowRuntime:
    market_slug: str
    window_start_ts: int
    phase: V4Phase = "OBSERVE"
    history: list[ObservationSnapshot] = field(default_factory=list)
    trend: TrendSignal | None = None
    trend_score: float = 0.0
    trend_probability: float = 0.0
    peak_ask: float | None = None
    trade_id: int | None = None
    trailing_logged: bool = False
    last_observation_ts: int = 0
    last_logged_phase: V4Phase | None = None
    last_blocked_signature: str = ""
    last_blocked_ts: int = 0


_window_states: dict[str, V4WindowRuntime] = {}


def _maybe_log_blocked(
    runtime: V4WindowRuntime,
    *,
    now_ts: int,
    reason: str,
    throttle_sec: int = 10,
    **fields: object,
) -> None:
    signature = blocked_signature(reason, **fields)
    if (
        signature == runtime.last_blocked_signature
        and now_ts - runtime.last_blocked_ts < throttle_sec
    ):
        return
    log_blocked(reason, **fields)
    runtime.last_blocked_signature = signature
    runtime.last_blocked_ts = now_ts


def _enter_phase(runtime: V4WindowRuntime, phase: V4Phase, **fields: object) -> None:
    transition = runtime.last_logged_phase is not None and runtime.last_logged_phase != phase
    runtime.phase = phase
    log_state_transition(phase, transition=transition, **fields)
    runtime.last_logged_phase = phase


def _side_bid(quotes: dict[str, float | None], side: str) -> float | None:
    key = "yes_bid" if side == "YES" else "no_bid"
    return quotes.get(key)


def _process_v4_trailing_tick(trade, entry_price: float, bid: float) -> TrailingTickResult:
    """Dollar trailing always enabled for V4 shadow (activation +0.03, offset 0.01)."""
    prev_max = float(trade["max_price_seen"] or entry_price)
    peak_bid = max(prev_max, bid)
    prev_active = bool(trade["trailing_active"])
    prev_highest = trade["highest_price"]
    prev_highest_f = float(prev_highest) if prev_highest is not None else None
    prev_activation = trade["trailing_activation_price"]
    prev_activation_f = float(prev_activation) if prev_activation is not None else None

    act_price = entry_price + TRAILING_ACTIVATION_PROFIT

    if prev_active:
        highest = max(prev_highest_f or bid, bid)
        snapshot = TrailingSnapshot(
            highest_bid=highest,
            trailing_active=True,
            trailing_stop_price=compute_trailing_stop_price(highest),
            activation_price=act_price,
        )
    elif activation_reached(entry_price, peak_bid):
        snapshot = TrailingSnapshot(
            highest_bid=bid,
            trailing_active=True,
            trailing_stop_price=compute_trailing_stop_price(bid),
            activation_price=act_price,
        )
    else:
        snapshot = TrailingSnapshot(
            highest_bid=peak_bid,
            trailing_active=False,
            trailing_stop_price=None,
            activation_price=act_price,
        )

    trailing_activation_price = prev_activation_f
    if snapshot.trailing_active and trailing_activation_price is None:
        trailing_activation_price = bid

    highest_price = prev_highest_f
    if snapshot.trailing_active:
        highest_price = snapshot.highest_bid

    max_profit_pct = None
    if snapshot.trailing_active and highest_price is not None:
        max_profit_pct = (highest_price - entry_price) / entry_price * 100

    return TrailingTickResult(
        snapshot=snapshot,
        peak_bid=peak_bid,
        trailing_activation_price=trailing_activation_price,
        highest_price=highest_price,
        max_profit_pct=max_profit_pct,
    )


def _resolve_v4_exit(
    *,
    entry_price: float,
    bid: float,
    trailing_active: bool,
    highest_after_activation: float | None,
    seconds_left: int,
) -> _EXIT_REASON | None:
    if trailing_active:
        highest = highest_after_activation or bid
        if bid <= highest - TRAILING_OFFSET:
            return "TRAILING_STOP"
    if seconds_left <= 0:
        return "TIME_STOP"
    return None


def _get_runtime(market: Btc5mMarket) -> V4WindowRuntime:
    state = _window_states.get(market.slug)
    if state is None or state.window_start_ts != market.window_start_ts:
        state = V4WindowRuntime(
            market_slug=market.slug,
            window_start_ts=market.window_start_ts,
        )
        _window_states[market.slug] = state
        log_state_transition(
            "OBSERVE",
            transition=False,
            elapsed=0,
            score="-",
            probability="-",
        )
        state.last_logged_phase = "OBSERVE"
    return state


def _persist_observation(conn: sqlite3.Connection, snapshot: ObservationSnapshot) -> None:
    insert_v4_shadow_observation(
        conn,
        market_slug=f"btc-updown-5m-{snapshot.window_start}",
        window_start_ts=snapshot.window_start,
        timestamp=snapshot.timestamp,
        seconds_from_start=snapshot.seconds_from_start,
        seconds_left=snapshot.seconds_left,
        btc_price=snapshot.btc_price,
        strike=snapshot.strike,
        delta=snapshot.delta,
        yes_bid=snapshot.yes_bid,
        yes_ask=snapshot.yes_ask,
        no_bid=snapshot.no_bid,
        no_ask=snapshot.no_ask,
        trend_score=snapshot.trend_score,
        trend_side=snapshot.trend_side,
        spread=snapshot.spread,
    )


def _open_shadow_trade(
    conn: sqlite3.Connection,
    market: Btc5mMarket,
    *,
    side: str,
    entry_price: float,
    entry_ts: int,
    score: float,
    probability: float,
    reason: str,
) -> int:
    trade_id = insert_v4_shadow_trade(
        conn,
        market_slug=market.slug,
        window_start_ts=market.window_start_ts,
        end_ts=market.end_ts,
        side=side,
        entry_price=entry_price,
        entry_ts=entry_ts,
        entry_score=score,
        entry_probability=probability,
        entry_reason=reason,
    )
    log_shadow_buy(
        side=side,
        entry_price=entry_price,
        score=score,
        probability=probability,
        reason=reason,
    )
    return trade_id


def _close_shadow_trade(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
    *,
    bid: float,
    exit_reason: _EXIT_REASON,
    now_ts: int,
    trailing_tick: TrailingTickResult | None = None,
) -> None:
    entry_price = float(trade["entry_price"])
    entry_ts = int(trade["entry_ts"])
    holding_time = now_ts - entry_ts

    if trailing_tick is not None:
        stats = compute_trailing_close_stats(
            entry_price,
            bid,
            activation_price_value=trailing_tick.trailing_activation_price,
            highest_price=trailing_tick.highest_price,
        )
    else:
        stats = compute_trailing_close_stats(
            entry_price,
            bid,
            activation_price_value=trade["trailing_activation_price"],
            highest_price=trade["highest_price"],
        )

    log_shadow_exit(entry_price=entry_price, stats=stats, exit_reason=exit_reason)
    log_v4_summary(
        side=trade["side"],
        stats=stats,
        holding_time_seconds=holding_time,
        exit_reason=exit_reason,
        entry_score=float(trade["entry_score"]),
        entry_probability=float(trade["entry_probability"]),
    )

    close_v4_shadow_trade(
        conn,
        trade["id"],
        exit_price=bid,
        exit_reason=exit_reason,
        holding_time_seconds=holding_time,
        trailing_activation_price=stats.activation_price,
        highest_price=stats.highest_price,
        max_profit_pct=stats.max_profit_pct,
        realized_profit_pct=stats.realized_profit_pct,
        profit_left_on_table_pct=stats.profit_left_on_table_pct,
    )


def _manage_open_shadow_trade(
    conn: sqlite3.Connection,
    trade: sqlite3.Row,
    *,
    market: Btc5mMarket,
    quotes: dict[str, float | None],
    now_ts: int,
    runtime: V4WindowRuntime,
) -> None:
    entry_price = float(trade["entry_price"])
    bid = _side_bid(quotes, trade["side"])
    if bid is None:
        return

    tick = _process_v4_trailing_tick(trade, entry_price, bid)
    snapshot = tick.snapshot
    seconds_left = max(0, market.end_ts - now_ts)

    if snapshot.trailing_active and not runtime.trailing_logged:
        log_trailing(
            entry_price=entry_price,
            bid=bid,
            highest=snapshot.highest_bid,
            stop=snapshot.trailing_stop_price,
            active=True,
        )
        runtime.trailing_logged = True
        _enter_phase(runtime, "TRAILING")
    elif snapshot.trailing_active:
        log_trailing(
            entry_price=entry_price,
            bid=bid,
            highest=snapshot.highest_bid,
            stop=snapshot.trailing_stop_price,
            active=True,
        )

    exit_reason = _resolve_v4_exit(
        entry_price=entry_price,
        bid=bid,
        trailing_active=snapshot.trailing_active,
        highest_after_activation=tick.highest_price,
        seconds_left=seconds_left,
    )

    if exit_reason:
        update_v4_shadow_trade_tracking(
            conn,
            trade["id"],
            max_price_seen=tick.peak_bid,
            last_bid=bid,
            trailing_active=snapshot.trailing_active,
            trailing_stop_price=snapshot.trailing_stop_price,
            trailing_activation_price=tick.trailing_activation_price,
            highest_price=tick.highest_price,
            max_profit_pct=tick.max_profit_pct,
        )
        _close_shadow_trade(
            conn,
            trade,
            bid=bid,
            exit_reason=exit_reason,
            now_ts=now_ts,
            trailing_tick=tick,
        )
        _enter_phase(runtime, "EXIT")
        return

    update_v4_shadow_trade_tracking(
        conn,
        trade["id"],
        max_price_seen=tick.peak_bid,
        last_bid=bid,
        trailing_active=snapshot.trailing_active,
        trailing_stop_price=snapshot.trailing_stop_price,
        trailing_activation_price=tick.trailing_activation_price,
        highest_price=tick.highest_price,
        max_profit_pct=tick.max_profit_pct,
    )
    if snapshot.trailing_active:
        runtime.phase = "TRAILING"
    elif runtime.last_logged_phase != "SHADOW_BUY":
        _enter_phase(runtime, "SHADOW_BUY", entry=f"{entry_price:.2f}")
    else:
        runtime.phase = "SHADOW_BUY"


def record_v4_observation_tick(
    conn: sqlite3.Connection,
    market: Btc5mMarket,
    quotes: dict[str, float | None],
    *,
    btc_price: float,
    strike: float | None,
    runtime: V4WindowRuntime | None = None,
    now_ts: int | None = None,
) -> bool:
    """Persist one observation row for research density (independent of trade FSM)."""
    now_ts = int(time.time()) if now_ts is None else now_ts
    runtime = runtime or _get_runtime(market)

    trend_score: float | None = None
    trend_side: str | None = None
    if runtime.trend is not None:
        trend_score = runtime.trend_score
        trend_side = runtime.trend.side

    snapshot = build_observation(
        now_ts=now_ts,
        window_start=market.window_start_ts,
        end_ts=market.end_ts,
        btc_price=btc_price,
        strike=strike,
        quotes=quotes,
        trend_score=trend_score,
        trend_side=trend_side,
    )

    if snapshot.timestamp == runtime.last_observation_ts:
        return False

    _persist_observation(conn, snapshot)
    runtime.last_observation_ts = snapshot.timestamp
    runtime.history.append(snapshot)
    if len(runtime.history) > 180:
        runtime.history = runtime.history[-180:]
    return True


def process_v4_shadow(
    conn: sqlite3.Connection,
    market: Btc5mMarket,
    quotes: dict[str, float | None],
    *,
    btc_price: float,
    strike: float | None,
) -> bool:
    """Run one V4 Shadow tick (intended at 1 Hz). Returns True if observation inserted."""
    now_ts = int(time.time())
    runtime = _get_runtime(market)

    inserted = record_v4_observation_tick(
        conn,
        market,
        quotes,
        btc_price=btc_price,
        strike=strike,
        runtime=runtime,
        now_ts=now_ts,
    )

    open_trade = get_open_v4_shadow_trade(conn, market_slug=market.slug)
    if open_trade is not None:
        _manage_open_shadow_trade(
            conn,
            open_trade,
            market=market,
            quotes=quotes,
            now_ts=now_ts,
            runtime=runtime,
        )
        return inserted

    if has_v4_shadow_trade(conn, market.slug):
        if runtime.last_logged_phase != "EXIT":
            _enter_phase(runtime, "EXIT")
        else:
            runtime.phase = "EXIT"
        return inserted

    if runtime.phase == "OBSERVE":
        if not runtime.history:
            return inserted
        snapshot = runtime.history[-1]
        log_observe(snapshot)
        elapsed = snapshot.seconds_from_start
        if in_observe_phase(elapsed, V4_OBSERVE_SECONDS):
            signal = detect_trend(runtime.history)
            if signal is None:
                _maybe_log_blocked(
                    runtime,
                    now_ts=now_ts,
                    reason="no trend detected",
                    elapsed=elapsed,
                    samples=len(runtime.history),
                    need_samples=10,
                )
                return inserted

            score, probability = compute_trend_score(signal)
            log_state_transition(
                "OBSERVE",
                transition=False,
                elapsed=elapsed,
                score=score,
                probability=probability,
            )

            if score < V4_MIN_SCORE:
                _maybe_log_blocked(
                    runtime,
                    now_ts=now_ts,
                    reason="score too low",
                    score=score,
                    need=V4_MIN_SCORE,
                )
                return inserted

            if probability < V4_MIN_PROBABILITY:
                _maybe_log_blocked(
                    runtime,
                    now_ts=now_ts,
                    reason=(
                        f"probability\n\n{probability:.2f} < {V4_MIN_PROBABILITY:.2f}"
                    ),
                )
                return inserted

            if meets_entry_threshold(
                score,
                probability,
                min_score=V4_MIN_SCORE,
                min_probability=V4_MIN_PROBABILITY,
            ):
                runtime.trend = signal
                runtime.trend_score = score
                runtime.trend_probability = probability
                _enter_phase(
                    runtime,
                    "TREND_FOUND",
                    side=signal.side,
                    score=score,
                    probability=probability,
                )
                log_trend_found(signal, score, probability)
                runtime.peak_ask = side_ask(quotes, signal.side)
                _enter_phase(
                    runtime,
                    "WAIT_PULLBACK",
                    highest=runtime.peak_ask,
                    current=runtime.peak_ask,
                    need=(runtime.peak_ask or 0) - V4_PULLBACK,
                )
            return inserted

        _maybe_log_blocked(
            runtime,
            now_ts=now_ts,
            reason="observe window ended",
            elapsed=elapsed,
            observe_seconds=V4_OBSERVE_SECONDS,
        )
        return inserted

    if runtime.phase in {"TREND_FOUND", "WAIT_PULLBACK"} and runtime.trend is not None:
        side = runtime.trend.side
        ask = side_ask(quotes, side)
        runtime.peak_ask = update_peak_ask(runtime.peak_ask, ask)
        need = (runtime.peak_ask or 0) - V4_PULLBACK
        log_state_transition(
            "WAIT_PULLBACK",
            transition=False,
            highest=runtime.peak_ask,
            current=ask,
            need=need,
        )
        log_wait_pullback(
            side=side,
            peak_ask=runtime.peak_ask,
            current_ask=ask,
            pullback=V4_PULLBACK,
        )
        if not pullback_reached(runtime.peak_ask, ask, V4_PULLBACK):
            _maybe_log_blocked(
                runtime,
                now_ts=now_ts,
                reason="pullback not reached",
                highest=runtime.peak_ask,
                current=ask,
                need=need,
            )
            return inserted

        if ask is None:
            _maybe_log_blocked(
                runtime,
                now_ts=now_ts,
                reason="missing ask quote",
                side=side,
            )
            return inserted

        reason = f"trend_{side.lower()}_pullback_{V4_PULLBACK:.2f}"
        runtime.trade_id = _open_shadow_trade(
            conn,
            market,
            side=side,
            entry_price=ask,
            entry_ts=now_ts,
            score=runtime.trend_score,
            probability=runtime.trend_probability,
            reason=reason,
        )
        _enter_phase(runtime, "SHADOW_BUY", entry=f"{ask:.2f}")
        return inserted

    return inserted


def close_due_v4_shadow_trades(conn: sqlite3.Connection, now_ts: int) -> int:
    """Close open V4 shadow trades whose market window has ended."""
    due = conn.execute(
        """
        SELECT * FROM v4_shadow_trades
        WHERE status = 'open' AND end_ts <= ?
        ORDER BY end_ts ASC
        """,
        (now_ts,),
    ).fetchall()

    for trade in due:
        bid = trade["last_bid"]
        if bid is None:
            bid = trade["entry_price"]
        _close_shadow_trade(
            conn,
            trade,
            bid=float(bid),
            exit_reason="TIME_STOP",
            now_ts=now_ts,
        )

    return len(due)
