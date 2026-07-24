"""S62.3 / S62.3.1 / S63.2 — Pattern Discovery Engine (research-only).

Discover statistically significant combinations of existing S56–S59 features.
S62.3.1 adds consolidation: canonical dedupe, root-cause grouping, robust
sections, and data-quality — report quality only; metrics unchanged.
S63.2 adaptive min_trades per universe (filtering only; metrics unchanged).

No new indicators. No strategy changes. No AI. No production writes.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from bot.research.market_events.signal_intelligence.drift_analyzer_s622 import (
    _enrich_from_snapshot_json,
    resolve_as_of,
)
from bot.research.market_events.signal_intelligence.feature_lab_s59 import load_lab_trades
from bot.research.market_events.signal_intelligence.trading_intelligence_report_s621 import (
    AI_BUCKETS,
    FUNDING_BUCKETS,
    PERIODS,
    RSI_BUCKETS,
    WEEKDAYS,
    _enrich_decisions,
    _regime_family,
    _safe_float,
    _trade_ts,
    default_report_dir,
    filter_by_period,
    overall_performance,
)

logger = logging.getLogger(__name__)

DEFAULT_MIN_TRADES = 50
DEFAULT_LAST_TRADES = (100, 500, 1000)
TOP_PATTERNS = 100
TOP_CANDIDATES = 20


def adaptive_min_trades(universe: str, n_trades: int, *, lifetime_min: int = DEFAULT_MIN_TRADES) -> int:
    """S63.2 — universe-specific min_trades (filtering only; metrics unchanged).

    lifetime → lifetime_min (default 50)
    24h → 30
    3h → max(8, trades * 0.3)
    1h → max(5, trades * 0.3)
    last_100 → 10
    last_500 → 20
    last_1000 → 30
    """
    n = max(0, int(n_trades))
    u = str(universe or "").strip().lower()
    if u == "lifetime":
        return max(1, int(lifetime_min))
    if u == "24h":
        return 30
    if u == "3h":
        return max(8, int(n * 0.3))
    if u == "1h":
        return max(5, int(n * 0.3))
    if u == "last_100":
        return 10
    if u == "last_500":
        return 20
    if u == "last_1000":
        return 30
    # Unknown universe: conservative floor relative to sample size
    return max(5, min(int(lifetime_min), max(1, int(n * 0.3))))


# Fixed combo templates (existing fields only).
COMBOS_2D: tuple[tuple[str, ...], ...] = (
    ("coin", "direction"),
    ("coin", "regime"),
    ("coin", "hour"),
    ("direction", "regime"),
    ("direction", "funding"),
    ("direction", "rsi"),
    ("hour", "regime"),
    ("hour", "coin"),
    ("weekday", "coin"),
    ("strategy", "coin"),
    ("strategy", "direction"),
)

COMBOS_3D: tuple[tuple[str, ...], ...] = (
    ("coin", "direction", "regime"),
    ("coin", "direction", "hour"),
    ("coin", "funding", "direction"),
    ("regime", "hour", "direction"),
    ("rsi", "funding", "direction"),
    ("ai", "direction", "coin"),
    ("strategy", "coin", "direction"),
)

DIM_LABEL = {
    "coin": "Coin",
    "direction": "Direction",
    "regime": "Regime",
    "hour": "Hour",
    "weekday": "Weekday",
    "strategy": "Strategy",
    "funding": "Funding",
    "rsi": "RSI",
    "ai": "AI Score",
}

# Canonical dimension order for labels / merge keys (report quality only).
DIM_ORDER = (
    "coin",
    "direction",
    "strategy",
    "regime",
    "weekday",
    "hour",
    "funding",
    "rsi",
    "ai",
)
_DIM_RANK = {d: i for i, d in enumerate(DIM_ORDER)}

# Uninformative values — deprioritized when choosing a representative label.
_LOW_INFO_VALUES = frozenset({"Unknown", "unknown", "—", "-", "Other", "other", "n/a", "N/A"})



def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "bot" / "research" / "market_events" / "__main__.py").exists():
            return p
    return Path.cwd()


def _bucket_name(value: float | None, specs: tuple) -> str | None:
    if value is None:
        return None
    for name, pred in specs:
        try:
            if pred(value):
                return name
        except Exception:
            continue
    return None


def _ai_scaled(r: dict[str, Any]) -> float | None:
    v = _safe_float(r.get("ai_score"))
    if v is None:
        return None
    if 0 <= v <= 1.0:
        return v * 100.0
    return v


def tag_trade(r: dict[str, Any]) -> dict[str, Any] | None:
    """Project a research trade onto categorical discovery dims + metric fields."""
    coin = str(r.get("symbol") or "").upper().replace("USDT", "").strip()
    direction = str(r.get("direction") or "").upper().strip()
    if not coin or direction not in ("LONG", "SHORT"):
        return None
    hour_v = r.get("hour")
    weekday_v = r.get("weekday")
    try:
        hour = f"H{int(hour_v):02d}" if hour_v is not None and 0 <= int(hour_v) <= 23 else None
    except (TypeError, ValueError):
        hour = None
    try:
        weekday = WEEKDAYS[int(weekday_v)] if weekday_v is not None and 0 <= int(weekday_v) <= 6 else None
    except (TypeError, ValueError):
        weekday = None

    hold = _safe_float(r.get("duration_sec"))
    if hold is None:
        hold = _safe_float(r.get("holding_seconds"))

    return {
        "coin": coin,
        "direction": direction,
        "regime": _regime_family(r.get("market_regime")),
        "hour": hour,
        "weekday": weekday,
        "strategy": str(r.get("s40_signal_type") or r.get("strategy_label") or "unknown"),
        "funding": _bucket_name(_safe_float(r.get("funding")), FUNDING_BUCKETS),
        "rsi": _bucket_name(_safe_float(r.get("rsi")), RSI_BUCKETS),
        "ai": _bucket_name(_ai_scaled(r), AI_BUCKETS),
        "pnl": float(r.get("pnl_usd") or 0.0),
        "ts": _trade_ts(r),
        "hold": float(hold) if hold is not None and hold >= 0 else None,
        # keep original for overall_performance reuse when needed
        "_row": r,
    }


def pattern_metrics_fast(tagged: list[dict[str, Any]]) -> dict[str, Any]:
    """Metrics without re-sorting original rows more than once."""
    n = len(tagged)
    if n == 0:
        return {
            "trades": 0,
            "pnl": 0.0,
            "profit_factor": None,
            "pf_inf": False,
            "winrate": None,
            "expectancy": None,
            "sharpe": None,
            "avg_hold_sec": None,
            "max_drawdown": None,
        }
    pnls = [t["pnl"] for t in tagged]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 1e-12:
        pf: float | None = round(gross_win / gross_loss, 4)
        pf_inf = False
    elif gross_win > 0:
        pf = None
        pf_inf = True
    else:
        pf = 0.0
        pf_inf = False

    # Sharpe
    sharpe = None
    if n >= 2:
        mean = sum(pnls) / n
        var = sum((x - mean) ** 2 for x in pnls) / n
        if var > 1e-18:
            sharpe = round(mean / math.sqrt(var), 4)
        else:
            sharpe = 0.0 if abs(mean) < 1e-12 else None

    holds = [t["hold"] for t in tagged if t.get("hold") is not None]
    ordered = sorted(tagged, key=lambda t: t["ts"])
    equity = peak = max_dd = 0.0
    for t in ordered:
        equity += t["pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return {
        "trades": n,
        "pnl": round(sum(pnls), 4),
        "profit_factor": pf,
        "pf_inf": pf_inf,
        "winrate": round(100.0 * len(wins) / n, 2),
        "expectancy": round(sum(pnls) / n, 4),
        "sharpe": sharpe,
        "avg_hold_sec": round(sum(holds) / len(holds), 1) if holds else None,
        "max_drawdown": round(max_dd, 4),
    }


def _pf_sort_key(m: dict[str, Any]) -> tuple:
    """Higher PF first; inf PF ranks highest; then expectancy."""
    if m.get("pf_inf"):
        pf_rank = 1e9
    elif m.get("profit_factor") is None:
        pf_rank = -1.0
    else:
        pf_rank = float(m["profit_factor"])
    exp = float(m.get("expectancy") or -1e9)
    return (-pf_rank, -exp)


def _pf_num(m: dict[str, Any] | None) -> float | None:
    if not m:
        return None
    if m.get("pf_inf"):
        return 99.0
    v = m.get("profit_factor")
    return float(v) if v is not None else None


def _delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return round(float(a) - float(b), 6)


def pattern_key(dims: tuple[str, ...], values: tuple[str, ...]) -> str:
    return "|".join(f"{d}={v}" for d, v in zip(dims, values))


def pattern_label(dims: tuple[str, ...], values: tuple[str, ...]) -> str:
    return " + ".join(f"{DIM_LABEL.get(d, d)}={v}" for d, v in zip(dims, values))


def canonical_pairs(dims: list[str] | tuple[str, ...], values: list[str] | tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """Order-invariant (dim, value) pairs sorted by canonical dim order."""
    pairs = list(zip([str(d) for d in dims], [str(v) for v in values]))
    pairs.sort(key=lambda pv: (_DIM_RANK.get(pv[0], 99), pv[0], pv[1]))
    return tuple(pairs)


def canonical_key_from_pairs(pairs: tuple[tuple[str, str], ...]) -> str:
    return "|".join(f"{d}={v}" for d, v in pairs)


def canonical_label_from_pairs(pairs: tuple[tuple[str, str], ...]) -> str:
    return " + ".join(f"{DIM_LABEL.get(d, d)}={v}" for d, v in pairs)


def _informativeness(pairs: tuple[tuple[str, str], ...]) -> tuple:
    """Higher = prefer as the display representative (no metric change)."""
    n_unknown = sum(1 for _, v in pairs if v in _LOW_INFO_VALUES)
    # Prefer fewer Unknowns, then canonical dim order already in pairs, then shorter keys
    return (-n_unknown, -len(pairs), canonical_label_from_pairs(pairs))


def consolidate_patterns(patterns: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge equivalent permutations; keep most informative label. Metrics untouched."""
    groups: dict[tuple[tuple[str, str], ...], list[dict[str, Any]]] = defaultdict(list)
    for p in patterns:
        dims = p.get("dims") or []
        values = p.get("values") or []
        if len(dims) != len(values):
            continue
        pairs = canonical_pairs(dims, values)
        groups[pairs].append(p)

    unique: list[dict[str, Any]] = []
    duplicates_removed = 0
    for pairs, members in groups.items():
        duplicates_removed += max(0, len(members) - 1)
        best = max(
            members,
            key=lambda m: (
                _informativeness(canonical_pairs(m.get("dims") or [], m.get("values") or [])),
                1 if tuple(m.get("dims") or []) == tuple(d for d, _ in pairs) else 0,
                int((m.get("metrics") or {}).get("trades") or 0),
            ),
        )
        card = dict(best)
        card["dims"] = [d for d, _ in pairs]
        card["values"] = [v for _, v in pairs]
        card["key"] = canonical_key_from_pairs(pairs)
        card["label"] = canonical_label_from_pairs(pairs)
        card["canonical_key"] = card["key"]
        card["merged_from"] = sorted({str(m.get("label") or m.get("key") or "") for m in members})
        card["n_duplicates_merged"] = len(members) - 1
        # Prefer filled by_period / deltas from any member (same stats for permutations)
        if any(m.get("by_period") for m in members):
            merged_bp: dict[str, Any] = {}
            for m in members:
                for pk, pv in (m.get("by_period") or {}).items():
                    cur = merged_bp.get(pk)
                    if cur is None or int((pv or {}).get("trades") or 0) >= int((cur or {}).get("trades") or 0):
                        merged_bp[pk] = pv
            card["by_period"] = merged_bp
        if any(m.get("period_deltas") for m in members):
            # keep from best (metrics identical)
            card["period_deltas"] = best.get("period_deltas") or next(
                (m.get("period_deltas") for m in members if m.get("period_deltas")),
                None,
            )
        if any(m.get("stability") is not None for m in members):
            card["stability"] = best.get("stability")
            if card["stability"] is None:
                for m in members:
                    if m.get("stability") is not None:
                        card["stability"] = m.get("stability")
                        break
        unique.append(card)

    return {
        "patterns": unique,
        "duplicates_removed": duplicates_removed,
        "unique_retained": len(unique),
        "raw_count": len(patterns),
    }


