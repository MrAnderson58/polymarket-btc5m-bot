"""Static + live formula / collector source map for core signals."""

from __future__ import annotations

import time
from typing import Any


# Diagnostic registry only — does not change how features are computed.
FORMULA_SOURCES: dict[str, dict[str, Any]] = {
    "RSI": {
        "feature_keys": ["rsi"],
        "compute_module": "bot.research.market_events.signal_intelligence.trade_intelligence_s55",
        "compute_function": "build_entry_features",
        "notes": "Hardcoded rsi=None in build_entry_features — never computed from candles.",
        "source_table": "market_events_trade_features_s55 / features_json",
        "collector": None,
        "expected_producer": "candle RSI (missing)",
    },
    "EMA": {
        "feature_keys": ["ema20", "ema50", "ema200", "ema20_distance", "ema50_distance", "ema200_distance", "ema_trend"],
        "compute_module": "bot.research.market_events.signal_intelligence.trade_intelligence_s55",
        "compute_function": "build_entry_features (ema_trend proxy from snapshot_trend)",
        "notes": "True EMA levels not written; only ema_trend proxy from trend string/float.",
        "source_table": "market_snapshots_g3 / S40 snapshot_trend",
        "collector": "g3 snapshot / S40 signal snapshot",
        "expected_producer": "candle EMA distances (missing)",
    },
    "VWAP": {
        "feature_keys": ["vwap", "vwap_distance"],
        "compute_module": "bot.research.market_events.signal_intelligence.feature_store",
        "compute_function": "extract_sample (_dist from vwap)",
        "notes": "VWAP not populated in S55/S40 — distances always NULL in Feature Store.",
        "source_table": None,
        "collector": None,
        "expected_producer": "session VWAP (missing)",
    },
    "ATR": {
        "feature_keys": ["atr", "volatility"],
        "compute_module": "bot.research.market_events.signal_intelligence.trade_intelligence_s55",
        "compute_function": "build_entry_features (from S40 snapshot_atr or market_snapshots_g3.atr)",
        "notes": "volatility aliased to atr; observed CONSTANT placeholder values in S55.",
        "source_table": "market_snapshots_g3.atr / S40.snapshot_atr",
        "collector": "g3-run / market snapshot collector",
        "expected_producer": "True Wilder ATR from candles",
    },
    "Funding": {
        "feature_keys": ["funding", "funding_sign"],
        "compute_module": "bot.research.market_events.signal_intelligence.trade_intelligence_s55",
        "compute_function": "build_entry_features",
        "notes": "From S40 snapshot_funding or latest market_snapshots_g3.funding.",
        "source_table": "market_snapshots_g3.funding",
        "collector": "g3 snapshot / Bybit (or configured) funding feed",
        "expected_producer": "per-symbol funding",
    },
    "OI": {
        "feature_keys": ["oi_delta", "open_interest"],
        "compute_module": "bot.research.market_events.signal_intelligence.trade_intelligence_s55",
        "compute_function": "build_entry_features (oi - prev_oi from last 2 snapshots)",
        "notes": "Often falls back to raw OI when prev missing; cross-symbol identical deltas observed.",
        "source_table": "market_snapshots_g3.open_interest",
        "collector": "g3 snapshot",
        "expected_producer": "per-symbol OI delta",
    },
    "Fear": {
        "feature_keys": ["fear_greed"],
        "compute_module": "bot.research.market_events.signal_intelligence.trade_intelligence_s55",
        "compute_function": "build_entry_features",
        "notes": "From S40 snapshot_fear_greed or market_snapshots_g3.fear_greed.",
        "source_table": "market_snapshots_g3.fear_greed",
        "collector": "macro / fear&greed collector",
        "expected_producer": "Alternative.me / configured FG feed",
    },
    "Trend": {
        "feature_keys": ["trend", "ema_trend"],
        "compute_module": "bot.research.market_events.signal_intelligence.trade_intelligence_s55",
        "compute_function": "build_entry_features",
        "notes": "From S40 snapshot_trend; often 0.0 constant in closed book.",
        "source_table": "S40.snapshot_trend / features_json.trend",
        "collector": "trend / MTF pipeline",
        "expected_producer": "signed trend score",
    },
    "AI score": {
        "feature_keys": ["ai_score"],
        "compute_module": "bot.research.market_events.signal_intelligence.trade_intelligence_s55",
        "compute_function": "build_entry_features (ai_score = decision_confidence alias)",
        "notes": "Not a separate model score — alias of snapshot_decision_confidence; often NULL.",
        "source_table": "S40.snapshot_decision_confidence",
        "collector": "candidate / decision engine",
        "expected_producer": "real AI / decision confidence",
    },
    "Neighbor EV": {
        "feature_keys": ["gate_expected_pnl_pct", "similar_count"],
        "compute_module": "bot.research.market_events.signal_intelligence.trade_intelligence_s55",
        "compute_function": "estimate_from_similar / gate path",
        "notes": "Neighbor EV used for S55 gate; stored on S55 columns.",
        "source_table": "market_events_trade_features_s55.gate_expected_pnl_pct",
        "collector": None,
        "expected_producer": "similar-trade estimator",
    },
    "Regime": {
        "feature_keys": ["market_regime", "regime_score", "regime_confidence"],
        "compute_module": "bot.research.market_events.signal_intelligence.market_regime_s57",
        "compute_function": "regime classification applied into S55 features",
        "notes": "S57 regime; closed book observed as 100% RANGE.",
        "source_table": "market_events_trade_features_s55.market_regime",
        "collector": None,
        "expected_producer": "market_regime_s57",
    },
}


