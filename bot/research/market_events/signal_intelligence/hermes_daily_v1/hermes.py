"""Hermes conclusion generator — package + docs only (research-only)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.hermes_daily_v1.package import (
    CONCLUSION_NAME,
    OUT_DIR,
    PACKAGE_NAME,
    TARGET_OUTPUT_TOKENS,
    estimate_tokens,
    load_docs_for_hermes,
    run_daily_research_package,
)

CONCLUSION_STRUCTURE = """Exactly this structure (Markdown headings required):

1. Executive Summary
(max 10 lines)

2. Today's Findings
maximum 10 findings

3. Top Mathematical Discoveries
Only statistically supported.

4. Rejected Trades Analysis
Why?

5. Accepted Trades Analysis
Why?

6. Replay Investigation

7. Reality Validation

8. Elite Review

9. New Hypotheses
Maximum 3
Each must include:
- Reason
- Expected Improvement
- Required Sample Size
- Expected Validation Method

10. Recommended Mathematics
ONLY new mathematical calculations that Python should implement.
NO code. Only ideas.

11. Research Priority
Rank P1, P2, P3
"""


def _system_prompt(docs: dict[str, str]) -> str:
    hermes = docs.get("HERMES_SYSTEM_PROMPT.md") or ""
    rules = docs.get("RESEARCH_RULES.md") or ""
    return "\n\n".join([
        "You are Hermes, the research analyst for this trading research lab.",
        "You receive ONLY a compact RESEARCH_PACKAGE.json and project docs.",
        "You MUST NEVER ask for raw trade history, all trades, or full SQL tables.",
        "Work only from the provided package statistics.",
        "Research only — no production / Gate / Strategy / Execution / Optimizer changes.",
        "",
        "=== HERMES_SYSTEM_PROMPT ===",
        hermes,
        "",
        "=== RESEARCH_RULES ===",
        rules,
        "",
        "Output must follow the required RESEARCH_CONCLUSION structure exactly.",
    ])


def _user_prompt(package: dict[str, Any], docs: dict[str, str]) -> str:
    state = docs.get("PROJECT_STATE.md") or ""
    arch = docs.get("ARCHITECTURE.md") or ""
    pkg_json = json.dumps(package, indent=2, default=str)
    # hard cap package text in prompt (~35k tokens budget leaving room for docs)
    max_pkg_chars = 120_000
    if len(pkg_json) > max_pkg_chars:
        pkg_json = pkg_json[:max_pkg_chars] + "\n…[package truncated for token budget]"
    return "\n\n".join([
        "=== PROJECT_STATE ===",
        state,
        "",
        "=== ARCHITECTURE ===",
        arch,
        "",
        "=== RESEARCH_PACKAGE.json ===",
        pkg_json,
        "",
        "=== REQUIRED OUTPUT ===",
        CONCLUSION_STRUCTURE,
        "",
        "Write RESEARCH_CONCLUSION.md content now.",
    ])


def offline_conclusion(package: dict[str, Any]) -> str:
    """Deterministic template when Claude is unavailable — still valid structure."""
    reality = package.get("reality") or {}
    replay = package.get("replay_recovery") or {}
    elite = package.get("elite") or {}
    funnel = package.get("decision_funnel") or {}
    books = package.get("book_statistics") or {}
    samples = package.get("decision_journal_samples") or {}
    top = (funnel.get("top_rejectors") or [{}])
    top0 = top[0] if top else {}

    lines = [
        "# RESEARCH_CONCLUSION",
        "",
        "## 1. Executive Summary",
        "",
        f"- Package generated_at={package.get('generated_at')} bytes={package.get('package_bytes')}",
        f"- Reality score={reality.get('reality_score')} lake_rows={reality.get('lake_rows')}",
        f"- Elite n={elite.get('n_elite')} categories={elite.get('categories')}",
        f"- Replay recoverable_ev={replay.get('recoverable_ev')} protected_ev={replay.get('protected_ev')}",
        f"- Top funnel rejector={top0.get('module')} n={top0.get('rejected')}",
        f"- Book B stats={books.get('B')}",
        f"- Samples closed={len(samples.get('last_100_closed') or [])} "
        f"accepted={len(samples.get('last_30_accepted') or [])} "
        f"rejected={len(samples.get('last_30_rejected') or [])}",
        "- Mode=offline_template (Claude not invoked)",
        "- research_only=true no_strategy_change=true",
        "",
        "## 2. Today's Findings",
        "",
        "1. Compact package built without reading the full Research Lake.",
        "2. Replay remains the dominant first-rejector when funnel stats are present.",
        "3. Reality binding is consumed as a scalar score + dataset meta only.",
        "4. Elite corpus size and category mix are summarized statistically.",
        "5. Book statistics are aggregates (WR/PF/EV/Sharpe), not raw rows.",
        "6. Last-N accepted/rejected samples are slim fields only.",
        "7. Forward/morning content included as truncated report heads when available.",
        "8. Fingerprint/Timeline included as compact summaries / report heads.",
        "9. Token budget targets: <50k input, <10k output.",
        "10. Offline conclusion used when Claude API is blocked or unconfigured.",
        "",
        "## 3. Top Mathematical Discoveries",
        "",
        "- Discoveries require Hermes LLM pass for narrative; offline mode reports only package metrics.",
        f"- Net Replay EV (protected−recoverable) if present: {replay.get('net_replay_ev')}",
        "",
        "## 4. Rejected Trades Analysis",
        "",
        f"- n last_30_rejected={len(samples.get('last_30_rejected') or [])}",
        "- Inspect slim module scores (replay/timeline/fingerprint/brain) in package samples.",
        "",
        "## 5. Accepted Trades Analysis",
        "",
        f"- n last_30_accepted={len(samples.get('last_30_accepted') or [])}",
        "- Compare confidence / historical_wr / historical_ev distributions in package.",
        "",
        "## 6. Replay Investigation",
        "",
        f"- recoverable_ev={replay.get('recoverable_ev')}",
        f"- protected_ev={replay.get('protected_ev')}",
        f"- largest_mistake={(replay.get('largest_mistake') if isinstance(replay.get('largest_mistake'), dict) else replay.get('largest_mistake'))}",
        "",
        "## 7. Reality Validation",
        "",
        f"- reality_score={reality.get('reality_score')}",
        f"- dataset hash/version/rows bound in package.reality",
        "",
        "## 8. Elite Review",
        "",
        f"- n_elite={elite.get('n_elite')} stats={elite.get('stats')}",
        "",
        "## 9. New Hypotheses",
        "",
        "### H1",
        "- Reason: Replay missing scores drive FN recoverable EV.",
        "- Expected Improvement: Higher attribution clarity for Replay coverage.",
        "- Required Sample Size: ≥1000 Replay-missing closed trades.",
        "- Expected Validation Method: Replay Recovery + Reality rebind on same lake hash.",
        "",
        "### H2",
        "- Reason: Funnel first-rejector ≠ sole causal module.",
        "- Expected Improvement: Better module influence vs waterfall interpretation.",
        "- Required Sample Size: full Book A corpus on integrity-bound lake.",
        "- Expected Validation Method: Decision Funnel module_influence deltas.",
        "",
        "### H3",
        "- Reason: Book D sparsity may be Feature Store / filter interaction.",
        "- Expected Improvement: Clearer math-book bottleneck ranking.",
        "- Required Sample Size: all math-book candidates under frozen filters.",
        "- Expected Validation Method: paper-math-report + integrity Book D gate.",
        "",
        "## 10. Recommended Mathematics",
        "",
        "- Coverage rate of Replay NULL vs below-floor rejects by coin/session.",
        "- Conditional EV of Replay FN given Timeline/Fingerprint pass.",
        "- Bootstrap CI on recoverable_ev and protected_ev.",
        "",
        "## 11. Research Priority",
        "",
        "- P1: Replay missing-score coverage mathematics",
        "- P2: Funnel influence vs first-rejector reconciliation",
        "- P3: Book D sparsity attribution under Feature Store gate",
        "",
    ]
    return "\n".join(lines)


def write_conclusion(text: str) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    root = BASE_DIR / CONCLUSION_NAME
    outp = OUT_DIR / CONCLUSION_NAME
    root.write_text(text, encoding="utf-8")
    outp.write_text(text, encoding="utf-8")
    return {"root": str(root), "out_dir": str(outp)}


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

    docs = load_docs_for_hermes()
    system = _system_prompt(docs)
    user = _user_prompt(package, docs)
    input_tokens_est = estimate_tokens(system) + estimate_tokens(user)

    mode = "offline"
    usage: dict[str, Any] = {}
    text: str

    if offline:
        text = offline_conclusion(package)
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
                text = offline_conclusion(package)
                mode = "offline_no_api_key"
            else:
                with telegram_claude_session():
                    resp = call_claude_g2(
                        system=system,
                        user_content=user,
                        max_tokens=min(4096, TARGET_OUTPUT_TOKENS),
                        label="hermes_daily",
                    )
                text = resp.text.strip()
                mode = "claude"
                usage = {
                    "input_tokens": resp.usage.input_tokens,
                    "output_tokens": resp.usage.output_tokens,
                    "cost_usd": resp.usage.cost_usd,
                    "model": resp.model,
                }
        except Exception as exc:
            text = offline_conclusion(package) + f"\n\n_Offline fallback reason: {exc}_\n"
            mode = f"offline_error:{type(exc).__name__}"

    paths = write_conclusion(text)
    out_tokens_est = estimate_tokens(text)
    elapsed = round(time.time() - t0, 3)
    terminal = "\n".join([
        "HERMES DAILY REPORT V1",
        "",
        f"elapsed={elapsed}s mode={mode}",
        f"package_bytes={package.get('package_bytes')}",
        f"estimated_input_tokens={input_tokens_est}",
        f"estimated_output_tokens={out_tokens_est}",
        f"usage={usage or 'n/a'}",
        "",
        "research_only=true package_only=true never_full_lake=true",
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
    "CONCLUSION_STRUCTURE",
    "offline_conclusion",
    "run_daily_hermes_report",
    "write_conclusion",
]
