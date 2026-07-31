"""Orchestrator for Alpha Validation Engine V2."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.dataset import (
    load_alpha_dataset,
)
from bot.research.market_events.signal_intelligence.alpha_validation_v2.report import (
    write_validation_artifacts,
)
from bot.research.market_events.signal_intelligence.alpha_validation_v2.rules import (
    load_candidates,
    rule_from_candidate,
)
from bot.research.market_events.signal_intelligence.alpha_validation_v2.schema import (
    ensure_alpha_validation_schema,
)
from bot.research.market_events.signal_intelligence.alpha_validation_v2.store import (
    finish_history_run,
    latest_run_id,
    load_history,
    load_validations,
    new_run_id,
    start_history_run,
    upsert_validation,
)
from bot.research.market_events.signal_intelligence.alpha_validation_v2.validation import (
    validate_candidate,
)

DEFAULT_CANDIDATES = (
    BASE_DIR / "reports" / "research" / "alpha_discovery_v1" / "alpha_candidates.json"
)


def _load_candidate_payload(path: Path | None = None) -> dict[str, Any]:
    p = path or Path(os.environ.get("ALPHA_VALIDATE_CANDIDATES", str(DEFAULT_CANDIDATES)))
    if not p.exists():
        raise FileNotFoundError(
            f"Alpha candidates not found at {p}. Run alpha-engine first."
        )
    return json.loads(p.read_text(encoding="utf-8"))


def run_alpha_validation_v2(
    conn: Any,
    *,
    candidates_path: Path | str | None = None,
    limit: int | None = None,
    write_reports: bool = True,
    persist: bool = True,
    backfill_candles: bool | None = None,
) -> dict[str, Any]:
    """Validate discovery candidates; research-only."""
    t0 = time.time()
    ensure_alpha_validation_schema(conn)
    if backfill_candles is None:
        backfill_candles = os.environ.get("ALPHA_VALIDATE_BACKFILL", "1") not in (
            "0", "false", "no",
        )
    cand_limit = limit
    if cand_limit is None:
        cand_limit = int(os.environ.get("ALPHA_VALIDATE_LIMIT", "50"))

    path = Path(candidates_path) if candidates_path else None
    payload = _load_candidate_payload(path)
    candidates = load_candidates(payload, limit=cand_limit)

    rows = load_alpha_dataset(conn, backfill_candles=bool(backfill_candles))
    run_id = new_run_id()
    if persist:
        start_history_run(
            conn, run_id=run_id, n_candidates=len(candidates), n_rows=len(rows),
        )

    results: list[dict[str, Any]] = []
    for i, cand in enumerate(candidates):
        try:
            rule = rule_from_candidate(cand)
        except Exception as exc:
            results.append({
                "rule_id": cand.get("id"),
                "rule_label": cand.get("label"),
                "features": cand.get("features") or [],
                "status": "REJECTED",
                "reject_reason": f"parse_error:{exc}",
                "n_total": len(rows),
                "n_matched": 0,
                "gates": {},
            })
            continue
        out = validate_candidate(rows, rule, seed=i % 10_000)
        results.append(out)
        if persist:
            upsert_validation(conn, run_id=run_id, result=out)

    n_passed = sum(1 for r in results if r.get("status") == "PASSED")
    n_rejected = sum(1 for r in results if r.get("status") == "REJECTED")
    n_insuf = sum(1 for r in results if r.get("status") == "INSUFFICIENT")
    paths: dict[str, str] = {}
    md = ""
    if write_reports:
        paths = write_validation_artifacts(
            run_id=run_id, n_rows=len(rows), results=results,
        )
        try:
            md = Path(paths["report_md"]).read_text(encoding="utf-8")
        except Exception:
            md = ""

    summary = {
        "run_id": run_id,
        "n_passed": n_passed,
        "n_rejected": n_rejected,
        "n_insufficient": n_insuf,
        "n_candidates": len(results),
    }
    if persist:
        finish_history_run(
            conn,
            run_id=run_id,
            n_passed=n_passed,
            n_rejected=n_rejected,
            n_insufficient=n_insuf,
            summary=summary,
            report_path=paths.get("report_md"),
        )
        try:
            conn.commit()
        except Exception:
            pass

    return {
        "ok": True,
        "run_id": run_id,
        "n_rows": len(rows),
        "n_candidates": len(results),
        "n_passed": n_passed,
        "n_rejected": n_rejected,
        "n_insufficient": n_insuf,
        "results": results,
        "passed": [r for r in results if r.get("status") == "PASSED"],
        "paths": paths,
        "report_markdown": md,
        "elapsed_sec": round(time.time() - t0, 3),
        "read_only_trading": True,
        "gate_strategy_paper_execution_unchanged": True,
    }


def run_alpha_validation_report(
    conn: Any,
    *,
    run_id: str | None = None,
    write_reports: bool = True,
) -> dict[str, Any]:
    ensure_alpha_validation_schema(conn)
    rid = run_id or latest_run_id(conn)
    hist = load_history(conn, limit=10)
    if not rid:
        md = (
            "# ALPHA_VALIDATION_REPORT\n\n"
            "_No validation runs found. Execute `alpha-validate` first._\n"
        )
        return {"ok": False, "report_markdown": md, "history": hist, "results": []}
    rows = load_validations(conn, run_id=rid)
    # Rebuild rich results from JSON columns for report
    rich = []
    for r in rows:
        pf = r.get("pf")
        if pf is not None and isinstance(pf, float) and pf == float("inf"):
            pf_out: Any = "inf"
        else:
            pf_out = pf
        item = {
            "rule_id": r.get("rule_id"),
            "rule_label": r.get("rule_label"),
            "status": r.get("status"),
            "reject_reason": r.get("reject_reason"),
            "n_matched": r.get("n_matched"),
            "winrate": r.get("winrate"),
            "expectancy": r.get("expectancy"),
            "pf": pf_out,
            "sharpe": r.get("sharpe"),
            "ci_ev": (r.get("ci_lo"), r.get("ci_hi")),
            "p_value": r.get("p_value"),
            "gates": {},
        }
        for key, col in (
            ("walk_forward", "walk_forward_json"),
            ("rolling", "rolling_json"),
            ("oos", "oos_json"),
            ("stability", "stability_json"),
        ):
            try:
                blob = json.loads(r.get(col) or "{}")
            except Exception:
                blob = {}
            item["gates"][key] = bool(blob.get("passed"))
            item[key] = blob
        rich.append(item)
    n_rows = 0
    for h in hist:
        if h.get("run_id") == rid:
            n_rows = int(h.get("n_rows") or 0)
            break
    paths = {}
    md = ""
    if write_reports:
        paths = write_validation_artifacts(run_id=rid, n_rows=n_rows, results=rich)
        md = Path(paths["report_md"]).read_text(encoding="utf-8")
    return {
        "ok": True,
        "run_id": rid,
        "results": rich,
        "history": hist,
        "paths": paths,
        "report_markdown": md,
    }


__all__ = ["run_alpha_validation_report", "run_alpha_validation_v2"]
