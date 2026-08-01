"""Orchestrator for Edge Discovery Engine V1 (research-only)."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.signal_intelligence.edge_discovery_v1.clustering import (
    cluster_edges,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v1.dataset import (
    load_edge_dataset,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v1.mining import (
    mine_edges,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v1.report import (
    write_artifacts,
)


def run_edge_discovery_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    limit: int | None = None,
    min_n: int | None = None,
) -> dict[str, Any]:
    """Mine durable multi-feature edges on full S42⋈S55 history."""
    t0 = time.time()
    rows, load_stats = load_edge_dataset(conn, limit=limit)
    mined = mine_edges(rows, min_n=min_n)
    clusters = cluster_edges(list(mined.get("candidates") or []))
    result = {
        "ok": True,
        **mined,
        "load_stats": load_stats,
        "clusters": clusters,
        "elapsed_sec": round(time.time() - t0, 3),
        "read_only_research": True,
        "gate_unchanged": True,
        "optimizer_unchanged": True,
        "strategy_unchanged": True,
        "paper_unchanged": True,
        "execution_unchanged": True,
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


__all__ = ["run_edge_discovery_v1"]
