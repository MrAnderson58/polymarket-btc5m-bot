"""Phase G.3.1 — per-symbol candidate pipeline with precise rejection reasons."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.config import CORE_SYMBOLS
from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.candles import (
    CandleBar,
    compute_atr,
    load_recent_candles,
)
from bot.research.market_events.signal_intelligence.config import (
    G31_DEFAULT_UNIVERSE,
    G31_ENABLED,
    G31_IDLE_SIGNAL_HOURS,
    G31_SIGNAL_SYMBOLS,
    G3_MIN_CONFIDENCE,
    G3_MIN_LIQUIDITY_PROB,
    G3_MIN_MARKET_SCORE,
    G3_MIN_RISK_REWARD,
)
from bot.research.market_events.signal_intelligence.dominance_context_f7 import classify_dominance
from bot.research.market_events.signal_intelligence.liquidity_engine_g3 import LiquidityStateG3
from bot.research.market_events.signal_intelligence.reversal_learning_g1 import lookup_historical_reversal_rate
from bot.research.market_events.signal_intelligence.trend_windows_g3 import TrendWindowG3

logger = logging.getLogger(__name__)

STATE_REJECTED = "rejected"
STATE_CANDIDATE = "candidate"
STATE_ACCEPTED = "accepted"
STATE_INSUFFICIENT = "insufficient_data"

_TABLE = "market_candidate_g31"


@dataclass(frozen=True)
class CandidateG31:
    symbol: str
    trend_score: float | None
    market_score: float | None
    liquidity_score: float | None
    confidence: float | None
    rr: float | None
    btc_alignment: str
    funding_score: float | None
    oi_score: float | None
    volume_score: float | None
    atr_score: float | None
    fear_greed: float | None
    candidate_state: str
    rejection_reason: str | None
    direction: str | None
    trend: TrendWindowG3 | None = None


def load_g31_universe_symbols(conn: Any) -> tuple[str, ...]:
    """All monitored symbols — core + extended + instrument registry."""
    symbols: set[str] = set(G31_DEFAULT_UNIVERSE)
    symbols.update(CORE_SYMBOLS)
    if G31_SIGNAL_SYMBOLS:
        symbols.update(G31_SIGNAL_SYMBOLS)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT canonical_asset FROM market_events_instruments
            WHERE asset_class = 'CRYPTO'
              AND (active = 1 OR paper_enabled = 1 OR observe_enabled = 1)
            """,
        ).fetchall()
        for r in rows:
            sym = str(r["canonical_asset"]).upper()
            if sym and sym != "TOTAL3":
                symbols.add(sym)
    except Exception as exc:
        logger.debug("g31 universe registry skipped: %s", exc)
    return tuple(sorted(symbols))


def _best_trend(trends: list[TrendWindowG3], symbol: str) -> TrendWindowG3 | None:
    sym_trends = [t for t in trends if t.symbol == symbol]
    if not sym_trends:
        return None
    return max(sym_trends, key=lambda t: t.trend_score)


def _volume_score(bars: list[CandleBar]) -> float:
    if len(bars) < 6:
        return 0.0
    recent = sum(b.volume for b in bars[-5:])
    prior = sum(b.volume for b in bars[-10:-5]) or 1.0
    ratio = recent / prior
    return round(min(100.0, max(0.0, 35.0 + ratio * 25.0)), 1)


def _funding_score(funding: float | None) -> float | None:
    if funding is None:
        return None
    return round(min(100.0, max(0.0, 50.0 + abs(funding) * 50000)), 1)


def _oi_score(oi_rising: bool | None) -> float:
    if oi_rising is True:
        return 72.0
    if oi_rising is False:
        return 38.0
    return 50.0


def _atr_score(atr: float | None, bars: list[CandleBar]) -> float:
    if not bars or not atr:
        return 50.0
    price = bars[-1].close
    if price <= 0:
        return 50.0
    pct = atr / price * 100.0
    return round(min(100.0, max(20.0, 40.0 + pct * 8.0)), 1)


def _btc_alignment_label(conn: Any, *, symbol: str, direction: str) -> tuple[str, bool]:
    dom = classify_dominance(conn, shock_symbol=symbol)
    if direction == "LONG" and dom.regime == "RISK OFF":
        return "Against", True
    if direction == "SHORT" and dom.regime == "RISK ON" and dom.btc_return > 1.5:
        return "Against", True
    if direction == "LONG" and dom.regime in ("RISK ON", "ALT SEASON"):
        return "Aligned", False
    if direction == "SHORT" and dom.regime == "RISK OFF":
        return "Aligned", False
    return "Neutral", False


