"""Map hypotheses → experiment type and run historical checks."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from bot.research.market_events.experiment_engine.filters import (
    and_pred,
    apply_pred,
    predicate_for_feature,
    resolve_feature_key,
)
from bot.research.market_events.experiment_engine.schema import (
    STATUS_FAILED,
    STATUS_REJECTED,
    STATUS_VALIDATED,
    STATUS_WEAK,
    TYPE_DEPENDENCY,
    TYPE_INTERACTION,
    TYPE_PATTERN,
    TYPE_REPLACEMENT,
    TYPE_SINGLE_FILTER,
)
from bot.research.market_events.experiment_engine.stats import (
    compare_groups,
    cohens_d,
    metrics_block,
    pnls_of,
    welch_t_pvalue,
)
from bot.research.market_events.hypothesis_engine.schema import (
    TYPE_CONDITIONAL_FEATURE,
    TYPE_FEATURE_DEPENDENCY,
    TYPE_PATTERN as HYP_PATTERN,
    TYPE_REPLACEMENT as HYP_REPLACEMENT,
    TYPE_STRONG_INTERACTION,
)

MIN_N = 8
MIN_VALIDATED_N = 20
MIN_DELTA_EV = 0.05
MIN_EFFECT = 0.15


def map_hypothesis_to_experiment_type(generated_from: str) -> str:
    g = str(generated_from or "")
    if g == TYPE_STRONG_INTERACTION:
        return TYPE_INTERACTION
    if g == TYPE_CONDITIONAL_FEATURE:
        return TYPE_SINGLE_FILTER
    if g == HYP_REPLACEMENT:
        return TYPE_REPLACEMENT
    if g == TYPE_FEATURE_DEPENDENCY:
        return TYPE_DEPENDENCY
    if g == HYP_PATTERN:
        return TYPE_PATTERN
    # fallback by key prefixes handled elsewhere
    return TYPE_SINGLE_FILTER


def _names_from_key(hypothesis_key: str) -> list[str]:
    key = str(hypothesis_key or "")
    if key.startswith("strong_ix:"):
        body = key.split(":", 1)[1]
        return [p.strip() for p in body.split("|") if p.strip()]
    if key.startswith("replace:"):
        body = key.split(":", 1)[1]
        return [p.strip() for p in body.split("|") if p.strip()]
    if key.startswith("dependency:"):
        body = key.split(":", 1)[1]
        if "->" in body:
            return [p.strip() for p in body.split("->", 1)]
        return [p.strip() for p in body.split("|") if p.strip()]
    if key.startswith("conditional:"):
        # conditional:Funding:rule...
        parts = key.split(":", 2)
        if len(parts) >= 2:
            return [parts[1]]
    if key.startswith("pattern:cluster_"):
        return [key.split(":", 1)[1]]
    return []


def _rule_from_evidence(evidence: list[dict[str, Any]], name: str) -> str | None:
    for ev in evidence:
        if str(ev.get("source_name") or "") == name:
            ref = str(ev.get("reference") or "")
            m = re.search(r"rule=([^ ]+)", ref)
            if m:
                return m.group(1)
            if "rule=" in ref:
                return ref.split("rule=", 1)[1].split(" status=")[0]
    return None


def decide_experiment_status(result: dict[str, Any]) -> str:
    """Map measured deltas → VALIDATED / REJECTED / WEAK / FAILED."""
    if result.get("error"):
        return STATUS_FAILED
    n = int(result.get("after_n") or result.get("dataset_size") or 0)
    delta = result.get("delta_ev")
    effect = result.get("effect_size")
    p = result.get("p_value")
    if n < MIN_N:
        return STATUS_WEAK
    if delta is None:
        return STATUS_FAILED
    # Explicit failure modes from runners
    if result.get("reject"):
        return STATUS_REJECTED
    if result.get("validate"):
        if n >= MIN_VALIDATED_N or (effect is not None and abs(float(effect)) >= MIN_EFFECT):
            return STATUS_VALIDATED
        return STATUS_WEAK
    # Generic: positive delta_ev + reasonable effect
    if float(delta) >= MIN_DELTA_EV and n >= MIN_VALIDATED_N:
        if p is not None and p > 0.2 and (effect is None or abs(float(effect)) < MIN_EFFECT):
            return STATUS_WEAK
        return STATUS_VALIDATED
    if float(delta) <= -MIN_DELTA_EV:
        return STATUS_REJECTED
    if float(delta) > 0 and n >= MIN_N:
        return STATUS_WEAK
    return STATUS_REJECTED


def run_single_filter(
    trades: list[dict[str, Any]],
    *,
    feature_name: str,
    rule_hint: str | None = None,
) -> dict[str, Any]:
    pred, rule, key = predicate_for_feature(trades, feature_name, prefer_rule=rule_hint)
    if pred is None or key is None:
        return {"error": f"no predicate for {feature_name}", "notes": rule}
    subset = apply_pred(trades, pred)
    cmp = compare_groups(trades, subset)
    cmp["notes"] = f"Single Filter: {rule}"
    cmp["after_n"] = cmp["after"]["n"]
    cmp["mfe_after"] = cmp["after"]["mfe"]
    cmp["mae_after"] = cmp["after"]["mae"]
    cmp["feature_key"] = key
    cmp["feature_name"] = feature_name
    cmp["rule"] = rule
    # Validate if filter improves EV
    if (cmp["delta_ev"] or 0) >= MIN_DELTA_EV and cmp["after"]["n"] >= MIN_N:
        cmp["validate"] = True
    elif (cmp["delta_ev"] or 0) <= 0:
        cmp["reject"] = True
    return cmp


def run_interaction(
    trades: list[dict[str, Any]],
    *,
    feature_a: str,
    feature_b: str,
) -> dict[str, Any]:
    pred_a, rule_a, _ = predicate_for_feature(trades, feature_a)
    pred_b, rule_b, _ = predicate_for_feature(trades, feature_b)
    if pred_a is None or pred_b is None:
        return {"error": f"missing predicates for {feature_a}/{feature_b}"}
    solo_a = apply_pred(trades, pred_a)
    solo_b = apply_pred(trades, pred_b)
    joint = apply_pred(trades, and_pred(pred_a, pred_b))
    m_a = metrics_block(solo_a)
    m_b = metrics_block(solo_b)
    m_j = metrics_block(joint)
    best_solo_ev = max(m_a["ev"], m_b["ev"])
    # before = best solo; after = joint
    cmp = compare_groups(solo_a if m_a["ev"] >= m_b["ev"] else solo_b, joint)
    # Override before metrics to report best solo explicitly
    cmp["ev_before"] = best_solo_ev
    cmp["before"] = m_a if m_a["ev"] >= m_b["ev"] else m_b
    cmp["after"] = m_j
    cmp["delta_ev"] = round(m_j["ev"] - best_solo_ev, 4)
    cmp["delta_pf"] = (
        None if m_j["pf"] is None or cmp["before"]["pf"] is None else round(m_j["pf"] - cmp["before"]["pf"], 4)
    )
    cmp["delta_wr"] = round(m_j["wr"] - cmp["before"]["wr"], 4)
    cmp["dataset_size"] = len(trades)
    cmp["after_n"] = m_j["n"]
    cmp["mfe_after"] = m_j["mfe"]
    cmp["mae_after"] = m_j["mae"]
    cmp["notes"] = (
        f"Interaction {feature_a}×{feature_b}: joint EV={m_j['ev']} "
        f"vs best solo={best_solo_ev} ({rule_a} AND {rule_b})"
    )
    cmp["solo_a"] = m_a
    cmp["solo_b"] = m_b
    if m_j["n"] >= MIN_N and m_j["ev"] > best_solo_ev + 0.02:
        cmp["validate"] = True
    elif m_j["n"] >= MIN_N and m_j["ev"] <= best_solo_ev:
        cmp["reject"] = True
    return cmp


def run_replacement(
    trades: list[dict[str, Any]],
    *,
    feature_a: str,
    feature_b: str,
) -> dict[str, Any]:
    pred_a, rule_a, _ = predicate_for_feature(trades, feature_a)
    pred_b, rule_b, _ = predicate_for_feature(trades, feature_b)
    if pred_a is None or pred_b is None:
        return {"error": f"missing predicates for {feature_a}/{feature_b}"}
    set_a = apply_pred(trades, pred_a)
    set_b = apply_pred(trades, pred_b)
    m_a = metrics_block(set_a)
    m_b = metrics_block(set_b)
    # before = A, after = B (can we replace A with B?)
    cmp = compare_groups(set_a, set_b)
    cmp["notes"] = (
        f"Replacement: can {feature_a} ({rule_a} EV={m_a['ev']}) "
        f"be replaced by {feature_b} ({rule_b} EV={m_b['ev']})?"
    )
    cmp["after_n"] = min(m_a["n"], m_b["n"])
    cmp["mfe_after"] = m_b["mfe"]
    cmp["mae_after"] = m_b["mae"]
    cmp["dataset_size"] = len(trades)
    # Similar behaviour → validate replacement; large gap → reject
    gap = abs((m_a["ev"] or 0) - (m_b["ev"] or 0))
    cmp["delta_ev"] = round((m_b["ev"] or 0) - (m_a["ev"] or 0), 4)
    if m_a["n"] >= MIN_N and m_b["n"] >= MIN_N and gap <= 0.15:
        cmp["validate"] = True
        cmp["effect_size"] = cohens_d(pnls_of(set_a), pnls_of(set_b))
        cmp["p_value"] = welch_t_pvalue(pnls_of(set_a), pnls_of(set_b))
    elif gap > 0.4:
        cmp["reject"] = True
    return cmp


def run_dependency(
    trades: list[dict[str, Any]],
    *,
    weak_name: str,
    strong_name: str,
) -> dict[str, Any]:
    pred_w, rule_w, _ = predicate_for_feature(trades, weak_name)
    pred_s, rule_s, _ = predicate_for_feature(trades, strong_name)
    if pred_w is None or pred_s is None:
        return {"error": f"missing predicates for {weak_name}/{strong_name}"}
    alone = apply_pred(trades, pred_w)
    joint = apply_pred(trades, and_pred(pred_w, pred_s))
    m_alone = metrics_block(alone)
    m_joint = metrics_block(joint)
    cmp = compare_groups(alone, joint)
    cmp["notes"] = (
        f"Dependency: {weak_name} alone EV={m_alone['ev']} n={m_alone['n']}; "
        f"with {strong_name} EV={m_joint['ev']} n={m_joint['n']} ({rule_w} AND {rule_s})"
    )
    cmp["after_n"] = m_joint["n"]
    cmp["mfe_after"] = m_joint["mfe"]
    cmp["mae_after"] = m_joint["mae"]
    cmp["dataset_size"] = len(trades)
    # Validates if joint >> alone (amplifier) and alone is weak
    if (
        m_joint["n"] >= MIN_N
        and (m_joint["ev"] - m_alone["ev"]) >= 0.05
        and m_alone["ev"] <= m_joint["ev"]
    ):
        cmp["validate"] = True
    elif m_joint["n"] >= MIN_N and m_joint["ev"] <= m_alone["ev"]:
        cmp["reject"] = True
    return cmp


def _load_cluster_indices(cluster_id: int, root: Path | None) -> list[int] | None:
    base = root or Path("reports/research")
    path = base / "patterns" / f"cluster_{int(cluster_id):03d}.json"
    if not path.exists():
        # try combined
        all_p = base / "patterns.json"
        if all_p.exists():
            try:
                payload = json.loads(all_p.read_text(encoding="utf-8"))
                for c in payload.get("clusters") or []:
                    if int(c.get("cluster_id")) == int(cluster_id):
                        return list(c.get("trade_indices") or []) or None
            except Exception:
                return None
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        # trade_indices may be omitted in export; fall back to rebuild
        idxs = payload.get("trade_indices")
        if idxs:
            return list(idxs)
    except Exception:
        pass
    return None


def run_pattern(
    trades: list[dict[str, Any]],
    *,
    cluster_id: int,
    patterns_root: Path | None = None,
    conn: Any = None,
) -> dict[str, Any]:
    """Compare cluster subset vs rest of sample."""
    idxs = _load_cluster_indices(cluster_id, patterns_root)
    # Always prefer live rebuild when conn available (indices stay aligned with trades)
    if conn is not None:
        try:
            from bot.research.market_events.pattern_discovery_v1 import build_pattern_discovery

            data = build_pattern_discovery(conn)
            for c in data.get("clusters") or []:
                if int(c.get("cluster_id")) == int(cluster_id):
                    idxs = list(c.get("trade_indices") or [])
                    break
        except Exception:
            pass
    if not idxs:
        return {"error": f"cluster {cluster_id} indices unavailable — run pattern-discovery first"}

    cluster = [trades[i] for i in idxs if 0 <= i < len(trades)]
    rest_idx = set(range(len(trades))) - set(i for i in idxs if 0 <= i < len(trades))
    rest = [trades[i] for i in sorted(rest_idx)]
    if len(cluster) < MIN_N:
        cmp_small = compare_groups(rest or trades, cluster)
        return {
            "error": None,
            "reject": False,
            "notes": f"Pattern cluster_{cluster_id} n={len(cluster)} too small",
            **cmp_small,
            "after_n": len(cluster),
            "mfe_after": (cmp_small.get("after") or {}).get("mfe"),
            "mae_after": (cmp_small.get("after") or {}).get("mae"),
            "dataset_size": len(trades),
        }
    cmp = compare_groups(rest if rest else trades, cluster)
    # Prefer Cohen's d / p of cluster vs rest
    cmp["effect_size"] = cohens_d(pnls_of(cluster), pnls_of(rest)) if rest else cmp.get("effect_size")
    cmp["p_value"] = welch_t_pvalue(pnls_of(cluster), pnls_of(rest)) if rest else cmp.get("p_value")
    cmp["notes"] = (
        f"Pattern Cluster_{cluster_id}: n={len(cluster)} EV={cmp['after']['ev']} "
        f"vs rest n={len(rest)} EV={cmp['before']['ev']}"
    )
    cmp["after_n"] = len(cluster)
    cmp["mfe_after"] = cmp["after"]["mfe"]
    cmp["mae_after"] = cmp["after"]["mae"]
    cmp["dataset_size"] = len(trades)
    # Distinct regime if meaningful effect (Cohen's d) or large ΔEV with supportive p
    d = abs(float(cmp["effect_size"] or 0))
    de = abs(float(cmp["delta_ev"] or 0))
    p = cmp.get("p_value")
    p_ok = p is None or float(p) < 0.15
    if len(cluster) >= MIN_VALIDATED_N and d >= MIN_EFFECT and p_ok:
        cmp["validate"] = True
    elif len(cluster) >= MIN_N and d >= 0.5 and p_ok:
        cmp["validate"] = True
    elif len(cluster) >= MIN_VALIDATED_N and de >= 0.25 and p_ok:
        cmp["validate"] = True
    elif len(cluster) >= MIN_N and d < 0.05 and de < 0.02:
        cmp["reject"] = True
    return cmp


def execute_hypothesis_experiment(
    trades: list[dict[str, Any]],
    hypothesis: dict[str, Any],
    *,
    patterns_root: Path | None = None,
    conn: Any = None,
) -> dict[str, Any]:
    """Dispatch runner by hypothesis type / key. Always returns a result dict."""
    exp_type = map_hypothesis_to_experiment_type(str(hypothesis.get("generated_from") or ""))
    key = str(hypothesis.get("hypothesis_key") or "")
    evidence = list(hypothesis.get("evidence") or [])
    names = _names_from_key(key)

    if key.startswith("pattern:") or exp_type == TYPE_PATTERN:
        m = re.search(r"cluster_(\d+)", key)
        if not m:
            m = re.search(r"Cluster[_\s]?(\d+)", str(hypothesis.get("title") or ""), re.I)
        if not m:
            return {"error": "pattern cluster id missing", "experiment_type": TYPE_PATTERN}
        result = run_pattern(
            trades,
            cluster_id=int(m.group(1)),
            patterns_root=patterns_root,
            conn=conn,
        )
        result["experiment_type"] = TYPE_PATTERN
        return result

    if exp_type == TYPE_INTERACTION or key.startswith("strong_ix:"):
        if len(names) < 2:
            return {"error": "interaction needs two features", "experiment_type": TYPE_INTERACTION}
        result = run_interaction(trades, feature_a=names[0], feature_b=names[1])
        result["experiment_type"] = TYPE_INTERACTION
        return result

    if exp_type == TYPE_REPLACEMENT or key.startswith("replace:"):
        if len(names) < 2:
            return {"error": "replacement needs two features", "experiment_type": TYPE_REPLACEMENT}
        result = run_replacement(trades, feature_a=names[0], feature_b=names[1])
        result["experiment_type"] = TYPE_REPLACEMENT
        return result

    if exp_type == TYPE_DEPENDENCY or key.startswith("dependency:"):
        if len(names) < 2:
            return {"error": "dependency needs weak->strong", "experiment_type": TYPE_DEPENDENCY}
        result = run_dependency(trades, weak_name=names[0], strong_name=names[1])
        result["experiment_type"] = TYPE_DEPENDENCY
        return result

    # Single filter / conditional
    fname = names[0] if names else None
    if not fname:
        # try evidence FeatureValidation source
        for ev in evidence:
            if ev.get("source_type") in ("FeatureValidation", "Knowledge"):
                fname = str(ev.get("source_name") or "")
                if fname:
                    break
    if not fname:
        return {"error": "single filter feature missing", "experiment_type": TYPE_SINGLE_FILTER}
    rule_hint = _rule_from_evidence(evidence, fname) or str(hypothesis.get("title") or "")
    result = run_single_filter(trades, feature_name=fname, rule_hint=rule_hint)
    result["experiment_type"] = TYPE_SINGLE_FILTER
    return result
