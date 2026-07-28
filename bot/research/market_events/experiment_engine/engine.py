"""Orchestrate Hypothesis → Experiment → Validated Knowledge."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.experiment_engine.knowledge_update import (
    update_knowledge_from_experiment,
)
from bot.research.market_events.experiment_engine.runners import (
    decide_experiment_status,
    execute_hypothesis_experiment,
    map_hypothesis_to_experiment_type,
)
from bot.research.market_events.experiment_engine.schema import (
    STATUS_FAILED,
    STATUS_RUNNING,
    ensure_experiment_engine_schema,
)
from bot.research.market_events.experiment_engine.store import (
    add_experiment_run,
    create_experiment,
    list_experiments,
    update_experiment_result,
)
from bot.research.market_events.hypothesis_engine.schema import (
    STATUS_NEW,
    STATUS_REJECTED,
    STATUS_TESTING,
    STATUS_VALIDATED,
    ensure_hypothesis_engine_schema,
)
from bot.research.market_events.hypothesis_engine.store import (
    list_evidence,
    list_hypotheses,
    record_hypothesis_history,
)
from bot.research.market_events.research_pack_01.historical import load_closed_s55_trades


def _dataset_hash(trades: list[dict[str, Any]]) -> str:
    payload = f"{len(trades)}:" + ",".join(
        f"{t.get('closed_at')}:{t.get('pnl_pct')}" for t in trades[:200]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _flatten_result(result: dict[str, Any]) -> dict[str, Any]:
    before = result.get("before") or {}
    after = result.get("after") or {}
    return {
        "dataset_size": result.get("dataset_size") or before.get("n"),
        "ev_before": result.get("ev_before", before.get("ev")),
        "ev_after": after.get("ev"),
        "pf_before": before.get("pf"),
        "pf_after": after.get("pf"),
        "wr_before": before.get("wr"),
        "wr_after": after.get("wr"),
        "delta_ev": result.get("delta_ev"),
        "delta_pf": result.get("delta_pf"),
        "delta_wr": result.get("delta_wr"),
        "p_value": result.get("p_value"),
        "confidence_interval": result.get("confidence_interval"),
        "effect_size": result.get("effect_size"),
        "mfe_after": result.get("mfe_after", after.get("mfe")),
        "mae_after": result.get("mae_after", after.get("mae")),
        "notes": result.get("notes") or result.get("error"),
        "after_n": result.get("after_n", after.get("n")),
    }


def _apply_hypothesis_status(
    conn: Any,
    hyp: dict[str, Any],
    exp_status: str,
) -> str:
    """NEW→TESTING→VALIDATED or TESTING→REJECTED from experiment outcome."""
    old = str(hyp.get("status") or STATUS_NEW)
    new = old
    if exp_status == "VALIDATED":
        new = STATUS_VALIDATED
    elif exp_status == "REJECTED":
        new = STATUS_REJECTED
    elif exp_status in ("WEAK", "FAILED"):
        new = STATUS_TESTING if old in (STATUS_NEW, STATUS_TESTING) else old
        if old == STATUS_NEW:
            new = STATUS_TESTING
    else:
        if old == STATUS_NEW:
            new = STATUS_TESTING

    if new != old:
        now = int(time.time())
        validated_at = now if new == STATUS_VALIDATED else None
        conn.execute(
            """
            UPDATE research_hypotheses SET
              status=?, last_checked_at=?,
              validated_at=CASE WHEN ? IS NOT NULL THEN ? ELSE validated_at END
            WHERE id=?
            """,
            (new, now, validated_at, validated_at, int(hyp["id"])),
        )
        record_hypothesis_history(
            conn,
            hypothesis_key=str(hyp.get("hypothesis_key") or hyp["id"]),
            field_name="status",
            old_value=old,
            new_value=new,
            note="experiment-engine",
            now=now,
        )
    else:
        conn.execute(
            "UPDATE research_hypotheses SET last_checked_at=? WHERE id=?",
            (int(time.time()), int(hyp["id"])),
        )
    return new


def run_all_experiments(
    conn: Any,
    *,
    patterns_root: Path | None = None,
    limit_trades: int = 50000,
    commit: bool = True,
) -> dict[str, Any]:
    """
    For each research hypothesis: create experiment, run historical check,
    update hypothesis status, and push Validated/Rejected into Knowledge.
    """
    ensure_hypothesis_engine_schema(conn)
    ensure_experiment_engine_schema(conn)

    hyps = list_hypotheses(conn)
    # Attach evidence; skip archived (no longer active research claims)
    bundle = []
    for h in hyps:
        if str(h.get("status") or "") == "ARCHIVED":
            continue
        item = dict(h)
        item["evidence"] = list_evidence(conn, int(h["id"]))
        bundle.append(item)

    trades = load_closed_s55_trades(conn, limit=limit_trades)
    ds_hash = _dataset_hash(trades) if trades else "empty"

    summary = {
        "n_hypotheses": len(bundle),
        "n_trades": len(trades),
        "ran": 0,
        "validated": 0,
        "rejected": 0,
        "weak": 0,
        "failed": 0,
        "knowledge_updates": 0,
        "experiments": [],
    }

    if not trades:
        summary["error"] = "no closed S55 trades"
        return summary

    if not bundle:
        summary["error"] = "no hypotheses — run hypothesis-validate first"
        return summary

    for hyp in bundle:
        exp_type = map_hypothesis_to_experiment_type(str(hyp.get("generated_from") or ""))
        exp_id = create_experiment(
            conn,
            hypothesis_id=int(hyp["id"]),
            experiment_type=exp_type,
        )
        conn.execute(
            "UPDATE research_experiments SET status=? WHERE id=?",
            (STATUS_RUNNING, exp_id),
        )
        # Move NEW → TESTING when experiment starts
        if str(hyp.get("status")) == STATUS_NEW:
            _apply_hypothesis_status(conn, hyp, "RUNNING")
            hyp["status"] = STATUS_TESTING

        t0 = time.time()
        result = execute_hypothesis_experiment(
            trades,
            hyp,
            patterns_root=patterns_root,
            conn=conn,
        )
        # Prefer runner-declared type
        if result.get("experiment_type"):
            exp_type = str(result["experiment_type"])
            conn.execute(
                "UPDATE research_experiments SET experiment_type=? WHERE id=?",
                (exp_type, exp_id),
            )

        flat = _flatten_result(result)
        status = decide_experiment_status(result)
        if result.get("error") and status != "WEAK":
            status = STATUS_FAILED

        duration_ms = int((time.time() - t0) * 1000)
        update_experiment_result(
            conn,
            exp_id,
            dataset_size=flat.get("dataset_size"),
            ev_before=flat.get("ev_before"),
            ev_after=flat.get("ev_after"),
            pf_before=flat.get("pf_before"),
            pf_after=flat.get("pf_after"),
            wr_before=flat.get("wr_before"),
            wr_after=flat.get("wr_after"),
            delta_ev=flat.get("delta_ev"),
            delta_pf=flat.get("delta_pf"),
            delta_wr=flat.get("delta_wr"),
            p_value=flat.get("p_value"),
            confidence_interval=flat.get("confidence_interval"),
            effect_size=flat.get("effect_size"),
            status=status,
            mfe_after=flat.get("mfe_after"),
            mae_after=flat.get("mae_after"),
            notes=flat.get("notes"),
        )
        add_experiment_run(
            conn,
            experiment_id=exp_id,
            dataset_hash=ds_hash,
            duration_ms=duration_ms,
            success=status not in (STATUS_FAILED,),
            notes=json.dumps(
                {
                    "status": status,
                    "delta_ev": flat.get("delta_ev"),
                    "effect_size": flat.get("effect_size"),
                    "p_value": flat.get("p_value"),
                },
                default=str,
            )[:500],
        )

        new_hyp_status = _apply_hypothesis_status(conn, hyp, status)
        exp_row = {
            "id": exp_id,
            "hypothesis_id": hyp["id"],
            "experiment_type": exp_type,
            "status": status,
            "delta_ev": flat.get("delta_ev"),
            "effect_size": flat.get("effect_size"),
            "p_value": flat.get("p_value"),
            "dataset_size": flat.get("dataset_size"),
            "ev_after": flat.get("ev_after"),
            "notes": flat.get("notes"),
        }
        # Knowledge integration for validated/rejected
        if status in ("VALIDATED", "REJECTED"):
            k = update_knowledge_from_experiment(
                conn,
                experiment=exp_row,
                hypothesis=hyp,
                result=result,
            )
            summary["knowledge_updates"] += sum(k.values())

        summary["ran"] += 1
        if status == "VALIDATED":
            summary["validated"] += 1
        elif status == "REJECTED":
            summary["rejected"] += 1
        elif status == "WEAK":
            summary["weak"] += 1
        elif status == "FAILED":
            summary["failed"] += 1
        summary["experiments"].append(
            {
                **exp_row,
                "hypothesis_status": new_hyp_status,
                "hypothesis_title": hyp.get("title"),
                "duration_ms": duration_ms,
            }
        )

    if commit:
        try:
            conn.commit()
        except Exception:
            pass
    return summary


def load_experiment_snapshot(conn: Any) -> dict[str, Any]:
    ensure_experiment_engine_schema(conn)
    experiments = list_experiments(conn)
    from bot.research.market_events.experiment_engine.store import list_recent_runs

    runs = list_recent_runs(conn, limit=40)
    validated = [e for e in experiments if e.get("status") == "VALIDATED"]
    rejected = [e for e in experiments if e.get("status") == "REJECTED"]
    running = [e for e in experiments if e.get("status") == "RUNNING"]
    by_effect = sorted(
        [e for e in experiments if e.get("effect_size") is not None],
        key=lambda e: abs(float(e.get("effect_size") or 0)),
        reverse=True,
    )
    by_reliable = sorted(
        [
            e
            for e in experiments
            if e.get("status") == "VALIDATED" and int(e.get("dataset_size") or 0) > 0
        ],
        key=lambda e: (
            abs(float(e.get("effect_size") or 0)) * (1.0 if e.get("p_value") is None else max(0.01, 1.0 - float(e.get("p_value") or 1))),
            int(e.get("dataset_size") or 0),
        ),
        reverse=True,
    )
    return {
        "experiments": experiments,
        "runs": runs,
        "validated": validated,
        "rejected": rejected,
        "running": running,
        "best_effect": by_effect[:10],
        "most_reliable": by_reliable[:10],
        "last_run": runs[0] if runs else None,
    }
