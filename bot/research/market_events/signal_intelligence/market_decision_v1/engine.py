"""Market Decision Engine V1 orchestrator."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.signal_intelligence.market_decision_v1.context import (
    build_decision_context,
)
from bot.research.market_events.signal_intelligence.market_decision_v1.decide import (
    decide_one,
    format_decision,
)
from bot.research.market_events.signal_intelligence.market_decision_v1.explain import (
    explain_from_decision,
    explain_trade,
    find_trade,
    format_explain,
)
from bot.research.market_events.signal_intelligence.market_decision_v1.replay import (
    run_decision_replay,
)
from bot.research.market_events.signal_intelligence.market_decision_v1.report import (
    format_terminal,
    write_artifacts,
)


def run_market_decision_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    limit: int | None = None,
    mode: str = "replay",
    trade_id: int | None = None,
) -> dict[str, Any]:
    """
    mode:
      - replay: full Decision Replay vs Production
      - decide: latest-trade probe only (still builds context)
      - explain: Explainability card for --trade-id (or latest + opposite sample)
    """
    t0 = time.time()
    ctx = build_decision_context(conn, limit=limit)
    if not ctx.get("ok"):
        return {
            "ok": False,
            "error": "empty_context",
            "terminal": "MARKET DECISION ENGINE V1\n\nERROR empty corpus",
            "research_only": True,
        }

    context_stats = {
        "n_fp": ctx.get("n_fp"),
        "n_tl": ctx.get("n_tl"),
        "n_dna": ctx.get("n_dna"),
        "n_ready_rules": ctx.get("n_ready_rules"),
        "n_blocks": ctx.get("n_blocks"),
        "n_edges": ctx.get("n_edges"),
        "n_replays": ctx.get("n_replays"),
        "n_causality": ctx.get("n_causality"),
        "elapsed_build_sec": ctx.get("elapsed_build_sec"),
    }

    if mode == "explain":
        trades = ctx.get("trades") or []
        if trade_id is not None:
            target = find_trade(ctx, int(trade_id))
            if target is None:
                elapsed = round(time.time() - t0, 3)
                return {
                    "ok": False,
                    "error": f"trade_id={trade_id} not found",
                    "elapsed_sec": elapsed,
                    "terminal": f"TRADE #{trade_id}\n\nERROR not found in research lake",
                    "research_only": True,
                }
        else:
            target = max(trades, key=lambda r: int(r.get("opened_at") or r.get("closed_at") or 0))

        primary = explain_trade(ctx, target)
        opposite = None
        if trade_id is None:
            want_trade = primary.get("decision") != "TRADE"
            for t in reversed(trades[-500:]):
                d = decide_one(ctx, t)
                if (d.get("decision") == "TRADE") == want_trade:
                    opposite = explain_from_decision(d)
                    break
        elapsed = round(time.time() - t0, 3)
        parts = [primary.get("explain_text") or format_explain(primary)]
        if opposite:
            parts.extend(
                ["", "======== opposite sample ========", "", opposite.get("explain_text") or ""]
            )
        terminal = "\n".join(parts)
        result = {
            "ok": True,
            "mode": "explain",
            "explain": primary,
            "opposite_sample": opposite,
            "elapsed_sec": elapsed,
            "context_stats": context_stats,
            "research_only": True,
            "terminal": terminal,
        }
        if write_reports:
            from bot.research.market_events.config import BASE_DIR

            path = BASE_DIR / "MARKET_DECISION_EXPLAIN.txt"
            path.write_text(terminal, encoding="utf-8")
            out_dir = BASE_DIR / "reports" / "research" / "market_decision_v1"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "MARKET_DECISION_EXPLAIN.txt").write_text(terminal, encoding="utf-8")
            result["paths"] = {"explain_txt": str(path)}
        return result

    if mode == "decide":
        trades = ctx.get("trades") or []
        latest = max(trades, key=lambda r: int(r.get("opened_at") or r.get("closed_at") or 0))
        sample = explain_from_decision(decide_one(ctx, latest))
        elapsed = round(time.time() - t0, 3)
        result = {
            "ok": True,
            "mode": "decide",
            "sample_decision": sample,
            "production": {},
            "decision_engine": {},
            "skipped": None,
            "elapsed_sec": elapsed,
            "context_stats": context_stats,
            "research_only": True,
            "terminal": sample.get("explain_text") or format_decision(sample),
        }
    else:
        replay = run_decision_replay(ctx)
        sample = replay.get("sample_decision")
        if sample:
            replay["sample_decision"] = explain_from_decision(sample)
        elapsed = round(time.time() - t0, 3)
        result = {
            "ok": True,
            "mode": "replay",
            **replay,
            "elapsed_sec": elapsed,
            "context_stats": context_stats,
            "research_only": True,
            "paper_unchanged": True,
            "execution_unchanged": True,
            "strategy_unchanged": True,
            "gate_unchanged": True,
            "optimizer_unchanged": True,
            "brain_unchanged": True,
        }
        term = format_terminal(result)
        sample2 = result.get("sample_decision") or {}
        if sample2.get("explain_text"):
            head = term.split("Latest probe")[0]
            term = (
                head
                + "Latest probe\n\n"
                + sample2["explain_text"]
                + f"\n\nelapsed={elapsed}s research_only=true"
            )
        result["terminal"] = term

    if write_reports:
        paths = write_artifacts(result)
        result["paths"] = paths
        result["report_markdown"] = paths.get("report_text")
    return result


__all__ = ["decide_one", "format_decision", "format_explain", "run_market_decision_v1"]
