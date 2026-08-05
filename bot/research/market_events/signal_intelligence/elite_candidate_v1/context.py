"""Load cached Decision Journal + Regime + Error-Learning context (no N+1)."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import BOOK_B
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.journal import (
    load_journal_rows,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.metrics import (
    partition_by_book,
)


def load_decision_book_rows(conn: Any, *, book: str = BOOK_B) -> list[dict[str, Any]]:
    rows = load_journal_rows(conn)
    by_book = partition_by_book(rows)
    return list(by_book.get(book) or [])


def load_regime_context(conn: Any) -> dict[str, Any]:
    """Single cached regime snapshot from transition tables (or safe defaults)."""
    out: dict[str, Any] = {
        "current_regime": "UNKNOWN",
        "current_transition": None,
        "regime_score": 0.4,
        "recommended_bias": "NO TRADE",
        "transition_probability": 0.0,
    }
    try:
        from bot.research.market_events.signal_intelligence.market_regime_transition_v1.schema import (
            EDGES_TABLE,
            TRANSITIONS_TABLE,
            ensure_regime_transition_schema,
        )

        ensure_regime_transition_schema(conn)
        # Most probable outgoing edge as proxy for current→next
        cur = conn.execute(
            f"""
            SELECT from_state, to_state, prob, expected_ev, expected_wr, expected_pf
            FROM {EDGES_TABLE}
            ORDER BY count DESC, prob DESC
            LIMIT 1
            """
        ).fetchone()
        if cur:
            d = dict(cur) if not isinstance(cur, dict) else cur
            # sqlite Row
            try:
                d = {k: cur[k] for k in cur.keys()}  # type: ignore[attr-defined]
            except Exception:
                pass
            from_s = d.get("from_state") or "UNKNOWN"
            to_s = d.get("to_state") or "UNKNOWN"
            prob = float(d.get("prob") or 0.0)
            out["current_regime"] = str(from_s)
            out["current_transition"] = f"{from_s}->{to_s}"
            out["transition_probability"] = round(prob, 4)
            # Prefer transition probability as regime confidence when edges exist
            out["regime_score"] = round(min(0.99, max(0.05, prob if prob > 0 else 0.4)), 4)
            if to_s in ("STRONG_BULL", "WEAK_BULL"):
                out["recommended_bias"] = "LONG"
            elif to_s in ("STRONG_BEAR", "WEAK_BEAR"):
                out["recommended_bias"] = "SHORT"
            else:
                out["recommended_bias"] = "NO TRADE"
        ready = conn.execute(
            f"""
            SELECT from_state, to_state, score, wr, pf, ev
            FROM {TRANSITIONS_TABLE}
            WHERE ready=1
            ORDER BY score DESC
            LIMIT 1
            """
        ).fetchone()
        if ready:
            try:
                rd = {k: ready[k] for k in ready.keys()}  # type: ignore[attr-defined]
            except Exception:
                rd = dict(ready) if isinstance(ready, dict) else {}
            if rd.get("from_state"):
                out["current_regime"] = str(rd.get("from_state") or out["current_regime"])
            if rd.get("to_state"):
                out["current_transition"] = (
                    f"{rd.get('from_state')}->{rd.get('to_state')}"
                )
            # Keep edge probability as primary regime_score; READY score is ranking not 0..1
            if out.get("transition_probability"):
                out["regime_score"] = round(
                    min(0.99, max(0.05, float(out["transition_probability"]))), 4
                )
    except Exception:
        pass
    return out


def load_error_learning_context(conn: Any) -> dict[str, Any]:
    """Top strict/weak modules from Decision Error Learning cache (optional)."""
    out: dict[str, Any] = {"strict_modules": [], "weak_modules": [], "ok": False}
    try:
        from bot.research.market_events.signal_intelligence.decision_error_learning_v1.schema import (
            PATTERNS_TABLE,
            ensure_decision_error_schema,
        )

        ensure_decision_error_schema(conn)
        rows = conn.execute(
            f"""
            SELECT primary_module, kind, n, recovered_ev
            FROM {PATTERNS_TABLE}
            ORDER BY n DESC
            LIMIT 50
            """
        ).fetchall()
        fr_counts: dict[str, int] = {}
        fa_counts: dict[str, int] = {}
        for r in rows:
            try:
                d = {k: r[k] for k in r.keys()}  # type: ignore[attr-defined]
            except Exception:
                d = dict(r) if isinstance(r, dict) else {}
            mod = str(d.get("primary_module") or "unknown")
            kind = str(d.get("kind") or "")
            n = int(d.get("n") or 0)
            if kind == "false_reject":
                fr_counts[mod] = fr_counts.get(mod, 0) + n
            elif kind == "false_accept":
                fa_counts[mod] = fa_counts.get(mod, 0) + n
        out["strict_modules"] = [
            m for m, _ in sorted(fr_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        ]
        out["weak_modules"] = [
            m for m, _ in sorted(fa_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        ]
        out["ok"] = True
    except Exception:
        pass
    return out


def regime_score_for_direction(regime_ctx: dict[str, Any], direction: str | None) -> float:
    base = float(regime_ctx.get("regime_score") or 0.4)
    bias = str(regime_ctx.get("recommended_bias") or "")
    d = str(direction or "").upper()
    if bias in ("LONG", "SHORT") and d:
        if (bias == "LONG" and d in ("LONG", "UP", "BUY")) or (
            bias == "SHORT" and d in ("SHORT", "DOWN", "SELL")
        ):
            return min(0.99, base + 0.15)
        return max(0.05, base * 0.5)
    return base


__all__ = [
    "load_decision_book_rows",
    "load_error_learning_context",
    "load_regime_context",
    "regime_score_for_direction",
]
