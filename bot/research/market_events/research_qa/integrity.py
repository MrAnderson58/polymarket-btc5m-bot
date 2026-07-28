"""Integrity checks for Knowledge / Hypothesis / Experiment layers."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.hypothesis_engine.schema import HYPOTHESIS_STATUSES
from bot.research.market_events.knowledge_engine.report import load_knowledge_snapshot


def check_knowledge_integrity(conn: Any) -> list[str]:
    fails: list[str] = []
    snap = load_knowledge_snapshot(conn)
    feats = snap.get("features") or []
    rules = snap.get("rules") or []
    ixs = snap.get("interactions") or []
    history = snap.get("history") or []

    names = [f.get("feature_name") for f in feats]
    if len(names) != len(set(names)):
        fails.append("knowledge_features has duplicate feature_name")

    rule_keys = [r.get("rule_key") for r in rules]
    if len(rule_keys) != len(set(rule_keys)):
        fails.append("knowledge_rules has duplicate rule_key")

    pair_keys = [x.get("pair_key") for x in ixs]
    if len(pair_keys) != len(set(pair_keys)):
        fails.append("knowledge_interactions has duplicate pair_key")

    valid_rule_status = {"candidate", "validated", "rejected"}
    for r in rules:
        if str(r.get("status") or "") not in valid_rule_status:
            fails.append(f"rule {r.get('rule_key')} bad status {r.get('status')}")

    if feats and not history:
        # Fresh first write should still create history events
        try:
            n = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM knowledge_history"
                ).fetchone()["n"]
            )
            if n == 0:
                fails.append("knowledge_history empty after features written")
        except Exception as exc:
            fails.append(f"knowledge_history unreadable: {exc}")

    return fails


def check_hypothesis_integrity(conn: Any) -> list[str]:
    fails: list[str] = []
    from bot.research.market_events.hypothesis_engine.store import load_hypothesis_bundle

    bundle = load_hypothesis_bundle(conn)
    keys: set[str] = set()
    for h in bundle:
        if h.get("status") == "ARCHIVED":
            continue
        key = str(h.get("hypothesis_key") or "")
        if not key:
            fails.append(f"hypothesis id={h.get('id')} missing hypothesis_key")
        if key in keys:
            fails.append(f"duplicate hypothesis_key {key}")
        keys.add(key)

        if not h.get("generated_from"):
            fails.append(f"{key}: missing generated_from source")
        evidence = h.get("evidence") or []
        if not evidence:
            fails.append(f"{key}: missing evidence")
        for ev in evidence:
            if not ev.get("source_type") or not ev.get("source_name"):
                fails.append(f"{key}: evidence missing source_type/name")

        conf = h.get("confidence")
        if conf is None:
            fails.append(f"{key}: empty confidence")
        pri = h.get("priority")
        if pri is None:
            fails.append(f"{key}: empty priority")
        elif float(pri) < 0:
            fails.append(f"{key}: negative priority {pri}")

        st = str(h.get("status") or "")
        if st not in HYPOTHESIS_STATUSES:
            fails.append(f"{key}: invalid status {st}")

    # No cyclic dependency: dependency A->B and B->A
    deps: dict[str, str] = {}
    for h in bundle:
        key = str(h.get("hypothesis_key") or "")
        if key.startswith("dependency:") and "->" in key:
            body = key.split(":", 1)[1]
            a, b = [p.strip() for p in body.split("->", 1)]
            deps[a] = b
            if deps.get(b) == a:
                fails.append(f"cyclic dependency {a}<->{b}")

    return fails


def check_experiment_integrity(conn: Any) -> list[str]:
    fails: list[str] = []
    from bot.research.market_events.experiment_engine.store import (
        list_experiments,
        list_recent_runs,
    )

    exps = list_experiments(conn)
    runs = list_recent_runs(conn, limit=500)
    hyp_ids = set()
    try:
        rows = conn.execute("SELECT id FROM research_hypotheses").fetchall()
        hyp_ids = {int(r["id"]) for r in rows}
    except Exception:
        fails.append("research_hypotheses unreadable")

    for e in exps:
        hid = e.get("hypothesis_id")
        if hid is None or int(hid) not in hyp_ids:
            fails.append(f"experiment#{e.get('id')} orphan hypothesis_id={hid}")
        if not e.get("experiment_type"):
            fails.append(f"experiment#{e.get('id')} missing type")
        if e.get("status") in ("VALIDATED", "REJECTED", "WEAK") and e.get("finished_at") is None:
            fails.append(f"experiment#{e.get('id')} finished without finished_at")

    exp_ids = {int(e["id"]) for e in exps}
    for r in runs:
        if int(r.get("experiment_id") or -1) not in exp_ids:
            fails.append(f"run#{r.get('id')} orphan experiment_id")

    # Validated/Rejected experiments should leave knowledge_history trails
    try:
        n_exp_hist = int(
            conn.execute(
                """
                SELECT COUNT(*) AS n FROM knowledge_history
                WHERE entity_type IN ('experiment', 'feature', 'rule', 'interaction', 'hypothesis')
                  AND (note LIKE 'experiment%' OR note LIKE 'experiment-engine%' OR entity_type='experiment')
                """
            ).fetchone()["n"]
        )
        terminal = [e for e in exps if e.get("status") in ("VALIDATED", "REJECTED")]
        if terminal and n_exp_hist == 0:
            fails.append("experiments finished but no knowledge_history experiment trail")
    except Exception as exc:
        fails.append(f"knowledge_history check failed: {exc}")

    # Hypothesis status should not stay NEW after experiments ran
    try:
        n_new = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM research_hypotheses WHERE status='NEW'"
            ).fetchone()["n"]
        )
        if exps and n_new > 0:
            fails.append(f"{n_new} hypotheses still NEW after experiments")
    except Exception:
        pass

    return fails


def check_report_files(reports_root: Any) -> list[str]:
    from pathlib import Path

    root = Path(reports_root)
    fails: list[str] = []
    required = [
        "feature_validation.md",
        "knowledge.md",
        "patterns.md",
        "hypotheses.md",
        "experiments.md",
    ]
    for name in required:
        p = root / name
        if not p.exists():
            fails.append(f"missing report {name}")
            continue
        text = p.read_text(encoding="utf-8")
        if len(text.strip()) < 20:
            fails.append(f"report {name} too short")
    # patterns.json
    if not (root / "patterns.json").exists():
        fails.append("missing patterns.json")
    return fails
