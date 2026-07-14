"""Phase G.5.1 — Research Data Lake orchestration."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.signal_intelligence.config import (
    G51_BUILD_INTERVAL_SEC,
    G51_ENABLED,
    G51_WINDOW_DAYS,
)
from bot.research.market_events.signal_intelligence.research_dataset_g51 import (
    build_research_lake_g51,
    query_research_dataset_g51,
)


def maybe_run_research_lake_g51(conn: Any) -> dict[str, Any] | None:
    if not G51_ENABLED:
        return None
    from bot.research.market_events.signal_intelligence.health_g3 import (
        get_g3_ops_state,
        set_g3_ops_state,
    )

    now = int(time.time())
    last_raw = get_g3_ops_state(conn, "last_g51_lake_ts")
    last = int(last_raw) if last_raw else 0
    if last and (now - last) < G51_BUILD_INTERVAL_SEC:
        return None

    result = build_research_lake_g51(conn, days=G51_WINDOW_DAYS)
    set_g3_ops_state(conn, "last_g51_lake_ts", str(now))
    return result


def enrich_dataset_with_lake_g51(
    conn: Any,
    dataset: dict[str, Any],
    *,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Attach G51 lake rows to G50 dataset for Claude context."""
    lake_rows = query_research_dataset_g51(conn, symbol=symbol, limit=100)
    by_candidate = {int(r["candidate_id"]): r for r in lake_rows if r.get("candidate_id")}

    enriched_records = []
    for rec in dataset.get("records") or []:
        cid = rec.get("candidate_id")
        lake = by_candidate.get(int(cid)) if cid else None
        if lake:
            rec = dict(rec)
            rec["market_evolution"] = lake.get("market_evolution")
            rec["funding_evolution"] = lake.get("funding_evolution")
            rec["oi_evolution"] = lake.get("oi_evolution")
            rec["replay_evolution"] = lake.get("replay_evolution")
            rec["dataset_completeness"] = lake.get("dataset_completeness")
        enriched_records.append(rec)

    out = dict(dataset)
    out["records"] = enriched_records
    out["lake_rows"] = lake_rows[:20]
    out["lake_sample_size"] = len(lake_rows)
    return out
