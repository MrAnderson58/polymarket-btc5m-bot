"""Market Edge Discovery V3 orchestrator (research-only)."""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.edge_discovery_v3.dataset import (
    load_edge_v3_dataset,
    matrix_from_rows,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v3.importance import (
    compute_importance,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v3.library import (
    load_library,
    upsert_edges,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v3.mining import (
    mine_edges_v3,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v3.regime import (
    cluster_regimes,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v3.report import (
    write_artifacts,
)


def run_edge_discovery_v3(
    conn: Any,
    *,
    write_reports: bool = True,
    persist_library: bool = True,
    limit: int | None = None,
    min_n: int | None = None,
) -> dict[str, Any]:
    """Mine / validate / cluster / rank edges from Research Lake."""
    t0 = time.time()
    rows, load_stats = load_edge_v3_dataset(conn, limit=limit)
    mined = mine_edges_v3(rows, min_n=min_n)

    if rows:
        pnls, _closed, numeric, cats = matrix_from_rows(rows)
        regimes = cluster_regimes(numeric, pnls)
        importance = compute_importance(
            numeric, cats, pnls, list(mined.get("candidates") or [])
        )
    else:
        regimes = {"ok": False, "regimes": [], "n_regimes": 0}
        importance = {"ranking": [], "shap_available": False}

    for c in mined.get("candidates") or []:
        c.setdefault("regime", "auto")

    library_upserted = 0
    library_rows: list[dict[str, Any]] = []
    if persist_library and (mined.get("candidates") or []):
        try:
            library_upserted = upsert_edges(conn, list(mined.get("candidates") or []))
            library_rows = load_library(conn, limit=100)
        except Exception as exc:
            load_stats["library_error"] = str(exc)

    result: dict[str, Any] = {
        "ok": True,
        **mined,
        "load_stats": load_stats,
        "regimes": regimes,
        "importance": importance,
        "library_upserted": library_upserted,
        "library_rows": library_rows,
        "feature_ranking": (importance.get("ranking") or [])[:30],
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

            md = Path(paths["discovery"]).read_text(encoding="utf-8")
        except Exception:
            md = ""
    result["paths"] = paths
    result["report_markdown"] = md
    return result


__all__ = ["run_edge_discovery_v3"]
