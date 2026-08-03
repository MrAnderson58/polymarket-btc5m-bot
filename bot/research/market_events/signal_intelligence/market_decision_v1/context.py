"""Build DecisionContext once from existing research engines/libraries."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.signal_intelligence.market_fingerprint_v1.clusters import (
    build_clusters,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.similarity import (
    build_similarity_index,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.snapshots import (
    load_candle_book,
    snapshot_trade,
)
from bot.research.market_events.signal_intelligence.market_timeline_v1.chains import (
    mine_chains,
)
from bot.research.market_events.signal_intelligence.market_timeline_v1.windows import (
    timeline_trade,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.features import (
    enrich_trades,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.rules import (
    find_forbidden,
    find_minimal_rules,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.setups import (
    mine_setups,
)
from bot.research.market_events.signal_intelligence.trading_rules_v1.validate import (
    build_predicate,
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
        return rows, {"source": "research_lake_v1", "n_lake": n, "n_loaded": len(rows)}
    except Exception as exc:
        return [], {"source": "empty", "error": str(exc)}


def _preload_libraries(conn: Any) -> dict[str, Any]:
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


def _compile_rule_preds(
    setups: list[dict[str, Any]],
    atomics: list[Any],
    *,
    limit: int = 40,
    rows: list[dict[str, Any]] | None = None,
    max_hit_share: float | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    n_rows = len(rows) if rows else 0
    for s in setups[: max(limit * 3, limit)]:
        conds = list(s.get("conditions") or s.get("rules") or [])
        if not conds and s.get("setup"):
            from bot.research.market_events.signal_intelligence.trading_rules_v1.parse import (
                rule_from_setup,
            )

            parsed = rule_from_setup(str(s.get("setup") or ""))
            conds = list(parsed.get("conditions") or [])
        if not conds:
            continue
        pred = build_predicate(conds, atomics=atomics)
        if pred is None:
            continue
        if rows and max_hit_share is not None and n_rows > 0:
            hits = 0
            for r in rows:
                try:
                    if pred(r):
                        hits += 1
                except Exception:
                    continue
            if hits / n_rows > max_hit_share:
                continue
        # Skip useless "blocks" that are not clearly losing
        wr = s.get("wr")
        pf = s.get("pf")
        if max_hit_share is not None:
            if wr is not None and float(wr) >= 45.0:
                continue
            if pf is not None and float(pf) >= 0.9:
                continue
        out.append({
            "conditions": conds,
            "pred": pred,
            "wr": s.get("wr"),
            "pf": s.get("pf"),
            "ev": s.get("ev"),
            "n": s.get("n"),
            "confidence": s.get("confidence"),
            "label": " + ".join(conds)[:120],
        })
        if len(out) >= limit:
            break
    return out


def build_decision_context(
    conn: Any,
    *,
    limit: int | None = None,
    skip_heavy: bool = False,
) -> dict[str, Any]:
    """
    Assemble existing research artifacts once.
    No new features — only Fingerprint / Timeline / DNA / Rules / Edge / Replay / Causality / Brain libs.
    """
    t0 = time.time()
    trades, load_stats = _load_trades(conn, limit=limit)
    libs = _preload_libraries(conn)
    book = {} if skip_heavy else load_candle_book(conn)

    fp_by_id: dict[int, dict[str, Any]] = {}
    fp_rows: list[dict[str, Any]] = []
    tl_by_id: dict[int, dict[str, Any]] = {}
    tl_rows: list[dict[str, Any]] = []
    if not skip_heavy and book:
        for tr in trades:
            snap = snapshot_trade(tr, book)
            if snap is not None:
                fp_rows.append(snap)
                fp_by_id[int(snap["trade_id"])] = snap
            tl = timeline_trade(tr, book)
            if tl is not None:
                tl_rows.append(tl)
                tl_by_id[int(tl["trade_id"])] = tl

    fp_index = build_similarity_index(fp_rows) if fp_rows else {"ok": False}
    clustered = build_clusters(fp_rows, k_per_bucket=6, min_n=40) if fp_rows else {"assignments": {}}
    mined_chains = mine_chains(tl_rows, top_n=100, min_n=12) if tl_rows else {
        "chains": [], "top_chains": [], "n_chains": 0, "n_ready": 0, "label_map": {}
    }
    from bot.research.market_events.signal_intelligence.market_timeline_v1.labels import (
        label_chain,
    )

    tl_labels: dict[int, list[str]] = {}
    for r in tl_rows:
        tl_labels[int(r["trade_id"])] = label_chain(r)
    tl_chain_by_key = {
        c.get("chain"): c for c in (mined_chains.get("chains") or []) if c.get("chain")
    }

    enriched, feat_stats = enrich_trades(trades, conn)
    enriched_by_id = {int(r.get("trade_id") or r.get("id") or 0): r for r in enriched}
    mined = mine_setups(enriched, top_n=80, min_n=25, max_combo=3) if enriched else {
        "atomic_rules": [], "masks": {}, "profitable": [], "losing": []
    }
    atomics = mined.get("atomic_rules") or []
    masks = mined.get("masks") or {}
    profitable = mined.get("profitable") or []
    losing = mined.get("losing") or []
    minimal = find_minimal_rules(enriched, atomics, masks, min_n=60) if enriched else []
    forbidden = find_forbidden(losing, max_pf=0.70, min_n=40, limit=30) if losing else []

    dna_preds = _compile_rule_preds(profitable + minimal, atomics, limit=50, rows=enriched)
    forbid_preds = _compile_rule_preds(
        forbidden, atomics, limit=20, rows=enriched, max_hit_share=0.20
    )

    # Rules: DNA profitable as soft READY; hard blocks from narrow forbidden DNA only.
    # Skip extract_blocks (combinatorial minimize) — too slow for Decision Replay.
    ready_preds = list(dna_preds[:25])
    block_preds = list(forbid_preds)

    elapsed = round(time.time() - t0, 3)
    return {
        "ok": len(trades) > 0,
        "trades": trades,
        "load_stats": load_stats,
        "n_trades": len(trades),
        "elapsed_build_sec": elapsed,
        "edges": libs["edges"],
        "replays": libs["replays"],
        "causality": libs["causality"],
        "optimizer_state": libs["optimizer_state"],
        "experiments": libs["experiments"],
        "fp_index": fp_index,
        "fp_by_id": fp_by_id,
        "fp_assignments": clustered.get("assignments") or {},
        "tl_by_id": tl_by_id,
        "tl_rows": tl_rows,
        "tl_chains": mined_chains.get("top_chains") or mined_chains.get("chains") or [],
        "tl_labels": tl_labels,
        "tl_chain_by_key": tl_chain_by_key,
        "enriched_by_id": enriched_by_id,
        "dna_preds": dna_preds,
        "ready_preds": ready_preds,
        "block_preds": block_preds,
        "feature_stats": feat_stats,
        "n_edges": len(libs["edges"]),
        "n_replays": len(libs["replays"]),
        "n_causality": len(libs["causality"]),
        "n_fp": len(fp_rows),
        "n_tl": len(tl_rows),
        "n_dna": len(dna_preds),
        "n_ready_rules": len(ready_preds),
        "n_blocks": len(block_preds),
        "research_only": True,
    }


__all__ = ["build_decision_context"]
