"""Market Replay Engine V1 orchestrator (research-only)."""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.market_replay_v1.dataset import (
    load_replay_trades,
    preload_candles_by_symbol,
    preload_snapshots_g3,
)
from bot.research.market_events.signal_intelligence.market_replay_v1.frames import (
    build_market_frames,
    future_path,
    timeline_index,
)
from bot.research.market_events.signal_intelligence.market_replay_v1.library import (
    load_replays,
    upsert_replays,
)
from bot.research.market_events.signal_intelligence.market_replay_v1.liquidity import (
    event_timeline,
    liquidity_evolution,
)
from bot.research.market_events.signal_intelligence.market_replay_v1.patterns import (
    discover_replay_patterns,
    missing_data_report,
)
from bot.research.market_events.signal_intelligence.market_replay_v1.similarity import (
    build_matrix,
    euclidean_distances,
    price_path_from_frames,
    similarity_for_trade,
)
from bot.research.market_events.signal_intelligence.market_replay_v1.report import (
    write_artifacts,
)


def _entry_features(trade: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "rsi", "atr", "atr_pct", "funding", "funding_delta", "oi_delta", "open_interest",
        "fear_greed", "btc_dominance", "ema20_distance", "ema50_distance", "ema200_distance",
        "vwap_distance", "macd", "adx", "stoch_k", "trend", "volume", "news_score",
        "ai_score", "pattern", "gate_decision", "alpha_cluster", "edge_cluster",
        "regime", "direction", "symbol", "confidence", "optimizer_state",
    )
    return {k: trade.get(k) for k in keys}


def _quality(frames: list[dict[str, Any]]) -> float:
    if not frames:
        return 0.0
    return round(float(np.mean([float(f.get("coverage") or 0) for f in frames])), 4)


def _missing_for_replay(frames: list[dict[str, Any]]) -> dict[str, Any]:
    field_c: Counter[str] = Counter()
    entry_missing: list[str] = []
    for f in frames:
        for m in f.get("missing") or []:
            field_c[str(m)] += 1
        if int(f.get("offset_min") or 0) == 0:
            entry_missing = list(f.get("missing") or [])
    return {"fields": dict(field_c), "entry_missing": entry_missing}


