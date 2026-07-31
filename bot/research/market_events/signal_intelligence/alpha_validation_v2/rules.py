"""Rebuild frozen Alpha Discovery predicates from candidate id / label."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)

Predicate = Callable[[dict[str, Any]], bool]

_RSI_BINS: dict[str, tuple[float, float]] = {
    "rsi_os": (0.0, 30.0),
    "rsi_low": (30.0, 45.0),
    "rsi_mid": (45.0, 55.0),
    "rsi_high": (55.0, 70.0),
    "rsi_ob": (70.0, 100.0),
}

_ATOMIC_RE = re.compile(
    r"^(?P<feat>[a-zA-Z0-9_]+?)_(?P<op>le|gt|ge|eq)_(?P<thr>.+)$"
)


@dataclass(frozen=True)
class FrozenAlphaRule:
    id: str
    label: str
    features: tuple[str, ...]
    pred: Predicate


def _between(feat: str, lo: float, hi: float) -> Predicate:
    return lambda r, f=feat, a=lo, b=hi: (
        _safe_float(r.get(f)) is not None and a <= float(r[f]) < b
    )


def _cmp(feat: str, op: str, thr: float) -> Predicate:
    if op == "le":
        return lambda r, f=feat, t=thr: (
            _safe_float(r.get(f)) is not None and float(r[f]) <= t
        )
    if op == "gt":
        return lambda r, f=feat, t=thr: (
            _safe_float(r.get(f)) is not None and float(r[f]) > t
        )
    if op == "ge":
        return lambda r, f=feat, t=thr: (
            _safe_float(r.get(f)) is not None and float(r[f]) >= t
        )
    raise ValueError(f"unknown cmp op {op}")


def _eq(feat: str, val: str) -> Predicate:
    return lambda r, f=feat, v=val: str(r.get(f) or "") == v


def parse_atomic_id(atomic_id: str) -> tuple[str, Predicate]:
    """Parse a single atomic rule id into (feature, predicate)."""
    aid = str(atomic_id).strip()
    if aid in _RSI_BINS:
        lo, hi = _RSI_BINS[aid]
        return "rsi", _between("rsi", lo, hi)
    m = _ATOMIC_RE.match(aid)
    if not m:
        raise ValueError(f"cannot parse atomic rule id: {aid}")
    feat = m.group("feat")
    op = m.group("op")
    thr_raw = m.group("thr")
    if op == "eq":
        return feat, _eq(feat, thr_raw)
    thr = float(thr_raw)
    return feat, _cmp(feat, op, thr)


def parse_rule_id(rule_id: str) -> FrozenAlphaRule:
    """Parse discovery candidate id into a frozen predicate."""
    rid = str(rule_id).strip()
    if rid.startswith("2|") or rid.startswith("3|"):
        parts = rid.split("|")
        atomic_ids = parts[1:]
        preds: list[Predicate] = []
        feats: list[str] = []
        for a in atomic_ids:
            f, p = parse_atomic_id(a)
            feats.append(f)
            preds.append(p)

        def combined(r: dict[str, Any], ps: tuple[Predicate, ...] = tuple(preds)) -> bool:
            return all(p(r) for p in ps)

        return FrozenAlphaRule(
            id=rid,
            label=rid,
            features=tuple(feats),
            pred=combined,
        )
    feat, pred = parse_atomic_id(rid)
    return FrozenAlphaRule(id=rid, label=rid, features=(feat,), pred=pred)


def rule_from_candidate(cand: dict[str, Any]) -> FrozenAlphaRule:
    """Build frozen rule from alpha_candidates.json row."""
    rid = str(cand.get("id") or "")
    base = parse_rule_id(rid)
    label = str(cand.get("label") or base.label)
    feats = cand.get("features")
    if isinstance(feats, list) and feats:
        features = tuple(str(x) for x in feats)
    else:
        features = base.features
    return FrozenAlphaRule(
        id=rid,
        label=label,
        features=features,
        pred=base.pred,
    )


def load_candidates(
    payload: dict[str, Any] | list[dict[str, Any]],
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        cands = payload
    else:
        cands = list(payload.get("candidates") or [])
    cands = [c for c in cands if c.get("id")]
    if limit is not None:
        cands = cands[: int(limit)]
    return cands


__all__ = [
    "FrozenAlphaRule",
    "load_candidates",
    "parse_atomic_id",
    "parse_rule_id",
    "rule_from_candidate",
]
