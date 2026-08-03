"""Fingerprint chain mining + history clusters."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.market_fingerprint_v1.stats import (
    cluster_performance,
)
from bot.research.market_events.signal_intelligence.market_timeline_v1.labels import (
    chain_duration_hint,
    chain_key,
    label_chain,
)
from bot.research.market_events.signal_intelligence.market_timeline_v1.windows import (
    CHAIN_WINDOWS,
    vector_matrix,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import (
    trade_metrics,
)

READY_MIN_N = 30
READY_MIN_WR = 55.0
READY_MIN_PF = 1.35
READY_MIN_CONF = 0.40

# Coarser path for statistically denser chains (still a formation sequence).
CORE_CHAIN_IDX: tuple[int, ...] = (
    CHAIN_WINDOWS.index("24h"),
    CHAIN_WINDOWS.index("4h"),
    CHAIN_WINDOWS.index("1h"),
    CHAIN_WINDOWS.index("15m"),
    CHAIN_WINDOWS.index("5m"),
    CHAIN_WINDOWS.index("Entry"),
)


def _pf_rank(c: dict[str, Any]) -> float:
    n = int(c.get("n") or 0)
    ev = float(c.get("ev") or 0)
    wr = float(c.get("wr") or 0)
    size = 1.0 + min(n, 250) / 250.0
    if c.get("pf") is not None:
        return min(float(c["pf"]), 6.0) * size + 0.01 * wr + 0.02 * n
    # pf_inf often means no negative pnl (many BE zeros) — require size × EV × WR.
    if c.get("pf_inf"):
        return max(0.0, ev) * (wr / 100.0) * size * 3.0 * min(1.0, n / 45.0)
    return ev * size


def _core_labels(full: list[str]) -> list[str]:
    """Drop 12h from full chain labels; keep direction terminal."""
    body = full[:-1]
    core_body = [body[i] for i in CORE_CHAIN_IDX if i < len(body)]
    # Drop leading gaps (often no 24h candle history).
    while core_body and core_body[0] == "missing":
        core_body = core_body[1:]
    if not core_body:
        core_body = ["Range"]
    return core_body + [full[-1]]


def _exit_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_exit: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        ex = str(r.get("exit_reason") or "unknown")
        by_exit[ex].append(float(r.get("pnl") or 0.0))
    if not by_exit:
        return {"best_exit": None, "worst_exit": None}
    ranked = sorted(
        ((k, float(np.mean(v)), len(v)) for k, v in by_exit.items() if v),
        key=lambda x: x[1],
        reverse=True,
    )
    return {
        "best_exit": {"reason": ranked[0][0], "avg_pnl": round(ranked[0][1], 4), "n": ranked[0][2]},
        "worst_exit": {
            "reason": ranked[-1][0],
            "avg_pnl": round(ranked[-1][1], 4),
            "n": ranked[-1][2],
        },
    }


def mine_chains(
    rows: list[dict[str, Any]],
    *,
    top_n: int = 100,
    min_n: int = 15,
) -> dict[str, Any]:
    """Aggregate identical label sequences into fingerprint chains."""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    label_map: dict[int, list[str]] = {}
    for r in rows:
        labs = label_chain(r)
        label_map[int(r["trade_id"])] = labs
        # Prefer coarser core path so sequences accumulate meaningful n.
        buckets[chain_key(_core_labels(labs))].append(r)
        # Also keep full path for rare high-resolution signatures (higher min later).
        buckets[chain_key(labs)].append(r)

    chains: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for key, items in buckets.items():
        if key in seen_keys:
            continue
        seen_keys.add(key)
        # Deduplicate trades inside a bucket (core+full may overlap same trades)
        uniq: dict[int, dict[str, Any]] = {}
        for it in items:
            uniq[int(it["trade_id"])] = it
        items = list(uniq.values())
        # Full-length signatures need more support; core paths use min_n.
        need = min_n if key.count("→") <= 6 else max(min_n, 25)
        if len(items) < need:
            continue
        pnls = [float(x["pnl"]) for x in items]
        hold = [float(x["hold_sec"]) for x in items if x.get("hold_sec") is not None]
        met = trade_metrics(pnls)
        # If PF is infinite only because losses are absent/zero, derive PF from signed legs.
        if met.get("pf_inf"):
            signed = [p for p in pnls if abs(p) > 1e-12]
            if signed:
                met2 = trade_metrics(signed)
                if met2.get("pf") is not None:
                    met["pf"] = met2["pf"]
                    met["pf_inf"] = False
                # keep full-set WR/EV/confidence
                met["wr"] = trade_metrics(pnls).get("wr")
                met["ev"] = trade_metrics(pnls).get("ev")
                met["confidence"] = trade_metrics(pnls).get("confidence")
        perf = cluster_performance(pnls, hold=hold or None)
        labs = _core_labels(label_map[int(items[0]["trade_id"])]) if key.count("→") <= 6 else label_map[int(items[0]["trade_id"])]
        # Prefer stored labels matching the key length
        if chain_key(labs) != key:
            # reconstruct from key
            labs = [p.strip() for p in key.split("→")]
        conf = float(met.get("confidence") or 0.0)
        pf = met.get("pf")
        wr = float(met.get("wr") or 0.0)
        ready = (
            len(items) >= READY_MIN_N
            and wr >= READY_MIN_WR
            and float(met.get("ev") or 0) > 0
            and conf >= READY_MIN_CONF
            and (
                (pf is not None and pf >= READY_MIN_PF)
                or (bool(met.get("pf_inf")) and wr >= READY_MIN_WR and len(items) >= READY_MIN_N)
            )
        )
        exits = _exit_stats(items)
        chains.append({
            "id": 0,
            "chain": key,
            "labels": labs,
            "n": len(items),
            "wr": met.get("wr"),
            "pf": pf,
            "pf_inf": bool(met.get("pf_inf")),
            "ev": met.get("ev"),
            "sharpe": perf.get("sharpe"),
            "max_dd": perf.get("max_dd"),
            "avg_hold": perf.get("avg_hold"),
            "confidence": conf,
            "ready": ready,
            "common_duration": chain_duration_hint(labs),
            "direction": labs[-1] if labs else None,
            "best_exit": exits.get("best_exit"),
            "worst_exit": exits.get("worst_exit"),
            "trade_ids": [int(x["trade_id"]) for x in items[:50]],
        })

    # Prefer robust profitable chains (n, finite PF, EV) over tiny all-win noise.
    chains.sort(
        key=lambda c: (
            1 if c.get("ready") else 0,
            _pf_rank(c),
            float(c.get("ev") or -999),
            int(c.get("n") or 0),
            float(c.get("confidence") or 0),
        ),
        reverse=True,
    )
    for new_id, c in enumerate(chains, start=1):
        c["id"] = new_id

    profitable = [
        c for c in chains
        if int(c.get("n") or 0) >= 20
        and (
            (c.get("pf") is not None and float(c["pf"]) >= 1.2)
            or (c.get("pf_inf") and int(c.get("n") or 0) >= READY_MIN_N)
            or (float(c.get("ev") or 0) > 0 and float(c.get("wr") or 0) >= 52)
        )
    ]
    ready = [c for c in chains if c.get("ready")]
    return {
        "chains": chains,
        "top_chains": chains[:top_n],
        "n_chains": len(chains),
        "n_raw_signatures": len(buckets),
        "n_profitable": len(profitable),
        "n_ready": len(ready),
        "label_map": label_map,
    }


def cluster_histories(
    rows: list[dict[str, Any]],
    *,
    k: int = 18,
    min_n: int = 40,
) -> dict[str, Any]:
    """Cluster similar formation histories (numeric timeline vectors)."""
    if len(rows) < max(min_n, k * 3):
        return {"clusters": [], "assignments": {}, "n": 0}

    try:
        from sklearn.cluster import MiniBatchKMeans
    except Exception:
        return {"clusters": [], "assignments": {}, "n": 0, "error": "sklearn_missing"}

    mat = vector_matrix(rows)
    mu = np.mean(mat, axis=0)
    sd = np.std(mat, axis=0)
    sd = np.where(sd < 1e-9, 1.0, sd)
    norm = (mat - mu) / sd
    kk = min(k, max(2, len(rows) // min_n))
    km = MiniBatchKMeans(
        n_clusters=kk,
        random_state=11,
        batch_size=min(4096, len(rows)),
        n_init=3,
    )
    labels = km.fit_predict(norm)

    # Name clusters from dominant chain motifs
    chain_labels = {int(r["trade_id"]): label_chain(r) for r in rows}
    clusters: list[dict[str, Any]] = []
    assignments: dict[int, str] = {}

    motif_names = {
        ("Bull", "Bull"): "Long trend continuation",
        ("Bear", "Bear"): "Short trend continuation",
        ("Bull", "Range"): "Bull into range",
        ("Bear", "Range"): "Bear into range",
        ("Range", "Bull"): "Range breakout long",
        ("Range", "Bear"): "Range breakout short",
        ("ATR_compression",): "Volatility compression",
        ("funding_squeeze",): "Funding squeeze",
        ("trend_fade",): "Trend exhaustion",
        ("OI_falling",): "OI unwind",
        ("MACD_cross_down",): "MACD rollover",
        ("MACD_cross_up",): "MACD turn-up",
    }

    for lab in range(kk):
        idxs = [i for i, x in enumerate(labels) if int(x) == lab]
        subset = [rows[i] for i in idxs]
        if len(subset) < max(20, min_n // 2):
            continue
        pnls = [float(s["pnl"]) for s in subset]
        hold = [float(s["hold_sec"]) for s in subset if s.get("hold_sec") is not None]
        perf = cluster_performance(pnls, hold=hold or None)
        exits = _exit_stats(subset)
        dirs = Counter(str(s.get("direction") or "") for s in subset)
        # motif from first two non-direction labels
        motif_counter: Counter[str] = Counter()
        for s in subset:
            labs = chain_labels.get(int(s["trade_id"])) or []
            core = [x for x in labs[:-1] if x != "missing"]
            if len(core) >= 2:
                motif_counter[f"{core[0]}|{core[1]}"] += 1
            for tag in core:
                if tag in (
                    "ATR_compression", "funding_squeeze", "trend_fade",
                    "OI_falling", "MACD_cross_down", "MACD_cross_up",
                ):
                    motif_counter[tag] += 3
        top_motif = motif_counter.most_common(1)[0][0] if motif_counter else "mixed"
        name = None
        if "|" in top_motif:
            a, b = top_motif.split("|", 1)
            name = motif_names.get((a, b))
        if name is None:
            name = motif_names.get((top_motif,))
        if name is None:
            top_dir = dirs.most_common(1)[0][0] if dirs else "UNK"
            name = f"{top_dir} formation {lab:02d}"
        cid = f"C{lab:02d}_{name.replace(' ', '_')}"
        for s in subset:
            assignments[int(s["trade_id"])] = cid
        clusters.append({
            "id": cid,
            "name": name,
            "n": len(subset),
            "wr": perf.get("wr"),
            "pf": perf.get("pf"),
            "ev": perf.get("ev"),
            "sharpe": perf.get("sharpe"),
            "max_dd": perf.get("max_dd"),
            "avg_duration": perf.get("avg_hold"),
            "best_exit": exits.get("best_exit"),
            "worst_exit": exits.get("worst_exit"),
            "direction_mode": dirs.most_common(1)[0][0] if dirs else None,
            "motif": top_motif,
        })

    clusters.sort(
        key=lambda c: (
            float(c["pf"]) if c.get("pf") is not None else -1.0,
            float(c.get("ev") or -999),
            int(c.get("n") or 0),
        ),
        reverse=True,
    )
    return {"clusters": clusters, "assignments": assignments, "n": len(rows), "n_clusters": len(clusters)}


__all__ = ["cluster_histories", "mine_chains"]
