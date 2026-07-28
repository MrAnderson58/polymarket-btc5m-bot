"""Generate research hypotheses from Knowledge / Feature Validation / Pattern Discovery."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from bot.research.market_events.hypothesis_engine.schema import (
    TYPE_CONDITIONAL_FEATURE,
    TYPE_FEATURE_DEPENDENCY,
    TYPE_PATTERN,
    TYPE_REPLACEMENT,
    TYPE_STRONG_INTERACTION,
)
from bot.research.market_events.knowledge_engine.report import load_knowledge_snapshot
from bot.research.market_events.knowledge_engine.schema import ensure_knowledge_engine_schema

MIN_SAMPLE_FLAG = 30
MIN_INTERACTION_N = 10
MIN_SYNERGY = 0.05
REPLACEMENT_EV_EPS = 0.15


def _f(v: Any) -> float | None:
    try:
        if v is None:
            return None
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except (TypeError, ValueError):
        return None


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return round(max(lo, min(hi, x)), 2)


def score_hypothesis(
    *,
    evidence: list[dict[str, Any]],
    sample_size: int,
    effect_magnitude: float,
) -> dict[str, Any]:
    """Compute evidence_score, confidence, priority from independent sources + effect."""
    source_types = {str(e.get("source_type") or "") for e in evidence if e.get("source_type")}
    # Independent confirmation families
    families = set()
    for st in source_types:
        s = st.lower()
        if "feature" in s or "validation" in s:
            families.add("FeatureValidation")
        elif "knowledge" in s or "rule" in s:
            families.add("Knowledge")
        elif "interaction" in s:
            families.add("Interaction")
        elif "pattern" in s:
            families.add("PatternDiscovery")
        else:
            families.add(st)
    evidence_score = len(families)

    n = max(0, int(sample_size))
    n_factor = min(1.0, math.log1p(n) / math.log1p(200)) if n else 0.0
    mag = abs(float(effect_magnitude))
    mag_factor = min(1.0, mag / 1.0)  # ~1% EV delta → full
    conf = _clamp(
        25.0 * evidence_score + 35.0 * n_factor + 30.0 * mag_factor + 5.0 * min(1.0, sum(float(e.get("weight") or 0) for e in evidence) / 3.0)
    )
    priority = _clamp(
        0.45 * conf + 25.0 * evidence_score + 20.0 * n_factor + 10.0 * mag_factor
    )
    return {
        "evidence_score": evidence_score,
        "confidence": conf,
        "priority": priority,
        "families": sorted(families),
    }


def _load_patterns(conn: Any, root: Path | None = None) -> list[dict[str, Any]]:
    """Prefer exported patterns.json; fall back to live Pattern Discovery build."""
    base = root or Path("reports/research")
    path = base / "patterns.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return list(payload.get("clusters") or [])
        except Exception:
            pass
    try:
        from bot.research.market_events.pattern_discovery_v1 import build_pattern_discovery

        data = build_pattern_discovery(conn)
        return list(data.get("clusters") or [])
    except Exception:
        return []


def generate_hypothesis_candidates(
    conn: Any,
    *,
    patterns_root: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Build hypothesis dicts (not yet persisted) from research layers only.
    Each candidate includes evidence[]; empty evidence is discarded.
    """
    ensure_knowledge_engine_schema(conn)
    snap = load_knowledge_snapshot(conn)
    features = list(snap.get("features") or [])
    rules = list(snap.get("rules") or [])
    interactions = list(snap.get("interactions") or [])
    feat_by_name = {str(f.get("feature_name") or ""): f for f in features}
    candidates: list[dict[str, Any]] = []

    # --- Type 1: Strong Interaction ---
    for ix in interactions:
        syn = _f(ix.get("synergy"))
        n = int(ix.get("sample_size") or 0)
        if syn is None or syn < MIN_SYNERGY or n < MIN_INTERACTION_N:
            continue
        if not ix.get("stronger_together"):
            continue
        a = str(ix.get("feature_a") or "")
        b = str(ix.get("feature_b") or "")
        if not a or not b:
            continue
        evidence: list[dict[str, Any]] = [
            {
                "source_type": "Interaction",
                "source_name": f"{a}×{b}",
                "reference": f"synergy={syn:+.4f} EV_joint={ix.get('ev_joint')}",
                "weight": min(3.0, abs(syn) * 2),
            }
        ]
        # Cross-link Feature Validation / Knowledge for each side
        for name in (a, b):
            f = feat_by_name.get(name)
            if f and f.get("ev_delta") is not None:
                evidence.append(
                    {
                        "source_type": "FeatureValidation",
                        "source_name": name,
                        "reference": f"ΔEV={_f(f.get('ev_delta')):+.4f}" if _f(f.get("ev_delta")) is not None else f"ΔEV={f.get('ev_delta')}",
                        "weight": 1.0,
                    }
                )
            matching_rules = [r for r in rules if str(r.get("feature_name") or "") == name]
            for r in matching_rules[:1]:
                evidence.append(
                    {
                        "source_type": "Knowledge",
                        "source_name": name,
                        "reference": f"rule={r.get('rule_text')} status={r.get('status')}",
                        "weight": 1.0,
                    }
                )
        scores = score_hypothesis(evidence=evidence, sample_size=n, effect_magnitude=syn)
        if scores["evidence_score"] < 1:
            continue
        candidates.append(
            {
                "hypothesis_key": f"strong_ix:{a}|{b}",
                "title": f"{a} работает только вместе с {b}",
                "description": (
                    f"Interaction {a}×{b} shows positive synergy ({syn:+.4f}). "
                    f"Joint EV={ix.get('ev_joint')}; solo EV_a={ix.get('ev_a')}, EV_b={ix.get('ev_b')}. "
                    "Research-only: do not enable as a live filter."
                ),
                "generated_from": TYPE_STRONG_INTERACTION,
                "sample_size": n,
                "effect_magnitude": syn,
                "evidence": evidence,
                **scores,
            }
        )

    # --- Type 2: Conditional Feature ---
    for r in rules:
        rule_text = str(r.get("rule_text") or "")
        name = str(r.get("feature_name") or "")
        if not name or not rule_text:
            continue
        low = rule_text.lower()
        is_conditional = any(
            tok in low
            for tok in (
                "direction",
                "volatility",
                "trend",
                "funding",
                "regime",
                "median",
                "short",
                "long",
            )
        )
        if not is_conditional:
            continue
        ev_d = _f(r.get("ev_delta"))
        n = int(r.get("sample_size") or 0)
        if n < 5:
            continue
        # Skip null-effect rejected rules (common on tiny flat samples).
        if str(r.get("status") or "").lower() == "rejected" and abs(ev_d or 0.0) < 0.01:
            continue
        evidence = [
            {
                "source_type": "Knowledge",
                "source_name": name,
                "reference": f"rule={rule_text} ΔEV={ev_d}",
                "weight": 1.2,
            }
        ]
        f = feat_by_name.get(name)
        if f:
            evidence.append(
                {
                    "source_type": "FeatureValidation",
                    "source_name": name,
                    "reference": f"verdict={f.get('verdict')} ΔEV={f.get('ev_delta')} conf={f.get('confidence')}",
                    "weight": 1.0,
                }
            )
        scores = score_hypothesis(
            evidence=evidence,
            sample_size=n,
            effect_magnitude=abs(ev_d or 0.0),
        )
        if scores["evidence_score"] < 1:
            continue
        candidates.append(
            {
                "hypothesis_key": f"conditional:{name}:{rule_text[:80]}",
                "title": f"{name}: условие «{rule_text}»",
                "description": (
                    f"Feature {name} appears conditional: {rule_text}. "
                    f"ΔEV={ev_d}, n={n}, knowledge status={r.get('status')}. "
                    "Investigate as a conditional edge, not a blanket filter."
                ),
                "generated_from": TYPE_CONDITIONAL_FEATURE,
                "sample_size": n,
                "effect_magnitude": abs(ev_d or 0.0),
                "evidence": evidence,
                **scores,
            }
        )

    # --- Type 3: Replacement ---
    ranked = [f for f in features if _f(f.get("ev_delta")) is not None]
    ranked.sort(key=lambda x: abs(_f(x.get("ev_delta")) or 0.0), reverse=True)
    seen_pairs: set[tuple[str, str]] = set()
    for i, fa in enumerate(ranked):
        for fb in ranked[i + 1 :]:
            a = str(fa.get("feature_name") or "")
            b = str(fb.get("feature_name") or "")
            if not a or not b or a == b:
                continue
            ea, eb = _f(fa.get("ev_delta")), _f(fb.get("ev_delta"))
            if ea is None or eb is None:
                continue
            if ea * eb <= 0:
                continue  # same sign required
            if abs(ea - eb) > REPLACEMENT_EV_EPS:
                continue
            pair = tuple(sorted((a, b)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            n = min(int(fa.get("sample_size") or 0), int(fb.get("sample_size") or 0))
            evidence = [
                {
                    "source_type": "FeatureValidation",
                    "source_name": a,
                    "reference": f"ΔEV={ea:+.4f} verdict={fa.get('verdict')}",
                    "weight": 1.0,
                },
                {
                    "source_type": "FeatureValidation",
                    "source_name": b,
                    "reference": f"ΔEV={eb:+.4f} verdict={fb.get('verdict')}",
                    "weight": 1.0,
                },
            ]
            # Knowledge parity
            for name, feat in ((a, fa), (b, fb)):
                matching = [r for r in rules if str(r.get("feature_name") or "") == name]
                if matching:
                    evidence.append(
                        {
                            "source_type": "Knowledge",
                            "source_name": name,
                            "reference": f"status={matching[0].get('status')} conf={matching[0].get('confidence')}",
                            "weight": 0.8,
                        }
                    )
            scores = score_hypothesis(
                evidence=evidence,
                sample_size=n,
                effect_magnitude=abs(ea),
            )
            if scores["evidence_score"] < 1:
                continue
            candidates.append(
                {
                    "hypothesis_key": f"replace:{pair[0]}|{pair[1]}",
                    "title": f"{a} можно заменить на {b}",
                    "description": (
                        f"{a} and {b} show similar Feature Validation behaviour "
                        f"(ΔEV {ea:+.4f} vs {eb:+.4f}, |diff|≤{REPLACEMENT_EV_EPS}). "
                        "Candidate for redundancy analysis — research only."
                    ),
                    "generated_from": TYPE_REPLACEMENT,
                    "sample_size": n,
                    "effect_magnitude": abs(ea),
                    "evidence": evidence,
                    **scores,
                }
            )

    # --- Type 4: Feature Dependency ---
    for ix in interactions:
        syn = _f(ix.get("synergy"))
        n = int(ix.get("sample_size") or 0)
        if syn is None or syn < MIN_SYNERGY or n < MIN_INTERACTION_N:
            continue
        a = str(ix.get("feature_a") or "")
        b = str(ix.get("feature_b") or "")
        ev_a, ev_b = _f(ix.get("ev_a")), _f(ix.get("ev_b"))
        if not a or not b:
            continue
        # Weak solo + strong joint → dependent amplifier
        weak_name = None
        strong_name = None
        if ev_a is not None and ev_b is not None:
            if abs(ev_a) < abs(ev_b) * 0.5 and syn > 0:
                weak_name, strong_name = a, b
            elif abs(ev_b) < abs(ev_a) * 0.5 and syn > 0:
                weak_name, strong_name = b, a
        # Also: feature with WATCH/REMOVE but strong interaction
        if weak_name is None:
            for name, partner in ((a, b), (b, a)):
                f = feat_by_name.get(name)
                if f and str(f.get("verdict") or "").upper() in ("WATCH", "REMOVE"):
                    weak_name, strong_name = name, partner
                    break
        if not weak_name or not strong_name:
            continue
        # Avoid duplicating strong-interaction keys exactly when already covered —
        # dependency is a different framing / key.
        evidence = [
            {
                "source_type": "Interaction",
                "source_name": f"{a}×{b}",
                "reference": f"synergy={syn:+.4f} EV_a={ev_a} EV_b={ev_b}",
                "weight": min(3.0, abs(syn) * 2),
            },
            {
                "source_type": "FeatureValidation",
                "source_name": weak_name,
                "reference": (
                    f"verdict={(feat_by_name.get(weak_name) or {}).get('verdict')} "
                    f"ΔEV={(feat_by_name.get(weak_name) or {}).get('ev_delta')}"
                ),
                "weight": 1.0,
            },
        ]
        scores = score_hypothesis(evidence=evidence, sample_size=n, effect_magnitude=syn)
        candidates.append(
            {
                "hypothesis_key": f"dependency:{weak_name}->{strong_name}",
                "title": f"{weak_name} не самостоятелен — усиливает {strong_name}",
                "description": (
                    f"{weak_name} looks dependent on {strong_name}: weak/solo signal but "
                    f"interaction synergy {syn:+.4f} (n={n}). Research dependency, do not gate on {weak_name} alone."
                ),
                "generated_from": TYPE_FEATURE_DEPENDENCY,
                "sample_size": n,
                "effect_magnitude": syn,
                "evidence": evidence,
                **scores,
            }
        )

    # --- Type 5: Pattern Hypothesis ---
    clusters = _load_patterns(conn, root=patterns_root)
    for c in clusters:
        cid = c.get("cluster_id")
        name = str(c.get("name") or f"Cluster_{cid}")
        metrics = c.get("metrics") or {}
        n = int(metrics.get("n") or 0)
        if n < 5:
            continue
        ev = _f(metrics.get("ev")) or 0.0
        score = _f(metrics.get("rank_score")) or 0.0
        evidence = [
            {
                "source_type": "PatternDiscovery",
                "source_name": f"Cluster_{cid}",
                "reference": f"name={name} EV={ev} WR={metrics.get('wr')} n={n} score={score}",
                "weight": min(3.0, abs(score) / 2.0 + 0.5),
            }
        ]
        # Link dominant features from cluster profile to knowledge
        feats = c.get("features") or {}
        for fname in ("funding", "trend", "volatility", "oi"):
            kn = {
                "funding": "Funding",
                "trend": "Trend",
                "volatility": "Volatility",
                "oi": "OI Δ",
            }.get(fname, fname)
            if feats.get(fname) is None:
                continue
            f = feat_by_name.get(kn) or feat_by_name.get(fname)
            if f:
                evidence.append(
                    {
                        "source_type": "FeatureValidation",
                        "source_name": str(f.get("feature_name") or kn),
                        "reference": f"cluster_centroid {fname}={feats.get(fname)} ΔEV={f.get('ev_delta')}",
                        "weight": 0.7,
                    }
                )
        scores = score_hypothesis(
            evidence=evidence,
            sample_size=n,
            effect_magnitude=abs(ev),
        )
        candidates.append(
            {
                "hypothesis_key": f"pattern:cluster_{cid}",
                "title": f"Cluster {cid} ({name}) — отдельный рыночный режим",
                "description": (
                    f"Pattern Discovery cluster {cid} «{name}» has n={n}, EV={ev}%, "
                    f"WR={metrics.get('wr')}%, rank_score={score}. "
                    "Worth studying as its own regime — research only."
                ),
                "generated_from": TYPE_PATTERN,
                "sample_size": n,
                "effect_magnitude": abs(ev),
                "evidence": evidence,
                **scores,
            }
        )

    # Drop any without evidence (hard requirement)
    out = [c for c in candidates if c.get("evidence")]
    # Prefer higher priority, dedupe by key
    by_key: dict[str, dict[str, Any]] = {}
    for c in out:
        k = str(c["hypothesis_key"])
        prev = by_key.get(k)
        if prev is None or float(c.get("priority") or 0) > float(prev.get("priority") or 0):
            by_key[k] = c
    return sorted(by_key.values(), key=lambda x: (-float(x.get("priority") or 0), -float(x.get("confidence") or 0)))
