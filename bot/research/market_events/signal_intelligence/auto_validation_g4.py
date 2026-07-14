"""Phase G.4 — auto validation engine: ingest, measure, optimize (research-only)."""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.config import (
    G3_DAILY_REPORT_HOUR_LOCAL,
    G3_MIN_CONFIDENCE,
    G3_MIN_LIQUIDITY_PROB,
    G3_MIN_MARKET_SCORE,
    G3_MIN_RISK_REWARD,
    G4_ENABLED,
    G4_VALIDATION_INTERVAL_SEC,
)

logger = logging.getLogger(__name__)

_DEFAULT_TZ = "Europe/Moscow"
_RECORDS_TABLE = "market_validation_records_g4"
_FACTOR_TABLE = "market_validation_factor_stats_g4"
_SYMBOL_TABLE = "market_validation_symbol_stats_g4"
_ANALYSIS_TABLE = "market_validation_analysis_g4"
_DAILY_TABLE = "market_validation_daily_g4"
_RECOMMEND_TABLE = "market_validation_recommendations_g4"

FACTOR_NAMES = (
    "Funding", "OI", "Trend", "Volume", "ATR", "Dominance",
    "FearGreed", "Whales", "Claude", "History", "Liquidity",
)

FALSE_REJECT_MIN_PNL = 1.0
FALSE_ACCEPT_MAX_PNL = -0.5


@dataclass(frozen=True)
class ValidationCycleStatsG4:
    ingested: int = 0
    factor_stats: int = 0
    symbol_stats: int = 0
    optimizer_symbols: int = 0
    false_rejects: int = 0
    false_accepts: int = 0
    recommendations: int = 0


def _local_tz() -> ZoneInfo:
    import os
    name = os.getenv("ME_G3_REPORT_TZ", _DEFAULT_TZ)
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _today_key() -> str:
    return datetime.now(_local_tz()).strftime("%Y-%m-%d")


def _yesterday_key() -> str:
    tz = _local_tz()
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if now.hour < G3_DAILY_REPORT_HOUR_LOCAL:
        start = start.fromtimestamp(start.timestamp() - 86400, tz=tz)
    return (start.fromtimestamp(start.timestamp() - 86400, tz=tz)).strftime("%Y-%m-%d")


def _wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 0.0
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 4 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return round(num / (den_x * den_y), 3)


def _profit_factor(rows: list[Any]) -> float:
    gross_win = sum(max(0.0, float(r["pnl_pct"] or 0)) for r in rows)
    gross_loss = sum(abs(min(0.0, float(r["pnl_pct"] or 0))) for r in rows)
    if gross_loss > 0:
        return round(gross_win / gross_loss, 2)
    return round(gross_win, 2) if gross_win > 0 else 0.0


def _blocking_filter(reason: str | None) -> str | None:
    if not reason:
        return None
    low = reason.lower()
    if "confidence" in low:
        return "Confidence"
    if "market score" in low:
        return "Market Score"
    if "liquidity" in low:
        return "Liquidity"
    if "rr" in low or "risk" in low:
        return "RR"
    if "funding" in low:
        return "Funding"
    if "volume" in low:
        return "Volume"
    if "trend" in low or "reversal" in low:
        return "Trend"
    if "btc" in low:
        return "BTC Alignment"
    return reason.split()[0] if reason else None


def _dominance_score(val: Any) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).lower()
    if "against" in s or "counter" in s:
        return 25.0
    if "with" in s or "aligned" in s:
        return 75.0
    return 50.0


def _factors_from_row(row: Any, conn: Any) -> dict[str, float | None]:
    d = dict(row) if not isinstance(row, dict) else row
    factors: dict[str, float | None] = {
        "Funding": float(d["funding_score"]) if d.get("funding_score") is not None else None,
        "OI": float(d["oi_score"]) if d.get("oi_score") is not None else None,
        "Trend": float(d["trend_score"]) if d.get("trend_score") is not None else None,
        "Volume": float(d["volume_score"]) if d.get("volume_score") is not None else None,
        "ATR": float(d["atr_score"]) if d.get("atr_score") is not None else None,
        "Dominance": _dominance_score(d.get("btc_alignment")),
        "FearGreed": float(d["fear_greed"]) if d.get("fear_greed") is not None else None,
        "Liquidity": float(d["liquidity_score"]) if d.get("liquidity_score") is not None else None,
        "Whales": None,
        "Claude": None,
        "History": None,
    }
    cid = d.get("candidate_id") or d.get("id")
    if cid:
        breakdown = conn.execute(
            """
            SELECT factor, contribution, score_type
            FROM market_score_breakdown_g34
            WHERE candidate_id = ?
            """,
            (int(cid),),
        ).fetchall()
        for b in breakdown:
            fac = str(b["factor"])
            val = float(b["contribution"] or 0)
            if fac in ("Whales", "Claude", "History"):
                factors[fac] = val
            elif fac == "FearGreed" and factors["FearGreed"] is None:
                factors["FearGreed"] = val
    if d.get("reversal_probability") is not None and factors["Claude"] is None:
        factors["Claude"] = float(d["reversal_probability"]) * 100.0
    return factors


