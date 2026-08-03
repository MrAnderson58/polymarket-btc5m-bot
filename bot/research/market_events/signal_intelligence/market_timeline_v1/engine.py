"""Market Timeline Intelligence Engine V1 orchestrator."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.signal_intelligence.market_timeline_v1.chains import (
    cluster_histories,
    mine_chains,
)
from bot.research.market_events.signal_intelligence.market_timeline_v1.report import (
    format_terminal,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.market_timeline_v1.similarity import (
    match_current,
)
from bot.research.market_events.signal_intelligence.market_timeline_v1.windows import (
    load_candle_book,
    timeline_trade,
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
        rows = load_research_lake_rows(conn, limit=limit)
        # Enrich hold / exit from S42
        meta: dict[int, tuple[Any, Any]] = {}
        try:
            for r in conn.execute(
                "SELECT id, holding_seconds, exit_reason "
                "FROM market_events_paper_trades_s42 WHERE status='CLOSED'"
            ).fetchall():
                meta[int(r[0])] = (r[1], r[2])
        except Exception:
            pass
        for row in rows:
            tid = int(row.get("trade_id") or 0)
            if tid in meta:
                h, ex = meta[tid]
                if row.get("holding_seconds") is None:
                    row["holding_seconds"] = h
                if not row.get("exit_reason"):
                    row["exit_reason"] = ex
        return rows, {"source": "research_lake_v1", "n_lake": n, "n_loaded": len(rows)}
    except Exception as exc:
        return [], {"source": "empty", "error": str(exc)}


def run_market_timeline_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    limit: int | None = None,
    top_chains: int = 100,
) -> dict[str, Any]:
    t0 = time.time()
    trades, load_stats = _load_trades(conn, limit=limit)
    book = load_candle_book(conn)

    timelines: list[dict[str, Any]] = []
    for tr in trades:
        row = timeline_trade(tr, book)
        if row is not None:
            timelines.append(row)

    mined = mine_chains(timelines, top_n=top_chains, min_n=12)
    clustered = cluster_histories(timelines, k=18, min_n=40)
    chains = mined.get("chains") or []
    top = mined.get("top_chains") or []
    sim = match_current(timelines, chains)

    top_chain = top[0] if top else None
    elapsed = round(time.time() - t0, 3)

    result: dict[str, Any] = {
        "ok": len(timelines) > 0,
        "n_trades": len(trades),
        "n_timelines": len(timelines),
        "n_candle_symbols": len(book),
        "elapsed_sec": elapsed,
        "load_stats": load_stats,
        "clusters": clustered.get("clusters") or [],
        "n_clusters": len(clustered.get("clusters") or []),
        "timeline_chains": mined.get("n_raw_signatures") or mined.get("n_chains") or 0,
        "n_chains": mined.get("n_chains") or 0,
        "n_profitable_chains": mined.get("n_profitable") or 0,
        "n_ready_chains": mined.get("n_ready") or 0,
        "top_chains": top,
        "top_chain": top_chain,
        "similarity": sim,
        "current_market": sim.get("current"),
        "research_only": True,
        "paper_unchanged": True,
        "execution_unchanged": True,
        "strategy_unchanged": True,
        "gate_unchanged": True,
        "optimizer_unchanged": True,
        "brain_unchanged": True,
    }
    result["terminal"] = format_terminal(result)
    if write_reports:
        paths = write_artifacts(result)
        result["paths"] = paths
        result["report_markdown"] = paths.get("report_text")
    return result


__all__ = ["run_market_timeline_v1"]
