"""Market Regime Transition Engine V1 orchestrator."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.signal_intelligence.market_regime_transition_v1.corpus import (
    build_ordered_corpus,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.discover import (
    discover_transitions,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.markov import (
    build_markov,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.predict import (
    adaptive_recommendation,
    predict_next,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.report import (
    format_terminal,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.schema import (
    ensure_regime_transition_schema,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.sequences import (
    mine_sequences,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.similarity import (
    find_similar_transitions,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.store import (
    persist_library,
)


def run_market_regime_transition_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    limit: int | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    t0 = time.time()
    ensure_regime_transition_schema(conn)
    ordered = build_ordered_corpus(conn, limit=limit)
    if len(ordered) < 10:
        return {
            "ok": False,
            "error": "empty_corpus",
            "terminal": "MARKET REGIME TRANSITION ENGINE V1\n\nERROR empty corpus",
            "research_only": True,
        }

    transitions = discover_transitions(ordered)
    markov = build_markov(ordered)
    sequences = mine_sequences(ordered)
    prediction = predict_next(markov)
    recommendation = adaptive_recommendation(
        markov=markov,
        prediction=prediction,
        top_transitions=transitions.get("profitable") or [],
    )

    stored = {"transitions": 0, "edges": 0, "sequences": 0}
    if persist:
        lib_rows = (transitions.get("profitable") or []) + (transitions.get("dangerous") or [])
        stored = persist_library(
            conn,
            transitions=lib_rows,
            edges=markov.get("edges") or [],
            sequences=sequences,
        )

    elapsed = round(time.time() - t0, 3)
    result = {
        "ok": True,
        "research_only": True,
        "n": len(ordered),
        "transitions": transitions,
        "markov": markov,
        "sequences": sequences,
        "prediction": prediction,
        "recommendation": recommendation,
        "stored": stored,
        "elapsed_sec": elapsed,
        "gate_unchanged": True,
        "strategy_unchanged": True,
        "optimizer_unchanged": True,
        "paper_unchanged": True,
        "execution_unchanged": True,
        "decision_engine_unchanged": True,
        "brain_unchanged": True,
    }
    result["terminal"] = format_terminal(result)
    if write_reports:
        result["paths"] = write_artifacts(result)
    return result


def run_market_transition_report(conn: Any, *, write_reports: bool = True) -> dict[str, Any]:
    return run_market_regime_transition_v1(conn, write_reports=write_reports, persist=False)


def run_market_transition_similarity(
    conn: Any,
    *,
    pattern: str | None = None,
    from_state: str | None = None,
    to_state: str | None = None,
    k: int = 10,
) -> dict[str, Any]:
    t0 = time.time()
    out = run_market_regime_transition_v1(conn, write_reports=False, persist=False)
    if not out.get("ok"):
        return out
    lib = (out.get("transitions") or {}).get("profitable") or []
    lib += (out.get("transitions") or {}).get("dangerous") or []
    query = {
        "pattern": pattern or "",
        "from_state": from_state or (out.get("prediction") or {}).get("current"),
        "to_state": to_state or "",
    }
    similar = find_similar_transitions(query, lib, k=k)
    terminal_lines = [
        "MARKET TRANSITION SIMILARITY V1",
        "",
        f"query={query}",
        "",
        "Matches",
    ]
    for s in similar:
        terminal_lines.append(
            f"  {s.get('similarity_pct')}%  {s.get('transition_key')} "
            f"n={s.get('n')} EV={s.get('ev')}"
        )
    terminal_lines.append(f"\nelapsed={round(time.time() - t0, 3)}s research_only=true")
    return {
        "ok": True,
        "research_only": True,
        "query": query,
        "similar": similar,
        "elapsed_sec": round(time.time() - t0, 3),
        "terminal": "\n".join(terminal_lines),
    }


__all__ = [
    "run_market_regime_transition_v1",
    "run_market_transition_report",
    "run_market_transition_similarity",
]