def group_by_root_cause(
    patterns: list[dict[str, Any]],
    *,
    top_n: int = 40,
) -> list[dict[str, Any]]:
    """Group unique patterns by shared single-dimension atoms (report structure only)."""
    by_atom: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for p in patterns:
        dims = p.get("dims") or []
        values = p.get("values") or []
        for d, v in zip(dims, values):
            if str(v) in _LOW_INFO_VALUES:
                continue
            by_atom[(str(d), str(v))].append(p)

    roots: list[dict[str, Any]] = []
    for (dim, value), members in by_atom.items():
        # Need multiple supporting patterns to call it a root cause cluster
        if len(members) < 2:
            continue
        evidence: dict[str, set[str]] = defaultdict(set)
        for m in members:
            for d, v in zip(m.get("dims") or [], m.get("values") or []):
                if d == dim and str(v) == value:
                    continue
                if str(v) in _LOW_INFO_VALUES:
                    continue
                evidence[str(d)].add(str(v))
        # Metrics summary: take best-PF member as exemplar (metrics already computed)
        exemplar = max(members, key=lambda m: _pf_sort_key(m.get("metrics") or {}))
        em = exemplar.get("metrics") or {}
        roots.append({
            "root_cause": f"{DIM_LABEL.get(dim, dim)}={value}",
            "dim": dim,
            "value": value,
            "n_patterns": len(members),
            "supporting_evidence": {
                DIM_LABEL.get(d, d): sorted(vs) for d, vs in sorted(evidence.items())
            },
            "exemplar_label": exemplar.get("label"),
            "trades": em.get("trades"),
            "pnl": em.get("pnl"),
            "profit_factor": em.get("profit_factor"),
            "pf_inf": em.get("pf_inf"),
            "expectancy": em.get("expectancy"),
            "winrate": em.get("winrate"),
            "stability": exemplar.get("stability"),
            "pattern_labels": [m.get("label") for m in members[:12]],
        })

    roots.sort(
        key=lambda r: (
            -int(r.get("n_patterns") or 0),
            -99.0 if r.get("pf_inf") else -(float(r["profit_factor"]) if r.get("profit_factor") is not None else -1.0),
        ),
    )
    return roots[:top_n]


