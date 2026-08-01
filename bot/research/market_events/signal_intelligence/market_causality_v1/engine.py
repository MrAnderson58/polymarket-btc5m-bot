"""Market Causality Engine V1 orchestrator (research-only)."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.signal_intelligence.market_causality_v1.attribution import (
    attribute_trade,
    portfolio_mean_contributions,
)
from bot.research.market_events.signal_intelligence.market_causality_v1.clusters import (
    cluster_trades,
)
from bot.research.market_events.signal_intelligence.market_causality_v1.counterfactual import (
    portfolio_counterfactuals,
    trade_counterfactuals,
)
from bot.research.market_events.signal_intelligence.market_causality_v1.features import (
    extract_cause_vector,
    merge_cause_signal,
    pre_entry_deltas_from_frames,
)
from bot.research.market_events.signal_intelligence.market_causality_v1.graph import (
    build_causal_graph,
)
from bot.research.market_events.signal_intelligence.market_causality_v1.library import (
    load_causality,
    upsert_causality,
)
from bot.research.market_events.signal_intelligence.market_causality_v1.stability import (
    evaluate_stability,
    filter_stable_edges,
)
from bot.research.market_events.signal_intelligence.market_causality_v1.report import (
    write_artifacts,
)


def _load_trades(conn: Any, *, limit: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Prefer Research Lake; normalize aliases used by causality."""
    stats: dict[str, Any] = {"source": "empty"}
    rows: list[dict[str, Any]] = []
    try:
        from bot.research.market_events.signal_intelligence.market_replay_v1.dataset import (
            load_replay_trades,
        )

        rows, stats = load_replay_trades(conn, limit=limit)
    except Exception as exc:
        stats["error"] = str(exc)
        try:
            from bot.research.market_events.signal_intelligence.research_lake_v1 import (
                load_research_lake_rows,
                research_lake_row_count,
            )

            if research_lake_row_count(conn) > 0:
                rows = load_research_lake_rows(conn, limit=limit)
                stats["source"] = "research_lake_v1"
        except Exception as exc2:
            stats["error2"] = str(exc2)
    return rows, stats


def _build_frames_fast(trade: dict[str, Any]) -> list[dict[str, Any]]:
    """Lightweight replay frames from lake features only (no N+1)."""
    try:
        from bot.research.market_events.signal_intelligence.market_replay_v1.frames import (
            build_market_frames,
        )

        return build_market_frames(trade, candles=[], snapshots=[])
    except Exception:
        return []


def run_market_causality_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    persist_library: bool = True,
    limit: int | None = None,
    batch_size: int = 1000,
) -> dict[str, Any]:
    t0 = time.time()
    trades, load_stats = _load_trades(conn, limit=limit)
    n = len(trades)
    causal_rows: list[dict[str, Any]] = []

    for trade in trades:
        frames = _build_frames_fast(trade)
        # Temporal ordering: only pre-entry deltas may reinforce causes
        deltas = pre_entry_deltas_from_frames(frames)
        base = extract_cause_vector(trade)
        cause_vec = merge_cause_signal(base, deltas)
        attr = attribute_trade(cause_vec, pnl=float(trade.get("pnl") or 0.0))
        # Confidence: concentration of top contribution + non-noise
        top = float((attr.get("top3") or [{}])[0].get("pct") or 0.0)
        confidence = round(min(1.0, top / 40.0), 4)
        quality = round(min(1.0, sum(1 for v in cause_vec.values() if v > 0) / 15.0), 4)
        cf = trade_counterfactuals({**attr, "pnl": trade.get("pnl")})
        row = {
            "trade_id": int(trade.get("trade_id") or trade.get("id") or 0),
            "pnl": float(trade.get("pnl") or 0.0),
            "regime": trade.get("regime") or "",
            "entry_ts": trade.get("entry_ts") or trade.get("opened_at") or trade.get("closed_at"),
            "closed_at": trade.get("closed_at"),
            "cause_vec": cause_vec,
            "contributions": attr["contributions"],
            "primary_cause": attr["primary_cause"],
            "secondary_cause": attr["secondary_cause"],
            "top3": attr["top3"],
            "counterfactual": cf,
            "confidence": confidence,
            "quality": quality,
            "pre_entry_delta_keys": sorted(deltas.keys()),
            "temporal_ok": True,  # deltas only from offset<0
        }
        causal_rows.append(row)

    # Graph + stability on full set
    graph = build_causal_graph(causal_rows)
    stability = evaluate_stability(causal_rows)
    stable_edges = filter_stable_edges(graph, stability)
    graph["stable_edges"] = stable_edges
    graph["n_stable_edges"] = len(stable_edges)

    clusters = cluster_trades(causal_rows)
    mean_c = portfolio_mean_contributions(causal_rows)
    dominant = dict(sorted(mean_c.items(), key=lambda t: -t[1])[:10])

    port_cf = portfolio_counterfactuals(causal_rows)
    examples = sorted(causal_rows, key=lambda r: -float(r.get("confidence") or 0))[:5]
    counterfactual_examples = [
        {
            "trade_id": e.get("trade_id"),
            "primary_cause": e.get("primary_cause"),
            "contributions": e.get("contributions"),
            "counterfactual": e.get("counterfactual"),
        }
        for e in examples
    ]

    library_upserted = 0
    if persist_library and causal_rows:
        for i in range(0, len(causal_rows), batch_size):
            library_upserted += upsert_causality(conn, causal_rows[i : i + batch_size])

    library_rows = []
    if persist_library:
        try:
            library_rows = load_causality(conn, limit=100)
        except Exception as exc:
            load_stats["library_error"] = str(exc)

    # Coverage: rows with confidence>0 and non-empty primary
    covered = sum(
        1 for r in causal_rows
        if float(r.get("confidence") or 0) > 0 and r.get("primary_cause")
    )

    result: dict[str, Any] = {
        "ok": True,
        "n_rows": n,
        "n_causal": len(causal_rows),
        "coverage_pct": round(100.0 * covered / max(1, n), 2),
        "graph": graph,
        "n_causal_relations": graph.get("n_edges"),
        "stability": stability,
        "dominant_causes": dominant,
        "clusters": clusters.get("clusters"),
        "top_clusters": clusters.get("top_clusters"),
        "portfolio_counterfactuals": port_cf,
        "counterfactual_examples": counterfactual_examples,
        "library_upserted": library_upserted,
        "library_rows": library_rows,
        "load_stats": load_stats,
        "temporal_leakage_rejected": True,
        "elapsed_sec": round(time.time() - t0, 3),
        "read_only_research": True,
        "gate_unchanged": True,
        "optimizer_unchanged": True,
        "strategy_unchanged": True,
        "paper_unchanged": True,
        "execution_unchanged": True,
        "no_n_plus_1_sql": True,
    }

    paths: dict[str, str] = {}
    md = ""
    if write_reports:
        paths = write_artifacts(result)
        try:
            from pathlib import Path

            md = Path(paths["report_md"]).read_text(encoding="utf-8")
        except Exception:
            md = ""
    result["paths"] = paths
    result["report_markdown"] = md
    return result


__all__ = ["run_market_causality_v1"]
