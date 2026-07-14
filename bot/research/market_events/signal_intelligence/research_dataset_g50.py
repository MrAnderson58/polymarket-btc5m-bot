"""Phase G.5.0 — unified research dataset for Quant Research Analyst."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any

DEFAULT_MAX_RECORDS = 1000
DEFAULT_DAYS = 30


def _iso_date(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def _tp_sl_from_rr(entry: float | None, rr: float | None, *, direction: str | None) -> tuple[float | None, float | None]:
    if entry is None or rr is None or entry <= 0:
        return None, None
    stop_pct = 0.01
    tp_pct = stop_pct * float(rr)
    is_long = str(direction or "LONG").upper() == "LONG"
    if is_long:
        return round(entry * (1 + tp_pct), 6), round(entry * (1 - stop_pct), 6)
    return round(entry * (1 - tp_pct), 6), round(entry * (1 + stop_pct), 6)


def _claude_summary(conn: Any, *, snapshot_id: int | None, symbol: str) -> str | None:
    if not snapshot_id:
        return None
    row = conn.execute(
        """
        SELECT g2.summary_ru, g2.market_story
        FROM market_snapshots_g3 s
        JOIN market_events e ON e.event_ts <= s.snapshot_ts
        JOIN market_events_ai_research_g2 g2 ON g2.event_id = e.id AND g2.symbol = ?
        WHERE s.id = ?
        ORDER BY g2.created_at DESC LIMIT 1
        """,
        (symbol, snapshot_id),
    ).fetchone()
    if not row:
        return None
    return str(row["summary_ru"] or row["market_story"] or "") or None


def _historical_pattern(conn: Any, *, symbol: str, direction: str | None) -> str | None:
    try:
        from bot.research.market_events.signal_intelligence.reversal_learning_g1 import (
            lookup_historical_reversal_rate,
        )
        rate = lookup_historical_reversal_rate(conn, symbol=symbol, direction=direction or "LONG")
        if rate is None:
            return None
        return f"historical_reversal_rate={rate:.2f}"
    except Exception:
        return None


def _paper_outcome(conn: Any, *, symbol: str, ts: int) -> str | None:
    try:
        row = conn.execute(
            """
            SELECT net_return, strategy_name FROM paper_strategy_runs p
            JOIN market_events e ON e.id = p.event_id
            WHERE e.symbol = ? AND p.exit_ts IS NOT NULL
              AND ABS(p.exit_ts - ?) < 7200
            ORDER BY ABS(p.exit_ts - ?) ASC LIMIT 1
            """,
            (symbol, ts, ts),
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    pnl = float(row["net_return"] or 0) * 100.0
    return f"{row['strategy_name']}: {pnl:+.2f}%"


def _row_to_record(conn: Any, row: Any) -> dict[str, Any]:
    d = dict(row)
    entry = float(d["price_entry"]) if d.get("price_entry") is not None else None
    rr = float(d["rr"]) if d.get("rr") is not None else None
    tp, sl = _tp_sl_from_rr(entry, rr, direction=d.get("direction"))
    created = int(d.get("created_at") or d.get("candidate_ts") or 0)
    outcome_ts = int(d.get("outcome_updated_at") or created)
    holding = max(0, outcome_ts - created) if outcome_ts and created else None

    state = str(d.get("candidate_state") or "")
    accepted = state == "accepted"
    rejected = state in ("rejected", "provisional")

    max_profit = d.get("max_profit_pct")
    max_dd = d.get("max_drawdown_pct")
    final_pnl = max_profit
    if d.get("signal_pnl_pct") is not None:
        final_pnl = d["signal_pnl_pct"]

    return {
        "symbol": str(d.get("symbol") or ""),
        "date": _iso_date(created),
        "trend": d.get("trend_score"),
        "trend_coverage_pct": d.get("trend_coverage_pct"),
        "funding": d.get("funding_score"),
        "oi": d.get("oi_score"),
        "volume": d.get("volume_score"),
        "atr": d.get("atr_score"),
        "fear_greed": d.get("fear_greed"),
        "dominance": d.get("btc_alignment"),
        "market_score": d.get("market_score"),
        "confidence": d.get("confidence"),
        "liquidity_probability": d.get("liquidity_score"),
        "rr": rr,
        "claude_summary": d.get("claude_summary") or _claude_summary(
            conn, snapshot_id=d.get("snapshot_id"), symbol=str(d.get("symbol") or ""),
        ),
        "accepted": accepted,
        "rejected": rejected,
        "rejection_reason": d.get("rejection_reason"),
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "max_profit": max_profit,
        "max_drawdown": max_dd,
        "final_pnl": final_pnl,
        "holding_time_sec": holding,
        "paper_outcome": _paper_outcome(conn, symbol=str(d.get("symbol") or ""), ts=created),
        "candidate_outcome": {
            "would_hit_tp": bool(d.get("would_hit_tp")),
            "replay_status": d.get("replay_status"),
            "is_win": bool(d.get("is_win")),
        },
        "historical_pattern": _historical_pattern(
            conn, symbol=str(d.get("symbol") or ""), direction=d.get("direction"),
        ),
        "author": d.get("source_type") or "candidate_pipeline",
        "btc_regime": d.get("btc_alignment"),
        "direction": d.get("direction"),
    }


def _aggregate_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {"sample_size": 0}

    wins = sum(
        1 for r in records
        if (r.get("candidate_outcome") or {}).get("is_win")
        or (r.get("final_pnl") or 0) > 0
    )
    by_symbol: dict[str, list[dict]] = {}
    for r in records:
        by_symbol.setdefault(str(r.get("symbol") or ""), []).append(r)

    factor_keys = ("funding", "oi", "trend", "volume", "atr", "fear_greed", "market_score", "confidence")
    factor_avgs: dict[str, float] = {}
    for key in factor_keys:
        vals = [float(r[key]) for r in records if r.get(key) is not None]
        if vals:
            factor_avgs[key] = round(sum(vals) / len(vals), 2)

    return {
        "sample_size": len(records),
        "win_rate": round(wins / len(records), 3),
        "symbols": {sym: len(rows) for sym, rows in by_symbol.items()},
        "factor_averages": factor_avgs,
        "accepted_count": sum(1 for r in records if r.get("accepted")),
        "rejected_count": sum(1 for r in records if r.get("rejected")),
    }


def build_research_dataset_g50(
    conn: Any,
    *,
    max_records: int = DEFAULT_MAX_RECORDS,
    days: int = DEFAULT_DAYS,
) -> dict[str, Any]:
    """Build unified JSON dataset — last N records within window."""
    since = int(time.time()) - days * 86400

    rows = conn.execute(
        """
        SELECT c.id AS candidate_id, c.snapshot_id, c.symbol, c.direction,
               c.trend_score, c.trend_coverage_pct, c.funding_score, c.oi_score,
               c.volume_score, c.atr_score, c.fear_greed, c.btc_alignment,
               c.market_score, c.confidence, c.liquidity_score, c.rr,
               c.candidate_state, c.rejection_reason, c.created_at, c.candidate_ts,
               o.price_entry, o.max_profit_pct, o.max_drawdown_pct, o.would_hit_tp,
               o.replay_status, o.updated_at AS outcome_updated_at,
               v.is_win, v.source_type, v.pnl_pct AS validation_pnl,
               s.pnl_pct AS signal_pnl_pct
        FROM market_candidate_g31 c
        LEFT JOIN market_candidate_outcomes_g32 o ON o.candidate_id = c.id
        LEFT JOIN market_validation_records_g4 v
          ON v.source_type IN ('replay_complete', 'rejected_candidate')
         AND v.source_id = o.id
        LEFT JOIN market_live_signals_g3 s ON s.snapshot_id = c.snapshot_id AND s.symbol = c.symbol
        WHERE c.created_at >= ?
        ORDER BY c.created_at DESC
        LIMIT ?
        """,
        (since, max_records),
    ).fetchall()

    records = [_row_to_record(conn, r) for r in rows]

    g4_factors: list[dict[str, Any]] = []
    try:
        g4_factors = [
            dict(r) for r in conn.execute(
                """
                SELECT factor, win_rate, profit_factor, sample_size, importance, predictor_type
                FROM market_validation_factor_stats_g4
                ORDER BY report_date DESC, rank_order ASC
                LIMIT 20
                """,
            ).fetchall()
        ]
    except Exception:
        pass

    payload = {
        "generated_at": int(time.time()),
        "window_days": days,
        "max_records": max_records,
        "sample_size": len(records),
        "records": records,
        "aggregate_stats": _aggregate_stats(records),
        "g4_factor_stats": g4_factors,
    }
    return payload


def dataset_hash_g50(dataset: dict[str, Any]) -> str:
    blob = json.dumps(dataset, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]
