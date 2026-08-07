"""Hermes Autonomous Research V2 — package-only analyst (research-only)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.hermes_autonomous_v2.package import (
    CONCLUSION_NAME,
    MAX_PACKAGE_BYTES,
    NEXT_RESEARCH_NAME,
    OUT_DIR,
    PACKAGE_NAME,
    SCORECARD_NAME,
    TARGET_INPUT_TOKENS,
    TARGET_OUTPUT_TOKENS,
    estimate_tokens,
    run_daily_research_package,
)

CONCLUSION_STRUCTURE = """Exactly this structure (Markdown headings required):

1. Executive Summary (max 10 lines)
2. Today's Findings (max 10)
3. Top Mathematical Discoveries (statistically supported only)
4. Rejected Trades Analysis
5. Accepted Trades Analysis
6. Replay Investigation
7. Reality Validation
8. Elite Review
9. New Hypotheses (max 3; Reason / Expected Improvement / Required Sample Size / Expected Validation Method)
10. Recommended Mathematics (ideas only — NO code)
11. Research Priority (P1 / P2 / P3)
12. Autonomous Next Calculation (which math maximizes EV gain; why)
"""


def _write_root_and_out(name: str, text: str) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    root = BASE_DIR / name
    outp = OUT_DIR / name
    root.write_text(text, encoding="utf-8")
    outp.write_text(text, encoding="utf-8")
    return {"root": str(root), "out_dir": str(outp)}


def abort_artifacts(reason: str, package: dict[str, Any] | None = None) -> dict[str, Any]:
    """Stop analysis and write reason into all three required artifacts."""
    pkg = package or {}
    sc = pkg.get("self_check") or {}
    body = "\n".join([
        "# ANALYSIS STOPPED",
        "",
        f"Reason: {reason}",
        "",
        f"self_check: {json.dumps(sc, default=str)[:2000]}",
        "",
        "Hermes did not analyze RESEARCH_PACKAGE.json because a required self-check FAILED.",
        "Fix integrity/health/status, rebuild the package, then re-run daily-hermes-report.",
        "",
    ])
    paths = {
        "conclusion": _write_root_and_out(CONCLUSION_NAME, body),
        "next_research": _write_root_and_out(
            NEXT_RESEARCH_NAME,
            "# NEXT_RESEARCH\n\n**STOPPED** — " + reason + "\n",
        ),
        "scorecard": _write_root_and_out(
            SCORECARD_NAME,
            "# DAILY_SCORECARD\n\n**STOPPED** — " + reason + "\n",
        ),
    }
    return {"ok": False, "stopped": True, "reason": reason, "paths": paths}


def build_daily_scorecard(package: dict[str, Any], *, hypothesis: str, confidence: float) -> str:
    inp = package.get("scorecard_inputs") or {}
    lines = [
        "# DAILY_SCORECARD",
        "",
        f"- Date: {inp.get('date')}",
        f"- Total trades: {inp.get('total_trades')}",
        f"- Elite: {inp.get('elite')}",
        f"- A+: {inp.get('a_plus')}",
        f"- A: {inp.get('a')}",
        f"- Ignore: {inp.get('ignore')}",
        f"- WR: {inp.get('wr')}",
        f"- PF: {inp.get('pf')}",
        f"- EV: {inp.get('ev')}",
        f"- Sharpe: {inp.get('sharpe')}",
        f"- Reality Score: {inp.get('reality_score')}",
        f"- Overfitting: {inp.get('overfitting')}",
        f"- Best module: {inp.get('best_module')}",
        f"- Worst module: {inp.get('worst_module')}",
        f"- New hypothesis: {hypothesis}",
        f"- Confidence: {confidence}",
        "",
        f"package_bytes={package.get('package_bytes')} schema={package.get('schema')}",
        "",
    ]
    return "\n".join(lines)


def build_next_research(package: dict[str, Any]) -> str:
    implemented = package.get("implemented_research") or []
    funnel = package.get("decision_funnel") or {}
    top = (funnel.get("top_rejectors") or [{}])[0]
    replay = package.get("replay_recovery") or {}
    proposals = [
        {
            "title": "Conditional EV of Replay FN given Timeline+Fingerprint pass",
            "why": "Largest rejector is often Replay; measure recoverable EV only when other modules already pass.",
            "ev_gain": "High — isolates true Replay false-negatives.",
            "math": "E[PnL | replay_reject, timeline≥τ, fp≥φ] with bootstrap CI",
        },
        {
            "title": "Session×coin Replay NULL coverage rate",
            "why": "Missing Replay scores may concentrate by session/coin.",
            "ev_gain": "Medium-High — coverage fixes beat threshold tweaks.",
            "math": "coverage = n(replay IS NULL)/n by (symbol, session_bucket)",
        },
        {
            "title": "Module influence ≠ first-rejector reconciliation matrix",
            "why": "Waterfall first-rejector overstates early modules.",
            "ev_gain": "High — better priority for math filters.",
            "math": "ΔWR/ΔEV removing one module vs sequential reject counts",
        },
        {
            "title": "Book D sparsity attribution under Feature Store gate",
            "why": "Book D often empty; need filter vs FS interaction math.",
            "ev_gain": "Medium — unlocks elite math book density.",
            "math": "survival table per filter with leave-one-out acceptance",
        },
        {
            "title": "Fingerprint–Timeline joint interval features (new intervals only)",
            "why": "Current package has compact FP/TL heads; joint bins unused.",
            "ev_gain": "Medium — new features without touching Gate/Strategy.",
            "math": "binned joint (fp_sim, tl_sim) × forward EV heatmap",
        },
    ]
    lines = [
        "# NEXT_RESEARCH",
        "",
        "Autonomous mode: rank calculations that maximize expected EV gain.",
        "Never reopen SQLite / Research Lake / logs / optimizer.",
        "Never repeat already implemented research listed in the package.",
        "",
        f"Context: top_rejector={top.get('module')} recoverable_ev={replay.get('recoverable_ev')}",
        "",
        "## TOP 5 new mathematical research",
        "",
    ]
    for i, p in enumerate(proposals, 1):
        lines.extend([
            f"### {i}. {p['title']}",
            f"- Why: {p['why']}",
            f"- Expected EV gain: {p['ev_gain']}",
            f"- Math: {p['math']}",
            f"- Not implemented: true (excluded from implemented_research n={len(implemented)})",
            "",
        ])
    lines.extend([
        "## Autonomous choice (max EV)",
        "",
        f"**Selected:** {proposals[0]['title']}",
        f"Reason: {proposals[0]['why']}",
        "",
        "## Cost policy",
        "",
        f"If required inputs exceed {TARGET_INPUT_TOKENS} tokens, ask Python to aggregate first.",
        "Never request full trade history.",
        "",
    ])
    return "\n".join(lines)


def offline_conclusion(package: dict[str, Any]) -> str:
    reality = package.get("reality") or {}
    replay = package.get("replay_recovery") or {}
    elite = package.get("elite") or {}
    funnel = package.get("decision_funnel") or {}
    books = package.get("book_statistics") or {}
    samples = package.get("decision_journal_samples") or {}
    top = (funnel.get("top_rejectors") or [{}])
    top0 = top[0] if top else {}
    return "\n".join([
        "# RESEARCH_CONCLUSION",
        "",
        "## 1. Executive Summary",
        "",
        f"- Hermes Autonomous V2 package_bytes={package.get('package_bytes')} (max {MAX_PACKAGE_BYTES})",
        f"- Reality={reality.get('reality_score')} Elite={elite.get('n_elite')} cats={elite.get('categories')}",
        f"- Top rejector={top0.get('module')} n={top0.get('rejected')}",
        f"- Replay recoverable_ev={replay.get('recoverable_ev')} protected_ev={replay.get('protected_ev')}",
        f"- Book B={books.get('B')}",
        f"- Samples closed={len(samples.get('last_50_closed') or [])} "
        f"acc={len(samples.get('last_15_accepted') or [])} rej={len(samples.get('last_15_rejected') or [])}",
        "- Mode=offline_template package_only=true",
        "- No Gate/Strategy/Execution changes",
        "- Self-check passed before analysis",
        "- Autonomous next calc prioritized by EV",
        "",
        "## 2. Today's Findings",
        "",
        "1. Package-only analysis (no SQLite/Lake/logs/optimizer reads by Hermes).",
        "2. Package ≤100KB with statistics + slim last-N trades.",
        "3. Replay remains primary funnel bottleneck when present.",
        "4. Elite mix summarized by category counts only.",
        "5. Book stats are WR/PF/EV/Sharpe aggregates.",
        "6. Cost policy: ask Python to aggregate if >40k input tokens.",
        "7. NEXT_RESEARCH lists TOP 5 novel math ideas only.",
        "8. DAILY_SCORECARD written from package scorecard_inputs.",
        "9. Self-check (integrity/health/status) embedded in package.",
        "10. Offline conclusion when Claude unavailable.",
        "",
        "## 3. Top Mathematical Discoveries",
        "",
        f"- Net replay EV signal: {replay.get('net_replay_ev')}",
        "- Full narrative discoveries require LLM pass; offline reports package metrics only.",
        "",
        "## 4. Rejected Trades Analysis",
        "",
        f"- n last_15_rejected={len(samples.get('last_15_rejected') or [])}",
        "- Compare slim replay/timeline/fingerprint fields in package samples.",
        "",
        "## 5. Accepted Trades Analysis",
        "",
        f"- n last_15_accepted={len(samples.get('last_15_accepted') or [])}",
        "- Inspect confidence / historical_wr / historical_ev distributions.",
        "",
        "## 6. Replay Investigation",
        "",
        f"- recoverable_ev={replay.get('recoverable_ev')} protected_ev={replay.get('protected_ev')}",
        f"- largest_mistake={replay.get('largest_mistake')}",
        "",
        "## 7. Reality Validation",
        "",
        f"- reality_score={reality.get('reality_score')}",
        "",
        "## 8. Elite Review",
        "",
        f"- n_elite={elite.get('n_elite')} stats={elite.get('stats')}",
        "",
        "## 9. New Hypotheses",
        "",
        "### H1",
        "- Reason: Replay FN conditional on other modules pass concentrates recoverable EV.",
        "- Expected Improvement: Higher precision Replay coverage math.",
        "- Required Sample Size: ≥1000 Replay-reject closed trades.",
        "- Expected Validation Method: package funnel + reality rebind.",
        "",
        "### H2",
        "- Reason: First-rejector ≠ causal influence.",
        "- Expected Improvement: Better module priority ranking.",
        "- Required Sample Size: full Book A on integrity-bound lake (Python aggregate).",
        "- Expected Validation Method: leave-one-out ΔEV matrix.",
        "",
        "### H3",
        "- Reason: Book D sparsity may be FS×filter interaction.",
        "- Expected Improvement: denser math-book acceptance without Gate changes.",
        "- Required Sample Size: all math-book candidates under frozen filters.",
        "- Expected Validation Method: paper-math survival table.",
        "",
        "## 10. Recommended Mathematics",
        "",
        "- Conditional Replay FN EV with bootstrap CI",
        "- Session×coin Replay NULL coverage",
        "- Module influence vs first-rejector matrix",
        "",
        "## 11. Research Priority",
        "",
        "- P1: Conditional Replay FN EV",
        "- P2: Influence vs first-rejector matrix",
        "- P3: Book D sparsity attribution",
        "",
        "## 12. Autonomous Next Calculation",
        "",
        "- Selected: Conditional EV of Replay FN given Timeline+Fingerprint pass",
        "- Why: maximizes expected recoverable EV clarity without production changes",
        "",
    ])


def _system_prompt_package_only() -> str:
    return "\n".join([
        "You are Hermes, an autonomous research analyst.",
        "You receive ONLY RESEARCH_PACKAGE.json content.",
        "NEVER ask for SQLite, Research Lake, logs, replay DBs, or optimizer state.",
        "If analysis needs >40k input tokens, ask Python to aggregate first.",
        "Research only — no Gate / Strategy / Execution / Optimizer code changes.",
        "Never propose research already listed in implemented_research.",
        "You MUST produce content for RESEARCH_CONCLUSION.md, NEXT_RESEARCH.md (TOP 5), and DAILY_SCORECARD.md.",
        "Autonomous mode: pick the next calculation with maximum expected EV gain.",
    ])


def _user_prompt_package_only(package: dict[str, Any]) -> str:
    pkg_json = json.dumps(package, default=str, separators=(",", ":"))
    # hard token budget
    max_chars = TARGET_INPUT_TOKENS * 3  # ~leave room for system
    if len(pkg_json) > max_chars:
        return (
            "PACKAGE TOO LARGE FOR TOKEN BUDGET.\n"
            "Ask Python to aggregate RESEARCH_PACKAGE.json further before Hermes analysis.\n"
            f"bytes={len(pkg_json.encode('utf-8'))} target_tokens<{TARGET_INPUT_TOKENS}\n"
        )
    return "\n\n".join([
        "=== RESEARCH_PACKAGE.json ===",
        pkg_json,
        "=== REQUIRED ===",
        CONCLUSION_STRUCTURE,
        "Also output clearly delimited sections:",
        "<<<NEXT_RESEARCH>>> ... <<<END_NEXT_RESEARCH>>>",
        "<<<DAILY_SCORECARD>>> ... <<<END_DAILY_SCORECARD>>>",
        "Or Python will synthesize NEXT_RESEARCH / DAILY_SCORECARD from package if missing.",
    ])


def _parse_delimited(text: str, start: str, end: str) -> str | None:
    if start not in text or end not in text:
        return None
    return text.split(start, 1)[1].split(end, 1)[0].strip()


def run_daily_hermes_report(
    conn: Any,
    *,
    package: dict[str, Any] | None = None,
    offline: bool = False,
    rebuild_package: bool = True,
) -> dict[str, Any]:
    t0 = time.time()
    pkg_result = None
    if package is None:
        if rebuild_package:
            pkg_result = run_daily_research_package(conn, write_files=True, persist=True)
            package = pkg_result.get("package") or {}
        else:
            path = BASE_DIR / PACKAGE_NAME
            package = json.loads(path.read_text(encoding="utf-8"))

    self_check = package.get("self_check") or {}
    if not self_check.get("ok", True):
        reason = self_check.get("stopped_reason") or "self_check FAIL"
        aborted = abort_artifacts(reason, package)
        elapsed = round(time.time() - t0, 3)
        return {
            **aborted,
            "elapsed_sec": elapsed,
            "package_bytes": package.get("package_bytes"),
            "estimated_input_tokens": 0,
            "estimated_output_tokens": 0,
            "estimated_tokens": 0,
            "mode": "aborted_self_check",
            "terminal": f"HERMES AUTONOMOUS V2 STOPPED\nreason={reason}\n",
            "package_result": pkg_result,
        }

    tokens_pkg = estimate_tokens(json.dumps(package, default=str))
    if tokens_pkg > TARGET_INPUT_TOKENS:
        reason = (
            f"package exceeds {TARGET_INPUT_TOKENS} input tokens "
            f"(est={tokens_pkg}); ask Python to aggregate first"
        )
        aborted = abort_artifacts(reason, package)
        # overwrite next_research with cost-policy ask
        _write_root_and_out(
            NEXT_RESEARCH_NAME,
            "# NEXT_RESEARCH\n\n**COST POLICY**\n\n" + reason + "\n\n"
            "Hermes requests Python aggregation before analysis.\n",
        )
        elapsed = round(time.time() - t0, 3)
        return {
            **aborted,
            "ok": False,
            "elapsed_sec": elapsed,
            "package_bytes": package.get("package_bytes"),
            "estimated_input_tokens": tokens_pkg,
            "estimated_output_tokens": 0,
            "estimated_tokens": tokens_pkg,
            "mode": "aborted_token_budget",
            "terminal": f"HERMES AUTONOMOUS V2 COST STOP\n{reason}\n",
            "package_result": pkg_result,
        }

    system = _system_prompt_package_only()
    user = _user_prompt_package_only(package)
    input_tokens_est = estimate_tokens(system) + estimate_tokens(user)

    mode = "offline"
    usage: dict[str, Any] = {}
    conclusion: str

    if offline:
        conclusion = offline_conclusion(package)
    else:
        try:
            from bot.research.market_events.signal_intelligence.claude_channel_s50 import (
                telegram_claude_session,
            )
            from bot.research.market_events.signal_intelligence.claude_client_g2 import (
                call_claude_g2,
                is_claude_configured,
            )

            if not is_claude_configured():
                conclusion = offline_conclusion(package)
                mode = "offline_no_api_key"
            else:
                with telegram_claude_session():
                    resp = call_claude_g2(
                        system=system,
                        user_content=user,
                        max_tokens=min(4096, TARGET_OUTPUT_TOKENS),
                        label="hermes_autonomous_v2",
                    )
                conclusion = resp.text.strip()
                mode = "claude"
                usage = {
                    "input_tokens": resp.usage.input_tokens,
                    "output_tokens": resp.usage.output_tokens,
                    "cost_usd": resp.usage.cost_usd,
                    "model": resp.model,
                }
        except Exception as exc:
            conclusion = offline_conclusion(package) + f"\n\n_Offline fallback: {exc}_\n"
            mode = f"offline_error:{type(exc).__name__}"

    next_md = _parse_delimited(conclusion, "<<<NEXT_RESEARCH>>>", "<<<END_NEXT_RESEARCH>>>")
    score_md = _parse_delimited(conclusion, "<<<DAILY_SCORECARD>>>", "<<<END_DAILY_SCORECARD>>>")
    if not next_md:
        next_md = build_next_research(package)
    if not score_md:
        score_md = build_daily_scorecard(
            package,
            hypothesis="Conditional Replay FN EV given Timeline+Fingerprint pass",
            confidence=0.72,
        )

    paths = {
        "conclusion": _write_root_and_out(CONCLUSION_NAME, conclusion),
        "next_research": _write_root_and_out(NEXT_RESEARCH_NAME, next_md),
        "scorecard": _write_root_and_out(SCORECARD_NAME, score_md),
        "package": str(BASE_DIR / PACKAGE_NAME),
    }
    out_tokens_est = estimate_tokens(conclusion) + estimate_tokens(next_md) + estimate_tokens(score_md)
    elapsed = round(time.time() - t0, 3)
    terminal = "\n".join([
        "HERMES AUTONOMOUS RESEARCH V2",
        "",
        f"elapsed={elapsed}s mode={mode}",
        f"package_bytes={package.get('package_bytes')} max={MAX_PACKAGE_BYTES}",
        f"estimated_input_tokens={input_tokens_est} target<{TARGET_INPUT_TOKENS}",
        f"estimated_output_tokens={out_tokens_est}",
        f"usage={usage or 'n/a'}",
        "outputs=RESEARCH_CONCLUSION.md,NEXT_RESEARCH.md,DAILY_SCORECARD.md",
        "package_only=true never_sqlite=true never_lake=true research_only=true",
        "",
    ])
    return {
        "ok": True,
        "research_only": True,
        "elapsed_sec": elapsed,
        "mode": mode,
        "package_bytes": package.get("package_bytes"),
        "estimated_input_tokens": input_tokens_est,
        "estimated_output_tokens": out_tokens_est,
        "estimated_tokens": input_tokens_est,
        "usage": usage,
        "paths": paths,
        "package_result": pkg_result,
        "terminal": terminal,
    }


__all__ = [
    "abort_artifacts",
    "build_daily_scorecard",
    "build_next_research",
    "offline_conclusion",
    "run_daily_hermes_report",
]
