"""Market Fingerprint Engine V1 orchestrator."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.signal_intelligence.market_fingerprint_v1.clusters import (
    build_clusters,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.coin_dna import (
    coin_dna,
    universal_dna,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.regime import (
    regime_transitions,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.report import (
    format_terminal,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.sequence import (
    sequence_analysis,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.similarity import (
    build_similarity_index,
    query_similarity,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.snapshots import (
    load_candle_book,
    snapshot_trade,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.stats import (
    feature_distributions,
    mae_mfe_summary,
)


def _load_trades(conn: Any, *, limit: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from bot.research.market_events.signal_intelligence.research_lake_v1 import (
            load_research_lake_rows,
            research_lake_row_count,
        )

        n = research_lake_row_count(conn)
        if n <= 0:
            return [], {"source": "empty", "n_lake": 0}
        try:
            import sqlite3

            if getattr(conn, "row_factory", None) is None:
                conn.row_factory = sqlite3.Row
        except Exception:
            pass
        # Enrich MAE/MFE from S42 when lake lacks them
        rows = load_research_lake_rows(conn, limit=limit)
        mae_map: dict[int, tuple[Any, Any, Any]] = {}
        try:
            for r in conn.execute(
                "SELECT id, mae_pct, mfe_pct, holding_seconds "
                "FROM market_events_paper_trades_s42 WHERE status='CLOSED'"
            ).fetchall():
                mae_map[int(r[0])] = (r[1], r[2], r[3])
        except Exception:
            pass
        for row in rows:
            tid = int(row.get("trade_id") or 0)
            if tid in mae_map:
                m, f, h = mae_map[tid]
                if row.get("mae_pct") is None:
                    row["mae_pct"] = m
                if row.get("mfe_pct") is None:
                    row["mfe_pct"] = f
                if row.get("holding_seconds") is None:
                    row["holding_seconds"] = h
        return rows, {"source": "research_lake_v1", "n_lake": n, "n_loaded": len(rows)}
    except Exception as exc:
        return [], {"source": "empty", "error": str(exc)}


def run_market_fingerprint_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    limit: int | None = None,
    k_neighbors: int = 100,
) -> dict[str, Any]:
    t0 = time.time()
    trades, load_stats = _load_trades(conn, limit=limit)
    book = load_candle_book(conn)

    snapshots: list[dict[str, Any]] = []
    for tr in trades:
        snap = snapshot_trade(tr, book)
        if snap is not None:
            snapshots.append(snap)

    distributions = feature_distributions(snapshots)
    mae_mfe = mae_mfe_summary(snapshots)
    clustered = build_clusters(snapshots, k_per_bucket=8, min_n=40)
    clusters = clustered.get("clusters") or []
    assignments = clustered.get("assignments") or {}

    sequences = sequence_analysis(snapshots)
    transitions = regime_transitions(snapshots)
    coins = coin_dna(snapshots, assignments, clusters)
    universal = universal_dna(snapshots, assignments, clusters)

    sim_index = build_similarity_index(snapshots)
    # Current market ≈ latest snapshot (research probe)
    current = None
    sim = {"ok": False, "recommendation": "RESEARCH ONLY"}
    if snapshots and sim_index.get("ok"):
        latest = max(snapshots, key=lambda r: int(r.get("opened_at") or 0))
        sim = query_similarity(
            sim_index,
            latest.get("vector") or [],
            k=k_neighbors,
            assignments=assignments,
        )
        current = {
            "trade_id": latest.get("trade_id"),
            "symbol": latest.get("symbol"),
            "direction": latest.get("direction"),
            "opened_at": latest.get("opened_at"),
            "regime": latest.get("regime"),
            "fingerprint": assignments.get(int(latest.get("trade_id") or 0)),
        }

    # Drop heavy sklearn objects before return/serialize
    similarity_library = {
        "ok": bool(sim_index.get("ok")),
        "n": sim_index.get("n"),
        "k_default": k_neighbors,
        "feature_keys": list(
            __import__(
                "bot.research.market_events.signal_intelligence.market_fingerprint_v1.snapshots",
                fromlist=["VECTOR_KEYS"],
            ).VECTOR_KEYS
        ),
    }

    win_fps = [c for c in clusters if c.get("result_bucket") == "WIN"][:15]
    loss_fps = [c for c in clusters if c.get("result_bucket") == "LOSS"][:15]

    elapsed = round(time.time() - t0, 3)
    result: dict[str, Any] = {
        "ok": len(snapshots) > 0,
        "n_trades": len(trades),
        "n_snapshots": len(snapshots),
        "n_candle_symbols": len(book),
        "elapsed_sec": elapsed,
        "load_stats": load_stats,
        "distributions": {
            k: {"n": v.get("n")} for k, v in distributions.items()
        },
        "distribution_detail": distributions,
        "mae_mfe": mae_mfe,
        "win_fingerprints": win_fps,
        "loss_fingerprints": loss_fps,
        "clusters": clusters,
        "transition_matrix": transitions,
        "sequences": sequences,
        "coin_dna": coins,
        "universal_dna": universal,
        "similarity_library": similarity_library,
        "current_market": current,
        "similarity": sim,
        "research_only": True,
        "paper_unchanged": True,
        "execution_unchanged": True,
        "strategy_unchanged": True,
        "gate_unchanged": True,
        "optimizer_unchanged": True,
        "brain_unchanged": True,
    }
    # Keep a lightweight runtime index for market-similarity CLI in-process only
    result["_sim_index"] = sim_index if sim_index.get("ok") else None
    result["_assignments"] = assignments
    result["_snapshots"] = snapshots  # for similarity CLI reuse when same process

    result["terminal"] = format_terminal(result)
    if write_reports:
        paths = write_artifacts(result)
        result["paths"] = paths
        result["report_markdown"] = paths.get("report_text")
    return result


__all__ = ["run_market_fingerprint_v1"]