def _has_reversal_confirmation(conn: Any, *, symbol: str, trend: TrendWindowG3) -> bool:
    key = f"{symbol}|{trend.pattern_type}|{trend.window_minutes}m"
    rate = lookup_historical_reversal_rate(conn, key)
    if rate is not None and rate >= 0.45:
        return True
    if trend.consecutive_candles >= 8 and trend.trend_score >= 55:
        return True
    if trend.pattern_type in ("slow_bleed", "capitulation", "accumulation", "distribution"):
        return True
    return False


def _score_candidate(
    *,
    trend: TrendWindowG3,
    liquidity: LiquidityStateG3,
    volume_score: float,
    f5_conf: float | None,
    f7_score: float | None,
    f7_conf: float | None,
) -> tuple[float, float, float]:
    liq_prob = liquidity.probabilities.get(liquidity.primary_state, 0.1)
    trend_score = trend.trend_score
    confidence = f7_conf or f5_conf or min(10.0, 5.0 + trend_score / 20.0)
    market_score = f7_score or min(100.0, trend_score * 0.55 + liq_prob * 100 * 0.25 + volume_score * 0.2)
    liquidity_score = round(liq_prob * 100.0, 1)
    return round(confidence, 2), round(market_score, 1), liquidity_score


def _compute_rr(*, confidence: float, probability: float) -> float:
    from bot.research.market_events.signal_intelligence.risk_reward_f5 import compute_risk_reward_f5
    rr_obj = compute_risk_reward_f5(
        expected_target_pct=2.5,
        expected_stop_pct=1.0,
        reversal_probability=probability,
        dynamic_confidence=confidence,
        historical_reversal_rate=0.75,
    )
    return float(rr_obj.risk_reward)


def evaluate_symbol_candidate_g31(
    conn: Any,
    *,
    symbol: str,
    trends: list[TrendWindowG3],
    liquidity: LiquidityStateG3,
    snapshot_row: Any,
    f5_conf: float | None = None,
    f7_score: float | None = None,
    f7_conf: float | None = None,
    waiting_claude: bool = False,
) -> CandidateG31:
    fear_greed = float(snapshot_row["fear_greed"]) if snapshot_row and snapshot_row["fear_greed"] is not None else None
    funding_raw = float(snapshot_row["funding"]) if snapshot_row and snapshot_row["funding"] is not None else None
    funding_score = _funding_score(funding_raw)
    oi_rising = liquidity.factors.get("oi_rising")
    oi_score = _oi_score(oi_rising if isinstance(oi_rising, bool) else None)
    atr_raw = float(snapshot_row["atr"]) if snapshot_row and snapshot_row["atr"] is not None else None

    bars = load_recent_candles(conn, symbol=symbol, venue="binance_futures", timeframe="5m", limit=48)
    volume_score = _volume_score(bars)
    atr_score = _atr_score(atr_raw, bars)

    trend = _best_trend(trends, symbol)
    if not trend or trend.trend_score < 25:
        ts = trend.trend_score if trend else None
        return CandidateG31(
            symbol=symbol,
            trend_score=ts,
            market_score=None,
            liquidity_score=None,
            confidence=None,
            rr=None,
            btc_alignment="Neutral",
            funding_score=funding_score,
            oi_score=oi_score,
            volume_score=volume_score,
            atr_score=atr_score,
            fear_greed=fear_greed,
            candidate_state=STATE_INSUFFICIENT,
            rejection_reason="Insufficient trend data",
            direction=None,
            trend=trend,
        )

    direction = "LONG" if trend.direction == "UP" else "SHORT"
    confidence, market_score, liquidity_score = _score_candidate(
        trend=trend,
        liquidity=liquidity,
        volume_score=volume_score,
        f5_conf=f5_conf,
        f7_score=f7_score,
        f7_conf=f7_conf,
    )
    liq_prob = liquidity.probabilities.get(liquidity.primary_state, 0.0)
    probability = min(0.95, liq_prob * 0.6 + (confidence / 10.0) * 0.4)
    rr = _compute_rr(confidence=confidence, probability=probability)
    btc_alignment, btc_conflict = _btc_alignment_label(conn, symbol=symbol, direction=direction)

    checks: list[tuple[bool, str]] = [
        (funding_raw is not None, "Funding unavailable"),
        (volume_score >= 40.0, f"Volume weak ({volume_score:.0f})"),
        (
            _has_reversal_confirmation(conn, symbol=symbol, trend=trend),
            "No reversal confirmation",
        ),
        (confidence >= G3_MIN_CONFIDENCE, f"Confidence {confidence:.1f} < {G3_MIN_CONFIDENCE}"),
        (market_score >= G3_MIN_MARKET_SCORE, f"Market Score {market_score:.0f} < {G3_MIN_MARKET_SCORE:.0f}"),
        (
            liq_prob >= G3_MIN_LIQUIDITY_PROB,
            f"Liquidity {liquidity_score:.0f}% < {G3_MIN_LIQUIDITY_PROB * 100:.0f}%",
        ),
        (rr >= G3_MIN_RISK_REWARD, f"RR {rr:.1f} < {G3_MIN_RISK_REWARD}"),
        (not btc_conflict, "BTC against trend"),
    ]
    for ok, reason in checks:
        if not ok:
            return CandidateG31(
                symbol=symbol,
                trend_score=trend.trend_score,
                market_score=market_score,
                liquidity_score=liquidity_score,
                confidence=confidence,
                rr=rr,
                btc_alignment=btc_alignment,
                funding_score=funding_score,
                oi_score=oi_score,
                volume_score=volume_score,
                atr_score=atr_score,
                fear_greed=fear_greed,
                candidate_state=STATE_REJECTED,
                rejection_reason=reason,
                direction=direction,
                trend=trend,
            )

    reason = "Waiting Claude" if waiting_claude else None
    return CandidateG31(
        symbol=symbol,
        trend_score=trend.trend_score,
        market_score=market_score,
        liquidity_score=liquidity_score,
        confidence=confidence,
        rr=rr,
        btc_alignment=btc_alignment,
        funding_score=funding_score,
        oi_score=oi_score,
        volume_score=volume_score,
        atr_score=atr_score,
        fear_greed=fear_greed,
        candidate_state=STATE_CANDIDATE,
        rejection_reason=reason,
        direction=direction,
        trend=trend,
    )