def _insert_record(
    conn: Any,
    *,
    source_type: str,
    source_id: int,
    symbol: str,
    direction: str | None,
    is_win: bool,
    pnl_pct: float | None,
    rr: float | None,
    confidence: float | None,
    market_score: float | None,
    liquidity_score: float | None,
    rejection_reason: str | None,
    blocking_filter: str | None,
    factors: dict[str, Any],
    outcome_ts: int | None,
) -> bool:
    now = int(time.time())
    try:
        insert_returning_id(
            conn,
            f"""
            INSERT OR IGNORE INTO {_RECORDS_TABLE} (
              source_type, source_id, symbol, direction, is_win, pnl_pct, rr,
              confidence, market_score, liquidity_score, rejection_reason,
              blocking_filter, factors_json, outcome_ts, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_type, source_id, symbol, direction,
                1 if is_win else 0, pnl_pct, rr,
                confidence, market_score, liquidity_score,
                rejection_reason, blocking_filter,
                json.dumps(factors), outcome_ts, now,
            ),
        )
        return True
    except Exception as exc:
        logger.debug("g4 ingest skipped %s/%s: %s", source_type, source_id, exc)
        return False


def sync_validation_records_g4(conn: Any) -> int:
    """Ingest completed replays, paper trades, G3 signals, rejected candidates."""
    if not G4_ENABLED:
        return 0
    n = 0

    replays = conn.execute(
        """
        SELECT o.id AS outcome_id, o.candidate_id, o.symbol, o.max_profit_pct,
               o.would_hit_tp, o.would_hit_sl, o.best_rr, o.updated_at,
               c.direction, c.confidence, c.market_score, c.liquidity_score, c.rr,
               c.rejection_reason, c.candidate_state,
               c.funding_score, c.oi_score, c.trend_score, c.volume_score,
               c.atr_score, c.fear_greed, c.btc_alignment
        FROM market_candidate_outcomes_g32 o
        JOIN market_candidate_g31 c ON c.id = o.candidate_id
        WHERE o.replay_status = 'COMPLETE'
        """,
    ).fetchall()
    for r in replays:
        pnl = float(r["max_profit_pct"] or 0)
        is_win = bool(r["would_hit_tp"]) or pnl > 0
        src = "rejected_candidate" if r["candidate_state"] != "accepted" else "replay_complete"
        factors = _factors_from_row({**dict(r), "candidate_id": r["candidate_id"]}, conn)
        if _insert_record(
            conn,
            source_type=src,
            source_id=int(r["outcome_id"]),
            symbol=str(r["symbol"]),
            direction=r["direction"],
            is_win=is_win,
            pnl_pct=pnl,
            rr=float(r["best_rr"] or r["rr"] or 0),
            confidence=float(r["confidence"]) if r["confidence"] is not None else None,
            market_score=float(r["market_score"]) if r["market_score"] is not None else None,
            liquidity_score=float(r["liquidity_score"]) if r["liquidity_score"] is not None else None,
            rejection_reason=r["rejection_reason"],
            blocking_filter=_blocking_filter(r["rejection_reason"]),
            factors=factors,
            outcome_ts=int(r["updated_at"] or time.time()),
        ):
            n += 1

    papers = conn.execute(
        """
        SELECT id, event_id, entry_ts, exit_ts, net_return, strategy_name
        FROM paper_strategy_runs
        WHERE exit_ts IS NOT NULL AND entry_ts IS NOT NULL
        """,
    ).fetchall()
    for p in papers:
        evt = conn.execute(
            "SELECT canonical_asset, direction FROM market_events WHERE id = ?",
            (p["event_id"],),
        ).fetchone()
        symbol = str(evt["canonical_asset"]) if evt else "UNKNOWN"
        direction = evt["direction"] if evt else None
        pnl = float(p["net_return"] or 0) * 100.0
        if _insert_record(
            conn,
            source_type="paper_trade",
            source_id=int(p["id"]),
            symbol=symbol,
            direction=direction,
            is_win=pnl > 0,
            pnl_pct=pnl,
            rr=None,
            confidence=None,
            market_score=None,
            liquidity_score=None,
            rejection_reason=None,
            blocking_filter=None,
            factors={"detector": p["strategy_name"]},
            outcome_ts=int(p["exit_ts"]),
        ):
            n += 1

    signals = conn.execute(
        """
        SELECT id, symbol, direction, confidence, market_score, risk_reward,
               pnl_pct, max_profit_pct, status, closed_at, created_at, snapshot_id
        FROM market_live_signals_g3
        WHERE status IN ('closed', 'stopped', 'target_hit') OR closed_at IS NOT NULL
        """,
    ).fetchall()
    for s in signals:
        pnl = float(s["pnl_pct"] if s["pnl_pct"] is not None else s["max_profit_pct"] or 0)
        liq_row = conn.execute(
            "SELECT probability FROM market_liquidity_state_g3 WHERE snapshot_id = ?",
            (s["snapshot_id"],),
        ).fetchone() if s["snapshot_id"] else None
        cand = conn.execute(
            """
            SELECT id, funding_score, oi_score, trend_score, volume_score, atr_score,
                   fear_greed, btc_alignment, liquidity_score
            FROM market_candidate_g31
            WHERE snapshot_id = ? AND symbol = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (s["snapshot_id"], s["symbol"]),
        ).fetchone() if s["snapshot_id"] else None
        factors: dict[str, Any] = {"detector": "g3_live"}
        if cand:
            factors.update(_factors_from_row(dict(cand), conn))
        if _insert_record(
            conn,
            source_type="g3_signal",
            source_id=int(s["id"]),
            symbol=str(s["symbol"]),
            direction=s["direction"],
            is_win=pnl > 0,
            pnl_pct=pnl,
            rr=float(s["risk_reward"] or 0),
            confidence=float(s["confidence"] or 0),
            market_score=float(s["market_score"] or 0),
            liquidity_score=float(liq_row["probability"]) if liq_row else None,
            rejection_reason=None,
            blocking_filter=None,
            factors=factors,
            outcome_ts=int(s["closed_at"] or s["created_at"]),
        ):
            n += 1

    rejected_only = conn.execute(
        """
        SELECT c.id, c.symbol, c.direction, c.confidence, c.market_score, c.liquidity_score, c.rr,
               c.rejection_reason, c.created_at,
               c.funding_score, c.oi_score, c.trend_score, c.volume_score,
               c.atr_score, c.fear_greed, c.btc_alignment
        FROM market_candidate_g31 c
        LEFT JOIN market_candidate_outcomes_g32 o ON o.candidate_id = c.id
        WHERE c.candidate_state IN ('rejected', 'provisional') AND o.id IS NULL
        """,
    ).fetchall()
    for r in rejected_only:
        factors = _factors_from_row({**dict(r), "candidate_id": r["id"]}, conn)
        if _insert_record(
            conn,
            source_type="rejected_candidate",
            source_id=int(r["id"]),
            symbol=str(r["symbol"]),
            direction=r["direction"],
            is_win=False,
            pnl_pct=None,
            rr=float(r["rr"] or 0),
            confidence=float(r["confidence"]) if r["confidence"] is not None else None,
            market_score=float(r["market_score"]) if r["market_score"] is not None else None,
            liquidity_score=float(r["liquidity_score"]) if r["liquidity_score"] is not None else None,
            rejection_reason=r["rejection_reason"],
            blocking_filter=_blocking_filter(r["rejection_reason"]),
            factors=factors,
            outcome_ts=int(r["created_at"]),
        ):
            n += 1

    return n


