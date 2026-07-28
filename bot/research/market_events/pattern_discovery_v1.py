"""Pattern Discovery V1 — cluster closed S55 trades into durable market patterns (research only)."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any

from bot.research.market_events.expectancy_intelligence.stats import (
    mean,
    reliability_rank_score,
    trade_outcome_stats,
)
from bot.research.market_events.research_pack_01.historical import load_closed_s55_trades

# Numeric dims used for clustering (same family as S55 similarity).
_CLUSTER_KEYS: tuple[str, ...] = (
    "trend",
    "funding",
    "oi_delta",
    "volatility",
    "fear_greed",
    "ai_score",
    "neighbor_ev",
)

_MIN_CLUSTER_N = 5
KMEANS_SEED = 42  # fixed — Pattern Discovery must be reproducible


def _finite(v: Any) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _direction_code(d: Any) -> float:
    s = str(d or "").upper()
    if s == "LONG":
        return 1.0
    if s == "SHORT":
        return -1.0
    return 0.0


def _normalize_matrix(
    trades: list[dict[str, Any]],
) -> tuple[list[list[float]], list[str], dict[str, tuple[float, float]]]:
    """Return feature matrix, feature names, and (mean, std) per column for denorm."""
    names = list(_CLUSTER_KEYS) + ["direction"]
    cols: list[list[float | None]] = []
    for key in _CLUSTER_KEYS:
        cols.append([_finite(t.get(key)) for t in trades])
    cols.append([_direction_code(t.get("direction")) for t in trades])

    # Impute missing with column median of available
    filled: list[list[float]] = []
    norms: dict[str, tuple[float, float]] = {}
    for name, col in zip(names, cols):
        present = [v for v in col if v is not None]
        fill = mean(present) if present else 0.0
        series = [float(v if v is not None else fill) for v in col]
        mu = mean(series)
        var = sum((x - mu) ** 2 for x in series) / max(1, len(series) - 1) if len(series) > 1 else 0.0
        sd = math.sqrt(var) if var > 0 else 1.0
        norms[name] = (mu, sd)
        filled.append([(x - mu) / sd for x in series])

    # rows
    matrix = [[filled[j][i] for j in range(len(names))] for i in range(len(trades))]
    return matrix, names, norms


def _dist(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(len(a))))


def _kmeans(
    matrix: list[list[float]],
    *,
    k: int,
    max_iter: int = 40,
    seed: int = KMEANS_SEED,
) -> list[int]:
    n = len(matrix)
    if n == 0:
        return []
    k = max(1, min(k, n))
    rng = random.Random(seed)
    # init: random distinct points
    idxs = list(range(n))
    rng.shuffle(idxs)
    centroids = [list(matrix[i]) for i in idxs[:k]]
    labels = [0] * n
    for _ in range(max_iter):
        changed = False
        for i, row in enumerate(matrix):
            best = min(range(k), key=lambda c: _dist(row, centroids[c]))
            if labels[i] != best:
                labels[i] = best
                changed = True
        # recompute
        for c in range(k):
            members = [matrix[i] for i in range(n) if labels[i] == c]
            if not members:
                centroids[c] = list(matrix[rng.randrange(n)])
                continue
            dim = len(members[0])
            centroids[c] = [sum(m[d] for m in members) / len(members) for d in range(dim)]
        if not changed:
            break
    return labels


def _choose_k(n: int) -> int:
    if n < 20:
        return max(2, n // 5 or 1)
    # aim ~30–50 patterns max for LLM
    return max(8, min(40, n // 40 or 8))


def _cluster_feature_profile(
    trades: list[dict[str, Any]],
    all_trades: list[dict[str, Any]],
) -> dict[str, Any]:
    profile: dict[str, Any] = {}
    for key in _CLUSTER_KEYS:
        vals = [_finite(t.get(key)) for t in trades]
        vals_f = [v for v in vals if v is not None]
        glob = [_finite(t.get(key)) for t in all_trades]
        glob_f = [v for v in glob if v is not None]
        if not vals_f:
            continue
        profile[key] = {
            "mean": round(mean(vals_f), 4),
            "global_mean": round(mean(glob_f), 4) if glob_f else None,
        }
    dirs = [str(t.get("direction") or "").upper() for t in trades]
    if dirs:
        long_n = sum(1 for d in dirs if d == "LONG")
        profile["direction"] = "LONG" if long_n >= len(dirs) / 2 else "SHORT"
        profile["direction_pct_long"] = round(100.0 * long_n / len(dirs), 1)
    regimes = [str(t.get("market_regime") or "").strip() for t in trades if t.get("market_regime")]
    if regimes:
        counts: dict[str, int] = {}
        for r in regimes:
            counts[r] = counts.get(r, 0) + 1
        profile["regime"] = max(counts.items(), key=lambda x: x[1])[0]
    return profile


def _auto_name(profile: dict[str, Any], metrics: dict[str, Any]) -> str:
    """Generate a short pattern label from dominant features (template, not LLM)."""
    parts: list[str] = []
    direction = profile.get("direction")
    trend = (profile.get("trend") or {}).get("mean")
    g_trend = (profile.get("trend") or {}).get("global_mean")
    funding = (profile.get("funding") or {}).get("mean")
    g_fund = (profile.get("funding") or {}).get("global_mean")
    oi = (profile.get("oi_delta") or {}).get("mean")
    g_oi = (profile.get("oi_delta") or {}).get("global_mean")
    vol = (profile.get("volatility") or {}).get("mean")
    g_vol = (profile.get("volatility") or {}).get("global_mean")

    if direction:
        parts.append("Long" if direction == "LONG" else "Short")

    if trend is not None and g_trend is not None:
        if trend > g_trend:
            parts.append("Trend Continuation")
        elif trend < g_trend:
            parts.append("Trend Pullback")
        else:
            parts.append("Neutral Trend")
    elif trend is not None:
        parts.append("Trend Bias")

    if oi is not None and g_oi is not None:
        if oi > g_oi:
            parts.append("OI Rising")
        elif oi < g_oi:
            parts.append("OI Falling")

    if funding is not None and g_fund is not None:
        if abs(funding - g_fund) > abs(g_fund) * 0.05 + 1e-9:
            parts.append("Crowded Funding" if funding > g_fund else "Cheap Funding")

    if vol is not None and g_vol is not None and vol > g_vol:
        parts.append("Elevated Vol")
    elif vol is not None and g_vol is not None and vol < g_vol:
        parts.append("Quiet Vol")

    if metrics.get("ev", 0) > 0.5 and metrics.get("wr", 0) >= 55:
        style = "Momentum Continuation"
    elif metrics.get("ev", 0) < -0.5 and (metrics.get("mae") or 0) < -1:
        style = "Liquidity Sweep Risk"
    elif "Pullback" in " ".join(parts):
        style = "Slow Trend Pullback"
    else:
        style = None

    # Prefer compact name
    if style and len(parts) >= 2:
        return f"{style} ({direction or '?'})"
    if parts:
        return " / ".join(parts[:4])
    return "Unlabeled Pattern"


def _cluster_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    s = trade_outcome_stats(trades, min_reliable_n=30)
    mfes = [float(t["mfe_pct"]) for t in trades if t.get("mfe_pct") is not None]
    maes = [float(t["mae_pct"]) for t in trades if t.get("mae_pct") is not None]
    holds = [float(t["duration_sec"]) for t in trades if t.get("duration_sec") is not None]
    ev = float(s["expectancy"])
    n = int(s["trades"])
    return {
        "n": n,
        "ev": ev,
        "pf": s["profit_factor"] if s["profit_factor"] != float("inf") else None,
        "wr": s["win_rate"],
        "mfe": round(mean(mfes), 4) if mfes else None,
        "mae": round(mean(maes), 4) if maes else None,
        "holding_sec": round(mean(holds), 1) if holds else None,
        "rank_score": reliability_rank_score(ev, n),
        "reliable": s["reliable"],
    }


def build_pattern_discovery(
    conn: Any,
    *,
    limit: int = 50000,
    k: int | None = None,
) -> dict[str, Any]:
    trades = load_closed_s55_trades(conn, limit=limit)
    n = len(trades)
    if n == 0:
        return {"n_trades": 0, "k": 0, "clusters": [], "top": [], "worst": []}

    matrix, feat_names, norms = _normalize_matrix(trades)
    kk = k if k is not None else _choose_k(n)
    labels = _kmeans(matrix, k=kk)

    by_c: dict[int, list[int]] = {}
    for i, lab in enumerate(labels):
        by_c.setdefault(lab, []).append(i)

    clusters: list[dict[str, Any]] = []
    for cid, idxs in sorted(by_c.items()):
        subset = [trades[i] for i in idxs]
        if len(subset) < _MIN_CLUSTER_N:
            continue
        metrics = _cluster_metrics(subset)
        profile = _cluster_feature_profile(subset, trades)
        name = _auto_name(profile, metrics)
        # centroid in original-ish space for export
        centroid = {}
        for key in _CLUSTER_KEYS:
            vals = [_finite(t.get(key)) for t in subset]
            vals_f = [v for v in vals if v is not None]
            if vals_f:
                centroid[key] = round(mean(vals_f), 4)
        centroid["direction"] = profile.get("direction")
        if profile.get("regime"):
            centroid["market_regime"] = profile["regime"]

        clusters.append(
            {
                "cluster_id": cid,
                "name": name,
                "features": {
                    "trend": centroid.get("trend"),
                    "funding": centroid.get("funding"),
                    "oi": centroid.get("oi_delta"),
                    "direction": centroid.get("direction"),
                    "volatility": centroid.get("volatility"),
                    "fear_greed": centroid.get("fear_greed"),
                    "ai_score": centroid.get("ai_score"),
                    "regime": centroid.get("market_regime"),
                },
                "profile": profile,
                "metrics": metrics,
                "trade_indices": idxs[:500],  # cap for JSON size
                "symbols_sample": sorted({str(t.get("symbol") or "") for t in subset})[:20],
            }
        )

    clusters.sort(key=lambda c: -c["metrics"]["rank_score"])
    top = clusters[:10]
    worst = sorted(clusters, key=lambda c: c["metrics"]["rank_score"])[:10]

    return {
        "n_trades": n,
        "k": kk,
        "feature_names": feat_names,
        "norms": {k: {"mean": v[0], "std": v[1]} for k, v in norms.items()},
        "clusters": clusters,
        "top": top,
        "worst": worst,
    }


def write_pattern_exports(
    data: dict[str, Any],
    root: Path | None = None,
) -> dict[str, Path]:
    out_dir = root or Path("reports/research")
    out_dir.mkdir(parents=True, exist_ok=True)
    patterns_dir = out_dir / "patterns"
    patterns_dir.mkdir(parents=True, exist_ok=True)

    # Clear old cluster jsons (keep folder)
    for old in patterns_dir.glob("cluster_*.json"):
        old.unlink()

    md_path = out_dir / "patterns.md"
    lines = [
        "# Pattern Discovery V1",
        "",
        f"Closed S55 trades: **{data['n_trades']}**  ·  k-means clusters kept (n≥{_MIN_CLUSTER_N}): "
        f"**{len(data['clusters'])}** (k={data['k']})",
        "",
        "Ranked by **EV × log(n)**. Research only — not live signals.",
        "",
        "## TOP 10 clusters",
        "",
    ]
    for i, c in enumerate(data["top"], 1):
        m = c["metrics"]
        f = c["features"]
        lines.append(
            f"### {i}. {c['name']} (id={c['cluster_id']})\n"
            f"- Trend={f.get('trend')}  Funding={f.get('funding')}  OI={f.get('oi')}  "
            f"Direction={f.get('direction')}  Vol={f.get('volatility')}\n"
            f"- EV={m['ev']}%  PF={m['pf']}  WR={m['wr']}%  MFE={m['mfe']}  MAE={m['mae']}  "
            f"Hold={m['holding_sec']}s  n={m['n']}  score={m['rank_score']}\n"
        )

    lines.extend(["", "## WORST 10 clusters", ""])
    for i, c in enumerate(data["worst"], 1):
        m = c["metrics"]
        lines.append(
            f"### {i}. {c['name']} (id={c['cluster_id']})\n"
            f"- EV={m['ev']}%  PF={m['pf']}  WR={m['wr']}%  n={m['n']}  score={m['rank_score']}\n"
        )

    lines.extend(["", "## All clusters (summary)", ""])
    for c in data["clusters"]:
        m = c["metrics"]
        lines.append(
            f"- `{c['cluster_id']}` **{c['name']}** — EV={m['ev']} n={m['n']} score={m['rank_score']}"
        )

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Combined JSON for LLM
    export_payload = {
        "version": 1,
        "n_trades": data["n_trades"],
        "k": data["k"],
        "clusters": [
            {
                "cluster_id": c["cluster_id"],
                "name": c["name"],
                "features": c["features"],
                "metrics": c["metrics"],
                "symbols_sample": c.get("symbols_sample"),
            }
            for c in data["clusters"]
        ],
        "top10_ids": [c["cluster_id"] for c in data["top"]],
        "worst10_ids": [c["cluster_id"] for c in data["worst"]],
    }
    all_json = out_dir / "patterns.json"
    all_json.write_text(json.dumps(export_payload, indent=2), encoding="utf-8")

    paths: dict[str, Path] = {"patterns_md": md_path, "patterns_json": all_json}
    for c in data["clusters"]:
        p = patterns_dir / f"cluster_{c['cluster_id']:03d}.json"
        p.write_text(
            json.dumps(
                {
                    "cluster_id": c["cluster_id"],
                    "name": c["name"],
                    "features": c["features"],
                    "profile": c["profile"],
                    "metrics": c["metrics"],
                    "symbols_sample": c.get("symbols_sample"),
                    "trade_indices": c.get("trade_indices") or [],
                    "n_trade_indices": len(c.get("trade_indices") or []),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        paths[f"cluster_{c['cluster_id']}"] = p
    return paths


def format_pattern_discovery_cli(data: dict[str, Any]) -> str:
    lines = [
        "PATTERN DISCOVERY V1 (research only)",
        f"  n_trades={data['n_trades']}  k={data['k']}  clusters={len(data['clusters'])}",
        "",
        "TOP 10 (EV×log(n)):",
    ]
    for i, c in enumerate(data["top"], 1):
        m = c["metrics"]
        lines.append(
            f"  {i}. {c['name']}  EV={m['ev']}% WR={m['wr']}% n={m['n']} score={m['rank_score']}"
        )
    if not data["top"]:
        lines.append("  (none)")
    lines.append("")
    lines.append("WORST 10:")
    for i, c in enumerate(data["worst"], 1):
        m = c["metrics"]
        lines.append(
            f"  {i}. {c['name']}  EV={m['ev']}% WR={m['wr']}% n={m['n']} score={m['rank_score']}"
        )
    if not data["worst"]:
        lines.append("  (none)")
    return "\n".join(lines)


def run_pattern_discovery(conn: Any, *, write_reports: bool = True) -> str:
    data = build_pattern_discovery(conn)
    text = format_pattern_discovery_cli(data)
    if write_reports and data["n_trades"]:
        paths = write_pattern_exports(data)
        text += f"\n\nWrote {paths['patterns_md']}\nWrote {paths['patterns_json']}\n"
        text += f"Per-cluster JSON: reports/research/patterns/ ({len(data['clusters'])} files)"
    return text
