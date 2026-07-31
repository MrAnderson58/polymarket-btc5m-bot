"""Part 7 — Project map: collector → table → feature → research consumers."""

from __future__ import annotations

from typing import Any


# Static usage map (diagnostic). "never_read" / dead flagged by convention.
PROJECT_MAP: list[dict[str, Any]] = [
    {
        "collector": "g3 snapshot / market_data collectors",
        "table": "market_snapshots_g3",
        "fields": ["funding", "open_interest", "atr", "fear_greed", "volume", "btc_dominance", "btc_price"],
        "features": ["funding", "oi_delta", "atr", "volatility", "fear_greed", "volume"],
        "research_consumers": ["trade_intelligence_s55.build_entry_features", "pipeline_audit_g0", "score_breakdown_g34"],
        "optimizer": "indirect via S55",
        "validation": "indirect",
        "ml": ["funding", "atr", "fear_greed", "volume", "oi_delta"],
        "usage": "used",
    },
    {
        "collector": "S40 signal learning / candidate path",
        "table": "market_events_signal_learning_s40_signals",
        "fields": [
            "snapshot_funding", "snapshot_atr", "snapshot_fear_greed",
            "snapshot_trend", "snapshot_news_score", "snapshot_decision_confidence",
        ],
        "features": ["funding", "atr", "fear_greed", "trend", "news_score", "confidence", "ai_score"],
        "research_consumers": ["trade_intelligence_s55", "signal learning reviews"],
        "optimizer": "confidence gate inputs (when present)",
        "validation": "FilterParams.confidence_threshold",
        "ml": ["confidence", "ai_score", "news_score", "trend"],
        "usage": "used",
    },
    {
        "collector": "trade_intelligence_s55 gate + feature log",
        "table": "market_events_trade_features_s55",
        "fields": ["rsi", "atr", "funding", "oi_delta", "market_regime", "gate_decision", "features_json"],
        "features": ["rsi", "atr", "funding", "oi_delta", "market_regime", "gate_decision"],
        "research_consumers": ["feature_store", "strategy_optimizer", "feature_validation_v1", "math_recovery_v1"],
        "optimizer": "segment reports via join",
        "validation": "ab_replay universe join",
        "ml": "FEATURE_SPEC numerics",
        "usage": "used",
    },
    {
        "collector": "S42 paper runner",
        "table": "market_events_paper_trades_s42",
        "fields": ["pnl_usd", "pnl_pct", "mfe_pct", "mae_pct", "holding_seconds", "decision_confidence"],
        "features": ["pnl", "mfe", "mae", "holding_time", "confidence"],
        "research_consumers": ["optimizer", "validation", "experiments", "feature_store"],
        "optimizer": "primary",
        "validation": "primary",
        "ml": "labels + confidence",
        "usage": "used",
    },
    {
        "collector": "G31 candidate engine",
        "table": "market_candidate_g31",
        "fields": ["confidence", "fear_greed", "atr_score", "volume_score", "rejection_reason"],
        "features": ["candidate confidence / scores (not always copied to S55)"],
        "research_consumers": ["auto_validation_g4", "research_dataset_g50", "pipeline flow"],
        "optimizer": "not directly",
        "validation": "not directly",
        "ml": "not in Feature Store V1",
        "usage": "partially_used",
    },
    {
        "collector": "NONE (missing producer)",
        "table": None,
        "fields": ["rsi candle series"],
        "features": ["rsi"],
        "research_consumers": ["feature_store expects rsi"],
        "optimizer": "unused (always null)",
        "validation": "unused",
        "ml": "column present but EMPTY",
        "usage": "never_read_from_live_source",
        "dead_code_hint": "rsi=None hardcoded in build_entry_features",
    },
    {
        "collector": "NONE (missing producer)",
        "table": None,
        "fields": ["ema20", "ema50", "ema200", "vwap"],
        "features": ["ema*_distance", "vwap_distance"],
        "research_consumers": ["feature_store.extract_sample"],
        "optimizer": "unused",
        "validation": "unused",
        "ml": "EMPTY distances",
        "usage": "never_read_from_live_source",
        "dead_code_hint": "distance derivation dead without level inputs",
    },
    {
        "collector": "market_regime_s57",
        "table": "S55.market_regime",
        "fields": ["market_regime", "regime_score"],
        "features": ["market_regime"],
        "research_consumers": ["gate funnel", "optimizer segments", "experiments"],
        "optimizer": "symbol/regime segments",
        "validation": "indirect",
        "ml": "categorical",
        "usage": "used",
    },
    {
        "collector": "research-only experiment / hypothesis engines",
        "table": "research_experiments / experiment_runs",
        "fields": ["ev_after", "pf_after"],
        "features": [],
        "research_consumers": ["hypothesis-validate", "experiment-run"],
        "optimizer": "separate from Adaptive Optimizer V1",
        "validation": "separate",
        "ml": "no",
        "usage": "used",
    },
]


def build_project_map(
    *,
    feature_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status_by_feat: dict[str, str] = {}
    for a in (feature_audit or {}).get("features") or []:
        status_by_feat[a["feature"]] = a.get("status") or "UNKNOWN"

    enriched = []
    for row in PROJECT_MAP:
        feats = row.get("features") or []
        feat_statuses = {}
        if isinstance(feats, list):
            for f in feats:
                if isinstance(f, str) and f in status_by_feat:
                    feat_statuses[f] = status_by_feat[f]
        item = dict(row)
        item["feature_statuses"] = feat_statuses
        enriched.append(item)

    never = [r for r in enriched if r.get("usage") == "never_read_from_live_source"]
    partial = [r for r in enriched if r.get("usage") == "partially_used"]
    return {
        "nodes": enriched,
        "never_read_sources": never,
        "partially_used": partial,
        "dead_code_hints": [r.get("dead_code_hint") for r in enriched if r.get("dead_code_hint")],
    }


def format_project_map_md(pmap: dict[str, Any]) -> str:
    lines = [
        "# Project Map — Signal Mathematics Recovery",
        "",
        "```",
        "collector → table → feature → research → optimizer → validation → ml",
        "```",
        "",
    ]
    for n in pmap.get("nodes") or []:
        lines.append(f"## {n.get('collector')}")
        lines.append(f"- table: `{n.get('table')}`")
        lines.append(f"- fields: `{n.get('fields')}`")
        lines.append(f"- features: `{n.get('features')}`")
        lines.append(f"- research: `{n.get('research_consumers')}`")
        lines.append(f"- optimizer: `{n.get('optimizer')}`")
        lines.append(f"- validation: `{n.get('validation')}`")
        lines.append(f"- ml: `{n.get('ml')}`")
        lines.append(f"- usage: **{n.get('usage')}**")
        if n.get("dead_code_hint"):
            lines.append(f"- dead_code: `{n.get('dead_code_hint')}`")
        if n.get("feature_statuses"):
            lines.append(f"- live statuses: `{n.get('feature_statuses')}`")
        lines.append("")
    return "\n".join(lines)


__all__ = ["PROJECT_MAP", "build_project_map", "format_project_map_md"]
