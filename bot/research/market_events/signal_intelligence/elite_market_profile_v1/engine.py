"""Elite Market Profile V1 orchestrator (research-only)."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.signal_intelligence.elite_candidate_v1.context import (
    load_decision_book_rows,
    load_error_learning_context,
    load_regime_context,
)
from bot.research.market_events.signal_intelligence.elite_candidate_v1.engine import score_row
from bot.research.market_events.signal_intelligence.elite_candidate_v1.score import (
    STORE_CATEGORIES,
    should_store,
)
from bot.research.market_events.signal_intelligence.elite_candidate_v1.store import (
    load_stored_candidates,
)
from bot.research.market_events.signal_intelligence.elite_market_profile_v1.features import (
    extract_tags,
)
from bot.research.market_events.signal_intelligence.elite_market_profile_v1.portrait import (
    dimension_portrait,
    elite_vs_ignore,
    mine_combos,
)
from bot.research.market_events.signal_intelligence.elite_market_profile_v1.report import (
    format_terminal,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.elite_market_profile_v1.schema import (
    ensure_elite_market_profile_schema,
)
from bot.research.market_events.signal_intelligence.elite_market_profile_v1.store import (
    persist_profile,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.loader import (
    load_research_lake_rows,
)


def _index_lake(conn: Any) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    try:
        rows = load_research_lake_rows(conn, require_pnl=False)
    except Exception:
        return out
    for r in rows:
        tid = int(r.get("trade_id") or 0)
        if tid:
            out[tid] = r
    return out


def _tag_records(
    rows: list[dict[str, Any]],
    lake_by_id: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    tagged: list[dict[str, Any]] = []
    for r in rows:
        tid = int(r.get("trade_id") or 0)
        lake = lake_by_id.get(tid)
        tags = extract_tags(r, lake=lake)
        tagged.append({
            **r,
            "tags": tags,
            "pnl": r.get("pnl") if r.get("pnl") is not None else (lake or {}).get("pnl"),
        })
    return tagged


def run_elite_market_profile_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    persist: bool = True,
    top_combos: int = 100,
) -> dict[str, Any]:
    """
    Build statistical portrait of ELITE/A+/A only; compare vs IGNORE.
    Research-only — does not change Execution / Live / Strategy / Gate / Optimizer.
    """
    t0 = time.time()
    ensure_elite_market_profile_schema(conn)

    elite = load_stored_candidates(conn, categories=list(STORE_CATEGORIES))
    journal = load_decision_book_rows(conn)
    if not elite:
        # Fallback: score journal once if store empty
        regime_ctx = load_regime_context(conn)
        del_ctx = load_error_learning_context(conn)
        scored = [
            score_row(r, regime_ctx=regime_ctx, del_ctx=del_ctx, learn=False)
            for r in journal
        ]
        elite = [r for r in scored if should_store(str(r.get("category") or ""))]
        ignore = [r for r in scored if str(r.get("category") or "") == "IGNORE"]
    else:
        elite_ids = {int(r.get("trade_id") or 0) for r in elite}
        # Non-elite journal rows = IGNORE comparison set (B not stored either)
        ignore = [r for r in journal if int(r.get("trade_id") or 0) not in elite_ids]

    if not elite:
        return {
            "ok": False,
            "error": "no_elite",
            "terminal": (
                "ELITE MARKET PROFILE V1\n\n"
                "ERROR no ELITE/A+/A — run: elite-candidates"
            ),
            "research_only": True,
            "elapsed_sec": round(time.time() - t0, 3),
        }

    lake_by_id = _index_lake(conn)
    # Enrich elite/ignore with journal module similarities when missing on store rows
    journal_by_id = {int(r.get("trade_id") or 0): r for r in journal if int(r.get("trade_id") or 0)}

    def _enrich(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            tid = int(r.get("trade_id") or 0)
            j = journal_by_id.get(tid) or {}
            merged = dict(r)
            for k in (
                "timeline_similarity", "fingerprint_similarity", "replay", "dna",
                "brain", "edge", "confidence", "pnl", "result",
            ):
                if merged.get(k) is None and j.get(k) is not None:
                    merged[k] = j.get(k)
            if merged.get("current_fingerprint") is None and j.get("fingerprint_similarity") is not None:
                merged["current_fingerprint"] = j.get("fingerprint_similarity")
            out.append(merged)
        return out

    elite_tagged = _tag_records(_enrich(elite), lake_by_id)
    ignore_tagged = _tag_records(_enrich(ignore), lake_by_id)

    coins = dimension_portrait(elite_tagged, prefix="COIN=")
    directions = dimension_portrait(elite_tagged, exact=["LONG", "SHORT"])
    hours = dimension_portrait(elite_tagged, prefix="HOUR=")
    weekdays = dimension_portrait(elite_tagged, prefix="WEEKDAY=")
    sessions = dimension_portrait(elite_tagged, prefix="SESSION=")
    market_all = dimension_portrait(elite_tagged)
    skip_prefixes = ("COIN=", "HOUR=", "WEEKDAY=", "SESSION=")
    skip_exact = {"LONG", "SHORT"}
    market_rows = [
        r for r in market_all
        if str(r.get("key")) not in skip_exact
        and not any(str(r.get("key") or "").startswith(p) for p in skip_prefixes)
    ]

    combos = mine_combos(elite_tagged, top_n=top_combos)
    compare = elite_vs_ignore(elite_tagged, ignore_tagged)

    dimensions: list[dict[str, Any]] = []
    for dim, rows in (
        ("coin", coins),
        ("direction", directions),
        ("hour", hours),
        ("weekday", weekdays),
        ("session", sessions),
        ("market", market_rows),
    ):
        for r in rows:
            dimensions.append({**r, "dimension": dim})

    stored = {"dimensions": 0, "combos": 0, "compare": 0}
    if persist:
        stored = persist_profile(
            conn, dimensions=dimensions, combos=combos, compare=compare
        )

    elapsed = round(time.time() - t0, 3)
    result = {
        "ok": True,
        "research_only": True,
        "elite_n": len(elite_tagged),
        "ignore_n": len(ignore_tagged),
        "coins": coins,
        "directions": directions,
        "hours": hours,
        "weekdays": weekdays,
        "sessions": sessions,
        "market": market_rows[:80],
        "combos": combos,
        "compare": compare[:80],
        "stored": stored,
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


def run_elite_profile_report(conn: Any, *, write_reports: bool = True) -> dict[str, Any]:
    return run_elite_market_profile_v1(conn, write_reports=write_reports, persist=False)


def run_elite_profile_review(conn: Any) -> dict[str, Any]:
    out = run_elite_market_profile_v1(conn, write_reports=False, persist=False)
    if not out.get("ok"):
        return out
    lines = [
        "ELITE MARKET PROFILE REVIEW V1",
        "",
        f"elite_n={out.get('elite_n')} ignore_n={out.get('ignore_n')}",
        "",
        "Top combos",
    ]
    for c in (out.get("combos") or [])[:10]:
        lines.append(f"  n={c.get('n')} WR={c.get('wr')} — {c.get('pattern')}")
    lines.extend(["", "ELITE vs IGNORE"])
    for v in (out.get("compare") or [])[:10]:
        lines.append(
            f"  {v.get('feature')}: {v.get('elite_pct')}% vs {v.get('ignore_pct')}%"
        )
    lines.extend(["", f"elapsed={out.get('elapsed_sec')}s research_only=true"])
    out["terminal"] = "\n".join(lines)
    return out


__all__ = [
    "run_elite_market_profile_v1",
    "run_elite_profile_report",
    "run_elite_profile_review",
]
