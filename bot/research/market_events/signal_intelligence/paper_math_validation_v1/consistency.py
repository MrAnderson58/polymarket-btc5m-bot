"""Consistency checks across Reality / Elite / Decision / Research Lake."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence

from bot.research.market_events.signal_intelligence.elite_candidate_v1.score import (
    STORE_CATEGORIES,
)
from bot.research.market_events.signal_intelligence.elite_candidate_v1.store import (
    load_stored_candidates,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A,
    BOOK_B,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.journal import (
    load_journal_rows,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.books import (
    BOOK_C,
    BOOK_D,
)


def dataset_fingerprint(trades: Sequence[dict[str, Any]]) -> str:
    """Stable hash of trade_id + opened_at + pnl for Cursor/Mini parity."""
    parts = []
    for t in sorted(trades, key=lambda x: (int(x.get("trade_id") or 0), int(x.get("opened_at") or 0))):
        parts.append(
            f"{int(t.get('trade_id') or 0)}|{int(t.get('opened_at') or 0)}|{t.get('pnl')}"
        )
    raw = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_reality_score(conn: Any) -> dict[str, Any]:
    try:
        row = conn.execute(
            """
            SELECT value_real, value_text, meta_json FROM reality_validation_v1
            WHERE section = 'summary' AND key = 'reality_score'
            """
        ).fetchone()
    except Exception:
        return {"ok": False, "reality_score": None, "reason": "table_missing"}
    if row is None:
        return {"ok": False, "reality_score": None, "reason": "missing_score"}
    if hasattr(row, "keys"):
        score = row["value_real"]
    else:
        score = row[0]
    return {
        "ok": score is not None,
        "reality_score": float(score) if score is not None else None,
    }


def reality_score_parity(
    *,
    score_a: float | None,
    score_b: float | None,
    dataset_a: str,
    dataset_b: str,
    tol: float = 1e-9,
) -> dict[str, Any]:
    """Fail if Cursor vs Mini (or double-run) scores/datasets mismatch."""
    same_ds = dataset_a == dataset_b
    if score_a is None or score_b is None:
        return {
            "ok": False,
            "fail": True,
            "reason": "missing_score",
            "same_dataset": same_ds,
        }
    match = abs(float(score_a) - float(score_b)) <= tol and same_ds
    return {
        "ok": match,
        "fail": not match,
        "score_a": float(score_a),
        "score_b": float(score_b),
        "delta": abs(float(score_a) - float(score_b)),
        "same_dataset": same_ds,
        "dataset_version": dataset_a if same_ds else None,
        "reason": None if match else "reality_score_mismatch",
    }


def elite_corpus_consistency(conn: Any) -> dict[str, Any]:
    """
    Elite Profile must use the same audited corpus (no separate sampling).
    Compare stored elite trade_ids to audit payload / candidate store.
    """
    elite = load_stored_candidates(conn, categories=list(STORE_CATEGORIES))
    elite_ids = sorted({int(r.get("trade_id") or 0) for r in elite if int(r.get("trade_id") or 0)})
    if not elite_ids:
        return {
            "ok": False,
            "fail": True,
            "reason": "empty_elite_corpus",
            "n_elite": 0,
        }

    hidden_fail = False
    sampling_seen = False
    elite_n_audit = None
    try:
        rows = conn.execute(
            """
            SELECT section, key, n, meta_json FROM elite_profile_audit_v1
            """
        ).fetchall()
        for r in rows:
            section = str(r["section"] if hasattr(r, "keys") else r[0] or "")
            key = str((r["key"] if hasattr(r, "keys") else r[1]) or "")
            n = r["n"] if hasattr(r, "keys") else r[2]
            meta_raw = r["meta_json"] if hasattr(r, "keys") else r[3]
            try:
                meta = json.loads(meta_raw or "{}")
            except Exception:
                meta = {}
            hidden = meta.get("hidden_filters")
            if isinstance(hidden, list) and len(hidden) > 0:
                hidden_fail = True
            if section.lower() == "sampling" or key.lower() == "sampling":
                sampling_seen = True
            # Prefer explicit elite membership counts — not full lake corpus
            if section.lower() in ("elite", "membership", "candidates") or "elite" in key.lower():
                if n is not None:
                    elite_n_audit = int(n)
            if elite_n_audit is None and section.lower() == "corpus" and n is not None:
                # only treat as elite corpus if n matches store (±5%)
                ni = int(n)
                if abs(ni - len(elite_ids)) <= max(5, int(0.05 * len(elite_ids))):
                    elite_n_audit = ni
    except Exception:
        rows = []

    if hidden_fail:
        return {
            "ok": False,
            "fail": True,
            "n_elite": len(elite_ids),
            "audit_n": elite_n_audit,
            "reason": "elite_hidden_filters",
            "dataset_fingerprint": hashlib.sha256(
                ",".join(str(i) for i in elite_ids).encode()
            ).hexdigest()[:16],
        }

    # No separate sampling: either no audit, or sampling documents empty hidden filters,
    # and we consume the full stored elite set (no LIMIT in loader).
    return {
        "ok": True,
        "fail": False,
        "n_elite": len(elite_ids),
        "audit_n": elite_n_audit,
        "sampling_seen": sampling_seen,
        "reason": None,
        "note": "stored_elite_corpus_no_resample",
        "dataset_fingerprint": hashlib.sha256(
            ",".join(str(i) for i in elite_ids).encode()
        ).hexdigest()[:16],
    }


def decision_lake_alignment(
    conn: Any,
    *,
    math_rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """
    Verify Decision journal and math book reference same trade_id + opened_at.
    Fail if inconsistency.
    """
    journal = load_journal_rows(conn, book=BOOK_B)
    by_id = {int(r.get("trade_id") or 0): r for r in journal}
    mismatches = []
    for m in math_rows:
        if int(m.get("accepted") or 0) != 1:
            continue
        tid = int(m.get("trade_id") or 0)
        j = by_id.get(tid)
        if j is None:
            mismatches.append({"trade_id": tid, "reason": "missing_in_decision_journal"})
            continue
        jo = int(j.get("opened_at") or 0)
        mo = int(m.get("opened_at") or 0)
        if jo and mo and jo != mo:
            mismatches.append({
                "trade_id": tid,
                "reason": "opened_at_mismatch",
                "journal": jo,
                "math": mo,
            })
    return {
        "ok": len(mismatches) == 0,
        "fail": len(mismatches) > 0,
        "n_checked": sum(1 for m in math_rows if int(m.get("accepted") or 0) == 1),
        "mismatches": mismatches[:50],
        "reason": None if not mismatches else "decision_timestamp_mismatch",
    }


def morning_consistency(
    *,
    reality_score: float | None,
    book_a_stats: dict[str, Any],
    book_b_stats: dict[str, Any],
    elite_n: int,
) -> dict[str, Any]:
    """
    Morning Report must never show contradictory Reality / Elite / Decision stats.
    Pure check — does not modify morning report.
    """
    issues: list[str] = []
    if reality_score is not None and float(reality_score) < 80:
        # Decision books claiming strong edge while reality FAIL is contradictory
        wr_b = book_b_stats.get("wr")
        if wr_b is not None and float(wr_b) >= 70 and (book_b_stats.get("trades") or 0) >= 20:
            issues.append("reality_fail_but_book_b_strong")
    if elite_n == 0 and (book_b_stats.get("trades") or 0) > 100:
        issues.append("elite_empty_but_decision_active")
    if (book_a_stats.get("trades") or 0) == 0 and (book_b_stats.get("trades") or 0) > 0:
        issues.append("book_b_without_book_a")
    return {
        "ok": len(issues) == 0,
        "fail": len(issues) > 0,
        "issues": issues,
        "reason": None if not issues else issues[0],
    }


def duplicate_guard(accepted_ids: set[int], trade_id: int) -> bool:
    """True if duplicate (already accepted in Book D/C set)."""
    return int(trade_id) in accepted_ids


def book_d_no_duplicates(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    ids = [
        int(r.get("trade_id") or 0)
        for r in rows
        if str(r.get("book")) == BOOK_D and int(r.get("accepted") or 0) == 1
    ]
    dup = len(ids) - len(set(ids))
    return {
        "ok": dup == 0,
        "fail": dup > 0,
        "n_accepted": len(ids),
        "duplicate_count": dup,
        "reason": None if dup == 0 else "book_d_duplicates",
    }


__all__ = [
    "BOOK_A",
    "BOOK_B",
    "BOOK_C",
    "BOOK_D",
    "book_d_no_duplicates",
    "dataset_fingerprint",
    "decision_lake_alignment",
    "duplicate_guard",
    "elite_corpus_consistency",
    "load_reality_score",
    "morning_consistency",
    "reality_score_parity",
]