def build_candidates_g31(
    conn: Any,
    *,
    snapshot_id: int,
    trends: list[TrendWindowG3],
    liquidity: LiquidityStateG3,
    event_id: int | None = None,
) -> list[CandidateG31]:
    if not G31_ENABLED:
        return []

    snapshot_row = conn.execute(
        "SELECT * FROM market_snapshots_g3 WHERE id = ?",
        (snapshot_id,),
    ).fetchone()

    f5_conf = f7_score = f7_conf = None
    waiting_claude = False
    if event_id:
        f5 = conn.execute(
            "SELECT dynamic_confidence FROM market_events_signal_reports_f5 WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        f7 = conn.execute(
            "SELECT market_score, final_confidence FROM market_events_market_intelligence_f7 WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        g2 = conn.execute(
            "SELECT 1 FROM market_events_ai_research_g2 WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if f5:
            f5_conf = float(f5["dynamic_confidence"])
        if f7:
            f7_score = float(f7["market_score"])
            f7_conf = float(f7["final_confidence"])
        waiting_claude = g2 is None

    symbols = load_g31_universe_symbols(conn)
    candidates: list[CandidateG31] = []
    for sym in symbols:
        try:
            cand = evaluate_symbol_candidate_g31(
                conn,
                symbol=sym,
                trends=trends,
                liquidity=liquidity,
                snapshot_row=snapshot_row,
                f5_conf=f5_conf,
                f7_score=f7_score,
                f7_conf=f7_conf,
                waiting_claude=waiting_claude,
            )
            candidates.append(cand)
        except Exception as exc:
            logger.debug("g31 candidate %s skipped: %s", sym, exc)
            candidates.append(CandidateG31(
                symbol=sym,
                trend_score=None,
                market_score=None,
                liquidity_score=None,
                confidence=None,
                rr=None,
                btc_alignment="Neutral",
                funding_score=None,
                oi_score=None,
                volume_score=None,
                atr_score=None,
                fear_greed=None,
                candidate_state=STATE_INSUFFICIENT,
                rejection_reason=f"Evaluation error: {exc}",
                direction=None,
            ))
    return candidates


def persist_candidates_g31(
    conn: Any,
    *,
    snapshot_id: int,
    candidates: list[CandidateG31],
    candidate_ts: int | None = None,
) -> int:
    ts = candidate_ts or int(time.time())
    n = 0
    for c in candidates:
        cid = insert_returning_id(
            conn,
            f"""
            INSERT INTO {_TABLE} (
              snapshot_id, candidate_ts, symbol, trend_score, market_score, liquidity_score,
              confidence, rr, btc_alignment, funding_score, oi_score, volume_score, atr_score,
              fear_greed, candidate_state, rejection_reason, direction, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id, ts, c.symbol, c.trend_score, c.market_score, c.liquidity_score,
                c.confidence, c.rr, c.btc_alignment, c.funding_score, c.oi_score,
                c.volume_score, c.atr_score, c.fear_greed, c.candidate_state,
                c.rejection_reason, c.direction, ts,
            ),
        )
        try:
            from bot.research.market_events.signal_intelligence.replay_g32 import seed_outcome_g32
            seed_outcome_g32(
                conn,
                candidate_id=cid,
                symbol=c.symbol,
                direction=c.direction,
                created_at=ts,
                rr=c.rr,
            )
        except Exception as exc:
            logger.debug("g32 seed skipped %s: %s", c.symbol, exc)
        n += 1
    return n


def run_candidate_pipeline_g31(
    conn: Any,
    *,
    snapshot_id: int,
    trends: list[TrendWindowG3],
    liquidity: LiquidityStateG3,
    event_id: int | None = None,
) -> list[CandidateG31]:
    candidates = build_candidates_g31(
        conn, snapshot_id=snapshot_id, trends=trends, liquidity=liquidity, event_id=event_id,
    )
    if candidates:
        persist_candidates_g31(conn, snapshot_id=snapshot_id, candidates=candidates)
    return candidates


def pick_best_candidate_g31(candidates: list[CandidateG31]) -> CandidateG31 | None:
    ready = [c for c in candidates if c.candidate_state == STATE_CANDIDATE and c.confidence is not None]
    if not ready:
        return None
    return max(ready, key=lambda c: (c.confidence or 0, c.market_score or 0))


def mark_candidate_accepted_g31(conn: Any, *, snapshot_id: int, symbol: str) -> None:
    conn.execute(
        f"""
        UPDATE {_TABLE} SET candidate_state = ?, rejection_reason = NULL
        WHERE snapshot_id = ? AND symbol = ? AND candidate_state = ?
        """,
        (STATE_ACCEPTED, snapshot_id, symbol, STATE_CANDIDATE),
    )


def _latest_cycle_ts(conn: Any) -> int | None:
    row = conn.execute(f"SELECT MAX(candidate_ts) AS ts FROM {_TABLE}").fetchone()
    return int(row["ts"]) if row and row["ts"] else None


def fetch_top_candidates_g31(conn: Any, *, limit: int = 20) -> list[Any]:
    ts = _latest_cycle_ts(conn)
    if not ts:
        return []
    return conn.execute(
        f"""
        SELECT * FROM {_TABLE}
        WHERE candidate_ts = ?
        ORDER BY (confidence IS NULL), confidence DESC, (market_score IS NULL), market_score DESC
        LIMIT ?
        """,
        (ts, limit),
    ).fetchall()


def format_candidates_report(conn: Any, *, limit: int = 20) -> str:
    rows = fetch_top_candidates_g31(conn, limit=limit)
    if not rows:
        return "No candidates yet — run g3-run to build pipeline."

    lines: list[str] = ["G3.1 Candidates — latest cycle TOP", ""]
    for r in rows:
        conf = r["confidence"]
        conf_s = f"{float(conf):.1f}" if conf is not None else "—"
        state = r["candidate_state"]
        if state == STATE_REJECTED or state == STATE_INSUFFICIENT:
            status = "Rejected"
            reason = r["rejection_reason"] or "—"
        elif state == STATE_ACCEPTED:
            status = "Accepted"
            reason = "Signal generated"
        else:
            status = "Candidate"
            reason = r["rejection_reason"] or "Passed all gates"

        lines.extend([
            str(r["symbol"]),
            "",
            "Confidence",
            conf_s,
            "",
            status,
            "",
            "Reason",
            "",
            reason,
            "",
            "------------",
            "",
        ])
    return "\n".join(lines).rstrip()


def format_candidate_stats(conn: Any, *, hours: int = 24) -> str:
    since = int(time.time()) - hours * 3600
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_TABLE} WHERE created_at >= ?",
        (since,),
    ).fetchone()
    accepted = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_TABLE} WHERE created_at >= ? AND candidate_state = ?",
        (since, STATE_ACCEPTED),
    ).fetchone()
    rejected = conn.execute(
        f"""
        SELECT COUNT(*) AS n FROM {_TABLE}
        WHERE created_at >= ? AND candidate_state IN (?, ?)
        """,
        (since, STATE_REJECTED, STATE_INSUFFICIENT),
    ).fetchone()
    avg = conn.execute(
        f"""
        SELECT AVG(confidence) AS c, AVG(market_score) AS m
        FROM {_TABLE} WHERE created_at >= ? AND confidence IS NOT NULL
        """,
        (since,),
    ).fetchone()

    reason_rows = conn.execute(
        f"""
        SELECT rejection_reason, COUNT(*) AS n FROM {_TABLE}
        WHERE created_at >= ? AND rejection_reason IS NOT NULL AND rejection_reason != ''
        GROUP BY rejection_reason ORDER BY n DESC LIMIT 10
        """,
        (since,),
    ).fetchall()

    lines = [
        f"G3.1 Candidate Stats — last {hours}h",
        "",
        "Total candidates",
        str(int(total["n"] or 0)),
        "",
        "Accepted",
        str(int(accepted["n"] or 0)),
        "",
        "Rejected",
        str(int(rejected["n"] or 0)),
        "",
        "Average confidence",
        f"{float(avg['c'] or 0):.2f}",
        "",
        "Average market score",
        f"{float(avg['m'] or 0):.1f}",
        "",
        "TOP rejection reasons",
    ]
    if not reason_rows:
        lines.append("  (none)")
    else:
        for r in reason_rows:
            lines.append(f"  {r['rejection_reason']}: {r['n']}")
    return "\n".join(lines)


def candidate_stats_dict(conn: Any, *, hours: int = 24) -> dict[str, Any]:
    since = int(time.time()) - hours * 3600
    total = int(conn.execute(
        f"SELECT COUNT(*) AS n FROM {_TABLE} WHERE created_at >= ?", (since,),
    ).fetchone()["n"] or 0)
    accepted = int(conn.execute(
        f"SELECT COUNT(*) AS n FROM {_TABLE} WHERE created_at >= ? AND candidate_state = ?",
        (since, STATE_ACCEPTED),
    ).fetchone()["n"] or 0)
    rejected = int(conn.execute(
        f"""
        SELECT COUNT(*) AS n FROM {_TABLE}
        WHERE created_at >= ? AND candidate_state IN (?, ?)
        """,
        (since, STATE_REJECTED, STATE_INSUFFICIENT),
    ).fetchone()["n"] or 0)
    best = conn.execute(
        f"""
        SELECT symbol, confidence, market_score, rejection_reason, candidate_state
        FROM {_TABLE}
        WHERE created_at >= ? AND confidence IS NOT NULL
        ORDER BY confidence DESC LIMIT 1
        """,
        (since,),
    ).fetchone()
    top_reasons = conn.execute(
        f"""
        SELECT rejection_reason, COUNT(*) AS n FROM {_TABLE}
        WHERE created_at >= ? AND rejection_reason IS NOT NULL AND rejection_reason != ''
        GROUP BY rejection_reason ORDER BY n DESC LIMIT 5
        """,
        (since,),
    ).fetchall()
    return {
        "total": total,
        "accepted": accepted,
        "rejected": rejected,
        "filtered": rejected,
        "best_candidate": dict(best) if best else None,
        "top_rejection_reasons": [dict(r) for r in top_reasons],
    }


def build_idle_candidate_telegram_g31(conn: Any, *, hours: int | None = None) -> str | None:
    """Digest when no live signals for N hours."""
    window = hours if hours is not None else G31_IDLE_SIGNAL_HOURS
    since = int(time.time()) - window * 3600

    sig = conn.execute(
        """
        SELECT 1 FROM market_live_signals_g3
        WHERE created_at >= ? AND telegram_sent = 1 AND dashboard_only = 0
        LIMIT 1
        """,
        (since,),
    ).fetchone()
    if sig:
        return None

    stats = candidate_stats_dict(conn, hours=window)
    if stats["total"] == 0:
        return None

    best = stats["best_candidate"]
    lines = [
        f"За последние {window} часов",
        "",
        "проанализировано",
        str(stats["total"]),
        "",
        "Candidate",
        "",
        "Отфильтровано",
        str(stats["filtered"]),
    ]
    if best:
        lines.extend([
            "",
            "Лучший Candidate",
            "",
            str(best["symbol"]),
            "",
            "Confidence",
            f"{float(best['confidence']):.1f}",
            "",
            "Причина отказа",
            "",
            str(best["rejection_reason"] or "Passed gates — awaiting promotion"),
        ])
    return "\n".join(lines)


def should_send_idle_candidate_digest(conn: Any, *, hours: int | None = None) -> bool:
    return build_idle_candidate_telegram_g31(conn, hours=hours) is not None
