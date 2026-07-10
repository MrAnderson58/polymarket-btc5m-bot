"""Pending reversal watcher — delayed R1-R5 confirmation after shock detection."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from bot.research.market_events.event_types import (
    EVENT_PHASE_PAPER_ENTRY,
    EVENT_PHASE_REVERSAL_CONFIRMED,
    SHOCK_DIRECTION_DOWN,
    SHOCK_DIRECTION_UP,
)
from bot.research.market_events.lifecycle_decisions import persist_reversal_decisions
from bot.research.market_events.price_feed import SymbolPriceState
from bot.research.market_events.reversal_confirmation import evaluate_all_reversals
from bot.research.market_events.shock_detector import ShockCandidate, ShockTrigger

MONITOR_HORIZONS_SEC = (30, 60, 180, 300, 600, 900)
MAX_MONITOR_SEC = 900

PHASE_MONITORING = "MONITORING_REVERSAL"
PHASE_EXPIRED = "EXPIRED_NO_REVERSAL"
PHASE_MANAGING = "MANAGING_POSITION"

ACTIVE_PHASES = (PHASE_MONITORING, PHASE_MANAGING)


@dataclass
class PendingShockState:
    event_id: int
    symbol: str
    direction: str
    detected_ts: int
    shock_return_pct: float
    shock: ShockCandidate
    extreme_price: float | None = None
    extreme_ts: int | None = None
    confirmed_variants: set[str] = field(default_factory=set)
    r5_first_confirm: int | None = None
    positions: list = field(default_factory=list)
    phase: str = PHASE_MONITORING


def create_pending_shock(
    conn: Any,
    *,
    event_id: int,
    symbol: str,
    direction: str,
    detected_ts: int,
    shock_return_pct: float,
    extreme_price: float | None,
) -> None:
    now = int(time.time())
    monitor_until = detected_ts + MAX_MONITOR_SEC
    conn.execute(
        """
        INSERT OR REPLACE INTO market_events_pending_shocks (
          event_id, symbol, direction, phase, detected_ts, shock_return_pct,
          shock_extreme_price, shock_extreme_ts, monitor_until_ts,
          monitor_horizons_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, symbol, direction, PHASE_MONITORING, detected_ts, shock_return_pct,
            extreme_price, detected_ts if extreme_price else None, monitor_until,
            json.dumps(list(MONITOR_HORIZONS_SEC)), now, now,
        ),
    )
    conn.execute(
        "UPDATE market_events SET phase = ? WHERE id = ?",
        (PHASE_MONITORING, event_id),
    )


def load_pending_shocks(conn: Any) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in ACTIVE_PHASES)
    return conn.execute(
        f"""
        SELECT p.*, e.detector_triggers_json, e.return_pct, e.detected_ts AS evt_detected_ts,
               e.velocity, e.acceleration, e.volume_zscore, e.market_return_pct,
               e.btc_return_pct, e.relative_return_pct, e.confidence, e.raw_metrics_json
        FROM market_events_pending_shocks p
        JOIN market_events e ON e.id = p.event_id
        WHERE p.phase IN ({placeholders})
        ORDER BY p.detected_ts
        """,
        ACTIVE_PHASES,
    ).fetchall()


def load_confirmed_variants(conn: Any, event_id: int) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT reversal_variant FROM paper_strategy_runs WHERE event_id = ?",
        (event_id,),
    ).fetchall()
    return {r["reversal_variant"] for r in rows}


def _reconstruct_shock(row: dict[str, Any]) -> ShockCandidate:
    triggers_raw = json.loads(row.get("detector_triggers_json") or "[]")
    triggers = [
        ShockTrigger(t, 60, float(row["shock_return_pct"])) for t in triggers_raw
    ] if triggers_raw else []
    return ShockCandidate(
        symbol=row["symbol"],
        direction=row["direction"],
        event_ts=int(row["detected_ts"]),
        detected_ts=int(row["evt_detected_ts"] or row["detected_ts"]),
        triggers=triggers,
        return_pct=float(row["shock_return_pct"]),
        velocity=float(row.get("velocity") or 0),
        acceleration=float(row.get("acceleration") or 0),
        volume_zscore=row.get("volume_zscore"),
        market_return_pct=row.get("market_return_pct"),
        btc_return_pct=row.get("btc_return_pct"),
        relative_return_pct=row.get("relative_return_pct"),
        confidence=float(row.get("confidence") or 0.5),
        raw_metrics=json.loads(row.get("raw_metrics_json") or "{}"),
    )


def row_to_pending_state(row: Any, *, confirmed: set[str] | None = None) -> PendingShockState:
    d = dict(row)
    shock = _reconstruct_shock(d)
    confirmed_variants = confirmed or set()
    if d.get("confirmed_reversal"):
        confirmed_variants.add(str(d["confirmed_reversal"]))
    return PendingShockState(
        event_id=int(d["event_id"]),
        symbol=d["symbol"],
        direction=d["direction"],
        detected_ts=int(d["detected_ts"]),
        shock_return_pct=float(d["shock_return_pct"]),
        shock=shock,
        extreme_price=d.get("shock_extreme_price"),
        extreme_ts=d.get("shock_extreme_ts"),
        confirmed_variants=confirmed_variants,
        r5_first_confirm=d.get("r5_first_confirm_ts"),
        phase=d.get("phase", PHASE_MONITORING),
    )


