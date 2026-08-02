"""Edge Reality Audit V1 orchestrator (research-only)."""

from __future__ import annotations

import time
import uuid
from typing import Any

from bot.research.market_events.signal_intelligence.edge_reality_v1.ablation import (
    brain_without_analysis,
    incremental_value,
    remove_one_analysis,
)
from bot.research.market_events.signal_intelligence.edge_reality_v1.attribution import (
    attribute_modules,
)
from bot.research.market_events.signal_intelligence.edge_reality_v1.library import (
    upsert_attribution,
)
from bot.research.market_events.signal_intelligence.edge_reality_v1.ranking import (
    complexity_audit,
    grade_modules,
    pareto_analysis,
    simplification_gain,
)
from bot.research.market_events.signal_intelligence.edge_reality_v1.redundancy import (
    redundancy_matrix,
)
from bot.research.market_events.signal_intelligence.edge_reality_v1.report import (
    format_edge_reality_report,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.edge_reality_v1.signals import (
    AUDIT_MODULES,
    collect_module_signals,
)


def _safe_float(v: Any) -> float:
    try:
        if v is None:
            return 0.0
        return float(v)
    except Exception:
        return 0.0


def _index_by_trade_id(rows: list[dict[str, Any]], id_key: str = "trade_id") -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for r in rows:
        try:
            tid = int(r.get(id_key) or 0)
        except Exception:
            continue
        if tid:
            out[tid] = r
    return out


def _load_trades(conn: Any, *, limit: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stats: dict[str, Any] = {"source": "empty"}
    try:
        from bot.research.market_events.signal_intelligence.research_lake_v1 import (
            load_research_lake_rows,
            research_lake_row_count,
        )

        n = research_lake_row_count(conn)
        if n > 0:
            rows = load_research_lake_rows(conn, limit=limit)
            return rows, {"source": "research_lake_v1", "n_lake": n, "n_loaded": len(rows)}
    except Exception as exc:
        stats["lake_error"] = str(exc)

    try:
        from bot.research.market_events.signal_intelligence.market_replay_v1.dataset import (
            load_replay_trades,
        )

        rows, st = load_replay_trades(conn, limit=limit)
        return rows, st
    except Exception as exc2:
        stats["replay_error"] = str(exc2)
    return [], stats


def _preload_context(conn: Any) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "edges": [],
        "replays": {},
        "causality": {},
        "optimizer_state": {},
        "experiments": [],
        "evolution": {},
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

        ctx["replays"] = _index_by_trade_id(load_replays(conn, limit=50_000))
    except Exception:
        pass
    try:
        from bot.research.market_events.signal_intelligence.market_causality_v1.library import (
            load_causality,
        )

        ctx["causality"] = _index_by_trade_id(load_causality(conn, limit=50_000))
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
    try:
        from bot.research.market_events.signal_intelligence.signal_evolution_v1.library import (
            load_evolution,
        )

        evo_rows = load_evolution(conn, limit=500)
        ctx["evolution"] = {str(r.get("signal")): r for r in evo_rows if r.get("signal")}
    except Exception:
        pass
    return ctx


def run_edge_reality_audit_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    persist_library: bool = True,
    limit: int | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    t0 = time.time()
    run_id = run_id or f"era1-{uuid.uuid4().hex[:10]}"
    trades, load_stats = _load_trades(conn, limit=limit)
    ctx = _preload_context(conn)

    module_actions: dict[str, list[str]] = {"production": []}
    for m in AUDIT_MODULES:
        module_actions[m] = []
    pnls: list[float] = []
    module_runtime_ms: dict[str, float] = {m: 0.0 for m in ("production", *AUDIT_MODULES)}

    for trade in trades:
        pnl = _safe_float(trade.get("pnl") if trade.get("pnl") is not None else trade.get("pnl_pct"))
        pnls.append(pnl)
        t_mod = time.perf_counter()
        sigs = collect_module_signals(trade, ctx)
        # Approximate per-module timing share (equal split of signal collection)
        elapsed_ms = (time.perf_counter() - t_mod) * 1000.0 / max(1, len(sigs))
        for name, sig in sigs.items():
            module_actions.setdefault(name, []).append(str(sig.get("action") or "SKIP"))
            module_runtime_ms[name] = module_runtime_ms.get(name, 0.0) + elapsed_ms

    # Truncate to common length
    n = len(pnls)
    for k in list(module_actions.keys()):
        module_actions[k] = (module_actions[k] + ["SKIP"] * n)[:n]

    attribution = attribute_modules(module_actions, pnls)
    incremental = incremental_value(module_actions, pnls)
    remove_one = remove_one_analysis(module_actions, pnls)
    brain_without = brain_without_analysis(trades, ctx, pnls)
    redundancy = redundancy_matrix(module_actions)
    complexity = complexity_audit(attribution, module_runtime_ms=module_runtime_ms)
    ranked = grade_modules(
        attribution,
        remove_one,
        complexity,
        incremental=incremental,
        brain_without=brain_without,
        redundancy=redundancy,
    )
    # Merge grades into attribution rows
    grade_by = {r["module"]: r for r in ranked}
    for row in attribution:
        g = grade_by.get(row["module"])
        if g:
            row["grade"] = g.get("grade")
            row["score"] = g.get("score")
            row["keep"] = g.get("keep")
            row["remove"] = g.get("remove")
    pareto = pareto_analysis(ranked)
    simplification = simplification_gain(ranked, remove_one)

    library_upserted = 0
    if persist_library:
        # Persist attribution + grades
        persist_rows = []
        for row in attribution:
            if row.get("module") == "production":
                persist_rows.append({**row, "grade": "BASE", "keep": True, "score": 0.0})
            else:
                persist_rows.append(row)
        for r in ranked:
            # ensure grade fields present
            pass
        library_upserted = upsert_attribution(conn, persist_rows, run_id=run_id)

    elapsed = round(time.time() - t0, 3)
    result: dict[str, Any] = {
        "ok": n > 0,
        "run_id": run_id,
        "n_trades": n,
        "elapsed_sec": elapsed,
        "load_stats": load_stats,
        "attribution": attribution,
        "incremental": incremental,
        "remove_one": remove_one,
        "brain_without": brain_without,
        "redundancy": redundancy,
        "complexity": complexity,
        "ranked": ranked,
        "pareto": pareto,
        "simplification": simplification,
        "library_upserted": library_upserted,
        "hurts_most": (remove_one[0].get("removed") if remove_one else None),
        "brain_hurts_most": (brain_without[0].get("removed") if brain_without else None),
        "research_only": True,
        "paper_unchanged": True,
        "execution_unchanged": True,
        "strategy_unchanged": True,
        "gate_unchanged": True,
        "optimizer_unchanged": True,
        "brain_unchanged": True,
    }
    if write_reports:
        result["paths"] = write_artifacts(result)
        result["report_markdown"] = format_edge_reality_report(result)
    return result


__all__ = ["run_edge_reality_audit_v1"]