def _factor_buckets(records: list[Any]) -> dict[str, list[Any]]:
    buckets: dict[str, list[Any]] = {f: [] for f in FACTOR_NAMES}
    for r in records:
        try:
            factors = json.loads(r["factors_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            factors = {}
        win = bool(r["is_win"])
        pnl = float(r["pnl_pct"] or 0)
        rr = float(r["rr"] or 0)
        for name in FACTOR_NAMES:
            val = factors.get(name)
            if val is None:
                continue
            buckets[name].append({"value": float(val), "win": win, "pnl": pnl, "rr": rr})
    return buckets


def compute_factor_stats_g4(conn: Any, *, report_date: str, days: int = 30) -> int:
    since = int(time.time()) - days * 86400
    records = conn.execute(
        f"SELECT * FROM {_RECORDS_TABLE} WHERE created_at >= ?",
        (since,),
    ).fetchall()
    buckets = _factor_buckets(records)
    now = int(time.time())
    stats: list[tuple[str, float, float, float, int, float, float, float]] = []

    for factor, items in buckets.items():
        if len(items) < 3:
            continue
        median = sorted(i["value"] for i in items)[len(items) // 2]
        high = [i for i in items if i["value"] >= median]
        if len(high) < 2:
            continue
        wins = sum(1 for i in high if i["win"])
        n = len(high)
        win_rate = wins / n
        ci_low, ci_high = _wilson_ci(wins, n)
        avg_rr = sum(i["rr"] for i in high if i["rr"]) / max(1, sum(1 for i in high if i["rr"]))
        pf = _profit_factor([{"pnl_pct": i["pnl"]} for i in high])
        win_flags = [1.0 if i["win"] else 0.0 for i in high]
        corr = _pearson([i["value"] for i in high], win_flags) or 0.0
        stats.append((factor, win_rate, avg_rr, pf, n, ci_low, ci_high, corr))

    stats.sort(key=lambda s: -abs(s[7]))
    n = 0
    for rank, (factor, win_rate, avg_rr, pf, sample, ci_low, ci_high, importance) in enumerate(stats, start=1):
        if importance >= 0.08:
            ptype = "useful"
        elif importance <= -0.08:
            ptype = "negative"
        else:
            ptype = "weak"
        conn.execute(
            f"""
            INSERT INTO {_FACTOR_TABLE} (
              report_date, factor, win_rate, avg_rr, profit_factor, sample_size,
              ci_low, ci_high, importance, rank_order, predictor_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_date, factor) DO UPDATE SET
              win_rate = excluded.win_rate, avg_rr = excluded.avg_rr,
              profit_factor = excluded.profit_factor, sample_size = excluded.sample_size,
              ci_low = excluded.ci_low, ci_high = excluded.ci_high,
              importance = excluded.importance, rank_order = excluded.rank_order,
              predictor_type = excluded.predictor_type, created_at = excluded.created_at
            """,
            (
                report_date, factor, round(win_rate, 3), round(avg_rr, 2), pf, sample,
                round(ci_low, 3), round(ci_high, 3), importance, rank, ptype, now,
            ),
        )
        n += 1
    return n


def compute_symbol_stats_g4(conn: Any, *, report_date: str, days: int = 30) -> int:
    since = int(time.time()) - days * 86400
    rows = conn.execute(
        f"""
        SELECT symbol, source_type, is_win, pnl_pct, rr, factors_json
        FROM {_RECORDS_TABLE}
        WHERE created_at >= ?
        """,
        (since,),
    ).fetchall()
    by_key: dict[tuple[str, str], list[Any]] = {}
    for r in rows:
        try:
            det = json.loads(r["factors_json"] or "{}").get("detector", r["source_type"])
        except (json.JSONDecodeError, TypeError):
            det = r["source_type"]
        setup = f"{r['source_type']}:{det}"
        by_key.setdefault((str(r["symbol"]), setup), []).append(r)

    now = int(time.time())
    n = 0
    for (symbol, setup), items in by_key.items():
        if len(items) < 2:
            continue
        wins = sum(1 for i in items if i["is_win"])
        win_rate = wins / len(items)
        avg_rr = sum(float(i["rr"] or 0) for i in items) / max(1, sum(1 for i in items if i["rr"]))
        pf = _profit_factor(items)
        best_det = setup.split(":", 1)[-1]
        conn.execute(
            f"""
            INSERT INTO {_SYMBOL_TABLE} (
              report_date, symbol, setup_label, win_rate, avg_rr, profit_factor,
              sample_size, best_detector, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_date, symbol, setup_label) DO UPDATE SET
              win_rate = excluded.win_rate, avg_rr = excluded.avg_rr,
              profit_factor = excluded.profit_factor, sample_size = excluded.sample_size,
              best_detector = excluded.best_detector, created_at = excluded.created_at
            """,
            (
                report_date, symbol, setup, round(win_rate, 3), round(avg_rr, 2),
                pf, len(items), best_det, now,
            ),
        )
        n += 1
    return n


def compute_feature_importance_g4(conn: Any, *, report_date: str | None = None) -> list[dict[str, Any]]:
    date = report_date or _today_key()
    rows = conn.execute(
        f"""
        SELECT factor, win_rate, profit_factor, sample_size, importance,
               predictor_type, ci_low, ci_high, rank_order
        FROM {_FACTOR_TABLE}
        WHERE report_date = ?
        ORDER BY rank_order ASC
        """,
        (date,),
    ).fetchall()
    return [dict(r) for r in rows]


def analyze_false_rejects_g4(conn: Any, *, report_date: str, days: int = 30) -> int:
    since = int(time.time()) - days * 86400
    rows = conn.execute(
        f"""
        SELECT source_type, source_id, symbol, pnl_pct, blocking_filter, rejection_reason
        FROM {_RECORDS_TABLE}
        WHERE created_at >= ? AND source_type = 'rejected_candidate'
          AND pnl_pct >= ? AND is_win = 1
        ORDER BY pnl_pct DESC
        LIMIT 50
        """,
        (since, FALSE_REJECT_MIN_PNL),
    ).fetchall()
    now = int(time.time())
    conn.execute(
        f"DELETE FROM {_ANALYSIS_TABLE} WHERE report_date = ? AND analysis_type = 'false_reject'",
        (report_date,),
    )
    n = 0
    for r in rows:
        conn.execute(
            f"""
            INSERT INTO {_ANALYSIS_TABLE} (
              report_date, analysis_type, source_type, source_id, symbol,
              pnl_pct, blocking_filter, misleading_factors_json, created_at
            ) VALUES (?, 'false_reject', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_date, r["source_type"], r["source_id"], r["symbol"],
                r["pnl_pct"], r["blocking_filter"] or _blocking_filter(r["rejection_reason"]),
                json.dumps({"rejection_reason": r["rejection_reason"]}), now,
            ),
        )
        n += 1
    return n


def analyze_false_accepts_g4(conn: Any, *, report_date: str, days: int = 30) -> int:
    since = int(time.time()) - days * 86400
    rows = conn.execute(
        f"""
        SELECT source_type, source_id, symbol, pnl_pct, factors_json
        FROM {_RECORDS_TABLE}
        WHERE created_at >= ?
          AND source_type IN ('replay_complete', 'g3_signal')
          AND is_win = 0 AND pnl_pct <= ?
        ORDER BY pnl_pct ASC
        LIMIT 50
        """,
        (since, FALSE_ACCEPT_MAX_PNL),
    ).fetchall()
    now = int(time.time())
    conn.execute(
        f"DELETE FROM {_ANALYSIS_TABLE} WHERE report_date = ? AND analysis_type = 'false_accept'",
        (report_date,),
    )
    n = 0
    for r in rows:
        try:
            factors = json.loads(r["factors_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            factors = {}
        misleading = {
            k: v for k, v in factors.items()
            if isinstance(v, (int, float)) and v >= 60
        }
        conn.execute(
            f"""
            INSERT INTO {_ANALYSIS_TABLE} (
              report_date, analysis_type, source_type, source_id, symbol,
              pnl_pct, blocking_filter, misleading_factors_json, created_at
            ) VALUES (?, 'false_accept', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_date, r["source_type"], r["source_id"], r["symbol"],
                r["pnl_pct"], "accepted_signal", json.dumps(misleading), now,
            ),
        )
        n += 1
    return n


def build_recommendations_g4(conn: Any, *, report_date: str, days: int = 30) -> int:
    from bot.research.market_events.signal_intelligence.config import G3_MIN_RISK_REWARD
    from bot.research.market_events.signal_intelligence.threshold_optimizer_g42 import (
        persist_optimizer_g42,
        run_threshold_optimizer_g42,
    )

    scenarios = run_threshold_optimizer_g42(conn, days=days)
    persist_optimizer_g42(conn, report_date=report_date, scenarios=scenarios)

    now = int(time.time())
    conn.execute(f"DELETE FROM {_RECOMMEND_TABLE} WHERE report_date = ?", (report_date,))
    n = 0

    for s in scenarios:
        recs: list[tuple[str | None, str, str]] = []
        if s.min_market_score < G3_MIN_MARKET_SCORE - 2:
            recs.append((
                s.symbol,
                f"Reduce Market Score threshold for {s.symbol}",
                f"Learned optimum {s.min_market_score:.0f} vs current {G3_MIN_MARKET_SCORE:.0f} "
                f"(PF {s.profit_factor:.2f}, n={s.sample_size})",
            ))
        if s.min_liquidity > G3_MIN_LIQUIDITY_PROB + 0.02:
            recs.append((
                s.symbol,
                f"Increase Liquidity threshold for {s.symbol}",
                f"Learned optimum {s.min_liquidity:.2f} vs current {G3_MIN_LIQUIDITY_PROB:.2f}",
            ))
        if s.min_rr > G3_MIN_RISK_REWARD + 0.2:
            recs.append((
                s.symbol,
                f"Increase RR on {s.symbol}",
                f"Learned optimum RR ≥{s.min_rr:.1f} vs current {G3_MIN_RISK_REWARD:.1f}",
            ))
        if s.min_confidence < G3_MIN_CONFIDENCE - 0.2:
            recs.append((
                s.symbol,
                f"Reduce Confidence threshold for {s.symbol}",
                f"Learned optimum {s.min_confidence:.1f} vs current {G3_MIN_CONFIDENCE:.1f}",
            ))

        factor_rows = conn.execute(
            f"""
            SELECT factor, predictor_type, importance
            FROM {_FACTOR_TABLE}
            WHERE report_date = ? AND predictor_type = 'negative'
            ORDER BY importance ASC LIMIT 3
            """,
            (report_date,),
        ).fetchall()
        for fr in factor_rows:
            recs.append((
                s.symbol,
                f"Ignore {fr['factor']} on {s.symbol}",
                f"Negative predictor (importance {fr['importance']:+.3f})",
            ))

        for symbol, text, rationale in recs[:6]:
            conn.execute(
                f"""
                INSERT INTO {_RECOMMEND_TABLE} (report_date, symbol, recommendation, rationale, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (report_date, symbol, text, rationale, now),
            )
            n += 1

    importance = compute_feature_importance_g4(conn, report_date=report_date)
    if importance:
        best = importance[0]
        worst = importance[-1]
        for text, rationale in (
            (
                f"Weight {best['factor']} higher",
                f"Top predictor (importance {best['importance']:+.3f}, WR {best['win_rate']:.0%})",
            ),
            (
                f"De-weight {worst['factor']}",
                f"Weakest/negative ({worst['predictor_type']}, {worst['importance']:+.3f})",
            ),
        ):
            conn.execute(
                f"""
                INSERT INTO {_RECOMMEND_TABLE} (report_date, symbol, recommendation, rationale, created_at)
                VALUES (?, NULL, ?, ?, ?)
                """,
                (report_date, text, rationale, now),
            )
            n += 1
    return n


def run_validation_cycle_g4(conn: Any, *, days: int = 30) -> ValidationCycleStatsG4:
    report_date = _today_key()
    ingested = sync_validation_records_g4(conn)
    factor_stats = compute_factor_stats_g4(conn, report_date=report_date, days=days)
    symbol_stats = compute_symbol_stats_g4(conn, report_date=report_date, days=days)
    false_rejects = analyze_false_rejects_g4(conn, report_date=report_date, days=days)
    false_accepts = analyze_false_accepts_g4(conn, report_date=report_date, days=days)
    recommendations = build_recommendations_g4(conn, report_date=report_date, days=days)

    from bot.research.market_events.signal_intelligence.threshold_optimizer_g42 import (
        run_threshold_optimizer_g42,
    )
    optimizer_symbols = len(run_threshold_optimizer_g42(conn, days=days))

    summary = {
        "report_date": report_date,
        "ingested": ingested,
        "factor_stats": factor_stats,
        "symbol_stats": symbol_stats,
        "false_rejects": false_rejects,
        "false_accepts": false_accepts,
        "recommendations": recommendations,
        "optimizer_symbols": optimizer_symbols,
    }
    now = int(time.time())
    conn.execute(
        f"""
        INSERT INTO {_DAILY_TABLE} (report_date, report_json, telegram_sent, created_at)
        VALUES (?, ?, 0, ?)
        ON CONFLICT(report_date) DO UPDATE SET report_json = excluded.report_json, created_at = excluded.created_at
        """,
        (report_date, json.dumps(summary), now),
    )
    from bot.research.market_events.signal_intelligence.health_g3 import set_g3_ops_state
    set_g3_ops_state(conn, "last_g4_validation_ts", str(now))
    return ValidationCycleStatsG4(
        ingested=ingested,
        factor_stats=factor_stats,
        symbol_stats=symbol_stats,
        optimizer_symbols=optimizer_symbols,
        false_rejects=false_rejects,
        false_accepts=false_accepts,
        recommendations=recommendations,
    )


def maybe_run_validation_g4(conn: Any) -> ValidationCycleStatsG4 | None:
    if not G4_ENABLED:
        return None
    from bot.research.market_events.signal_intelligence.health_g3 import get_g3_ops_state
    now = int(time.time())
    last_raw = get_g3_ops_state(conn, "last_g4_validation_ts")
    last = int(last_raw) if last_raw else 0
    if now - last < G4_VALIDATION_INTERVAL_SEC:
        return None
    try:
        return run_validation_cycle_g4(conn)
    except Exception as exc:
        logger.debug("g4 validation cycle skipped: %s", exc)
        return None


def _yesterday_bounds() -> tuple[int, int]:
    tz = _local_tz()
    now_local = datetime.now(tz)
    start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    if now_local.hour < G3_DAILY_REPORT_HOUR_LOCAL:
        start = start.fromtimestamp(start.timestamp() - 86400, tz=tz)
    day_start = int(start.astimezone(timezone.utc).timestamp()) - 86400
    day_end = day_start + 86400 - 1
    return day_start, day_end


def _yesterday_validation_summary(conn: Any) -> dict[str, Any]:
    day_start, day_end = _yesterday_bounds()
    day = datetime.fromtimestamp(day_start, tz=_local_tz()).strftime("%Y-%m-%d")
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_RECORDS_TABLE} WHERE outcome_ts BETWEEN ? AND ?",
        (day_start, day_end),
    ).fetchone()
    wins = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_RECORDS_TABLE} WHERE outcome_ts BETWEEN ? AND ? AND is_win = 1",
        (day_start, day_end),
    ).fetchone()
    n_total = int(total["n"] if total else 0)
    n_wins = int(wins["n"] if wins else 0)
    wr = (n_wins / n_total) if n_total else 0.0

    by_source = conn.execute(
        f"""
        SELECT source_type,
               COUNT(*) AS n,
               SUM(CASE WHEN is_win = 1 THEN 1 ELSE 0 END) AS wins
        FROM {_RECORDS_TABLE}
        WHERE outcome_ts BETWEEN ? AND ?
        GROUP BY source_type
        ORDER BY n DESC
        """,
        (day_start, day_end),
    ).fetchall()
    detectors = [
        {"source": r["source_type"], "n": int(r["n"]), "win_rate": int(r["wins"]) / int(r["n"])}
        for r in by_source if int(r["n"]) >= 1
    ]
    best_det = max(detectors, key=lambda d: d["win_rate"], default=None)
    worst_det = min(detectors, key=lambda d: d["win_rate"], default=None)

    importance = compute_feature_importance_g4(conn, report_date=_today_key())
    best_factor = importance[0] if importance else None
    worst_factor = importance[-1] if importance else None

    recs = conn.execute(
        f"SELECT recommendation, rationale FROM {_RECOMMEND_TABLE} WHERE report_date = ? LIMIT 5",
        (_today_key(),),
    ).fetchall()

    return {
        "date": day,
        "signals": n_total,
        "win_rate": round(wr, 3),
        "best_detector": best_det,
        "worst_detector": worst_det,
        "best_factor": dict(best_factor) if best_factor else None,
        "worst_factor": dict(worst_factor) if worst_factor else None,
        "recommendations": [dict(r) for r in recs],
    }


def build_validation_daily_telegram_g4(conn: Any) -> str:
    s = _yesterday_validation_summary(conn)
    lines = [
        "G4 Validation Report",
        "",
        "Yesterday",
        s["date"],
        "",
        "Signals",
        str(s["signals"]),
        "",
        "Win Rate",
        f"{s['win_rate'] * 100:.0f}%",
        "",
    ]
    if s["best_detector"]:
        lines.extend([
            "Best detector",
            f"{s['best_detector']['source']} ({s['best_detector']['win_rate'] * 100:.0f}%)",
            "",
        ])
    if s["worst_detector"]:
        lines.extend([
            "Worst detector",
            f"{s['worst_detector']['source']} ({s['worst_detector']['win_rate'] * 100:.0f}%)",
            "",
        ])
    if s["best_factor"]:
        lines.extend([
            "Most useful factor",
            f"{s['best_factor']['factor']} ({s['best_factor']['importance']:+.3f})",
            "",
        ])
    if s["worst_factor"]:
        lines.extend([
            "Worst factor",
            f"{s['worst_factor']['factor']} ({s['worst_factor']['importance']:+.3f})",
            "",
        ])
    if s["recommendations"]:
        lines.extend(["Suggested improvements", ""])
        for r in s["recommendations"][:5]:
            lines.extend([r["recommendation"], r["rationale"] or "", ""])
    return "\n".join(lines).rstrip()


def maybe_send_validation_daily_g4(conn: Any) -> bool:
    if not G4_ENABLED:
        return False
    tz = _local_tz()
    now_local = datetime.now(tz)
    if now_local.hour != G3_DAILY_REPORT_HOUR_LOCAL:
        return False

    report_date = _today_key()
    existing = conn.execute(
        f"SELECT telegram_sent FROM {_DAILY_TABLE} WHERE report_date = ?",
        (report_date,),
    ).fetchone()
    if existing and int(existing["telegram_sent"] or 0):
        return False

    msg = build_validation_daily_telegram_g4(conn)
    from bot.research.market_events.alert_config import alert_shock_enabled
    from bot.research.market_events.market_event_alerts import _safe_alert

    sent = False
    if alert_shock_enabled():
        sent = _safe_alert(
            conn,
            event_id=0,
            alert_type="HEARTBEAT",
            detail=f"g4-validation-{report_date}",
            message=msg,
            enabled=True,
        )

    now = int(time.time())
    conn.execute(
        f"""
        INSERT INTO {_DAILY_TABLE} (report_date, report_json, telegram_sent, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(report_date) DO UPDATE SET telegram_sent = excluded.telegram_sent
        """,
        (report_date, json.dumps(_yesterday_validation_summary(conn)), 1 if sent else 0, now),
    )
    return sent


def validation_dashboard_g4(conn: Any, *, days: int = 30) -> dict[str, Any]:
    report_date = _today_key()
    since = int(time.time()) - days * 86400
    total = conn.execute(f"SELECT COUNT(*) AS n FROM {_RECORDS_TABLE} WHERE created_at >= ?", (since,)).fetchone()
    wins = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_RECORDS_TABLE} WHERE created_at >= ? AND is_win = 1",
        (since,),
    ).fetchone()
    n = int(total["n"] if total else 0)
    w = int(wins["n"] if wins else 0)
    return {
        "tab": "Validation",
        "report_date": report_date,
        "days": days,
        "records_total": n,
        "win_rate": round(w / n, 3) if n else 0.0,
        "factor_importance": compute_feature_importance_g4(conn, report_date=report_date),
        "false_rejects": [
            dict(r) for r in conn.execute(
                f"SELECT * FROM {_ANALYSIS_TABLE} WHERE report_date = ? AND analysis_type = 'false_reject' LIMIT 30",
                (report_date,),
            ).fetchall()
        ],
        "false_accepts": [
            dict(r) for r in conn.execute(
                f"SELECT * FROM {_ANALYSIS_TABLE} WHERE report_date = ? AND analysis_type = 'false_accept' LIMIT 30",
                (report_date,),
            ).fetchall()
        ],
        "optimizer": [
            dict(r) for r in conn.execute(
                "SELECT * FROM market_validation_optimizer_g4 WHERE report_date = ? ORDER BY profit_factor DESC",
                (report_date,),
            ).fetchall()
        ],
        "recommendations": [
            dict(r) for r in conn.execute(
                f"SELECT * FROM {_RECOMMEND_TABLE} WHERE report_date = ?",
                (report_date,),
            ).fetchall()
        ],
    }


def format_validation_report_g4(conn: Any, *, days: int = 30) -> str:
    dash = validation_dashboard_g4(conn, days=days)
    lines = [
        "G4 Validation Report",
        "",
        f"Window: last {days} days",
        "",
        "Records",
        str(dash["records_total"]),
        "",
        "Win Rate",
        f"{dash['win_rate'] * 100:.0f}%",
        "",
        "Factor stats",
        "",
    ]
    for f in dash["factor_importance"][:8]:
        lines.append(
            f"  {f['factor']}: WR {f['win_rate']:.0%} PF {f['profit_factor']:.2f} "
            f"n={f['sample_size']} ({f['predictor_type']})"
        )
    return "\n".join(lines)


def format_feature_importance_report_g4(conn: Any) -> str:
    rows = compute_feature_importance_g4(conn, report_date=_today_key())
    if not rows:
        return "G4 Feature Importance — run validation cycle first."
    lines = ["G4 Feature Importance", "", "Ranking (most → least useful)", ""]
    for r in rows:
        lines.append(
            f"{r['rank_order']}. {r['factor']}: {r['importance']:+.3f} "
            f"({r['predictor_type']}, WR {r['win_rate']:.0%}, n={r['sample_size']})"
        )
    return "\n".join(lines)


def format_false_rejects_report_g4(conn: Any) -> str:
    rows = conn.execute(
        f"""
        SELECT symbol, pnl_pct, blocking_filter, misleading_factors_json
        FROM {_ANALYSIS_TABLE}
        WHERE report_date = ? AND analysis_type = 'false_reject'
        ORDER BY pnl_pct DESC LIMIT 20
        """,
        (_today_key(),),
    ).fetchall()
    if not rows:
        return "G4 False Rejects — none found (or run validation cycle)."
    lines = ["G4 False Reject Analysis", ""]
    for r in rows:
        try:
            meta = json.loads(r["misleading_factors_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        lines.extend([
            str(r["symbol"]),
            f"+{float(r['pnl_pct']):.1f}%",
            "",
            "Blocked by",
            str(r["blocking_filter"] or meta.get("rejection_reason") or "—"),
            "",
        ])
    return "\n".join(lines).rstrip()


def format_false_accepts_report_g4(conn: Any) -> str:
    rows = conn.execute(
        f"""
        SELECT symbol, pnl_pct, misleading_factors_json
        FROM {_ANALYSIS_TABLE}
        WHERE report_date = ? AND analysis_type = 'false_accept'
        ORDER BY pnl_pct ASC LIMIT 20
        """,
        (_today_key(),),
    ).fetchall()
    if not rows:
        return "G4 False Accepts — none found (or run validation cycle)."
    lines = ["G4 False Accept Analysis", ""]
    for r in rows:
        try:
            misleading = json.loads(r["misleading_factors_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            misleading = {}
        factors = ", ".join(f"{k}={v}" for k, v in misleading.items()) or "—"
        lines.extend([
            str(r["symbol"]),
            f"{float(r['pnl_pct']):+.1f}%",
            "",
            "Misleading factors",
            factors,
            "",
        ])
    return "\n".join(lines).rstrip()
