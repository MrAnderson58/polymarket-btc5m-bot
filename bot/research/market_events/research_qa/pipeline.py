"""Pipeline runner + fingerprint for Research QA regression."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bot.research.market_events.experiment_engine.engine import run_all_experiments
from bot.research.market_events.experiment_engine.report import write_experiments_report
from bot.research.market_events.feature_validation_v1 import (
    build_feature_validation,
    write_feature_validation_report,
)
from bot.research.market_events.hypothesis_engine.report import write_hypotheses_report
from bot.research.market_events.hypothesis_engine.store import load_hypothesis_bundle
from bot.research.market_events.hypothesis_engine.validate import run_hypothesis_validate
from bot.research.market_events.knowledge_engine.report import (
    load_knowledge_snapshot,
    write_knowledge_report,
)
from bot.research.market_events.pattern_discovery_v1 import (
    build_pattern_discovery,
    write_pattern_exports,
)

# Statistical regression tolerances
TOL_EV = 0.01
TOL_PF = 0.05
TOL_WR = 0.5
TOL_COHEN_D = 0.05
TOL_P = 0.05
TOL_CONF = 1.0


def run_full_research_pipeline(
    conn: Any,
    *,
    reports_root: Path,
    patterns_root: Path | None = None,
) -> dict[str, Any]:
    """
    Feature Validation → Knowledge → Pattern → Hypothesis → Experiment.
    Writes all research reports under reports_root.
    """
    reports_root.mkdir(parents=True, exist_ok=True)
    pref = patterns_root or reports_root

    fv = build_feature_validation(conn)
    write_feature_validation_report(fv, root=reports_root)
    # Knowledge update is inside run_feature_validation; replicate upsert here
    from bot.research.market_events.knowledge_engine.store import upsert_from_feature_validation

    upsert_from_feature_validation(conn, fv)
    write_knowledge_report(conn, root=reports_root)

    patterns = build_pattern_discovery(conn)
    write_pattern_exports(patterns, root=pref)

    hyp_stats = run_hypothesis_validate(conn, patterns_root=pref)
    write_hypotheses_report(conn, root=reports_root)

    exp_summary = run_all_experiments(conn, patterns_root=pref)
    write_experiments_report(conn, root=reports_root)

    try:
        conn.commit()
    except Exception:
        pass

    return {
        "feature_validation": fv,
        "knowledge": load_knowledge_snapshot(conn),
        "patterns": patterns,
        "hypotheses": load_hypothesis_bundle(conn),
        "hypothesis_stats": hyp_stats,
        "experiments": exp_summary,
        "reports_root": str(reports_root),
    }


def pipeline_fingerprint(result: dict[str, Any]) -> dict[str, Any]:
    """Stable JSON-serializable fingerprint for golden comparison."""
    fv = result.get("feature_validation") or {}
    ranking = list(fv.get("ranking") or [])
    top_features = [str(r.get("feature") or r.get("key") or "") for r in ranking[:10]]
    keep = [str(r.get("feature") or "") for r in (fv.get("keep") or [])]

    patterns = result.get("patterns") or {}
    top_patterns = [
        {"id": c.get("cluster_id"), "name": c.get("name"), "ev": (c.get("metrics") or {}).get("ev"), "n": (c.get("metrics") or {}).get("n")}
        for c in (patterns.get("top") or [])[:10]
    ]

    hyps = result.get("hypotheses") or []
    hyp_rows = [
        {
            "key": h.get("hypothesis_key"),
            "status": h.get("status"),
            "generated_from": h.get("generated_from"),
            "confidence": h.get("confidence"),
            "priority": h.get("priority"),
            "evidence_score": h.get("evidence_score"),
            "sample_size": h.get("sample_size"),
            "n_evidence": len(h.get("evidence") or []),
        }
        for h in hyps
        if h.get("status") != "ARCHIVED"
    ]
    hyp_rows.sort(key=lambda x: str(x.get("key") or ""))

    exp = result.get("experiments") or {}
    exp_list = list(exp.get("experiments") or [])
    exp_rows = [
        {
            "type": e.get("experiment_type"),
            "status": e.get("status"),
            "delta_ev": e.get("delta_ev"),
            "effect_size": e.get("effect_size"),
            "p_value": e.get("p_value"),
            "dataset_size": e.get("dataset_size"),
        }
        for e in exp_list
    ]

    knowledge = result.get("knowledge") or {}
    return {
        "n_trades": int(fv.get("n_trades") or patterns.get("n_trades") or 0),
        "n_features_ranked": len(ranking),
        "top_features": top_features,
        "keep_features": keep,
        "n_interactions": len(fv.get("interactions") or []),
        "n_patterns": len(patterns.get("clusters") or []),
        "top_patterns": top_patterns,
        "n_hypotheses": len(hyp_rows),
        "hypothesis_statuses": sorted(
            f"{h.get('key')}:{h.get('status')}" for h in hyp_rows
        ),
        "hypotheses": hyp_rows,
        "n_experiments": int(exp.get("ran") or len(exp_list)),
        "experiment_status_counts": {
            "validated": int(exp.get("validated") or 0),
            "rejected": int(exp.get("rejected") or 0),
            "weak": int(exp.get("weak") or 0),
            "failed": int(exp.get("failed") or 0),
        },
        "experiments": exp_rows,
        "n_knowledge_features": len(knowledge.get("features") or []),
        "n_knowledge_rules": len(knowledge.get("rules") or []),
        "n_knowledge_interactions": len(knowledge.get("interactions") or []),
        # Overall baseline stats from FV
        "baseline": (fv.get("baseline") or fv.get("overall") or {}),
    }


def _num(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def compare_fingerprints(
    actual: dict[str, Any],
    expected: dict[str, Any],
) -> list[str]:
    """Return list of failure messages (empty = PASS)."""
    fails: list[str] = []

    def eq(key: str) -> None:
        if actual.get(key) != expected.get(key):
            fails.append(f"{key}: got {actual.get(key)!r} expected {expected.get(key)!r}")

    for key in (
        "n_trades",
        "n_features_ranked",
        "top_features",
        "keep_features",
        "n_interactions",
        "n_patterns",
        "n_hypotheses",
        "hypothesis_statuses",
        "n_experiments",
        "n_knowledge_features",
        "n_knowledge_rules",
        "n_knowledge_interactions",
    ):
        eq(key)

    # Pattern order by id+name (ev within tol)
    a_pat = actual.get("top_patterns") or []
    e_pat = expected.get("top_patterns") or []
    if len(a_pat) != len(e_pat):
        fails.append(f"top_patterns length {len(a_pat)} != {len(e_pat)}")
    else:
        for i, (a, e) in enumerate(zip(a_pat, e_pat)):
            if a.get("id") != e.get("id") or a.get("name") != e.get("name"):
                fails.append(f"top_patterns[{i}] order/id mismatch {a} vs {e}")
            ae, ee = _num(a.get("ev")), _num(e.get("ev"))
            if ae is not None and ee is not None and abs(ae - ee) > TOL_EV:
                fails.append(f"top_patterns[{i}].ev Δ={abs(ae-ee):.4f} > {TOL_EV}")

    # Hypothesis confidence / priority within tol
    a_hyps = {h["key"]: h for h in (actual.get("hypotheses") or []) if h.get("key")}
    e_hyps = {h["key"]: h for h in (expected.get("hypotheses") or []) if h.get("key")}
    if set(a_hyps) != set(e_hyps):
        fails.append(f"hypothesis keys differ: {sorted(set(a_hyps)^set(e_hyps))}")
    for k, eh in e_hyps.items():
        ah = a_hyps.get(k)
        if not ah:
            continue
        if ah.get("status") != eh.get("status"):
            fails.append(f"hypothesis {k} status {ah.get('status')} != {eh.get('status')}")
        ac, ec = _num(ah.get("confidence")), _num(eh.get("confidence"))
        if ac is not None and ec is not None and abs(ac - ec) > TOL_CONF:
            fails.append(f"hypothesis {k} confidence Δ={abs(ac-ec):.2f}")

    # Experiment stats
    a_exps = actual.get("experiments") or []
    e_exps = expected.get("experiments") or []
    if len(a_exps) != len(e_exps):
        fails.append(f"experiments count {len(a_exps)} != {len(e_exps)}")
    else:
        for i, (a, e) in enumerate(zip(a_exps, e_exps)):
            if a.get("type") != e.get("type") or a.get("status") != e.get("status"):
                fails.append(f"experiment[{i}] {a.get('type')}/{a.get('status')} != {e.get('type')}/{e.get('status')}")
            for field, tol in (
                ("delta_ev", TOL_EV),
                ("effect_size", TOL_COHEN_D),
                ("p_value", TOL_P),
            ):
                av, ev = _num(a.get(field)), _num(e.get(field))
                if av is None and ev is None:
                    continue
                if av is None or ev is None:
                    fails.append(f"experiment[{i}].{field} None mismatch")
                    continue
                if abs(av - ev) > tol:
                    fails.append(f"experiment[{i}].{field} Δ={abs(av-ev):.4f} > {tol}")

    # Status counts
    if actual.get("experiment_status_counts") != expected.get("experiment_status_counts"):
        fails.append(
            f"experiment_status_counts {actual.get('experiment_status_counts')} "
            f"!= {expected.get('experiment_status_counts')}"
        )

    return fails


def golden_expectations_path() -> Path:
    return Path(__file__).resolve().parent / "golden" / "expectations_v1.json"


def load_golden_expectations() -> dict[str, Any]:
    path = golden_expectations_path()
    return json.loads(path.read_text(encoding="utf-8"))


def save_golden_expectations(fp: dict[str, Any]) -> Path:
    path = golden_expectations_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fp, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def report_structure_fingerprint(md_text: str) -> list[str]:
    """Extract markdown ## / # headings for structural snapshot (ignore timestamps)."""
    heads: list[str] = []
    for line in md_text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            # drop generated timestamps / dates in heading content
            if "Generated:" in s or s.startswith("# ") and "T" in s and "Z" in s:
                continue
            heads.append(s)
    return heads
