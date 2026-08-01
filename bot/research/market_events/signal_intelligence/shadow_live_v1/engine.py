"""Shadow Live Evaluation V1 — research-only brain vs production comparison.

No trade execution. Does not modify Paper / Execution / Gate / Strategy / Optimizer.
"""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.signal_intelligence.shadow_live_v1.actions import (
    production_action_from_trade,
)
from bot.research.market_events.signal_intelligence.shadow_live_v1.library import (
    load_shadow_decisions,
    upsert_shadow_decisions,
)
from bot.research.market_events.signal_intelligence.shadow_live_v1.metrics import (
    calibration_summary,
    confidence_curve,
    evaluate_row,
    promotion_recommendation,
    rolling_statistics,
)
from bot.research.market_events.signal_intelligence.shadow_live_v1.report import (
    write_artifacts,
)


def _load_candidates(conn: Any, *, limit: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Prefer Research Lake exclusively. Never silently fall back to market_math."""
    stats: dict[str, Any] = {"source": "empty"}
    try:
        from bot.research.market_events.signal_intelligence.research_lake_v1 import (
            load_research_lake_rows,
            research_lake_row_count,
        )

        n = research_lake_row_count(conn)
        stats["n_lake"] = n
        if n > 0:
            rows = load_research_lake_rows(conn, limit=limit)
            stats["source"] = "research_lake"
            stats["n_raw"] = len(rows)
            return rows, stats
        stats["error"] = "research_lake_empty"
    except Exception as exc:
        stats["lake_error"] = str(exc)
    return [], stats


def _preload_brain_context(conn: Any) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "edges": [],
        "replays": {},
        "causality": {},
        "optimizer_state": {},
        "experiments": [],
    }
    try:
        from bot.research.market_events.signal_intelligence.edge_discovery_v3.library import (
            load_library,
        )

        ctx["edges"] = load_library(conn, limit=200)
    except Exception:
        pass
    try:
        from bot.research.market_events.signal_intelligence.market_replay_v1.library import (
            load_replays,
        )

        for r in load_replays(conn, limit=50_000):
            tid = int(r.get("trade_id") or 0)
            if tid:
                ctx["replays"][tid] = r
    except Exception:
        pass
    try:
        from bot.research.market_events.signal_intelligence.market_causality_v1.library import (
            load_causality,
        )

        for r in load_causality(conn, limit=50_000):
            tid = int(r.get("trade_id") or 0)
            if tid:
                ctx["causality"][tid] = r
    except Exception:
        pass
    try:
        rows = conn.execute(
            """
            SELECT key, value FROM market_events_ops_state
            WHERE key LIKE 'optimizer%' OR key LIKE 'g42%'
            LIMIT 30
            """
        ).fetchall()
        ctx["optimizer_state"] = {str(r[0]): r[1] for r in rows} if rows else {}
    except Exception:
        pass
    try:
        rows = conn.execute(
            """
            SELECT id, name, status FROM market_events_experiments_v1
            ORDER BY id DESC LIMIT 10
            """
        ).fetchall()
        ctx["experiments"] = [
            {"id": r[0], "name": r[1], "status": r[2]} for r in rows
        ] if rows else []
    except Exception:
        pass
    return ctx


def _brain_decision_for_trade(trade: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    from bot.research.market_events.signal_intelligence.market_brain_v1.explain import (
        explain_decision,
    )
    from bot.research.market_events.signal_intelligence.market_brain_v1.fusion import (
        bayesian_fusion,
        detect_conflict,
    )
    from bot.research.market_events.signal_intelligence.market_brain_v1.modules import (
        collect_opinions,
    )

    tid = int(trade.get("trade_id") or trade.get("id") or 0)
    opinions = collect_opinions(
        trade,
        replay=ctx["replays"].get(tid),
        edges=ctx["edges"],
        causal=ctx["causality"].get(tid),
        optimizer_state=ctx["optimizer_state"],
        experiments=ctx["experiments"],
    )
    conflict = detect_conflict(opinions)
    fusion = bayesian_fusion(opinions, conflict=conflict)
    explanation = explain_decision(fusion, opinions, trade=trade)
    prob = float(fusion.get("probability_buy") or 0.5)
    conf_raw = float(fusion.get("confidence_raw") or 0.0)
    # Reliability curve uses strength of conviction (raw or |P-0.5|·2)
    conf = max(conf_raw, abs(prob - 0.5) * 2.0)
    return {
        "brain_action": str(fusion.get("decision") or "HOLD"),
        "brain_probability": fusion.get("probability_buy"),
        "brain_confidence": round(min(1.0, conf), 4),
        "expected_ev": fusion.get("expected_ev"),
        "expected_pf": fusion.get("expected_pf"),
        "conflict": str((conflict or {}).get("level") or "LOW"),
        "conflict_score": (conflict or {}).get("conflict_score"),
        "explanation": str((explanation or {}).get("text") or (explanation or {}).get("headline") or ""),
        "explanation_struct": explanation,
        "opinions": opinions,
        "fusion": fusion,
    }


def shadow_decide_one(
    trade: dict[str, Any],
    ctx: dict[str, Any] | None = None,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    """Single-candidate shadow decision (<1s target). No execution."""
    t0 = time.perf_counter()
    ctx = ctx or {
        "edges": [],
        "replays": {},
        "causality": {},
        "optimizer_state": {},
        "experiments": [],
    }
    prod = production_action_from_trade(trade)
    brain = _brain_decision_for_trade(trade, ctx)
    tid = trade.get("trade_id") or trade.get("id")
    pnl = trade.get("pnl")
    if pnl is None:
        pnl = trade.get("pnl_pct")
    row = {
        "candidate_id": trade.get("candidate_id") or trade.get("g31_candidate_id") or tid,
        "trade_id": int(tid) if tid is not None else None,
        "ts": int(trade.get("closed_at") or trade.get("opened_at") or trade.get("ts") or now or time.time()),
        "symbol": str(trade.get("symbol") or ""),
        "production_action": prod,
        "brain_action": brain["brain_action"],
        "brain_probability": brain["brain_probability"],
        "brain_confidence": brain["brain_confidence"],
        "expected_ev": brain["expected_ev"],
        "expected_pf": brain["expected_pf"],
        "explanation": brain["explanation"],
        "conflict": brain["conflict"],
        "conflict_score": brain["conflict_score"],
        "pnl": float(pnl) if pnl is not None else None,
    }
    row = evaluate_row(row)
    row["decision_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
    row["no_execution"] = True
    return row


def run_shadow_live_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    persist_library: bool = True,
    limit: int | None = None,
    batch_size: int = 500,
) -> dict[str, Any]:
    t0 = time.time()
    trades, load_stats = _load_candidates(conn, limit=limit)
    ctx = _preload_brain_context(conn)

    decisions: list[dict[str, Any]] = []
    decision_ms: list[float] = []
    for trade in trades:
        row = shadow_decide_one(trade, ctx)
        decisions.append(row)
        decision_ms.append(float(row.get("decision_ms") or 0.0))

    # Delayed evaluation already applied via evaluate_row when pnl present
    n_evaluated = sum(1 for d in decisions if d.get("evaluated"))
    rolling = rolling_statistics(decisions)
    curve = confidence_curve(decisions)
    cal = calibration_summary(curve)
    promo = promotion_recommendation(rolling, cal)

    library_upserted = 0
    if persist_library and decisions:
        for i in range(0, len(decisions), batch_size):
            library_upserted += upsert_shadow_decisions(conn, decisions[i : i + batch_size])

    mean_ms = round(float(sum(decision_ms) / len(decision_ms)), 3) if decision_ms else None
    p95_ms = None
    if decision_ms:
        xs = sorted(decision_ms)
        p95_ms = round(xs[int(0.95 * (len(xs) - 1))], 3)

    scorecard = {
        "production_vs_brain": rolling.get("all") or {},
        "rolling": {k: rolling[k] for k in rolling if k.startswith("last_")},
        "calibration": cal,
        "confidence_curve": curve,
        "promotion": promo,
        "runtime": {
            "mean_decision_ms": mean_ms,
            "p95_decision_ms": p95_ms,
            "budget_ms": 1000,
            "within_budget": bool(mean_ms is not None and mean_ms < 1000.0),
        },
    }

    result: dict[str, Any] = {
        "ok": True,
        "n_candidates": len(trades),
        "n_decisions": len(decisions),
        "n_evaluated": n_evaluated,
        "load_stats": load_stats,
        "rolling": rolling,
        "confidence_curve": curve,
        "calibration": cal,
        "promotion": promo,
        "scorecard": scorecard,
        "mean_decision_ms": mean_ms,
        "p95_decision_ms": p95_ms,
        "library_upserted": library_upserted,
        "library_sample": load_shadow_decisions(conn, limit=20) if persist_library else [],
        "examples": decisions[:8],
        "elapsed_sec": round(time.time() - t0, 3),
        "research_only": True,
        "no_execution": True,
        "gate_unchanged": True,
        "strategy_unchanged": True,
        "paper_unchanged": True,
        "execution_unchanged": True,
        "optimizer_unchanged": True,
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


__all__ = ["run_shadow_live_v1", "shadow_decide_one"]
