"""Validate rules on full corpus: metrics, CI, universality."""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable

import numpy as np

from bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.stats import (
    bootstrap_expectancy_ci,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import (
    trade_metrics,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.setups import DNARule

Predicate = Callable[[dict[str, Any]], bool]

MIN_N_READY = 500
MIN_N_BLOCK = 40
MAX_SINGLE_SYMBOL_SHARE = 0.55
MAX_SINGLE_HOUR_SHARE = 0.45
MAX_SINGLE_WEEKDAY_SHARE = 0.55


def _label_to_rule(atomics: list[DNARule]) -> dict[str, DNARule]:
    return {r.label: r for r in atomics}


def build_predicate(
    conditions: list[str],
    *,
    atomics: list[DNARule],
    raw_conditions: list[str] | None = None,
) -> Predicate | None:
    """AND of atomic DNA predicates for given labels."""
    by_label = _label_to_rule(atomics)
    # also index by canonical aliases used in parse
    from bot.research.market_events.signal_intelligence.trading_rules_v1.parse import (
        canonicalize,
    )

    canon_map: dict[str, DNARule] = {}
    for r in atomics:
        canon_map[canonicalize(r.label)] = r
        canon_map[r.label] = r

    preds: list[Predicate] = []
    src = raw_conditions or conditions
    for i, cond in enumerate(conditions):
        raw = src[i] if i < len(src) else cond
        rule = canon_map.get(cond) or canon_map.get(raw) or by_label.get(raw) or by_label.get(cond)
        if rule is None:
            return None
        preds.append(rule.pred)
    if not preds:
        return None

    def _and(row: dict[str, Any], ps: list[Predicate] = preds) -> bool:
        return all(p(row) for p in ps)

    return _and


def apply_mask(rows: list[dict[str, Any]], pred: Predicate) -> list[dict[str, Any]]:
    return [r for r in rows if pred(r)]


def universality(selected: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(selected)
    if n == 0:
        return {
            "ok": False,
            "n_symbols": 0,
            "n_hours": 0,
            "n_weekdays": 0,
            "top_symbol_share": 1.0,
            "top_hour_share": 1.0,
            "top_weekday_share": 1.0,
            "reason": "empty",
        }
    syms = Counter(str(r.get("symbol") or "") for r in selected)
    hours = Counter(r.get("hour") for r in selected if r.get("hour") is not None)
    wds = Counter(r.get("weekday") for r in selected if r.get("weekday") is not None)
    top_sym_share = (syms.most_common(1)[0][1] / n) if syms else 1.0
    top_hour_share = (hours.most_common(1)[0][1] / n) if hours else 0.0
    top_wd_share = (wds.most_common(1)[0][1] / n) if wds else 0.0
    reasons = []
    if len(syms) < 2:
        reasons.append("single_coin")
    if top_sym_share > MAX_SINGLE_SYMBOL_SHARE:
        reasons.append("coin_concentration")
    if hours and top_hour_share > MAX_SINGLE_HOUR_SHARE and len(hours) <= 2:
        reasons.append("hour_concentration")
    if wds and top_wd_share > MAX_SINGLE_WEEKDAY_SHARE and len(wds) <= 2:
        reasons.append("weekday_concentration")
    ok = len(reasons) == 0 and len(syms) >= 3
    return {
        "ok": ok,
        "n_symbols": len(syms),
        "n_hours": len(hours),
        "n_weekdays": len(wds),
        "top_symbol_share": round(top_sym_share, 4),
        "top_hour_share": round(top_hour_share, 4),
        "top_weekday_share": round(top_wd_share, 4),
        "reason": ",".join(reasons) if reasons else "ok",
    }


def validate_rule(
    rows: list[dict[str, Any]],
    conditions: list[str],
    *,
    atomics: list[DNARule],
    raw_conditions: list[str] | None = None,
    min_n: int = MIN_N_READY,
    kind: str = "ready",
) -> dict[str, Any] | None:
    pred = build_predicate(conditions, atomics=atomics, raw_conditions=raw_conditions)
    if pred is None:
        return None
    hit = apply_mask(rows, pred)
    pnls = [float(r["pnl"]) for r in hit]
    met = trade_metrics(pnls)
    if int(met.get("n") or 0) < min_n:
        return None
    if int(met.get("n_nonzero") or 0) < max(20, min_n // 10):
        return None
    lo, hi = bootstrap_expectancy_ci(pnls, n_boot=200, seed=7)
    uni = universality(hit)
    pf = met.get("pf")
    pf_v = 99.0 if (pf is None and met.get("pf_inf")) else float(pf or 0.0)
    passes_n = int(met["n"]) >= min_n
    ready_ok = (
        kind == "ready"
        and passes_n
        and uni["ok"]
        and float(met.get("ev") or 0) > 0
        and (lo is None or lo > 0)
        and pf_v >= 1.3
        and int(met.get("n_wins") or 0) >= 30
    )
    block_ok = (
        kind == "block"
        and int(met["n"]) >= MIN_N_BLOCK
        and float(met.get("ev") or 0) < 0
        and pf is not None
        and float(pf) <= 0.70
        and int(met.get("n_losses") or 0) >= 5
    )
    return {
        "conditions": list(conditions),
        "n": met["n"],
        "wr": met["wr"],
        "pf": met["pf"],
        "pf_inf": met.get("pf_inf"),
        "ev": met["ev"],
        "confidence": met["confidence"],
        "ci_lo": lo,
        "ci_hi": hi,
        "passes_min_n": passes_n,
        "min_n": min_n,
        "universal": uni,
        "ready_ok": ready_ok,
        "block_ok": block_ok,
        "n_wins": met.get("n_wins"),
        "n_losses": met.get("n_losses"),
    }


__all__ = [
    "MIN_N_BLOCK",
    "MIN_N_READY",
    "build_predicate",
    "universality",
    "validate_rule",
]
