"""Portrait dimensions + co-occurrence mining + ELITE vs IGNORE compare."""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
from typing import Any, Sequence

from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import trade_metrics


def _pnl(row: dict[str, Any]) -> float | None:
    v = row.get("pnl")
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def dimension_portrait(
    tagged: Sequence[dict[str, Any]],
    *,
    prefix: str | None = None,
    exact: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Aggregate WR/PF/EV by tag (optionally filtered by prefix or exact set)."""
    buckets: dict[str, list[float]] = defaultdict(list)
    for rec in tagged:
        tags = rec.get("tags") or []
        pnl = _pnl(rec)
        if pnl is None:
            continue
        for t in tags:
            if prefix and not str(t).startswith(prefix):
                continue
            if exact is not None and t not in exact:
                continue
            buckets[str(t)].append(pnl)

    n_total = max(1, sum(1 for r in tagged if _pnl(r) is not None))
    rows: list[dict[str, Any]] = []
    for key, pnls in buckets.items():
        met = trade_metrics(pnls)
        rows.append({
            "key": key,
            "n": met["n"],
            "pct": round(100.0 * met["n"] / n_total, 2),
            "wr": met["wr"],  # trade_metrics already returns 0..100
            "pf": met["pf"],
            "ev": met["ev"],
        })
    rows.sort(key=lambda r: (r["n"], r.get("wr") or 0), reverse=True)
    return rows


def mine_combos(
    tagged: Sequence[dict[str, Any]],
    *,
    top_n: int = 100,
    sizes: Sequence[int] = (3,),
    min_n: int = 8,
    max_tags_per_trade: int = 10,
) -> list[dict[str, Any]]:
    """
    Mine frequent tag combinations characteristic of ELITE set.
    Rank by n, then WR, then PF. Bounded for <60s on 50k.
    """
    # Prefer market-structure tags for combos (skip ultra-common hour alone noise)
    priority_prefixes = (
        "COIN=", "Regime=", "SESSION=", "ATR", "Funding", "MACD", "RSI",
        "ADX", "EMA", "VWAP", "OI", "Fear", "Fingerprint", "Timeline",
    )
    priority_exact = {"LONG", "SHORT"}

    counts: Counter[tuple[str, ...]] = Counter()
    pnl_map: dict[tuple[str, ...], list[float]] = defaultdict(list)

    for rec in tagged:
        raw_tags = [str(t) for t in (rec.get("tags") or [])]
        preferred = [
            t for t in raw_tags
            if t in priority_exact or any(t.startswith(p) for p in priority_prefixes)
        ]
        tags = sorted(set(preferred or raw_tags))[:max_tags_per_trade]
        if len(tags) < min(sizes):
            continue
        pnl = _pnl(rec)
        for sz in sizes:
            if len(tags) < sz:
                continue
            for combo in combinations(tags, sz):
                key = tuple(combo)
                counts[key] += 1
                if pnl is not None:
                    pnl_map[key].append(pnl)

    ranked: list[dict[str, Any]] = []
    for key, n in counts.items():
        if n < min_n:
            continue
        met = trade_metrics(pnl_map.get(key) or [])
        ranked.append({
            "combo_key": "|".join(key),
            "pattern": " + ".join(key),
            "tags": list(key),
            "n": n,
            "wr": met["wr"],
            "pf": met["pf"],
            "ev": met["ev"],
        })

    ranked.sort(
        key=lambda r: (
            int(r["n"]),
            float(r["wr"] or 0),
            float(r["pf"] or 0),
        ),
        reverse=True,
    )
    out = ranked[:top_n]
    for i, r in enumerate(out, 1):
        r["rank"] = i
    return out


def feature_presence_rate(
    tagged: Sequence[dict[str, Any]],
    feature: str,
) -> tuple[float, int]:
    """% of trades carrying exact feature tag."""
    if not tagged:
        return 0.0, 0
    hit = sum(1 for r in tagged if feature in (r.get("tags") or []))
    return round(100.0 * hit / len(tagged), 2), hit


def elite_vs_ignore(
    elite_tagged: Sequence[dict[str, Any]],
    ignore_tagged: Sequence[dict[str, Any]],
    *,
    min_elite_pct: float = 5.0,
) -> list[dict[str, Any]]:
    """Compare feature presence rates ELITE-group vs IGNORE."""
    elite_tags = Counter()
    for r in elite_tagged:
        elite_tags.update(set(r.get("tags") or []))
    ignore_tags = Counter()
    for r in ignore_tagged:
        ignore_tags.update(set(r.get("tags") or []))

    features = set(elite_tags) | set(ignore_tags)
    n_e = max(1, len(elite_tagged))
    n_i = max(1, len(ignore_tagged))
    rows: list[dict[str, Any]] = []
    for f in features:
        e_pct = round(100.0 * elite_tags.get(f, 0) / n_e, 2)
        i_pct = round(100.0 * ignore_tags.get(f, 0) / n_i, 2)
        if e_pct < min_elite_pct and i_pct < min_elite_pct:
            continue
        rows.append({
            "feature": f,
            "elite_pct": e_pct,
            "ignore_pct": i_pct,
            "delta_pct": round(e_pct - i_pct, 2),
            "elite_n": elite_tags.get(f, 0),
            "ignore_n": ignore_tags.get(f, 0),
        })
    rows.sort(key=lambda r: abs(float(r["delta_pct"])), reverse=True)
    return rows


__all__ = [
    "dimension_portrait",
    "elite_vs_ignore",
    "feature_presence_rate",
    "mine_combos",
]
