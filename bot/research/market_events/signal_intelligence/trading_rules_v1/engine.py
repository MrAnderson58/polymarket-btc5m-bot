"""Trading Rules Extraction V1 orchestrator."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.trading_dna_v1.features import (
    enrich_trades,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.setups import (
    mine_setups,
)
from bot.research.market_events.signal_intelligence.trading_rules_v1.format_out import (
    format_report,
    format_terminal,
)
from bot.research.market_events.signal_intelligence.trading_rules_v1.minimize import (
    extract_blocks,
    extract_candidates,
)
from bot.research.market_events.signal_intelligence.trading_rules_v1.validate import (
    MIN_N_READY,
)

REPORT_MD = BASE_DIR / "TRADING_RULES_REPORT.md"


def _load_trades(conn: Any, *, limit: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        from bot.research.market_events.signal_intelligence.research_lake_v1 import (
            load_research_lake_rows,
            research_lake_row_count,
        )

        n = research_lake_row_count(conn)
        if n > 0:
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


def run_trading_rules_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    limit: int | None = None,
    min_n: int = MIN_N_READY,
) -> dict[str, Any]:
    t0 = time.time()
    trades, load_stats = _load_trades(conn, limit=limit)
    enriched, feature_stats = enrich_trades(trades, conn)

    # Reuse DNA mining — no new features
    mined = mine_setups(enriched, top_n=100, min_n=25, max_combo=3)
    atomics = mined.get("atomic_rules") or []
    profitable = mined.get("profitable") or []
    losing = mined.get("losing") or []

    candidates = extract_candidates(
        enriched, profitable, atomics=atomics, min_n=min_n, limit=40
    )
    ready = candidates[:10]

    blocks = extract_blocks(enriched, losing, atomics=atomics, limit=20)
    # Prefer universal-ish blocks; always keep gate blocks first
    hard = []
    for b in blocks:
        hard.append(b)
        if len(hard) >= 10:
            break

    elapsed = round(time.time() - t0, 3)
    result: dict[str, Any] = {
        "ok": len(enriched) > 0,
        "n_trades": len(enriched),
        "elapsed_sec": elapsed,
        "load_stats": load_stats,
        "feature_stats": feature_stats,
        "n_candidates": len(candidates),
        "ready_for_paper": ready,
        "hard_block": hard,
        "min_n": min_n,
        "research_only": True,
        "paper_unchanged": True,
        "execution_unchanged": True,
        "strategy_unchanged": True,
        "gate_unchanged": True,
        "optimizer_unchanged": True,
        "brain_unchanged": True,
        "no_new_features": True,
    }
    result["terminal"] = format_terminal(result)
    if write_reports:
        md = format_report(result)
        REPORT_MD.write_text(md, encoding="utf-8")
        out = BASE_DIR / "reports" / "research" / "trading_rules_v1"
        out.mkdir(parents=True, exist_ok=True)
        (out / "TRADING_RULES_REPORT.md").write_text(md, encoding="utf-8")
        result["paths"] = {
            "TRADING_RULES_REPORT.md": str(REPORT_MD),
            "out/TRADING_RULES_REPORT.md": str(out / "TRADING_RULES_REPORT.md"),
        }
        result["report_markdown"] = md
    return result


__all__ = ["run_trading_rules_v1"]
