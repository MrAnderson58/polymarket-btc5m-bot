"""Phase G.3 — liquidity engine v2 (funding, OI, liquidations, dominance, volume, ATR)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id

STATE_ACCUMULATION = "Accumulation"
STATE_DISTRIBUTION = "Distribution"
STATE_SHORT_SQUEEZE = "Short squeeze"
STATE_LONG_SQUEEZE = "Long squeeze"
STATE_CAPITULATION = "Capitulation"
STATE_EXHAUSTION = "Exhaustion"
STATE_RECOVERY = "Recovery"
STATE_CONTINUATION = "Continuation"

ALL_STATES = (
    STATE_ACCUMULATION,
    STATE_DISTRIBUTION,
    STATE_SHORT_SQUEEZE,
    STATE_LONG_SQUEEZE,
    STATE_CAPITULATION,
    STATE_EXHAUSTION,
    STATE_RECOVERY,
    STATE_CONTINUATION,
)


@dataclass(frozen=True)
class LiquidityStateG3:
    primary_state: str
    probabilities: dict[str, float]
    factors: dict[str, Any]


def _norm_probs(raw: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, v) for v in raw.values()) or 1.0
    return {k: round(max(0.0, v) / total, 3) for k, v in raw.items()}


def compute_liquidity_state_g3(
    conn: Any,
    *,
    snapshot_id: int,
) -> LiquidityStateG3:
    row = conn.execute(
        "SELECT * FROM market_snapshots_g3 WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    if not row:
        return LiquidityStateG3(
            primary_state=STATE_CONTINUATION,
            probabilities={s: 0.125 for s in ALL_STATES},
            factors={},
        )

    funding = float(row["funding"] or 0)
    oi = float(row["open_interest"] or 0)
    liq = float(row["liquidations"] or 0)
    btc_d = float(row["btc_dominance"] or 50)
    total3 = float(row["total3"] or 0)
    volume = float(row["volume"] or 0)
    atr = float(row["atr"] or 0)
    vol_delta = float(row["volume_delta"] or 0)

    prev = conn.execute(
        """
        SELECT open_interest, volume FROM market_snapshots_g3
        WHERE id < ? ORDER BY id DESC LIMIT 1
        """,
        (snapshot_id,),
    ).fetchone()
    oi_rising = prev and oi > float(prev["open_interest"] or 0)
    vol_rising = prev and volume > float(prev["volume"] or 0)

    whale_hint = conn.execute(
        """
        SELECT whale_score FROM market_events_market_intelligence_f7
        ORDER BY created_at DESC LIMIT 1
        """,
    ).fetchone()
    whale_score = float(whale_hint["whale_score"]) if whale_hint and whale_hint["whale_score"] else 50.0

    raw = {
        STATE_ACCUMULATION: 0.05,
        STATE_DISTRIBUTION: 0.05,
        STATE_SHORT_SQUEEZE: 0.05,
        STATE_LONG_SQUEEZE: 0.05,
        STATE_CAPITULATION: 0.05,
        STATE_EXHAUSTION: 0.05,
        STATE_RECOVERY: 0.05,
        STATE_CONTINUATION: 0.10,
    }

    if funding < -0.0001 and oi_rising:
        raw[STATE_ACCUMULATION] += 0.25
    if funding > 0.0003 and vol_rising:
        raw[STATE_DISTRIBUTION] += 0.22
    if funding < -0.0005 and liq > 0:
        raw[STATE_SHORT_SQUEEZE] += 0.28
    if funding > 0.0005 and liq > 0:
        raw[STATE_LONG_SQUEEZE] += 0.25
    if total3 < -2.5 and liq > 0 and funding < 0:
        raw[STATE_CAPITULATION] += 0.35
    if atr > 0 and vol_delta < 0 and abs(total3) < 0.5:
        raw[STATE_EXHAUSTION] += 0.20
    if total3 > 1.0 and funding < 0:
        raw[STATE_RECOVERY] += 0.22
    if btc_d > 55 and total3 < 0:
        raw[STATE_CONTINUATION] += 0.18
    elif btc_d < 48 and total3 > 0:
        raw[STATE_CONTINUATION] += 0.15

    if whale_score >= 70:
        raw[STATE_ACCUMULATION] += 0.08
    elif whale_score <= 30:
        raw[STATE_DISTRIBUTION] += 0.08

    probs = _norm_probs(raw)
    primary = max(probs, key=probs.get)  # type: ignore[arg-type]

    factors = {
        "funding": funding,
        "oi_rising": oi_rising,
        "liquidations": liq,
        "btc_dominance": btc_d,
        "total3": total3,
        "volume": volume,
        "atr": atr,
        "volume_delta": vol_delta,
        "whale_score": whale_score,
    }
    return LiquidityStateG3(primary_state=primary, probabilities=probs, factors=factors)


def persist_liquidity_state_g3(conn: Any, *, snapshot_id: int, state: LiquidityStateG3) -> int:
    now = int(time.time())
    existing = conn.execute(
        "SELECT id FROM market_liquidity_state_g3 WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()
    vals = (
        state.primary_state,
        state.probabilities.get(STATE_ACCUMULATION, 0),
        state.probabilities.get(STATE_DISTRIBUTION, 0),
        state.probabilities.get(STATE_SHORT_SQUEEZE, 0),
        state.probabilities.get(STATE_LONG_SQUEEZE, 0),
        state.probabilities.get(STATE_CAPITULATION, 0),
        state.probabilities.get(STATE_EXHAUSTION, 0),
        state.probabilities.get(STATE_RECOVERY, 0),
        state.probabilities.get(STATE_CONTINUATION, 0),
        json.dumps(state.factors),
        now,
    )
    if existing:
        conn.execute(
            """
            UPDATE market_liquidity_state_g3 SET
              primary_state = ?, accumulation_prob = ?, distribution_prob = ?,
              short_squeeze_prob = ?, long_squeeze_prob = ?, capitulation_prob = ?,
              exhaustion_prob = ?, recovery_prob = ?, continuation_prob = ?,
              factors_json = ?, created_at = ?
            WHERE snapshot_id = ?
            """,
            (*vals, snapshot_id),
        )
        return int(existing["id"])
    return insert_returning_id(
        conn,
        """
        INSERT INTO market_liquidity_state_g3 (
          snapshot_id, primary_state,
          accumulation_prob, distribution_prob, short_squeeze_prob, long_squeeze_prob,
          capitulation_prob, exhaustion_prob, recovery_prob, continuation_prob,
          factors_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (snapshot_id, *vals),
    )
