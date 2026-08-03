"""Market Decision Engine V1 orchestrator."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.signal_intelligence.market_decision_v1.context import (
    build_decision_context,
)
from bot.research.market_events.signal_intelligence.market_decision_v1.decide import (
    decide_one,
    format_decision,
)
from bot.research.market_events.signal_intelligence.market_decision_v1.replay import (
    run_decision_replay,
)
from bot.research.market_events.signal_intelligence.market_decision_v1.report import (
    format_terminal,
    write_artifacts,
)


def run_market_decision_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    limit: int | None = None,
    mode: str = "replay",
) -> dict[str, Any]:
    """
    mode:
      - replay: full Decision Replay vs Production
      - decide: latest-trade probe only (still builds context)
    """
    t0 = time.time()
    ctx = build_decision_context(conn, limit=limit)
    if not ctx.get("ok"):
        return {
            "ok": False,
            "error": "empty_context",
            "terminal": "MARKET DECISION ENGINE V1\n\nERROR empty corpus",
            "research_only": True,
        }

    context_stats = {
        "n_fp": ctx.get("n_fp"),
        "n_tl": ctx.get("n_tl"),
        "n_dna": ctx.get("n_dna"),
        "n_ready_rules": ctx.get("n_ready_rules"),
        "n_blocks": ctx.get("n_blocks"),
        "n_edges": ctx.get("n_edges"),
        "n_replays": ctx.get("n_replays"),
        "n_causality": ctx.get("n_causality"),
        "elapsed_build_sec": ctx.get("elapsed_build_sec"),
    }

    if mode == "decide":
        trades = ctx.get("trades") or []
        latest = max(trades, key=lambda r: int(r.get("opened_at") or r.get("closed_at") or 0))
        sample = decide_one(ctx, latest)
        elapsed = round(time.time() - t0, 3)
        result = {
            "ok": True,
            "mode": "decide",
            "sample_decision": sample,
            "production": {},
            "decision_engine": {},
            "skipped": None,
            "elapsed_sec": elapsed,
            "context_stats": context_stats,
            "research_only": True,
            "terminal": format_decision(sample),
        }
    else:
        replay = run_decision_replay(ctx)
        elapsed = round(time.time() - t0, 3)
        result = {
            "ok": True,
            "mode": "replay",
            **replay,
            "elapsed_sec": elapsed,
            "context_stats": context_stats,
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


__all__ = ["decide_one", "format_decision", "run_market_decision_v1"]
