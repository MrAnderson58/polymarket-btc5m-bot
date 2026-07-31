"""Part 8 — Final MATHEMATICAL_RECOVERY_REPORT orchestrator."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.math_recovery_v1.feature_audit import (
    run_feature_audit,
)
from bot.research.market_events.signal_intelligence.math_recovery_v1.feature_information import (
    run_feature_information,
)
from bot.research.market_events.signal_intelligence.math_recovery_v1.pipeline_flow import (
    run_pipeline_flow_audit,
)
from bot.research.market_events.signal_intelligence.math_recovery_v1.project_map import (
    build_project_map,
    format_project_map_md,
)

REPORT_PATH = BASE_DIR / "MATHEMATICAL_RECOVERY_REPORT.md"
REPORT_DIR = BASE_DIR / "reports" / "research"


def run_mathematical_recovery(
    conn: Any,
    *,
    write_reports: bool = True,
    feature_audit: dict[str, Any] | None = None,
    pipeline: dict[str, Any] | None = None,
    feature_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose final recovery report from audits (runs missing pieces if needed)."""
    t0 = time.time()
    fa = feature_audit or run_feature_audit(conn, write_reports=write_reports)
    pf = pipeline or run_pipeline_flow_audit(conn, write_reports=write_reports)
    fi = feature_info or run_feature_information(conn, write_reports=write_reports)
    pmap = build_project_map(feature_audit=fa)

    by_status = fa.get("by_status") or {}
    buckets = fi.get("buckets") or {}
    qa = fi.get("ml_dataset_qa") or {}
    combos = fi.get("combinations_top100") or []

    useful = buckets.get("USEFUL") or []
    weak = buckets.get("WEAK") or []
    # production set: GOOD status ∩ (USEFUL|WEAK) plus always-needed categoricals if filled
    good = set(by_status.get("GOOD") or [])
    prod = [f for f in (useful + weak) if f in good or f in useful]
    # de-dup preserve order
    seen = set()
    prod_set = []
    for f in prod + ["symbol", "direction", "market_regime", "gate_decision", "hour", "weekday"]:
        if f not in seen:
            seen.add(f)
            prod_set.append(f)

    ml_set = [f for f in (fi.get("top20") or []) if f.get("bucket") in ("USEFUL", "WEAK")]
    ml_names = [f["feature"] for f in ml_set] or list(useful)

    research_set = sorted(
        set(useful + weak + (by_status.get("GOOD") or []) + (by_status.get("LOW_VARIANCE") or []))
    )

    immediate = []
    if "rsi" in (by_status.get("EMPTY") or []) or "rsi" in (by_status.get("BROKEN") or []):
        immediate.append("Implement real RSI from candles into build_entry_features (stop hardcoding None).")
    if any(x.startswith("ema") for x in (by_status.get("EMPTY") or []) + (by_status.get("BROKEN") or [])):
        immediate.append("Persist EMA20/50/200 (or distances) from candle history into S55/Feature Store.")
    if "vwap_distance" in (by_status.get("EMPTY") or []) or "vwap_distance" in (by_status.get("BROKEN") or []):
        immediate.append("Compute and store VWAP so vwap_distance is non-null.")
    for name in ("atr", "funding", "fear_greed", "trend", "volume"):
        if name in (by_status.get("CONSTANT") or []):
            immediate.append(f"Fix {name}: currently CONSTANT placeholder — wire per-symbol live values.")
    if "confidence" in (by_status.get("EMPTY") or []) or "confidence" in (by_status.get("BROKEN") or []):
        immediate.append("Populate decision_confidence on S40/S42 — required for optimizer confidence gates.")
    for b in pf.get("bottlenecks") or []:
        immediate.append(
            f"Unblock pipeline: {b.get('from')} → {b.get('to')} drop {b.get('drop_pct')}% ({b.get('label')})."
        )
    for c in qa.get("critical") or []:
        immediate.append(c)

    critical_bugs = []
    for f in (fa.get("formula_sources") or {}).get("formulas") or []:
        if f.get("status") == "BROKEN SOURCE":
            critical_bugs.append(f"BROKEN SOURCE: {f.get('formula')} — {f.get('notes')}")
    critical_bugs.extend(qa.get("critical") or [])

    dead_collectors = [
        n for n in (pmap.get("never_read_sources") or [])
    ]
    dead_tables = []
    # Heuristic: empty candidate outcome tables etc. checked lightly
    try:
        for table, label in (
            ("market_events_signal_outcomes_f1", "F1 outcomes"),
            ("market_live_signals_g3", "live g3 signals"),
            ("ai_paper_trades_s47", "AI paper trades"),
        ):
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if int(n) == 0:
                dead_tables.append({"table": table, "label": label, "rows": 0})
    except Exception:
        pass

    md = format_recovery_report(
        {
            "feature_audit": fa,
            "pipeline": pf,
            "feature_info": fi,
            "project_map": pmap,
            "recommended_production": prod_set,
            "recommended_ml": ml_names,
            "recommended_research": research_set,
            "immediate_fixes": immediate,
            "critical_bugs": critical_bugs,
            "dead_collectors": dead_collectors,
            "dead_tables": dead_tables,
            "best_combos": combos[:15],
        }
    )

    result = {
        "ok": True,
        "generated_at": int(time.time()),
        "processing_time_ms": round((time.time() - t0) * 1000, 1),
        "report_markdown": md,
        "recommended_production_feature_set": prod_set,
        "recommended_ml_feature_set": ml_names,
        "recommended_research_feature_set": research_set,
        "immediate_fixes": immediate,
        "critical_bugs": critical_bugs,
        "read_only": True,
        "gate_trading_unchanged": True,
    }
    if write_reports:
        path = Path(os.environ.get("MATH_RECOVERY_REPORT", str(REPORT_PATH)))
        path.write_text(md, encoding="utf-8")
        result["report_path"] = str(path)
        # also project map
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "PROJECT_MAP.md").write_text(format_project_map_md(pmap), encoding="utf-8")
        (REPORT_DIR / "mathematical_recovery.json").write_text(
            json.dumps({
                "recommended_production_feature_set": prod_set,
                "recommended_ml_feature_set": ml_names,
                "recommended_research_feature_set": research_set,
                "immediate_fixes": immediate,
                "critical_bugs": critical_bugs,
                "best_combos": combos[:100],
            }, indent=2, default=str),
            encoding="utf-8",
        )
    return result


