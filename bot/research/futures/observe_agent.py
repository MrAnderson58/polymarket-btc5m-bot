"""Observe-only futures signal agent — no order placement."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from bot.research.futures.config import MIN_SOURCE_N, OBSERVE_FEATURE_VERSION, OBSERVE_MODEL_VERSION
from bot.research.futures.market_snapshot import build_market_snapshot
from bot.research.futures.models_abc import walk_forward_abc
from bot.research.futures.parse_pipeline import parse_and_store_messages
from bot.research.futures.parser import SignalParser
from bot.research.futures.schema import RECOMMENDATIONS_TABLE, SIGNALS_TABLE, insert_signal
from bot.research.futures.source_data import RawMessage
from bot.research.futures.source_scoring import score_sources

REC_FOLLOW = "FOLLOW"
REC_SKIP = "SKIP"
REC_CONTRARIAN = "CONTRARIAN_CANDIDATE"
REC_INSUFFICIENT = "INSUFFICIENT_DATA"


def observe_new_signal(
    conn: sqlite3.Connection,
    msg: RawMessage,
) -> dict[str, Any]:
    """Parse, snapshot, score, recommend — observe-only."""
    parser = SignalParser()
    parsed = parser.parse(msg.text)
    sid = insert_signal(conn, {
        "source": msg.source,
        "message_id": msg.message_id,
        "timestamp": msg.timestamp,
        "symbol": parsed.symbol,
        "side": parsed.side,
        "entry_min": parsed.entry_min,
        "entry_max": parsed.entry_max,
        "stop_loss": parsed.stop_loss,
        "leverage": parsed.leverage,
        "timeframe": parsed.timeframe,
        "confidence": parsed.confidence,
        "raw_text": msg.text,
        "parser_confidence": parsed.parser_confidence,
        "parser_version": parser.parser_version,
    })

    reasons: list[str] = []
    if not parsed.side or not parsed.symbol:
        rec = REC_INSUFFICIENT
        reasons.append("parse_incomplete")
        confidence = 0.0
    else:
        features, coverage = build_market_snapshot(parsed.symbol, msg.timestamp)
        unavailable = [k for k, v in coverage.items() if v.startswith("unavailable")]
        if unavailable:
            reasons.append(f"coverage_gaps:{','.join(unavailable[:5])}")

        leaderboard = score_sources(conn)
        source_stats = next((s for s in leaderboard if s["source"] == msg.source), None)
        wf = walk_forward_abc(conn)

        if source_stats and source_stats["sufficient_sample"] and source_stats["pf"] >= 1.2:
            rec = REC_FOLLOW
            confidence = min(0.9, source_stats["pf"] / 3.0)
            reasons.append(f"source_pf={source_stats['pf']:.2f}")
        elif source_stats and source_stats["sufficient_sample"] and source_stats["pf"] < 0.8:
            rec = REC_SKIP
            confidence = 0.6
            reasons.append("weak_source_history")
        elif wf.get("c_beats_b"):
            rec = REC_FOLLOW
            confidence = 0.55
            reasons.append("model_c_historical_edge")
        else:
            rec = REC_INSUFFICIENT
            confidence = 0.3
            reasons.append("insufficient_validated_edge")

        regime = features.get("market_regime")
        if parsed.side == "LONG" and regime == "bear":
            reasons.append("countertrend_long_in_bear")
            if rec == REC_FOLLOW:
                rec = REC_CONTRARIAN
        if parsed.side == "SHORT" and regime == "bull":
            reasons.append("countertrend_short_in_bull")
            if rec == REC_FOLLOW:
                rec = REC_CONTRARIAN

    conn.execute(
        f"""
        INSERT INTO {RECOMMENDATIONS_TABLE} (
            signal_id, model_version, feature_version,
            recommendation, confidence, reasons_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sid, OBSERVE_MODEL_VERSION, OBSERVE_FEATURE_VERSION,
            rec, confidence, json.dumps(reasons), int(time.time()),
        ),
    )
    return {
        "signal_id": sid,
        "recommendation": rec,
        "confidence": confidence,
        "reasons": reasons,
    }
