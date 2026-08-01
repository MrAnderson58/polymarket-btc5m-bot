"""Adaptive Market Brain V1 orchestrator (research-only intelligence layer)."""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.market_brain_v1.explain import (
    explain_decision,
)
from bot.research.market_events.signal_intelligence.market_brain_v1.fusion import (
    agreement_matrix,
    apply_calibration,
    bayesian_fusion,
    calibrate_confidence,
    detect_conflict,
)
from bot.research.market_events.signal_intelligence.market_brain_v1.library import (
    load_decisions,
    upsert_decisions,
)
from bot.research.market_events.signal_intelligence.market_brain_v1.modules import (
    DEFAULT_WEIGHTS,
    collect_opinions,
)
from bot.research.market_events.signal_intelligence.market_brain_v1.report import (
    write_artifacts,
)


def _load_trades(conn: Any, *, limit: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stats: dict[str, Any] = {"source": "empty"}
    try:
        from bot.research.market_events.signal_intelligence.market_replay_v1.dataset import (
            load_replay_trades,
        )

        rows, stats = load_replay_trades(conn, limit=limit)
        return rows, stats
    except Exception as exc:
        stats["error"] = str(exc)
    try:
        from bot.research.market_events.signal_intelligence.research_lake_v1 import (
            load_research_lake_rows,
            research_lake_row_count,
        )

        if research_lake_row_count(conn) > 0:
            return load_research_lake_rows(conn, limit=limit), {"source": "research_lake_v1"}
    except Exception as exc2:
        stats["error2"] = str(exc2)
    return [], stats


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


def _preload_context(conn: Any) -> dict[str, Any]:
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
    return ctx


def _module_hit(direction: str, pnl: float) -> bool:
    if direction == "BUY":
        return pnl > 0
    if direction == "SELL":
        return pnl < 0
    return abs(pnl) < 0.25  # HOLD roughly flat


def run_market_brain_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    persist_library: bool = True,
    limit: int | None = None,
    batch_size: int = 1000,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    t0 = time.time()
    trades, load_stats = _load_trades(conn, limit=limit)
    ctx = _preload_context(conn)
    module_weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    n = len(trades)

    decisions: list[dict[str, Any]] = []
    raw_confs: list[float] = []
    wins: list[bool] = []
    conflict_examples: list[dict[str, Any]] = []
    agreement_acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    module_correct: Counter = Counter()
    module_total: Counter = Counter()
    brain_correct = 0
    brain_total = 0

    for trade in trades:
        tid = int(trade.get("trade_id") or trade.get("id") or 0)
        opinions = collect_opinions(
            trade,
            replay=ctx["replays"].get(tid),
            edges=ctx["edges"],
            causal=ctx["causality"].get(tid),
            optimizer_state=ctx["optimizer_state"],
            experiments=ctx["experiments"],
            weights=module_weights,
        )
        conflict = detect_conflict(opinions)
        fusion = bayesian_fusion(opinions, conflict=conflict)
        explanation = explain_decision(fusion, opinions, trade=trade)

        # agreement matrix accumulate
        mat = agreement_matrix(opinions)
        for a, row in mat.items():
            for b, v in row.items():
                agreement_acc[a][b].append(float(v))

        pnl = float(trade.get("pnl") or 0.0)
        # Module solo performance
        for o in opinions:
            module_total[o["module"]] += 1
            if _module_hit(str(o["direction"]), pnl):
                module_correct[o["module"]] += 1

        decision = str(fusion.get("decision"))
        if decision != "NO_TRADE":
            brain_total += 1
            if _module_hit(str(fusion.get("direction")), pnl):
                brain_correct += 1

        raw = float(fusion.get("confidence_raw") or 0.0)
        raw_confs.append(raw)
        wins.append(pnl > 0)

        if conflict.get("level") in ("HIGH", "MED"):
            conflict_examples.append({
                "trade_id": tid,
                "level": conflict.get("level"),
                "conflict_score": conflict.get("conflict_score"),
                "strong_modules": conflict.get("strong_modules"),
            })

        decisions.append({
            "trade_id": tid,
            "decision": decision,
            "direction": fusion.get("direction"),
            "probability_buy": fusion.get("probability_buy"),
            "expected_ev": fusion.get("expected_ev"),
            "expected_pf": fusion.get("expected_pf"),
            "risk": fusion.get("risk"),
            "confidence_raw": raw,
            "confidence_calibrated": raw,  # filled after calibration pass
            "conflict_level": conflict.get("level"),
            "conflict_score": conflict.get("conflict_score"),
            "votes": fusion.get("votes"),
            "opinions_slim": [
                {
                    "module": o["module"],
                    "direction": o["direction"],
                    "confidence": o["confidence"],
                    "edge": o["edge"],
                    "weight": o["weight"],
                }
                for o in opinions
            ],
            "explanation": explanation,
            "module_weights": module_weights,
            "pnl": pnl,
        })

    # Calibration pass
    table = calibrate_confidence(raw_confs, wins)
    for d, raw in zip(decisions, raw_confs):
        d["confidence_calibrated"] = apply_calibration(raw, table)

    # Agreement summary
    agreement_summary: dict[str, dict[str, float]] = {}
    for a, row in agreement_acc.items():
        agreement_summary[a] = {
            b: round(float(np.mean(vs)), 4) for b, vs in row.items()
        }

    n_no_trade = sum(1 for d in decisions if d.get("decision") == "NO_TRADE")
    conflict_stats = {
        "n_conflicts_logged": len(conflict_examples),
        "n_no_trade": n_no_trade,
        "conflict_rate": round(n_no_trade / max(1, n), 4),
        "levels": dict(Counter(c.get("level") for c in conflict_examples)),
    }

    replay_performance = {
        "brain": {
            "n": brain_total,
            "hit_rate": round(brain_correct / brain_total, 4) if brain_total else None,
        },
        "modules": {
            m: {
                "n": int(module_total[m]),
                "hit_rate": round(module_correct[m] / module_total[m], 4) if module_total[m] else None,
            }
            for m in module_weights
        },
    }

    library_upserted = 0
    if persist_library and decisions:
        for i in range(0, len(decisions), batch_size):
            library_upserted += upsert_decisions(conn, decisions[i : i + batch_size])

    library_rows = []
    if persist_library:
        try:
            library_rows = load_decisions(conn, limit=100)
        except Exception as exc:
            load_stats["library_error"] = str(exc)

    # Explanation examples: prefer decisive BUY/SELL with high calibrated conf
    examples = sorted(
        [d for d in decisions if d.get("decision") in ("BUY", "SELL", "NO_TRADE")],
        key=lambda d: -float(d.get("confidence_calibrated") or 0),
    )[:8]

    result: dict[str, Any] = {
        "ok": True,
        "n_rows": n,
        "n_decisions": len(decisions),
        "n_no_trade": n_no_trade,
        "module_weights": module_weights,
        "agreement_summary": agreement_summary,
        "conflict_stats": conflict_stats,
        "conflict_examples": conflict_examples[:50],
        "calibration_table": table,
        "mean_confidence_raw": round(float(np.mean(raw_confs)), 4) if raw_confs else None,
        "mean_confidence_calibrated": round(
            float(np.mean([d["confidence_calibrated"] for d in decisions])), 4
        ) if decisions else None,
        "replay_performance": replay_performance,
        "explanation_examples": examples,
        "library_upserted": library_upserted,
        "library_rows": library_rows,
        "load_stats": load_stats,
        "elapsed_sec": round(time.time() - t0, 3),
        "research_only": True,
        "gate_unchanged": True,
        "strategy_unchanged": True,
        "paper_unchanged": True,
        "execution_unchanged": True,
        "optimizer_read_only": True,
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


__all__ = ["run_market_brain_v1"]
