"""Hermes Weekly (Opus) — adversarial audit of 7 daily reports + metrics."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.hermes_schedule_v1.model_router import (
    route_llm,
)

WEEKLY_REPORT = "WEEKLY_OPUS_AUDIT.md"
OUT_DIR = BASE_DIR / "reports" / "research" / "hermes_schedule_v1"
DAILY_DIR = OUT_DIR
DAILY_ROOT = BASE_DIR / "DAILY_RESEARCH_REPORT.md"
MAX_DAILY_CHARS = 12_000


def _collect_daily_reports(*, max_days: int = 7) -> list[dict[str, str]]:
    reports: list[dict[str, str]] = []
    # Prefer dated copies if present; else latest daily report once.
    dated = sorted(OUT_DIR.glob("DAILY_RESEARCH_REPORT_*.md"))[-max_days:]
    if dated:
        for p in dated:
            text = p.read_text(encoding="utf-8")
            reports.append({"name": p.name, "text": text[:MAX_DAILY_CHARS]})
        return reports
    for p in (DAILY_ROOT, OUT_DIR / "DAILY_RESEARCH_REPORT.md"):
        if p.exists():
            reports.append({"name": p.name, "text": p.read_text(encoding="utf-8")[:MAX_DAILY_CHARS]})
            break
    return reports


def _compact_metrics(conn: Any) -> dict[str, Any]:
    """Pull compact stats only — never full lake."""
    out: dict[str, Any] = {"sources": []}
    try:
        from bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package import (
            build_research_package_v2,
            enforce_package_size_v2,
        )

        pkg = build_research_package_v2(conn, run_checks=True)
        pkg, nbytes = enforce_package_size_v2(pkg)
        out["package_bytes"] = nbytes
        out["scorecard"] = pkg.get("scorecard_inputs")
        out["reality"] = {
            k: (pkg.get("reality") or {}).get(k)
            for k in ("reality_score", "lake_rows", "dataset_version")
        }
        out["elite"] = {
            "n_elite": (pkg.get("elite") or {}).get("n_elite"),
            "categories": (pkg.get("elite") or {}).get("categories"),
        }
        out["funnel_top"] = ((pkg.get("decision_funnel") or {}).get("top_rejectors") or [])[:5]
        out["replay"] = {
            k: (pkg.get("replay_recovery") or {}).get(k)
            for k in ("recoverable_ev", "protected_ev", "net_replay_ev")
        }
        out["self_check_ok"] = bool((pkg.get("self_check") or {}).get("ok"))
        out["sources"].append("research_package_v2")
    except Exception as exc:
        out["error"] = str(exc)[:200]
    return out


def offline_weekly(metrics: dict[str, Any], dailies: list[dict[str, str]], *, reason: str) -> str:
    return "\n".join([
        "# WEEKLY_OPUS_AUDIT",
        "",
        f"mode=offline reason={reason}",
        f"daily_reports_n={len(dailies)}",
        f"metrics_keys={list(metrics.keys())}",
        "",
        "## executive conclusion",
        "- Offline weekly template. Live Opus required for adversarial depth.",
        "",
        "## strongest evidence",
        f"- scorecard={metrics.get('scorecard')}",
        "",
        "## weakest evidence",
        f"- funnel_top={metrics.get('funnel_top')}",
        "",
        "## overfitting risk",
        "- Review Book D sparsity and Elite category concentration before promoting filters.",
        "",
        "## data integrity",
        f"- self_check_ok={metrics.get('self_check_ok')}",
        "",
        "## forward validation",
        "- Consume forward report heads from package only.",
        "",
        "## mathematical weaknesses",
        "- Replay FN attribution; first-rejector vs influence mismatch.",
        "",
        "## recommended next experiments",
        "- Conditional Replay FN EV; module influence matrix; Book D leave-one-out.",
        "",
        "## experiments NOT recommended",
        "- Live Gate/Strategy/Execution changes; optimizer promotion without integrity PASS.",
        "",
        "## confidence",
        "- low (offline)",
        "",
        "opus_weekly=true research_only=true no_automatic_code_changes=true",
        "",
    ])


def run_hermes_weekly(
    conn: Any,
    *,
    offline: bool = False,
) -> dict[str, Any]:
    t0 = time.time()
    dailies = _collect_daily_reports()
    metrics = _compact_metrics(conn)
    if not dailies and not metrics.get("scorecard"):
        text = "# WEEKLY_OPUS_AUDIT\n\nINSUFFICIENT_DATA\n"
        paths = _write(text)
        return {
            "ok": False,
            "mode": "insufficient_data",
            "paths": paths,
            "elapsed_sec": round(time.time() - t0, 3),
            "terminal": "HERMES WEEKLY: INSUFFICIENT_DATA\n",
        }

    bundle = {
        "daily_reports": dailies,
        "weekly_metrics": metrics,
        "rules": {
            "research_only": True,
            "no_automatic_code_changes": True,
            "no_gate_strategy_execution_changes": True,
        },
    }
    pkg_text = json.dumps(bundle, default=str, separators=(",", ":"))
    system = (
        "You are Hermes weekly auditor (Claude Opus). "
        "Adversarial review only. Do NOT change code. "
        "Challenge DeepSeek daily conclusions. Flag overfitting. "
        "Work from compact weekly bundle only."
    )
    user = (
        "=== WEEKLY_BUNDLE ===\n"
        + pkg_text
        + "\n\nWrite WEEKLY_OPUS_AUDIT.md with sections:\n"
        "- executive conclusion\n- strongest evidence\n- weakest evidence\n"
        "- overfitting risk\n- data integrity\n- forward validation\n"
        "- mathematical weaknesses\n- recommended next experiments\n"
        "- experiments NOT recommended\n- confidence\n"
    )
    result = route_llm(
        role="weekly",
        system=system,
        user=user,
        package_text=pkg_text,
        offline=offline,
    )
    if result.mode in {"insufficient_data", "cost_guard"}:
        text = f"# WEEKLY_OPUS_AUDIT\n\n{result.text}\n"
    elif result.mode == "offline" or not result.text:
        text = offline_weekly(metrics, dailies, reason=result.error or "offline")
    else:
        text = result.text
        if not text.lstrip().startswith("#"):
            text = "# WEEKLY_OPUS_AUDIT\n\n" + text

    paths = _write(text)
    elapsed = round(time.time() - t0, 3)
    terminal = "\n".join([
        "HERMES WEEKLY (Opus) V1",
        "",
        f"elapsed={elapsed}s mode={result.mode} provider={result.provider} model={result.model}",
        f"daily_n={len(dailies)} tokens_in={result.input_tokens} cost_usd={result.cost_usd}",
        "research_only=true no_automatic_code_changes=true opus_weekly=true",
        "",
    ])
    return {
        "ok": True,
        "mode": result.mode,
        "provider": result.provider,
        "model": result.model,
        "daily_n": len(dailies),
        "estimated_input_tokens": result.input_tokens,
        "estimated_output_tokens": result.output_tokens,
        "cost_usd": result.cost_usd,
        "paths": paths,
        "elapsed_sec": elapsed,
        "terminal": terminal,
        "research_only": True,
    }


def _write(text: str) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    root = BASE_DIR / WEEKLY_REPORT
    outp = OUT_DIR / WEEKLY_REPORT
    root.write_text(text, encoding="utf-8")
    outp.write_text(text, encoding="utf-8")
    return {"root": str(root), "out_dir": str(outp)}