def format_recovery_report(payload: dict[str, Any]) -> str:
    fa = payload["feature_audit"]
    pf = payload["pipeline"]
    fi = payload["feature_info"]
    by_status = fa.get("by_status") or {}
    buckets = fi.get("buckets") or {}
    lines = [
        "# MATHEMATICAL_RECOVERY_REPORT",
        "",
        "_Signal Mathematics Recovery V1 — analysis only. Gate / Trading / Paper / Execution unchanged._",
        "",
        "## 1 Current pipeline",
        "",
        "```",
        "Market → Collector → Candidate → Features → Gate → S55 → S42 → Close → Research → Feature Store → ML",
        "```",
        "",
        f"- S42 closed samples: **{(fa.get('n_closed_samples'))}**",
        f"- S55 rows: **{(fa.get('n_s55_rows'))}**",
        f"- Pipeline bottlenecks: **{len(pf.get('bottlenecks') or [])}**",
        "",
    ]
    for st in pf.get("stages") or []:
        mark = " ⚠ CRITICAL BOTTLENECK" if st.get("critical") else ""
        lines.append(
            f"- {st['name']}: in={st.get('input_rows')} out={st.get('output_rows')} "
            f"window={st.get('window_rows')} drop%={st.get('drop_pct')}{mark}"
        )

    lines.extend(["", "## 2 Broken features", ""])
    for f in by_status.get("BROKEN") or []:
        lines.append(f"- `{f}`")
    if not by_status.get("BROKEN"):
        lines.append("_None classified BROKEN (check EMPTY)._")

    lines.extend(["", "## 3 Constant features", ""])
    for f in by_status.get("CONSTANT") or []:
        lines.append(f"- `{f}`")

    lines.extend(["", "## 4 Empty features", ""])
    for f in by_status.get("EMPTY") or []:
        lines.append(f"- `{f}`")

    lines.extend(["", "## 5 Dead collectors", ""])
    for n in payload.get("dead_collectors") or []:
        lines.append(f"- `{n.get('collector')}` → features `{n.get('features')}` ({n.get('dead_code_hint')})")
    if not payload.get("dead_collectors"):
        lines.append("_No fully dead collectors in static map._")

    lines.extend(["", "## 6 Dead tables", ""])
    for t in payload.get("dead_tables") or []:
        lines.append(f"- `{t.get('table')}` ({t.get('label')}) rows={t.get('rows')}")
    if not payload.get("dead_tables"):
        lines.append("_No empty known satellite tables detected (or query skipped)._")

    lines.extend(["", "## 7 Useful features", ""])
    for f in buckets.get("USEFUL") or []:
        lines.append(f"- `{f}`")
    if not buckets.get("USEFUL"):
        lines.append("_No USEFUL features under current scoring — data quality too weak._")

    lines.extend(["", "## 8 Noise features", ""])
    for f in (buckets.get("NOISE") or []) + (buckets.get("REMOVE") or []):
        lines.append(f"- `{f}`")

    lines.extend(["", "## 9 Best feature combinations", ""])
    for c in payload.get("best_combos") or []:
        lines.append(
            f"- `{' + '.join(c['features'])}` — n={c['n']} EV={c.get('ev')} "
            f"PF={c.get('pf')} WR={c.get('wr')} p={c.get('p_value')}"
        )
    if not payload.get("best_combos"):
        lines.append("_No combinations cleared minimum n._")

    lines.extend(["", "## 10 Recommended production feature set", ""])
    for f in payload.get("recommended_production") or []:
        lines.append(f"- `{f}`")

    lines.extend(["", "## 11 Recommended ML feature set", ""])
    for f in payload.get("recommended_ml") or []:
        lines.append(f"- `{f}`")

    lines.extend(["", "## 12 Recommended research feature set", ""])
    for f in payload.get("recommended_research") or []:
        lines.append(f"- `{f}`")

    lines.extend(["", "## 13 Dead code", ""])
    for h in (payload.get("project_map") or {}).get("dead_code_hints") or []:
        lines.append(f"- `{h}`")

    lines.extend(["", "## 14 Critical bugs", ""])
    for b in payload.get("critical_bugs") or []:
        lines.append(f"- {b}")

    lines.extend(["", "## 15 Immediate fixes", ""])
    for i, fix in enumerate(payload.get("immediate_fixes") or [], 1):
        lines.append(f"{i}. {fix}")
    lines.extend([
        "",
        "## Safety",
        "",
        "- Read-only diagnostics",
        "- No Gate / Trading / Paper / Execution modifications",
        "",
    ])
    return "\n".join(lines)


__all__ = ["run_mathematical_recovery"]
