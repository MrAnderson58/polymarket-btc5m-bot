"""Orchestrator + report writers for Market Mathematics Research V1."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.market_math_v1.dataset import (
    count_corpus,
    load_market_math_dataset,
)
from bot.research.market_events.signal_intelligence.market_math_v1.studies import (
    study_explainable_tree,
    study_feature_buckets,
    study_feature_pairs,
    study_feature_triples,
    study_gate_contribution,
    study_harmful_regimes,
    study_market_map,
    study_recommendations,
    study_symbol_rankings,
    study_top_rules,
)

REPORT_MD = BASE_DIR / "MARKET_MATHEMATICS_REPORT.md"
OUT_DIR = BASE_DIR / "reports" / "research" / "market_math"


def run_market_math_research(
    conn: Any,
    *,
    write_reports: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run studies 1–10 on the full available S42/S55 corpus."""
    t0 = time.time()
    load_stats = count_corpus(conn)
    rows = load_market_math_dataset(conn, limit=limit, print_stats=True)
    # Adaptive min_n for tiny local books
    n = len(rows)
    min_n = 8 if n < 200 else 20
    min_n_pair = 10 if n < 200 else 25
    min_n_trip = 8 if n < 200 else 20

    features = study_feature_buckets(rows, min_n=max(5, min_n // 2))
    pairs = study_feature_pairs(rows, min_n=min_n_pair)
    triples = study_feature_triples(rows, min_n=min_n_trip)
    tree = study_explainable_tree(rows, min_n=max(8, min_n))
    gate = study_gate_contribution(rows, min_n=max(5, min_n // 2))
    top_rules = study_top_rules(pairs, triples, features, tree, top_n=100)
    symbols = study_symbol_rankings(rows, min_n=max(3, min_n // 3))
    harmful = study_harmful_regimes(features, pairs, min_n=max(5, min_n // 2))
    market_map = study_market_map(features, pairs, triples, top_rules, harmful, gate)
    recommendations = study_recommendations(gate, harmful, top_rules)

    result = {
        "ok": True,
        "n_trades": n,
        "loaded_closed_trades": load_stats.get("closed_s42"),
        "loaded_s55_rows": load_stats.get("s55"),
        "matched_rows": load_stats.get("matched"),
        "load_stats": load_stats,
        "limit_note": "full available CLOSED S42 INNER JOIN S55 history (no default LIMIT)",
        "baseline": features.get("baseline"),
        "feature_statistics": features,
        "pair_statistics": pairs,
        "triple_statistics": triples,
        "decision_tree": tree,
        "gate_contribution": gate,
        "top_rules": top_rules,
        "symbol_rankings": symbols,
        "market_regimes": harmful,
        "market_map": market_map,
        "recommendations": recommendations,
        "elapsed_sec": round(time.time() - t0, 3),
        "gate_strategy_paper_optimizer_unchanged": True,
        "read_only_research": True,
    }
    paths: dict[str, str] = {}
    md = ""
    if write_reports:
        paths = write_market_math_artifacts(result)
        try:
            md = Path(paths["report_md"]).read_text(encoding="utf-8")
        except Exception:
            md = ""
    result["paths"] = paths
    result["report_markdown"] = md
    return result


def write_market_math_artifacts(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = {
        "feature_statistics.json": result.get("feature_statistics"),
        "pair_statistics.json": result.get("pair_statistics"),
        "triple_statistics.json": result.get("triple_statistics"),
        "gate_contribution.json": result.get("gate_contribution"),
        "market_regimes.json": result.get("market_regimes"),
        "top_rules.json": result.get("top_rules"),
        "symbol_rankings.json": result.get("symbol_rankings"),
        "recommendations.json": result.get("recommendations"),
        "decision_tree.json": result.get("decision_tree"),
        "market_map.json": result.get("market_map"),
    }
    paths: dict[str, str] = {}
    for name, payload in files.items():
        p = OUT_DIR / name
        p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        paths[name] = str(p)

    md = format_market_math_report(result)
    REPORT_MD.write_text(md, encoding="utf-8")
    copy = OUT_DIR / "MARKET_MATHEMATICS_REPORT.md"
    copy.write_text(md, encoding="utf-8")
    paths["report_md"] = str(REPORT_MD)
    paths["report_copy"] = str(copy)
    return paths


def format_market_math_report(result: dict[str, Any]) -> str:
    base = result.get("baseline") or {}
    top = result.get("top_rules") or []
    gate = (result.get("gate_contribution") or {}).get("filters") or []
    symbols = result.get("symbol_rankings") or []
    harmful = (result.get("market_regimes") or {}).get("regimes") or []
    rec = result.get("recommendations") or {}
    mmap = result.get("market_map") or {}
    lines = [
        "# MARKET_MATHEMATICS_REPORT",
        "",
        "_Market Mathematics Research V1 — explainable statistics only. "
        "No Gate / Strategy / Optimizer / Paper Trading changes._",
        "",
        f"- Trades analyzed: **{result.get('n_trades')}** (full available CLOSED S42×S55 history)",
        (
            "> **Note:** Local analytics DB currently exposes fewer CLOSED rows than a full ~30k "
            "production corpus. Re-run `market-math-research` on the host with the complete book."
            if int(result.get("n_trades") or 0) < 1000
            else "> Full corpus mode (n≥1000)."
        ),
        f"- Baseline EV={base.get('expectancy')} PF={base.get('profit_factor')} "
        f"WR={base.get('winrate')} Sharpe={base.get('sharpe')}",
        f"- Elapsed: {result.get('elapsed_sec')}s",
        "",
        "## TOP rules",
        "",
        "| # | Rule | n | WR | EV | PF | CI95 | conf |",
        "|---:|---|---:|---:|---:|---:|---|---:|",
    ]
    for i, r in enumerate(top[:25], 1):
        lines.append(
            f"| {i} | `{str(r.get('rule'))[:70]}` | {r.get('n')} | {r.get('winrate')} | "
            f"{r.get('expectancy')} | {r.get('profit_factor')} | {r.get('ci95')} | {r.get('confidence')} |"
        )
    lines.extend(["", "## Gate filter contribution", ""])
    for f in gate:
        lines.append(
            f"- **{f.get('filter')}**: {f.get('status')} "
            f"(ΔEV={f.get('delta_ev')}, ΔPF={f.get('delta_pf')}, rule=`{f.get('good_rule')}`)"
        )
    lines.extend(["", "## Symbol grades", ""])
    for s in symbols[:20]:
        lines.append(
            f"- `{s.get('symbol')}` [{s.get('grade')}] n={s.get('n')} "
            f"EV={s.get('expectancy')} PF={s.get('profit_factor')} WR={s.get('winrate')}"
        )
    lines.extend(["", "## Harmful regimes (BLOCK candidates)", ""])
    for h in harmful[:15]:
        lines.append(
            f"- `{h.get('rule')}` n={h.get('n')} EV={h.get('expectancy')} PF={h.get('profit_factor')}"
        )
    lines.extend([
        "",
        "## Market map",
        "",
        f"- Stable/useful features: {', '.join(mmap.get('most_stable_features') or []) or '—'}",
        f"- Useless/harmful features: {', '.join(mmap.get('most_useless_features') or []) or '—'}",
        f"- Significant triples kept: {mmap.get('significant_triples')}",
        "",
        "## Recommendations (observe-only)",
        "",
        f"- Can remove: {len(rec.get('can_remove') or [])}",
        f"- Can strengthen: {len(rec.get('can_strengthen') or [])}",
        f"- Can weaken: {len(rec.get('can_weaken') or [])}",
        f"- Need check: {len(rec.get('need_check') or [])}",
        "",
    ])
    for section, key in (
        ("Can remove", "can_remove"),
        ("Can strengthen", "can_strengthen"),
        ("Can weaken", "can_weaken"),
    ):
        lines.append(f"### {section}")
        items = rec.get(key) or []
        if not items:
            lines.append("_none_")
        for it in items[:12]:
            lines.append(f"- `{it.get('filter')}`: {it.get('reason')}")
        lines.append("")
    lines.extend([
        "## Safety",
        "",
        "- Research-only module",
        "- Artifacts under `reports/research/market_math/`",
        "- Gate / Strategy / Optimizer / Paper Trading unchanged",
        "",
    ])
    return "\n".join(lines)


def run_market_math_report(*, write_reports: bool = True) -> dict[str, Any]:
    """Reload artifacts from disk into a markdown report."""
    if not OUT_DIR.exists():
        md = (
            "# MARKET_MATHEMATICS_REPORT\n\n"
            "_No artifacts found. Run `market-math-research` first._\n"
        )
        if write_reports:
            REPORT_MD.write_text(md, encoding="utf-8")
        return {"ok": False, "report_markdown": md, "paths": {}}
    payload = {
        "n_trades": None,
        "baseline": None,
        "feature_statistics": _read_json(OUT_DIR / "feature_statistics.json"),
        "pair_statistics": _read_json(OUT_DIR / "pair_statistics.json"),
        "triple_statistics": _read_json(OUT_DIR / "triple_statistics.json"),
        "gate_contribution": _read_json(OUT_DIR / "gate_contribution.json"),
        "market_regimes": _read_json(OUT_DIR / "market_regimes.json"),
        "top_rules": _read_json(OUT_DIR / "top_rules.json") or [],
        "symbol_rankings": _read_json(OUT_DIR / "symbol_rankings.json") or [],
        "recommendations": _read_json(OUT_DIR / "recommendations.json") or {},
        "market_map": _read_json(OUT_DIR / "market_map.json") or {},
        "decision_tree": _read_json(OUT_DIR / "decision_tree.json"),
        "elapsed_sec": None,
    }
    feat = payload.get("feature_statistics") or {}
    payload["baseline"] = feat.get("baseline")
    # infer n from baseline
    if payload["baseline"]:
        payload["n_trades"] = payload["baseline"].get("n")
    md = format_market_math_report(payload)
    paths = {}
    if write_reports:
        REPORT_MD.write_text(md, encoding="utf-8")
        (OUT_DIR / "MARKET_MATHEMATICS_REPORT.md").write_text(md, encoding="utf-8")
        paths = {"report_md": str(REPORT_MD)}
    return {"ok": True, "report_markdown": md, "paths": paths, "n_trades": payload.get("n_trades")}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


__all__ = [
    "OUT_DIR",
    "REPORT_MD",
    "format_market_math_report",
    "run_market_math_report",
    "run_market_math_research",
    "write_market_math_artifacts",
]