def run_market_replay_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    persist_library: bool = True,
    limit: int | None = None,
    similar_k: int = 100,
    batch_size: int = 1000,
) -> dict[str, Any]:
    """Reconstruct timelines for CLOSED lake trades; similarity + library."""
    t0 = time.time()
    trades, load_stats = load_replay_trades(conn, limit=limit)
    n = len(trades)

    symbols = sorted({str(t.get("symbol") or "") for t in trades if t.get("symbol")})
    candles_by_sym = preload_candles_by_symbol(conn, symbols)
    snapshots = preload_snapshots_g3(conn)
    load_stats["n_symbols"] = len(symbols)
    load_stats["n_candles"] = sum(len(v) for v in candles_by_sym.values())
    load_stats["n_snapshots_g3"] = len(snapshots)

    replays: list[dict[str, Any]] = []
    frame_paths: list[np.ndarray] = []
    pending: list[dict[str, Any]] = []
    library_upserted = 0

    for i, trade in enumerate(trades):
        sym = str(trade.get("symbol") or "")
        frames = build_market_frames(
            trade,
            candles=candles_by_sym.get(sym) or [],
            snapshots=snapshots,
        )
        liq = liquidity_evolution(frames)
        events = event_timeline(trade, frames)
        path = price_path_from_frames(frames)
        frame_paths.append(path)
        q = _quality(frames)
        replay = {
            "trade_id": int(trade.get("trade_id") or 0),
            "timeline": timeline_index(frames),
            "market_frames": frames,
            "events": events,
            "features": _entry_features(trade),
            "future_path": future_path(frames),
            "regime": trade.get("regime") or "",
            "similar_ids": [],  # filled after matrix pass
            "quality": q,
            "liquidity": liq,
            "missing_report": _missing_for_replay(frames),
            "pnl": trade.get("pnl"),
            "symbol": trade.get("symbol"),
            "direction": trade.get("direction"),
        }
        replays.append(replay)
        pending.append(replay)
        if persist_library and len(pending) >= batch_size:
            # similar_ids filled later — upsert twice or defer; defer to end for speed
            pass

    # Similarity pass — sklearn kNN when available (scales to 30k)
    similar_map: dict[int, list[int]] = {}
    similarity_accuracy: dict[str, Any] = {}
    if n > 0:
        X, _ = build_matrix(trades)
        neighbor_idx = None
        neighbor_dist = None
        try:
            from sklearn.neighbors import NearestNeighbors

            nn = NearestNeighbors(
                n_neighbors=min(similar_k + 1, n),
                algorithm="auto",
                metric="euclidean",
            )
            nn.fit(X)
            neighbor_dist, neighbor_idx = nn.kneighbors(X, return_distance=True)
        except Exception:
            neighbor_idx = None

        id_to_idx = {int(trades[j].get("trade_id") or 0): j for j in range(n)}
        for i in range(n):
            if neighbor_idx is not None:
                idxs = [int(j) for j in neighbor_idx[i] if int(j) != i][:similar_k]
                dist_map = {
                    int(j): float(d)
                    for j, d in zip(neighbor_idx[i], neighbor_dist[i])
                    if int(j) != i
                }
            else:
                d = euclidean_distances(X, i)
                d[i] = np.inf
                kk = min(similar_k, n - 1)
                if kk <= 0:
                    similar_map[i] = []
                    replays[i]["similar_ids"] = []
                    continue
                idx = np.argpartition(d, kth=min(kk, len(d) - 1))[: kk + 3]
                idx = idx[np.argsort(d[idx])][:kk]
                idxs = [int(j) for j in idx]
                dist_map = {int(j): float(d[int(j)]) for j in idxs}

            ids = [int(trades[j].get("trade_id") or 0) for j in idxs]
            similar_map[i] = ids
            replays[i]["similar_ids"] = ids
            replays[i]["similar_preview"] = [
                {
                    "trade_id": tid,
                    "distance": round(dist_map.get(j, 0.0), 6),
                    "metric": "euclidean",
                }
                for tid, j in zip(ids[:10], idxs[:10])
            ]

        hits = 0
        total = 0
        for i in range(n):
            for tid in (similar_map.get(i) or [])[:10]:
                j = id_to_idx.get(tid)
                if j is None:
                    continue
                total += 1
                if (
                    str(trades[i].get("regime") or "") == str(trades[j].get("regime") or "")
                    or str(trades[i].get("direction") or "") == str(trades[j].get("direction") or "")
                ):
                    hits += 1
        similarity_accuracy = {
            "metric": "euclidean_top10_regime_or_direction_match",
            "hit_rate": round(hits / total, 4) if total else None,
            "n_pairs": total,
        }

    # Multi-metric examples (first 3 trades) for SIMILARITY_REPORT
    examples: list[dict[str, Any]] = []
    for i in range(min(3, n)):
        examples.append({
            "trade_id": int(trades[i].get("trade_id") or 0),
            "metrics": similarity_for_trade(
                trades, i, k=min(similar_k, 100), frame_paths=frame_paths
            ),
            "replay_excerpt": {
                "quality": replays[i]["quality"],
                "timeline_labels": [f["label"] for f in replays[i]["market_frames"]],
                "entry_frame": next(
                    (f for f in replays[i]["market_frames"] if f.get("offset_min") == 0),
                    None,
                ),
                "liquidity": replays[i]["liquidity"],
                "events": replays[i]["events"][:8],
            },
        })

    if persist_library and replays:
        # batch upsert
        for i in range(0, len(replays), batch_size):
            library_upserted += upsert_replays(conn, replays[i : i + batch_size])

    patterns = discover_replay_patterns(replays)
    missing = missing_data_report(replays)
    library_rows = []
    if persist_library:
        try:
            library_rows = load_replays(conn, limit=100)
        except Exception as exc:
            load_stats["library_load_error"] = str(exc)

    result: dict[str, Any] = {
        "ok": True,
        "n_rows": n,
        "n_replays": len(replays),
        "replay_coverage_pct": missing.get("coverage_pct"),
        "mean_quality": missing.get("mean_quality"),
        "missing_data_report": missing,
        "similarity_accuracy": similarity_accuracy,
        "patterns": patterns,
        "examples": examples,
        "top20_replays": sorted(replays, key=lambda r: -float(r.get("quality") or 0))[:20],
        "library_upserted": library_upserted,
        "library_rows": library_rows,
        "load_stats": load_stats,
        "elapsed_sec": round(time.time() - t0, 3),
        "similar_k": similar_k,
        "read_only_research": True,
        "gate_unchanged": True,
        "optimizer_unchanged": True,
        "strategy_unchanged": True,
        "paper_unchanged": True,
        "execution_unchanged": True,
        "no_n_plus_1_sql": True,
    }

    # Slim candidates for JSON size — strip full frames from top20 copy in artifacts
    result["_replays_full_count"] = len(replays)

    paths: dict[str, str] = {}
    md = ""
    if write_reports:
        paths = write_artifacts(result, replays=replays)
        try:
            from pathlib import Path

            md = Path(paths["report_md"]).read_text(encoding="utf-8")
        except Exception:
            md = ""
    result["paths"] = paths
    result["report_markdown"] = md
    return result


__all__ = ["run_market_replay_v1"]
