"""Trading DNA Discovery V1 orchestrator."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.signal_intelligence.trading_dna_v1.coins import coin_dna
from bot.research.market_events.signal_intelligence.trading_dna_v1.features import (
    enrich_trades,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.report import (
    format_morning_summary,
    format_report,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.rules import (
    find_forbidden,
    find_minimal_rules,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.segments import (
    common_factors,
    profile_all,
    split_segments,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.setups import (
    mine_setups,
)


def _load_trades(conn: Any, *, limit: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from bot.research.market_events.signal_intelligence.research_lake_v1 import (
            load_research_lake_rows,
            research_lake_row_count,
        )

        n = research_lake_row_count(conn)
        if n > 0:
            # Ensure Row factory for lake loader
            try:
                import sqlite3

                if getattr(conn, "row_factory", None) is None:
                    conn.row_factory = sqlite3.Row
            except Exception:
                pass
            rows = load_research_lake_rows(conn, limit=limit)
            return rows, {"source": "research_lake_v1", "n_lake": n, "n_loaded": len(rows)}
    except Exception as exc:
        return [], {"source": "empty", "error": str(exc)}
    return [], {"source": "empty"}


def run_trading_dna_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    limit: int | None = None,
    top_n: int = 100,
) -> dict[str, Any]:
    t0 = time.time()
    trades, load_stats = _load_trades(conn, limit=limit)
    enriched, feature_stats = enrich_trades(trades, conn)

    segments = split_segments(enriched)
    profiles = profile_all(segments)
    factors = common_factors(profiles)

    mined = mine_setups(enriched, top_n=top_n, min_n=25, max_combo=3)
    # Drop heavy objects before return
    atomic_rules = mined.get("atomic_rules") or []
    masks = mined.get("masks") or {}
    profitable = mined.get("profitable") or []
    losing = mined.get("losing") or []

    minimal = find_minimal_rules(enriched, atomic_rules, masks, min_n=60)
    forbidden = find_forbidden(losing, max_pf=0.70, min_n=40, limit=30)
    coins = coin_dna(enriched, top_n=20)

    elapsed = round(time.time() - t0, 3)
    result: dict[str, Any] = {
        "ok": len(enriched) > 0,
        "n_trades": len(enriched),
        "elapsed_sec": elapsed,
        "load_stats": load_stats,
        "feature_stats": feature_stats,
        "profiles": profiles,
        "common_factors": factors,
        "profitable": profitable,
        "losing": losing,
        "minimal_rules": minimal,
        "forbidden": forbidden,
        "coins": coins,
        "n_setups_scored": mined.get("n_scored"),
        "research_only": True,
        "paper_unchanged": True,
        "execution_unchanged": True,
        "strategy_unchanged": True,
        "gate_unchanged": True,
        "optimizer_unchanged": True,
        "brain_unchanged": True,
    }
    result["morning_summary"] = format_morning_summary(result)
    if write_reports:
        result["paths"] = write_artifacts(result)
        result["report_markdown"] = format_report(result)
    return result


__all__ = ["run_trading_dna_v1"]