def _max_ts(conn: Any, sql: str, params: tuple = ()) -> int | None:
    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return None
        v = row[0]
        return int(v) if v is not None else None
    except Exception:
        return None


def live_source_updates(conn: Any) -> dict[str, Any]:
    """Last-update timestamps for upstream tables (read-only)."""
    now = int(time.time())
    snap = _max_ts(conn, "SELECT MAX(snapshot_ts) FROM market_snapshots_g3")
    s40 = _max_ts(conn, "SELECT MAX(created_at) FROM market_events_signal_learning_s40_signals")
    s55 = _max_ts(conn, "SELECT MAX(created_at) FROM market_events_trade_features_s55")
    s42 = _max_ts(conn, "SELECT MAX(COALESCE(closed_at, created_at)) FROM market_events_paper_trades_s42")
    g31 = _max_ts(conn, "SELECT MAX(created_at) FROM market_candidate_g31")
    candles = _max_ts(conn, "SELECT MAX(open_ts) FROM market_events_historical_candles")

    def age(ts: int | None) -> dict[str, Any]:
        if ts is None:
            return {"last_update": None, "age_sec": None, "status": "BROKEN SOURCE"}
        return {
            "last_update": ts,
            "age_sec": now - ts,
            "status": "STALE" if (now - ts) > 6 * 3600 else "OK",
        }

    return {
        "market_snapshots_g3": age(snap),
        "market_events_signal_learning_s40_signals": age(s40),
        "market_events_trade_features_s55": age(s55),
        "market_events_paper_trades_s42": age(s42),
        "market_candidate_g31": age(g31),
        "market_events_historical_candles": age(candles),
        "checked_at": now,
    }


def audit_formula_sources(conn: Any) -> dict[str, Any]:
    """Combine static formula map with live table freshness."""
    live = live_source_updates(conn)
    results: list[dict[str, Any]] = []
    for name, meta in FORMULA_SOURCES.items():
        table = meta.get("source_table")
        collector = meta.get("collector")
        status = "OK"
        # Detect broken sources by known missing producers
        if name in ("RSI", "VWAP"):
            status = "BROKEN SOURCE"
        elif name == "EMA" and "missing" in str(meta.get("notes") or "").lower():
            status = "BROKEN SOURCE"
        # ATR/Funding/Fear depend on snapshots
        snap_status = (live.get("market_snapshots_g3") or {}).get("status")
        if name in ("ATR", "Funding", "OI", "Fear") and snap_status == "BROKEN SOURCE":
            status = "BROKEN SOURCE"
        elif name in ("ATR", "Funding", "OI", "Fear") and snap_status == "STALE":
            status = "STALE"
        results.append({
            "formula": name,
            "feature_keys": meta.get("feature_keys"),
            "compute_module": meta.get("compute_module"),
            "compute_function": meta.get("compute_function"),
            "source_table": table,
            "collector": collector,
            "notes": meta.get("notes"),
            "expected_producer": meta.get("expected_producer"),
            "status": status,
            "live": {
                "snapshots": live.get("market_snapshots_g3"),
                "s40": live.get("market_events_signal_learning_s40_signals"),
                "s55": live.get("market_events_trade_features_s55"),
            },
        })
    return {"formulas": results, "live_tables": live}


__all__ = ["FORMULA_SOURCES", "audit_formula_sources", "live_source_updates"]
