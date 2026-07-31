"""Orchestrator for Alpha Discovery Engine V1."""

from __future__ import annotations

import os
import time
from typing import Any

from bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.dataset import (
    load_alpha_dataset,
)
from bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.mining import (
    mine_alphas,
)
from bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.report import (
    rank_candidates,
    write_artifacts,
)


def run_alpha_engine_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    limit: int | None = None,
    min_n: int | None = None,
    backfill_candles: bool | None = None,
) -> dict[str, Any]:
    """Mine alphas on full S42 (+ candle feature recovery). Research-only."""
    t0 = time.time()
    if backfill_candles is None:
        backfill_candles = os.environ.get("ALPHA_ENGINE_BACKFILL", "1") not in ("0", "false", "no")
    rows = load_alpha_dataset(
        conn,
        limit=limit,
        backfill_candles=bool(backfill_candles),
    )
    mined = mine_alphas(rows, min_n=min_n)
    ranked = rank_candidates(mined.get("candidates") or [])
    paths = {}
    md = ""
    if write_reports:
        paths = write_artifacts(result=mined, ranked=ranked)
        try:
            from pathlib import Path
            md = Path(paths["report_md"]).read_text(encoding="utf-8")
        except Exception:
            md = ""
    return {
        "ok": True,
        "n_rows": mined.get("n_rows"),
        "n_atoms": mined.get("n_atoms"),
        "n_rules_tested": mined.get("n_rules_tested"),
        "n_candidates": len(ranked),
        "n_significant_fdr": sum(1 for r in ranked if r.get("significant_fdr")),
        "baseline": mined.get("baseline"),
        "top_candidates": ranked[:20],
        "paths": paths,
        "report_markdown": md,
        "elapsed_sec": round(time.time() - t0, 3),
        "read_only": True,
        "gate_trading_unchanged": True,
        "optimizer_unchanged": True,
        "execution_unchanged": True,
    }


__all__ = ["run_alpha_engine_v1"]