def _update_extreme(state: PendingShockState, price: float, now: int) -> None:
    if state.extreme_price is None:
        state.extreme_price = price
        state.extreme_ts = now
        return
    if state.direction == SHOCK_DIRECTION_DOWN and price < state.extreme_price:
        state.extreme_price = price
        state.extreme_ts = now
    elif state.direction == SHOCK_DIRECTION_UP and price > state.extreme_price:
        state.extreme_price = price
        state.extreme_ts = now


def _path_from_extreme(state: PendingShockState, price: float) -> dict[str, float | None]:
    if not state.extreme_price or state.extreme_price <= 0:
        return {"extreme_price": None, "reclaim_pct": None}
    if state.direction == SHOCK_DIRECTION_DOWN:
        reclaim = (price - state.extreme_price) / state.extreme_price * 100.0
    else:
        reclaim = (state.extreme_price - price) / state.extreme_price * 100.0
    return {"extreme_price": state.extreme_price, "reclaim_pct": reclaim}


def process_pending_shock(
    conn: Any,
    state: PendingShockState,
    price_state: SymbolPriceState,
    now: int,
    *,
    open_paper_fn: Callable[..., None],
) -> str | None:
    """Returns 'expired' when monitor window ends, else None."""
    if state.phase not in ACTIVE_PHASES:
        return None
    if now > state.detected_ts + MAX_MONITOR_SEC:
        if not state.confirmed_variants:
            _expire_pending(conn, state, now)
            return "expired"
        return "done"
    if price_state.last_price is None:
        return None

    price = price_state.last_price
    _update_extreme(state, price, now)
    path = _path_from_extreme(state, price)

    revs = evaluate_all_reversals(
        state.shock, price_state, now, r5_first_confirm_ts=state.r5_first_confirm,
    )
    persist_reversal_decisions(conn, event_id=state.event_id, decision_ts=now, results=revs)

    any_new = False
    for rev in revs:
        if not rev.confirmed or rev.variant in state.confirmed_variants:
            continue
        if rev.variant == "R1" and state.r5_first_confirm is None:
            state.r5_first_confirm = now
        latency = now - state.detected_ts
        open_paper_fn(
            state.event_id, state.shock, rev.confirm_ts or now,
            rev.confirm_price or price, rev.variant,
        )
        state.confirmed_variants.add(rev.variant)
        any_new = True
        conn.execute(
            """
            UPDATE market_events_pending_shocks SET
              phase = ?, confirmed_reversal = ?, confirm_ts = ?,
              confirm_latency_sec = ?, confirm_price = ?,
              shock_extreme_price = ?, shock_extreme_ts = ?,
              path_from_extreme_json = ?, r5_first_confirm_ts = ?, updated_at = ?
            WHERE event_id = ?
            """,
            (
                PHASE_MANAGING, rev.variant, rev.confirm_ts or now,
                latency, rev.confirm_price or price,
                state.extreme_price, state.extreme_ts,
                json.dumps(path), state.r5_first_confirm, int(time.time()),
                state.event_id,
            ),
        )
        conn.execute(
            "UPDATE market_events SET phase = ? WHERE id = ?",
            (EVENT_PHASE_REVERSAL_CONFIRMED, state.event_id),
        )
        conn.execute(
            "UPDATE market_events SET phase = ? WHERE id = ?",
            (EVENT_PHASE_PAPER_ENTRY, state.event_id),
        )
        state.phase = PHASE_MANAGING

    conn.execute(
        """
        UPDATE market_events_pending_shocks SET
          shock_extreme_price = ?, shock_extreme_ts = ?,
          path_from_extreme_json = ?, r5_first_confirm_ts = ?, updated_at = ?
        WHERE event_id = ?
        """,
        (
            state.extreme_price, state.extreme_ts, json.dumps(path),
            state.r5_first_confirm, int(time.time()), state.event_id,
        ),
    )
    return None


def _expire_pending(conn: Any, state: PendingShockState, now: int) -> None:
    if state.confirmed_variants:
        return
    conn.execute(
        """
        UPDATE market_events_pending_shocks SET
          phase = ?, expired_ts = ?, updated_at = ?
        WHERE event_id = ?
        """,
        (PHASE_EXPIRED, now, int(time.time()), state.event_id),
    )
    conn.execute(
        "UPDATE market_events SET phase = ? WHERE id = ?",
        (PHASE_EXPIRED, state.event_id),
    )
    state.phase = PHASE_EXPIRED


def restore_pending_map(conn: Any) -> dict[int, PendingShockState]:
    rows = load_pending_shocks(conn)
    out: dict[int, PendingShockState] = {}
    for r in rows:
        eid = int(r["event_id"])
        confirmed = load_confirmed_variants(conn, eid)
        out[eid] = row_to_pending_state(r, confirmed=confirmed)
    return out