def most_robust_patterns(patterns: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Report sections over consolidated unique patterns (no recalculation)."""
    def _card(p: dict[str, Any]) -> dict[str, Any]:
        m = p.get("metrics") or {}
        return {
            "label": p.get("label"),
            "key": p.get("key"),
            "dims": p.get("dims"),
            "values": p.get("values"),
            "metrics": m,
            "stability": p.get("stability"),
        }

    high_stab = sorted(
        [p for p in patterns if float(p.get("stability") or 0) > 70],
        key=lambda p: -float(p.get("stability") or 0),
    )[:20]
    high_n = sorted(
        [p for p in patterns if int((p.get("metrics") or {}).get("trades") or 0) > 500],
        key=lambda p: -int((p.get("metrics") or {}).get("trades") or 0),
    )[:20]
    high_pf = sorted(patterns, key=lambda p: _pf_sort_key(p.get("metrics") or {}))[:20]
    low_dd = sorted(
        [
            p for p in patterns
            if (p.get("metrics") or {}).get("max_drawdown") is not None
        ],
        key=lambda p: (
            float((p.get("metrics") or {}).get("max_drawdown") or 1e18),
            -float((p.get("metrics") or {}).get("trades") or 0),
        ),
    )[:20]
    return {
        "highest_stability": [_card(p) for p in high_stab],
        "highest_trade_count": [_card(p) for p in high_n],
        "highest_pf": [_card(p) for p in high_pf],
        "lowest_drawdown": [_card(p) for p in low_dd],
    }


def data_quality_stats(
    *,
    rows: list[dict[str, Any]],
    tagged: list[dict[str, Any]],
    consolidation: dict[str, Any],
) -> dict[str, Any]:
    n_rows = len(rows)
    n_tagged = len(tagged)
    n_skipped = max(0, n_rows - n_tagged)

    def _pct(num: int, den: int) -> float:
        if den <= 0:
            return 0.0
        return round(100.0 * num / den, 2)

    # Missingness among loaded research rows (original fields)
    unknown_regime = 0
    missing_ai = 0
    missing_funding = 0
    missing_rsi = 0
    for r in rows:
        reg = r.get("market_regime")
        if not reg or str(reg).strip() in ("", "Unknown", "unknown"):
            # also count tagged Unknown family
            unknown_regime += 1
        if r.get("ai_score") is None:
            missing_ai += 1
        if r.get("funding") is None:
            missing_funding += 1
        if r.get("rsi") is None:
            missing_rsi += 1

    return {
        "rows_analysed": n_rows,
        "rows_tagged": n_tagged,
        "rows_skipped": n_skipped,
        "unknown_regime_pct": _pct(unknown_regime, n_rows),
        "missing_ai_score_pct": _pct(missing_ai, n_rows),
        "missing_funding_pct": _pct(missing_funding, n_rows),
        "missing_rsi_pct": _pct(missing_rsi, n_rows),
        "duplicate_patterns_removed": int(consolidation.get("duplicates_removed") or 0),
        "unique_patterns_retained": int(consolidation.get("unique_retained") or 0),
        "raw_patterns_before_consolidation": int(consolidation.get("raw_count") or 0),
    }


def _dedupe_ranked_list(cards: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    """Apply canonical merge to a ranked list; preserve relative order of first occurrence."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for c in cards:
        pairs = canonical_pairs(c.get("dims") or [], c.get("values") or [])
        ck = canonical_key_from_pairs(pairs)
        if ck in seen:
            continue
        seen.add(ck)
        card = dict(c)
        card["dims"] = [d for d, _ in pairs]
        card["values"] = [v for _, v in pairs]
        card["key"] = ck
        card["label"] = canonical_label_from_pairs(pairs)
        card["canonical_key"] = ck
        out.append(card)
        if len(out) >= limit:
            break
    return out


def discover_combos(
    tagged: list[dict[str, Any]],
    *,
    min_trades: int,
    combos: Iterable[tuple[str, ...]] | None = None,
) -> list[dict[str, Any]]:
    """Group tagged trades by each combo template; keep n >= min_trades."""
    templates = tuple(combos) if combos is not None else COMBOS_2D + COMBOS_3D
    out: list[dict[str, Any]] = []
    for dims in templates:
        buckets: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for t in tagged:
            vals = []
            skip = False
            for d in dims:
                v = t.get(d)
                if v is None or v == "":
                    skip = True
                    break
                vals.append(str(v))
            if skip:
                continue
            buckets[tuple(vals)].append(t)
        for values, members in buckets.items():
            if len(members) < min_trades:
                continue
            metrics = pattern_metrics_fast(members)
            out.append({
                "dims": list(dims),
                "values": list(values),
                "key": pattern_key(dims, values),
                "label": pattern_label(dims, values),
                "n_dims": len(dims),
                "metrics": metrics,
                "_members": members,  # stripped before export
            })
    return out


def last_n_trades(tagged: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    if n <= 0:
        return []
    return sorted(tagged, key=lambda t: t["ts"], reverse=True)[:n]


def stability_score(by_period: dict[str, dict[str, Any]]) -> float:
    """0–100 pure statistics: n, PF consistency, expectancy consistency, DD stability."""
    life = by_period.get("lifetime") or {}
    n = int(life.get("trades") or 0)
    # Trade count (0..40)
    if n <= 0:
        n_score = 0.0
    else:
        n_score = min(40.0, 12.0 * math.log10(max(n, 1)))

    period_order = [p for p in PERIODS if int((by_period.get(p) or {}).get("trades") or 0) >= 10]
    pfs = [_pf_num(by_period[p]) for p in period_order]
    pfs = [p for p in pfs if p is not None]
    exps = [
        _safe_float((by_period[p] or {}).get("expectancy"))
        for p in period_order
    ]
    exps = [e for e in exps if e is not None]
    dds = [
        _safe_float((by_period[p] or {}).get("max_drawdown"))
        for p in period_order
    ]
    dds = [d for d in dds if d is not None]

    def _consistency(vals: list[float], cap: float) -> float:
        if len(vals) < 2:
            return cap * 0.5
        mean = sum(vals) / len(vals)
        if abs(mean) < 1e-12:
            cv = 1.0 if any(abs(v) > 1e-12 for v in vals) else 0.0
        else:
            std = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
            cv = min(2.0, abs(std / mean))
        return cap * max(0.0, 1.0 - cv / 2.0)

    pf_score = _consistency(pfs, 30.0)
    exp_score = _consistency(exps, 20.0)

    # DD stability: prefer lower relative variance of DD
    if len(dds) < 2:
        dd_score = 5.0
    else:
        mean_dd = sum(dds) / len(dds)
        std_dd = math.sqrt(sum((d - mean_dd) ** 2 for d in dds) / len(dds))
        if mean_dd < 1e-12:
            dd_score = 10.0 if std_dd < 1e-12 else 2.0
        else:
            dd_score = 10.0 * max(0.0, 1.0 - min(1.0, std_dd / mean_dd))

    return round(max(0.0, min(100.0, n_score + pf_score + exp_score + dd_score)), 1)


def candidate_confidence(
    *,
    metrics: dict[str, Any],
    stability: float,
    mode: str,
) -> float:
    """0–100 confidence for enable/disable suggestion (stats only)."""
    n = int(metrics.get("trades") or 0)
    n_part = min(35.0, 10.0 * math.log10(max(n, 1)))
    stab_part = float(stability) * 0.35
    pf = _pf_num(metrics)
    exp = _safe_float(metrics.get("expectancy")) or 0.0
    if mode == "enable":
        edge = 0.0
        if pf is not None:
            edge += min(20.0, max(0.0, (pf - 1.0) * 10.0))
        edge += min(15.0, max(0.0, exp * 20.0))
    else:
        edge = 0.0
        if pf is not None:
            edge += min(20.0, max(0.0, (1.0 - pf) * 12.0))
        edge += min(15.0, max(0.0, -exp * 20.0))
    return round(max(0.0, min(100.0, n_part + stab_part + edge)), 1)


def _period_deltas(by_period: dict[str, dict[str, Any]]) -> dict[str, Any]:
    life = by_period.get("lifetime") or {}
    out: dict[str, Any] = {}
    for p in ("24h", "3h", "1h"):
        cur = by_period.get(p) or {}
        out[p] = {
            "delta_profit_factor": _delta(_pf_num(cur), _pf_num(life)),
            "delta_expectancy": _delta(
                _safe_float(cur.get("expectancy")),
                _safe_float(life.get("expectancy")),
            ),
            "delta_winrate": _delta(
                _safe_float(cur.get("winrate")),
                _safe_float(life.get("winrate")),
            ),
            "delta_pnl": _delta(_safe_float(cur.get("pnl")), _safe_float(life.get("pnl"))),
            "trades": cur.get("trades"),
            "profit_factor": cur.get("profit_factor"),
            "pf_inf": cur.get("pf_inf"),
            "expectancy": cur.get("expectancy"),
            "winrate": cur.get("winrate"),
            "pnl": cur.get("pnl"),
        }
    # Biggest recent deterioration score: lifetime → 24h PF/E drop
    d24 = out.get("24h") or {}
    det = 0.0
    if d24.get("delta_profit_factor") is not None and d24["delta_profit_factor"] < 0:
        det += abs(float(d24["delta_profit_factor"]))
    if d24.get("delta_expectancy") is not None and d24["delta_expectancy"] < 0:
        det += abs(float(d24["delta_expectancy"])) * 2.0
    out["deterioration_score"] = round(det, 6)
    return out


def _strip_members(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned = []
    for p in patterns:
        d = {k: v for k, v in p.items() if k != "_members"}
        cleaned.append(d)
    return cleaned


def run_pattern_discovery(
    conn: Any,
    *,
    periods: list[str] | None = None,
    last_trades: list[int] | None = None,
    min_trades: int = DEFAULT_MIN_TRADES,
    now: int | None = None,
    report_dir: Path | None = None,
    top_n: int = TOP_PATTERNS,
    top_candidates: int = TOP_CANDIDATES,
) -> dict[str, Any]:
    t0 = time.time()
    rows = load_lab_trades(conn)
    _enrich_decisions(conn, rows)
    _enrich_from_snapshot_json(rows)

    tagged_all = [t for t in (tag_trade(r) for r in rows) if t is not None]
    as_of_info = resolve_as_of(rows, wall_now=now)
    as_of = int(as_of_info["as_of"])

    # Default = all reports
    selected_periods = list(periods) if periods else list(PERIODS)
    selected_last = list(last_trades) if last_trades is not None else list(DEFAULT_LAST_TRADES)
    # Always need lifetime members for cross-period pattern tracking
    need_periods = list(dict.fromkeys(["lifetime", *selected_periods, "24h", "3h", "1h"]))

    period_tagged: dict[str, list[dict[str, Any]]] = {}
    for p in need_periods:
        subset = filter_by_period(rows, p, now=as_of)
        # map via object identity of underlying rows is hard; re-tag filtered rows
        period_tagged[p] = [t for t in (tag_trade(r) for r in subset) if t is not None]

    # Index lifetime patterns by key for period recomputation
    life_n = len(period_tagged.get("lifetime") or tagged_all)
    life_min = adaptive_min_trades("lifetime", life_n, lifetime_min=min_trades)
    life_patterns = discover_combos(
        period_tagged.get("lifetime") or tagged_all,
        min_trades=life_min,
    )
    # Build value→metrics for each period without re-discovering keys from short windows only
    # Re-group each period for same templates (fast) then join by key
    period_metrics_by_key: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for p, tagged in period_tagged.items():
        # Use min_trades=1 for period fill so we can show deterioration even if thin
        found = discover_combos(tagged, min_trades=1)
        for pat in found:
            period_metrics_by_key[pat["key"]][p] = pat["metrics"]

    enriched: list[dict[str, Any]] = []
    for pat in life_patterns:
        key = pat["key"]
        by_period = {
            p: period_metrics_by_key.get(key, {}).get(p)
            or {"trades": 0, "pnl": 0.0, "profit_factor": None, "pf_inf": False,
                "winrate": None, "expectancy": None, "sharpe": None,
                "avg_hold_sec": None, "max_drawdown": None}
            for p in PERIODS
        }
        # Ensure lifetime metrics match discovery
        by_period["lifetime"] = pat["metrics"]
        deltas = _period_deltas(by_period)
        stab = stability_score(by_period)
        card = {
            "dims": pat["dims"],
            "values": pat["values"],
            "key": key,
            "label": pat["label"],
            "n_dims": pat["n_dims"],
            "metrics": pat["metrics"],
            "by_period": by_period,
            "period_deltas": deltas,
            "stability": stab,
            "deterioration_score": deltas.get("deterioration_score"),
        }
        enriched.append(card)

    stab_by_canon: dict[str, float] = {}
    for e in enriched:
        ck = canonical_key_from_pairs(canonical_pairs(e.get("dims") or [], e.get("values") or []))
        if e.get("stability") is not None:
            stab_by_canon[ck] = float(e["stability"])

    # Universe rankings (selected) — S63.2 adaptive min_trades per universe
    universes: dict[str, Any] = {}
    thresholds_used: dict[str, int] = {}

    def _rank_universe(name: str, tagged: list[dict[str, Any]]) -> dict[str, Any]:
        n_trades = len(tagged)
        thresh = adaptive_min_trades(name, n_trades, lifetime_min=min_trades)
        thresholds_used[name] = thresh
        found = discover_combos(tagged, min_trades=thresh)
        cards = []
        for pat in found:
            ck = canonical_key_from_pairs(
                canonical_pairs(pat.get("dims") or [], pat.get("values") or []),
            )
            stab = stab_by_canon.get(ck)
            if stab is None:
                # thin local stability from this universe only
                stab = stability_score({name if name in PERIODS else "lifetime": pat["metrics"]})
            cards.append({
                "key": pat["key"],
                "label": pat["label"],
                "dims": pat["dims"],
                "values": pat["values"],
                "n_dims": pat["n_dims"],
                "metrics": pat["metrics"],
                "stability": stab,
            })
        best = sorted(cards, key=lambda c: _pf_sort_key(c["metrics"]))[: top_n * 2]
        worst = sorted(
            cards,
            key=lambda c: (
                1e9 if c["metrics"].get("pf_inf") else (
                    float(c["metrics"]["profit_factor"])
                    if c["metrics"].get("profit_factor") is not None else 1e9
                ),
                float(c["metrics"].get("expectancy") or 1e9),
            ),
        )[: top_n * 2]
        uniq = consolidate_patterns(cards)
        return {
            "n_trades": n_trades,
            "min_trades_threshold": thresh,
            "adaptive_threshold": thresh,
            "n_patterns": len(cards),
            "n_patterns_unique": uniq["unique_retained"],
            "duplicates_removed": uniq["duplicates_removed"],
            "top_best": _dedupe_ranked_list(best, limit=top_n),
            "top_worst": _dedupe_ranked_list(worst, limit=top_n),
        }

    for p in selected_periods:
        universes[p] = _rank_universe(p, period_tagged.get(p) or [])

    for n in selected_last:
        universes[f"last_{n}"] = _rank_universe(f"last_{n}", last_n_trades(tagged_all, n))

    # Biggest recent deterioration (lifetime patterns)
    deterioration = sorted(
        [e for e in enriched if float(e.get("deterioration_score") or 0) > 0],
        key=lambda e: -float(e.get("deterioration_score") or 0),
    )[: top_n * 2]
    deterioration = _dedupe_ranked_list(deterioration, limit=top_n)

    # --- S62.3.1 consolidation (report quality only; metrics unchanged) ---
    consolidation = consolidate_patterns(enriched)
    unique_patterns = consolidation["patterns"]
    root_causes = group_by_root_cause(unique_patterns)
    robust = most_robust_patterns(unique_patterns)
    quality = data_quality_stats(
        rows=rows,
        tagged=tagged_all,
        consolidation=consolidation,
    )

    # Candidates from consolidated lifetime best/worst
    life_rank = universes.get("lifetime") or {"top_best": [], "top_worst": []}
    enables = []
    for c in life_rank.get("top_best") or []:
        m = c["metrics"]
        pf = _pf_num(m)
        if pf is not None and pf < 1.1 and (m.get("expectancy") or 0) <= 0:
            continue
        stab = float(c.get("stability") or 0)
        conf = candidate_confidence(metrics=m, stability=stab, mode="enable")
        enables.append({
            "action": "Enable",
            "label": c["label"],
            "key": c["key"],
            "values": c["values"],
            "dims": c["dims"],
            "confidence": conf,
            "confidence_pct": f"{conf:.0f}%",
            "metrics": m,
            "stability": stab,
        })
        if len(enables) >= top_candidates:
            break

    disables = []
    for c in life_rank.get("top_worst") or []:
        m = c["metrics"]
        pf = _pf_num(m)
        if pf is not None and pf > 0.95 and (m.get("expectancy") or 0) >= 0:
            continue
        stab = float(c.get("stability") or 0)
        conf = candidate_confidence(metrics=m, stability=stab, mode="disable")
        disables.append({
            "action": "Disable",
            "label": c["label"],
            "key": c["key"],
            "values": c["values"],
            "dims": c["dims"],
            "confidence": conf,
            "confidence_pct": f"{conf:.0f}%",
            "metrics": m,
            "stability": stab,
        })
        if len(disables) >= top_candidates:
            break

    elapsed = round(time.time() - t0, 3)
    report = {
        "ok": True,
        "stage": "S63.2",
        "as_of": as_of,
        "as_of_mode": as_of_info["as_of_mode"],
        "n_trades_loaded": len(rows),
        "n_trades_tagged": len(tagged_all),
        "min_trades": min_trades,
        "min_trades_lifetime": life_min,
        "adaptive_thresholds": thresholds_used,
        "selected_periods": selected_periods,
        "selected_last_trades": selected_last,
        "combos_2d": [list(c) for c in COMBOS_2D],
        "combos_3d": [list(c) for c in COMBOS_3D],
        "universes": universes,
        "patterns": _strip_members(enriched),
        "patterns_unique": _strip_members(unique_patterns),
        "consolidation": {
            "duplicates_removed": consolidation["duplicates_removed"],
            "unique_retained": consolidation["unique_retained"],
            "raw_count": consolidation["raw_count"],
        },
        "root_causes": root_causes,
        "most_robust": robust,
        "data_quality": quality,
        "biggest_deterioration": _strip_members(deterioration),
        "candidate_enables": enables,
        "candidate_disables": disables,
        "elapsed_sec": elapsed,
        "overall_lifetime": overall_performance(rows),
    }

    out_dir = Path(report_dir) if report_dir else default_report_dir(_repo_root())
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "patterns.md"
    json_path = out_dir / "patterns.json"
    cand_path = out_dir / "pattern_candidates.md"
    md_path.write_text(format_patterns_markdown(report), encoding="utf-8")
    # JSON without huge duplication: keep patterns slim (no full by_period for all if huge?)
    # Keep full — needed as AI input; 1786 trades → few hundred patterns is fine.
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    cand_path.write_text(format_candidates_markdown(report), encoding="utf-8")
    report["export_paths"] = {
        "markdown": str(md_path.resolve()),
        "json": str(json_path.resolve()),
        "candidates": str(cand_path.resolve()),
    }
    return report


def _fmt(v: Any, *, digits: int = 4) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f) >= 100:
        return f"{f:.2f}"
    return f"{f:.{digits}g}"


def format_patterns_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Pattern Discovery (S63.2)",
        "",
        f"_trades={report.get('n_trades_tagged')} lifetime_min={report.get('min_trades_lifetime')} "
        f"as_of_mode={report.get('as_of_mode')} elapsed={report.get('elapsed_sec')}s_",
        "",
        "Statistics only. No new indicators. No strategy changes. No AI.",
        "Consolidation merges equivalent permutations; metrics are unchanged.",
        "S63.2 adaptive min_trades per universe (filtering only).",
        "",
    ]

    thresh = report.get("adaptive_thresholds") or {}
    if thresh:
        lines.extend([
            "## Adaptive thresholds",
            "",
        ])
        for name, t in thresh.items():
            u = (report.get("universes") or {}).get(name) or {}
            lines.append(
                f"- **{name}**: trades={u.get('n_trades', '—')} "
                f"adaptive_threshold=**{t}** patterns={u.get('n_patterns', '—')}"
            )
        lines.append("")

    dq = report.get("data_quality") or {}
    lines.extend([
        "## Data Quality",
        "",
        f"- Rows analysed: **{dq.get('rows_analysed')}**",
        f"- Rows skipped: **{dq.get('rows_skipped')}**",
        f"- Unknown regime %: **{dq.get('unknown_regime_pct')}**",
        f"- Missing AI score %: **{dq.get('missing_ai_score_pct')}**",
        f"- Missing Funding %: **{dq.get('missing_funding_pct')}**",
        f"- Missing RSI %: **{dq.get('missing_rsi_pct')}**",
        f"- Duplicate patterns removed: **{dq.get('duplicate_patterns_removed')}**",
        f"- Unique patterns retained: **{dq.get('unique_patterns_retained')}**",
        "",
    ])

    lines.extend(["## Most Robust Patterns", ""])
    robust = report.get("most_robust") or {}

    def _robust_table(title: str, cards: list[dict[str, Any]]) -> None:
        lines.extend([
            f"### {title}",
            "",
            "| # | Pattern | Trades | PF | E | MaxDD | Stability |",
            "|---:|---|---:|---:|---:|---:|---:|",
        ])
        if not cards:
            lines.append("| — | _none_ | | | | | |")
        for i, c in enumerate(cards, 1):
            m = c.get("metrics") or {}
            pf = "∞" if m.get("pf_inf") else _fmt(m.get("profit_factor"))
            lines.append(
                f"| {i} | {c.get('label')} | {m.get('trades')} | {pf} | "
                f"{_fmt(m.get('expectancy'))} | {_fmt(m.get('max_drawdown'))} | "
                f"{_fmt(c.get('stability'))} |"
            )
        lines.append("")

    _robust_table("Highest Stability (>70)", robust.get("highest_stability") or [])
    _robust_table("Highest Trade Count (>500)", robust.get("highest_trade_count") or [])
    _robust_table("Highest PF", robust.get("highest_pf") or [])
    _robust_table("Lowest Drawdown", robust.get("lowest_drawdown") or [])

    lines.extend(["## Root Causes", ""])
    roots = report.get("root_causes") or []
    if not roots:
        lines.append("_No multi-pattern root causes._\n")
    for i, r in enumerate(roots[:25], 1):
        ev = r.get("supporting_evidence") or {}
        ev_bits = []
        for k, vs in ev.items():
            ev_bits.append(f"{k}={','.join(vs[:6])}")
        pf = "∞" if r.get("pf_inf") else _fmt(r.get("profit_factor"))
        lines.extend([
            f"### {i}. Root cause: **{r.get('root_cause')}**",
            "",
            f"- Supporting patterns: {r.get('n_patterns')}",
            f"- Supporting evidence: {'; '.join(ev_bits) if ev_bits else '—'}",
            f"- Exemplar: {r.get('exemplar_label')}",
            f"- Trades={r.get('trades')} PF={pf} E={_fmt(r.get('expectancy'))} "
            f"WR={_fmt(r.get('winrate'))} Stability={_fmt(r.get('stability'))}",
            "",
        ])

    for uname, u in (report.get("universes") or {}).items():
        lines.extend([
            f"## Universe: {uname} "
            f"(n={u.get('n_trades')}, adaptive_threshold={u.get('adaptive_threshold')}, "
            f"raw={u.get('n_patterns')}, unique={u.get('n_patterns_unique')})",
            "",
            "### TOP Best Patterns",
            "",
            "| # | Pattern | Trades | PnL | PF | WR | E | Sharpe | Hold | MaxDD | Stability |",
            "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for i, c in enumerate(u.get("top_best") or [], 1):
            m = c.get("metrics") or {}
            pf = "∞" if m.get("pf_inf") else _fmt(m.get("profit_factor"))
            lines.append(
                f"| {i} | {c.get('label')} | {m.get('trades')} | {_fmt(m.get('pnl'))} | {pf} | "
                f"{_fmt(m.get('winrate'))} | {_fmt(m.get('expectancy'))} | {_fmt(m.get('sharpe'))} | "
                f"{_fmt(m.get('avg_hold_sec'))} | {_fmt(m.get('max_drawdown'))} | {_fmt(c.get('stability'))} |"
            )
        lines.extend([
            "",
            "### TOP Worst Patterns",
            "",
            "| # | Pattern | Trades | PnL | PF | WR | E | Sharpe | Hold | MaxDD | Stability |",
            "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for i, c in enumerate(u.get("top_worst") or [], 1):
            m = c.get("metrics") or {}
            pf = "∞" if m.get("pf_inf") else _fmt(m.get("profit_factor"))
            lines.append(
                f"| {i} | {c.get('label')} | {m.get('trades')} | {_fmt(m.get('pnl'))} | {pf} | "
                f"{_fmt(m.get('winrate'))} | {_fmt(m.get('expectancy'))} | {_fmt(m.get('sharpe'))} | "
                f"{_fmt(m.get('avg_hold_sec'))} | {_fmt(m.get('max_drawdown'))} | {_fmt(c.get('stability'))} |"
            )
        lines.append("")

    lines.extend([
        "## Biggest Recent Deterioration (Lifetime → 24h)",
        "",
        "| # | Pattern | Life PF | 24h PF | ΔPF | ΔE | ΔWR | ΔPnL | Stability |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for i, e in enumerate(report.get("biggest_deterioration") or [], 1):
        life = (e.get("by_period") or {}).get("lifetime") or {}
        d24 = (e.get("period_deltas") or {}).get("24h") or {}
        lines.append(
            f"| {i} | {e.get('label')} | {_fmt(_pf_num(life))} | "
            f"{_fmt(d24.get('profit_factor') if not d24.get('pf_inf') else 99)} | "
            f"{_fmt(d24.get('delta_profit_factor'))} | {_fmt(d24.get('delta_expectancy'))} | "
            f"{_fmt(d24.get('delta_winrate'))} | {_fmt(d24.get('delta_pnl'))} | {_fmt(e.get('stability'))} |"
        )
    if not (report.get("biggest_deterioration") or []):
        lines.append("| — | _none_ | | | | | | | |")

    lines.extend(["", "---", "_Primary input for future AI: ranked statistical patterns only._", ""])
    return "\n".join(lines)


def format_candidates_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Pattern Candidates (S62.3.1)",
        "",
        "Report only — do **not** auto-apply. No strategy changes.",
        "Labels are consolidated (canonical dim order).",
        "",
        "## TOP 20 Candidate Disables",
        "",
    ]
    for i, c in enumerate(report.get("candidate_disables") or [], 1):
        vals = " / ".join(str(v) for v in (c.get("values") or []))
        m = c.get("metrics") or {}
        lines.extend([
            f"### {i}. Disable",
            "",
            f"- Pattern: **{c.get('label')}**",
            f"- Values: `{vals}`",
            f"- Confidence: **{c.get('confidence_pct')}**",
            f"- Trades={m.get('trades')} PF={_fmt(_pf_num(m))} E={_fmt(m.get('expectancy'))} "
            f"WR={_fmt(m.get('winrate'))} Stability={_fmt(c.get('stability'))}",
            "",
        ])
    if not (report.get("candidate_disables") or []):
        lines.append("_None._\n")

    lines.extend(["## TOP 20 Candidate Enables", ""])
    for i, c in enumerate(report.get("candidate_enables") or [], 1):
        vals = " / ".join(str(v) for v in (c.get("values") or []))
        m = c.get("metrics") or {}
        lines.extend([
            f"### {i}. Enable",
            "",
            f"- Pattern: **{c.get('label')}**",
            f"- Values: `{vals}`",
            f"- Confidence: **{c.get('confidence_pct')}**",
            f"- Trades={m.get('trades')} PF={_fmt(_pf_num(m))} E={_fmt(m.get('expectancy'))} "
            f"WR={_fmt(m.get('winrate'))} Stability={_fmt(c.get('stability'))}",
            "",
        ])
    if not (report.get("candidate_enables") or []):
        lines.append("_None._\n")

    lines.extend(["---", "_Statistics only. No indicator recommendations._", ""])
    return "\n".join(lines)


def format_discovery_summary(report: dict[str, Any]) -> str:
    dq = report.get("data_quality") or {}
    cons = report.get("consolidation") or {}
    lines = [
        "S63.2 Pattern Discovery (adaptive min_trades)",
        f"  trades={report.get('n_trades_tagged')} lifetime_min={report.get('min_trades_lifetime')} "
        f"elapsed={report.get('elapsed_sec')}s as_of_mode={report.get('as_of_mode')}",
        f"  consolidation: raw={cons.get('raw_count')} "
        f"unique={cons.get('unique_retained')} removed={cons.get('duplicates_removed')}",
        f"  data_quality: skipped={dq.get('rows_skipped')} "
        f"unknown_regime%={dq.get('unknown_regime_pct')} "
        f"missing_ai%={dq.get('missing_ai_score_pct')}",
    ]
    for name, u in (report.get("universes") or {}).items():
        best = (u.get("top_best") or [None])[0]
        worst = (u.get("top_worst") or [None])[0]
        lines.append(
            f"  {name}: trades={u.get('n_trades')} threshold={u.get('adaptive_threshold')} "
            f"patterns={u.get('n_patterns')} unique={u.get('n_patterns_unique')} "
            f"best={best.get('label') if best else '—'} "
            f"worst={worst.get('label') if worst else '—'}"
        )
    lines.append(
        f"  candidates: enable={len(report.get('candidate_enables') or [])} "
        f"disable={len(report.get('candidate_disables') or [])} "
        f"root_causes={len(report.get('root_causes') or [])}"
    )
    for k, p in (report.get("export_paths") or {}).items():
        lines.append(f"  {k}: {p}")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_MIN_TRADES",
    "adaptive_min_trades",
    "consolidate_patterns",
    "format_candidates_markdown",
    "format_discovery_summary",
    "format_patterns_markdown",
    "run_pattern_discovery",
]
