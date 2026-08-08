"""Hermes Daily (DeepSeek) — compact package only, research-only."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package import (
    PACKAGE_NAME,
    run_daily_research_package,
)
from bot.research.market_events.signal_intelligence.hermes_schedule_v1.model_router import (
    package_fingerprint,
    route_llm,
)

DAILY_REPORT = "DAILY_RESEARCH_REPORT.md"
OUT_DIR = BASE_DIR / "reports" / "research" / "hermes_schedule_v1"
STATE_PATH = OUT_DIR / "daily_state.json"

DAILY_QUESTIONS = """
Answer these 10 questions explicitly:

1. What changed?
2. What improved?
3. What worsened?
4. Which statistically significant changes?
5. Which hypotheses confirmed?
6. Which hypotheses falsified?
7. Which data errors found?
8. Which mathematical tests to run next?
9. What NOT to do?
10. Should filters change? (recommend only — NEVER change Strategy/Gate/Execution)

Rules:
- Research only. No code. No strategy mutation.
- Work ONLY from RESEARCH_PACKAGE.json statistics.
- Never request raw SQLite / full trade history.
- If data insufficient, respond exactly: INSUFFICIENT_DATA
"""


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def offline_daily_report(package: dict[str, Any], *, reason: str) -> str:
    sc = package.get("scorecard_inputs") or {}
    reality = package.get("reality") or {}
    funnel = package.get("decision_funnel") or {}
    top = (funnel.get("top_rejectors") or [{}])
    top0 = top[0] if top else {}
    return "\n".join([
        "# DAILY_RESEARCH_REPORT",
        "",
        f"mode=offline reason={reason}",
        f"date={sc.get('date')} package_bytes={package.get('package_bytes')}",
        f"reality={reality.get('reality_score')} wr={sc.get('wr')} ev={sc.get('ev')} pf={sc.get('pf')}",
        f"worst_module={sc.get('worst_module') or top0.get('module')}",
        "",
        "## 1. What changed?",
        "- Offline template: compare scorecard_inputs vs prior day when state available.",
        "",
        "## 2. What improved?",
        "- See Reality / Book B WR-PF-EV if trending up in package.",
        "",
        "## 3. What worsened?",
        f"- Dominant rejector={top0.get('module')} n={top0.get('rejected')}",
        "",
        "## 4. Statistically significant changes?",
        "- Requires DeepSeek live pass on consecutive packages.",
        "",
        "## 5–6. Hypotheses confirmed / falsified?",
        "- Deferred to live DeepSeek daily.",
        "",
        "## 7. Data errors?",
        f"- self_check={package.get('self_check')}",
        "",
        "## 8. Next mathematical tests?",
        "- Conditional Replay FN EV; influence vs first-rejector matrix; Book D sparsity.",
        "",
        "## 9. What NOT to do?",
        "- Do not change Gate / Strategy / Execution / live trading.",
        "",
        "## 10. Change filters?",
        "- No automatic filter changes. Recommend research-only experiments only.",
        "",
        "provider=deepseek_daily research_only=true no_strategy_mutation=true",
        "",
    ])


def run_hermes_daily(
    conn: Any,
    *,
    offline: bool = False,
    rebuild_package: bool = True,
) -> dict[str, Any]:
    t0 = time.time()
    if rebuild_package:
        pkg_out = run_daily_research_package(conn, write_files=True, persist=True)
        package = pkg_out.get("package") or {}
    else:
        path = BASE_DIR / PACKAGE_NAME
        package = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    if not package:
        text = "INSUFFICIENT_DATA"
        paths = _write(text)
        return {
            "ok": False,
            "mode": "insufficient_data",
            "paths": paths,
            "terminal": "HERMES DAILY: INSUFFICIENT_DATA\n",
            "elapsed_sec": round(time.time() - t0, 3),
        }

    # skip duplicate analysis if fingerprint unchanged
    fp = package_fingerprint(package)
    state = _load_state()
    if state.get("last_fingerprint") == fp and not offline:
        text = (
            "# DAILY_RESEARCH_REPORT\n\n"
            "NO_CHANGE: package fingerprint unchanged vs prior daily run. "
            "Skipped LLM to control cost.\n"
            f"fingerprint={fp}\n"
        )
        paths = _write(text)
        return {
            "ok": True,
            "mode": "skipped_unchanged",
            "fingerprint": fp,
            "paths": paths,
            "elapsed_sec": round(time.time() - t0, 3),
            "terminal": f"HERMES DAILY skipped unchanged fingerprint={fp}\n",
        }

    self_check = package.get("self_check") or {}
    if self_check and self_check.get("ok") is False:
        text = (
            "# DAILY_RESEARCH_REPORT\n\nANALYSIS STOPPED\n\n"
            f"self_check FAIL: {self_check.get('stopped_reason')}\n"
        )
        paths = _write(text)
        return {
            "ok": False,
            "mode": "aborted_self_check",
            "paths": paths,
            "elapsed_sec": round(time.time() - t0, 3),
            "terminal": "HERMES DAILY stopped: self_check FAIL\n",
        }

    pkg_text = json.dumps(package, default=str, separators=(",", ":"))
    system = (
        "You are Hermes daily researcher. Use DeepSeek. "
        "Compact RESEARCH_PACKAGE only. No strategy/gate/execution changes. "
        "Minimize cost. Never ask for raw lake/SQL."
    )
    user = f"=== RESEARCH_PACKAGE.json ===\n{pkg_text}\n\n{DAILY_QUESTIONS}"

    result = route_llm(
        role="daily",
        system=system,
        user=user,
        package_text=pkg_text,
        offline=offline,
    )

    if result.mode in {"insufficient_data", "cost_guard"}:
        text = f"# DAILY_RESEARCH_REPORT\n\n{result.text}\n"
    elif result.mode == "offline" or not result.text:
        text = offline_daily_report(package, reason=result.error or "offline")
    else:
        text = result.text
        if not text.lstrip().startswith("#"):
            text = "# DAILY_RESEARCH_REPORT\n\n" + text

    paths = _write(text)
    _save_state({
        "last_fingerprint": fp,
        "last_run_at": int(time.time()),
        "provider": result.provider,
        "model": result.model,
        "mode": result.mode,
    })
    elapsed = round(time.time() - t0, 3)
    terminal = "\n".join([
        "HERMES DAILY (DeepSeek) V1",
        "",
        f"elapsed={elapsed}s mode={result.mode} provider={result.provider} model={result.model}",
        f"tokens_in={result.input_tokens} tokens_out={result.output_tokens} cost_usd={result.cost_usd}",
        f"fingerprint={fp}",
        "research_only=true no_strategy_mutation=true deepseek_daily=true",
        "",
    ])
    return {
        "ok": True,
        "mode": result.mode,
        "provider": result.provider,
        "model": result.model,
        "fingerprint": fp,
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
    root = BASE_DIR / DAILY_REPORT
    outp = OUT_DIR / DAILY_REPORT
    root.write_text(text, encoding="utf-8")
    outp.write_text(text, encoding="utf-8")
    return {"root": str(root), "out_dir": str(outp)}
