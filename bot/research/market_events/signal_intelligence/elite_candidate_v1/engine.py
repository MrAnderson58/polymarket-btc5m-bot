"""Elite Candidate Engine V1 orchestrator (research-only)."""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from bot.research.market_events.signal_intelligence.elite_candidate_v1.context import (
    load_decision_book_rows,
    load_error_learning_context,
    load_regime_context,
    regime_score_for_direction,
)
from bot.research.market_events.signal_intelligence.elite_candidate_v1.learn import (
    apply_learning,
)
from bot.research.market_events.signal_intelligence.elite_candidate_v1.report import (
    format_terminal,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.elite_candidate_v1.schema import (
    ensure_elite_candidate_schema,
)
from bot.research.market_events.signal_intelligence.elite_candidate_v1.score import (
    candidate_score,
    categorize,
    component_scores,
    score_breakdown,
    should_store,
)
from bot.research.market_events.signal_intelligence.elite_candidate_v1.store import (
    load_stored_candidates,
    persist_candidates,
)
from bot.research.market_events.signal_intelligence.elite_candidate_v1.why import (
    build_why,
    build_why_not,
    supporting_and_rejecting,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import BOOK_B


def _f(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _similarity(row: dict[str, Any]) -> float | None:
    vals = [
        _f(row.get("fingerprint_similarity")),
        _f(row.get("timeline_similarity")),
        _f(row.get("replay")),
        _f(row.get("dna")),
    ]
    present = [v for v in vals if v is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 4)


def score_row(
    row: dict[str, Any],
    *,
    regime_ctx: dict[str, Any],
    del_ctx: dict[str, Any],
    learn: bool = True,
) -> dict[str, Any]:
    """Score one Decision Journal row through the research stack."""
    direction = row.get("direction")
    regime_s = regime_score_for_direction(regime_ctx, direction)
    comps = component_scores(
        decision_confidence=_f(row.get("confidence")),
        brain=_f(row.get("brain")),
        replay=_f(row.get("replay")),
        fingerprint=_f(row.get("fingerprint_similarity")),
        timeline=_f(row.get("timeline_similarity")),
        dna=_f(row.get("dna")),
        rules=_f(row.get("rules")),
        regime=regime_s,
        decision=row.get("decision"),
        edge=_f(row.get("edge")),
        causality=_f(row.get("causality")),
    )
    base = candidate_score(comps)
    category = categorize(base)
    supporting, rejecting = supporting_and_rejecting(comps)
    why = build_why(
        components=comps,
        supporting=supporting,
        category=category,
        decision=row.get("decision"),
        direction=direction,
    )
    why_not = build_why_not(
        components=comps,
        rejecting=rejecting,
        del_strict_modules=del_ctx.get("strict_modules") or [],
    )

    pnl = _f(row.get("pnl"))
    result = row.get("result")
    learned_score = None
    learn_meta = None
    score = base
    if learn and result not in (None, "OPEN", "UNKNOWN", "REJECTED") and pnl is not None:
        learn_meta = apply_learning(base, pnl=pnl, result=str(result) if result else None)
        learned_score = learn_meta["new_score"]
        score = learned_score
        category = categorize(score)

    hist_sim = _similarity(row)
    expected_ev = _f(row.get("historical_ev"))
    if expected_ev is None:
        expected_ev = round(score / 100.0 * 2.0, 4)  # soft research estimate
    expected_dd = round(abs(float(expected_ev)) * 0.6, 4) if expected_ev is not None else None
    # 5m markets default holding estimate
    expected_hold = 300.0

    return {
        "trade_id": int(row.get("trade_id") or 0),
        "symbol": row.get("symbol"),
        "opened_at": row.get("opened_at"),
        "direction": direction,
        "decision": row.get("decision"),
        "category": category,
        "score": score,
        "base_score": base,
        "learned_score": learned_score,
        "learn": learn_meta,
        "why": why,
        "why_not": why_not,
        "supporting_modules": supporting,
        "rejecting_modules": rejecting,
        "historical_wr": _f(row.get("historical_wr")),
        "historical_ev": _f(row.get("historical_ev")),
        "historical_pf": _f(row.get("historical_pf")),
        "historical_similarity": hist_sim,
        "current_regime": regime_ctx.get("current_regime"),
        "current_transition": regime_ctx.get("current_transition"),
        "current_fingerprint": _f(row.get("fingerprint_similarity")),
        "decision_confidence": _f(row.get("confidence")),
        "brain_confidence": _f(row.get("brain")),
        "expected_ev": expected_ev,
        "expected_holding_time": expected_hold,
        "expected_drawdown": expected_dd,
        "components": comps,
        "breakdown": score_breakdown(comps),
        "result": result,
        "pnl": pnl,
        "accepted": int(row.get("accepted") or 0) == 1,
    }


def run_elite_candidates_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    persist: bool = True,
    book: str = BOOK_B,
    learn: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    """
    Build Elite Candidate scores from cached Decision Journal + Regime + Error Learning.
    Research-only — does not change Execution / Live / Strategy / Gate / Optimizer.
    """
    t0 = time.time()
    ensure_elite_candidate_schema(conn)
    rows = load_decision_book_rows(conn, book=book)
    if limit is not None:
        rows = rows[: int(limit)]
    if not rows:
        return {
            "ok": False,
            "error": "empty_journal",
            "terminal": (
                "ELITE CANDIDATE ENGINE V1\n\n"
                "ERROR empty journal — run: paper-decision-books"
            ),
            "research_only": True,
            "elapsed_sec": round(time.time() - t0, 3),
        }

    regime_ctx = load_regime_context(conn)
    del_ctx = load_error_learning_context(conn)

    scored: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    for row in rows:
        rec = score_row(row, regime_ctx=regime_ctx, del_ctx=del_ctx, learn=learn)
        scored.append(rec)
        lm = rec.get("learn")
        if lm and lm.get("delta"):
            history.append({
                "trade_id": rec["trade_id"],
                "event": lm.get("event"),
                "old_score": lm.get("old_score"),
                "new_score": lm.get("new_score"),
                "delta": lm.get("delta"),
                "pnl": rec.get("pnl"),
                "result": rec.get("result"),
                "note": "overnight auto-learn",
            })

    categories = Counter(r["category"] for r in scored)
    stored_candidates = [r for r in scored if should_store(r["category"])]
    stored_candidates.sort(key=lambda r: float(r.get("score") or 0), reverse=True)

    stored = {"candidates": 0, "history": 0}
    if persist:
        stored = persist_candidates(
            conn,
            candidates=stored_candidates,
            history=history,
            replace=True,
        )

    elapsed = round(time.time() - t0, 3)
    result = {
        "ok": True,
        "research_only": True,
        "n_scored": len(scored),
        "n_stored": len(stored_candidates),
        "categories": dict(categories),
        "candidates": stored_candidates[:200],
        "top_elite": [c for c in stored_candidates if c["category"] == "ELITE"][:20],
        "regime": regime_ctx,
        "error_learning": {
            "strict_modules": del_ctx.get("strict_modules"),
            "weak_modules": del_ctx.get("weak_modules"),
        },
        "stored": stored,
        "learn_events": len(history),
        "elapsed_sec": elapsed,
        "execution_unchanged": True,
        "live_unchanged": True,
        "strategy_unchanged": True,
        "gate_unchanged": True,
        "optimizer_unchanged": True,
    }
    result["terminal"] = format_terminal(result)
    if write_reports:
        result["paths"] = write_artifacts(result)
    return result


def run_elite_report(conn: Any, *, write_reports: bool = True) -> dict[str, Any]:
    return run_elite_candidates_v1(conn, write_reports=write_reports, persist=False)


def run_elite_review(conn: Any) -> dict[str, Any]:
    out = run_elite_candidates_v1(conn, write_reports=False, persist=False)
    if not out.get("ok"):
        return out
    lines = [
        "ELITE CANDIDATE REVIEW V1",
        "",
        f"scored={out.get('n_scored')} stored={out.get('n_stored')} "
        f"cats={out.get('categories')}",
        "",
        "Top",
    ]
    for i, c in enumerate((out.get("candidates") or [])[:10], 1):
        lines.append(
            f"  {i}. {c.get('symbol')} {c.get('direction')} "
            f"Score {c.get('score')} {c.get('category')} "
            f"WR={c.get('historical_wr')} EV={c.get('historical_ev')}"
        )
    lines.extend(["", f"elapsed={out.get('elapsed_sec')}s research_only=true"])
    out["terminal"] = "\n".join(lines)
    return out


def run_elite_explain(conn: Any, *, trade_id: int | None = None) -> dict[str, Any]:
    """Explain one candidate (from store or live score)."""
    t0 = time.time()
    ensure_elite_candidate_schema(conn)
    cand = None
    if trade_id:
        stored = load_stored_candidates(conn, limit=5000)
        for c in stored:
            if int(c.get("trade_id") or 0) == int(trade_id):
                cand = c
                break
        if cand is None:
            rows = load_decision_book_rows(conn)
            regime_ctx = load_regime_context(conn)
            del_ctx = load_error_learning_context(conn)
            for row in rows:
                if int(row.get("trade_id") or 0) == int(trade_id):
                    cand = score_row(row, regime_ctx=regime_ctx, del_ctx=del_ctx, learn=True)
                    break
    else:
        out = run_elite_candidates_v1(conn, write_reports=False, persist=False)
        cands = out.get("candidates") or []
        cand = cands[0] if cands else None
        if cand is None:
            return {
                "ok": False,
                "error": "no_candidates",
                "terminal": "ELITE EXPLAIN\n\nNo A+/A/ELITE candidates.",
                "elapsed_sec": round(time.time() - t0, 3),
            }

    if cand is None:
        return {
            "ok": False,
            "error": "not_found",
            "terminal": f"ELITE EXPLAIN\n\ntrade_id={trade_id} not found",
            "elapsed_sec": round(time.time() - t0, 3),
        }

    lines = [
        "ELITE EXPLAIN V1",
        "",
        f"trade_id={cand.get('trade_id')} {cand.get('symbol')} {cand.get('direction')}",
        f"Score {cand.get('score')}  category={cand.get('category')}",
        f"base={cand.get('base_score')} learned={cand.get('learned_score')}",
        "",
        "WHY",
    ]
    for w in cand.get("why") or cand.get("why_json") or []:
        lines.append(f"  - {w}")
    lines.append("")
    lines.append("WHY NOT")
    for w in cand.get("why_not") or cand.get("why_not_json") or []:
        lines.append(f"  - {w}")
    lines.extend([
        "",
        f"supporting={cand.get('supporting_modules') or cand.get('supporting_modules_json')}",
        f"rejecting={cand.get('rejecting_modules') or cand.get('rejecting_modules_json')}",
        f"regime={cand.get('current_regime')} transition={cand.get('current_transition')}",
        f"WR={cand.get('historical_wr')} PF={cand.get('historical_pf')} EV={cand.get('historical_ev')}",
        f"decision_conf={cand.get('decision_confidence')} brain={cand.get('brain_confidence')}",
        f"expected_ev={cand.get('expected_ev')} hold={cand.get('expected_holding_time')} "
        f"dd={cand.get('expected_drawdown')}",
        "",
        "research_only=true",
    ])
    return {
        "ok": True,
        "candidate": cand,
        "terminal": "\n".join(lines),
        "elapsed_sec": round(time.time() - t0, 3),
        "research_only": True,
    }


def today_elite_slice(
    candidates: list[dict[str, Any]],
    *,
    now: int | None = None,
) -> list[dict[str, Any]]:
    """Candidates opened today (UTC day) in store categories."""
    wall = int(now or time.time())
    day = 86400
    cut = wall - (wall % day)
    out = []
    for c in candidates:
        oa = int(c.get("opened_at") or 0)
        if oa >= cut and should_store(str(c.get("category") or "")):
            out.append(c)
    out.sort(key=lambda r: float(r.get("score") or 0), reverse=True)
    return out


__all__ = [
    "run_elite_candidates_v1",
    "run_elite_explain",
    "run_elite_report",
    "run_elite_review",
    "score_row",
    "today_elite_slice",
]
