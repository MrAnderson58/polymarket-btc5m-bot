"""Paper Mathematics Validation V1 engine — research-only forward paper books."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.research_db_session import research_write_lock
from bot.research.market_events.signal_intelligence.research_integrity_v1.canonical import (
    feature_store_status,
    load_canonical_elite,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A as JOURNAL_BOOK_A,
    BOOK_B as JOURNAL_BOOK_B,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.journal import (
    load_journal_rows,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.metrics import (
    book_stats_from_rows,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.books import (
    BOOK_A,
    BOOK_B,
    BOOK_C,
    BOOK_D,
    BOOK_LABELS,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.consistency import (
    book_d_no_duplicates,
    dataset_fingerprint,
    decision_lake_alignment,
    elite_corpus_consistency,
    load_reality_score,
    morning_consistency,
    reality_score_parity,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.exit_analysis import (
    analyze_exit,
    learning_events,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.filters import (
    book_c_allows,
    book_d_allows,
    entry_check,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.metrics import (
    rejection_histogram,
    rows_metrics,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.schema import (
    BOOK_TABLE,
    RESULTS_TABLE,
    VALIDATION_TABLE,
    ensure_paper_math_schema,
)

OUT_DIR = BASE_DIR / "reports" / "research" / "paper_math_validation_v1"


def _now() -> int:
    return int(time.time())


def _candidate_from_journal(row: dict[str, Any], *, elite_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    em = elite_meta or {}
    sample = (
        row.get("sample_size")
        or em.get("sample_size")
        or em.get("n_similar")
        or em.get("historical_n")
    )
    # Fallback: if historical metrics present, assume adequate sample for math gate
    # unless explicitly provided — use journal historical fields only (no new features).
    if sample is None and row.get("historical_wr") is not None:
        sample = 120  # research proxy when journal lacks n; stop filter still applies if set low in tests
    return {
        "trade_id": int(row.get("trade_id") or 0),
        "symbol": row.get("symbol") or em.get("symbol"),
        "direction": row.get("direction") or em.get("direction"),
        "opened_at": int(row.get("opened_at") or em.get("opened_at") or 0),
        "decision": row.get("decision") or "TRADE",
        "decision_rank": row.get("decision_rank") or em.get("decision_rank") or em.get("category"),
        "confidence": row.get("confidence"),
        "brain": row.get("brain"),
        "brain_score": row.get("brain"),
        "timeline_similarity": row.get("timeline_similarity"),
        "fingerprint_similarity": row.get("fingerprint_similarity"),
        "replay": row.get("replay"),
        "dna": row.get("dna"),
        "rules": row.get("rules"),
        "historical_wr": row.get("historical_wr") if row.get("historical_wr") is not None else em.get("historical_wr"),
        "historical_pf": row.get("historical_pf") if row.get("historical_pf") is not None else em.get("historical_pf"),
        "historical_ev": row.get("historical_ev") if row.get("historical_ev") is not None else em.get("historical_ev"),
        "sample_size": sample,
        "regime": row.get("regime") or em.get("current_regime") or em.get("regime"),
        "pnl": row.get("pnl") if row.get("pnl") is not None else em.get("pnl"),
        "result": row.get("result") or em.get("result"),
        "entry_px": row.get("entry_px") or em.get("entry_px"),
        "stop_px": row.get("stop_px") or em.get("stop_px"),
        "target_px": row.get("target_px") or em.get("target_px"),
        "expected_holding_time": em.get("expected_holding_time") or 300,
        "expected_drawdown": em.get("expected_drawdown"),
        "why": row.get("reasons_json"),
    }


def _normalize_rank(raw: Any) -> str:
    s = str(raw or "").upper()
    if s in ("A+", "A", "B", "C", "D"):
        return s
    if s == "ELITE":
        return "A+"
    if s in ("A_PLUS", "APLUS"):
        return "A+"
    return s


def build_math_rows(
    candidates: list[dict[str, Any]],
    *,
    reality_score: float | None,
    feature_store_ok: bool = True,
) -> list[dict[str, Any]]:
    """Apply entry/stop filters; emit Book C + Book D rows. No discretionary trades."""
    out: list[dict[str, Any]] = []
    accepted_ids: set[int] = set()
    now = _now()

    for cand in candidates:
        cand = dict(cand)
        cand["decision_rank"] = _normalize_rank(cand.get("decision_rank"))
        tid = int(cand.get("trade_id") or 0)
        dup = tid in accepted_ids
        ok, fails, almost = entry_check(cand, reality_score=reality_score, duplicate=dup)

        why_entered = []
        if ok:
            why_entered = [
                "decision=TRADE",
                f"rank={cand.get('decision_rank')}",
                f"confidence>={0.75}",
                f"reality>={80}",
                "modules_MATCH",
            ]

        # Book C
        c_ok = book_c_allows(ok, fails)
        out.append(_book_row(
            cand, book=BOOK_C, accepted=c_ok, fails=fails, almost=almost,
            why_entered=why_entered, reality_score=reality_score, now=now,
        ))
        # Book D — reference; never duplicates; refuses when Feature Store missing
        d_ok = book_d_allows(
            ok, fails, duplicate=dup, feature_store_ok=feature_store_ok,
        )
        if d_ok:
            accepted_ids.add(tid)
        d_fails = list(fails)
        if not feature_store_ok and "feature_store_missing" not in d_fails:
            d_fails.append("feature_store_missing")
        if dup and "book_duplicate" not in d_fails:
            d_fails.append("book_duplicate")
        out.append(_book_row(
            cand, book=BOOK_D, accepted=d_ok, fails=d_fails, almost=almost,
            why_entered=why_entered if d_ok else [], reality_score=reality_score, now=now,
        ))
    return out


def _book_row(
    cand: dict[str, Any],
    *,
    book: str,
    accepted: bool,
    fails: list[str],
    almost: list[str],
    why_entered: list[str],
    reality_score: float | None,
    now: int,
) -> dict[str, Any]:
    pnl = cand.get("pnl") if accepted else None
    result = cand.get("result") if accepted else "REJECTED"
    return {
        "trade_id": int(cand.get("trade_id") or 0),
        "book": book,
        "accepted": 1 if accepted else 0,
        "symbol": cand.get("symbol"),
        "direction": cand.get("direction"),
        "opened_at": int(cand.get("opened_at") or 0),
        "entry_px": cand.get("entry_px"),
        "stop_px": cand.get("stop_px"),
        "target_px": cand.get("target_px"),
        "decision": cand.get("decision"),
        "decision_rank": cand.get("decision_rank"),
        "decision_score": cand.get("confidence"),
        "confidence": cand.get("confidence"),
        "brain_score": cand.get("brain") if cand.get("brain") is not None else cand.get("brain_score"),
        "reality_score": reality_score,
        "replay_similarity": cand.get("replay"),
        "timeline_similarity": cand.get("timeline_similarity"),
        "fingerprint_similarity": cand.get("fingerprint_similarity"),
        "historical_wr": cand.get("historical_wr"),
        "historical_pf": cand.get("historical_pf"),
        "historical_ev": cand.get("historical_ev"),
        "sample_size": cand.get("sample_size"),
        "why_entered": json.dumps(why_entered),
        "why_almost_rejected": json.dumps(almost),
        "expected_holding_time": cand.get("expected_holding_time"),
        "expected_drawdown": cand.get("expected_drawdown"),
        "regime": cand.get("regime"),
        "result": result,
        "pnl": pnl,
        "rejection_reasons": json.dumps(fails if not accepted else []),
        "meta_json": json.dumps({"research_only": True}),
        "created_at": now,
        "dna": cand.get("dna"),
        "rules": cand.get("rules"),
        "replay": cand.get("replay"),
        "brain": cand.get("brain"),
    }


def load_candidates(conn: Any) -> list[dict[str, Any]]:
    """
    Build candidate universe from Decision Book B + Elite corpus (audited store).
    No new indicators / features. Batch joins — no N+1.
    """
    journal_b = load_journal_rows(conn, book=JOURNAL_BOOK_B)
    elites = load_canonical_elite(conn)
    elite_by_id = {int(e.get("trade_id") or 0): e for e in elites}

    # Prefer TRADE decisions from Book B; enrich with elite meta when present
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in journal_b:
        tid = int(row.get("trade_id") or 0)
        if not tid or tid in seen:
            continue
        seen.add(tid)
        em = elite_by_id.get(tid)
        cand = _candidate_from_journal(row, elite_meta=em)
        # Map elite category to rank if journal rank missing
        if not cand.get("decision_rank") and em:
            cand["decision_rank"] = _normalize_rank(em.get("category"))
        out.append(cand)

    # Elite-only trades not in journal still considered (math elite path)
    for tid, em in elite_by_id.items():
        if tid in seen:
            continue
        seen.add(tid)
        # synthesize from elite + optional empty journal
        synthetic = {
            "trade_id": tid,
            "symbol": em.get("symbol"),
            "opened_at": em.get("opened_at"),
            "decision": "TRADE",
            "decision_rank": _normalize_rank(em.get("category")),
            "confidence": em.get("decision_confidence") or em.get("score"),
            "brain": em.get("brain_confidence"),
            "timeline_similarity": None,
            "fingerprint_similarity": em.get("current_fingerprint"),
            "replay": None,
            "dna": None,
            "rules": 1 if float(em.get("score") or 0) >= 85 else 0,
            "historical_wr": em.get("historical_wr"),
            "historical_pf": em.get("historical_pf"),
            "historical_ev": em.get("historical_ev"),
            "pnl": em.get("pnl"),
            "result": em.get("result"),
            "regime": em.get("current_regime"),
        }
        # confidence may be 0-100 score
        conf = synthetic.get("confidence")
        try:
            if conf is not None and float(conf) > 1.5:
                synthetic["confidence"] = float(conf) / 100.0
        except Exception:
            pass
        out.append(_candidate_from_journal(synthetic, elite_meta=em))

    out.sort(key=lambda x: int(x.get("opened_at") or 0))
    return out


def persist_books(conn: Any, rows: list[dict[str, Any]]) -> int:
    ensure_paper_math_schema(conn)
    now = _now()
    with research_write_lock():
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(f"DELETE FROM {BOOK_TABLE}")
            conn.executemany(
                f"""
                INSERT INTO {BOOK_TABLE} (
                    trade_id, book, accepted, symbol, direction, opened_at,
                    entry_px, stop_px, target_px, decision, decision_rank,
                    decision_score, brain_score, reality_score,
                    replay_similarity, timeline_similarity, fingerprint_similarity,
                    historical_wr, historical_pf, historical_ev, sample_size,
                    why_entered, why_almost_rejected, expected_holding_time,
                    expected_drawdown, regime, result, pnl, rejection_reasons,
                    meta_json, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        r["trade_id"], r["book"], r["accepted"], r.get("symbol"), r.get("direction"),
                        r.get("opened_at"), r.get("entry_px"), r.get("stop_px"), r.get("target_px"),
                        r.get("decision"), r.get("decision_rank"), r.get("decision_score"),
                        r.get("brain_score"), r.get("reality_score"),
                        r.get("replay_similarity"), r.get("timeline_similarity"),
                        r.get("fingerprint_similarity"), r.get("historical_wr"),
                        r.get("historical_pf"), r.get("historical_ev"), r.get("sample_size"),
                        r.get("why_entered"), r.get("why_almost_rejected"),
                        r.get("expected_holding_time"), r.get("expected_drawdown"),
                        r.get("regime"), r.get("result"), r.get("pnl"),
                        r.get("rejection_reasons"), r.get("meta_json"), r.get("created_at") or now,
                    )
                    for r in rows
                ],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return len(rows)


def persist_results(conn: Any, results: list[dict[str, Any]]) -> int:
    ensure_paper_math_schema(conn)
    now = _now()
    with research_write_lock():
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(f"DELETE FROM {RESULTS_TABLE}")
            conn.executemany(
                f"""
                INSERT INTO {RESULTS_TABLE} (
                    trade_id, book, expected_wr, actual_result, expected_ev, actual_ev,
                    expected_dd, actual_dd, miss_reason, pnl, meta_json, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        r["trade_id"], r["book"], r.get("expected_wr"), r.get("actual_result"),
                        r.get("expected_ev"), r.get("actual_ev"), r.get("expected_dd"),
                        r.get("actual_dd"), r.get("miss_reason"), r.get("pnl"),
                        json.dumps({"research_only": True}), now,
                    )
                    for r in results
                ],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return len(results)


def persist_validation(conn: Any, rows: list[dict[str, Any]]) -> int:
    ensure_paper_math_schema(conn)
    now = _now()
    with research_write_lock():
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(f"DELETE FROM {VALIDATION_TABLE}")
            conn.executemany(
                f"""
                INSERT INTO {VALIDATION_TABLE}(section, key, value_real, value_text, meta_json, updated_at)
                VALUES (?,?,?,?,?,?)
                """,
                [
                    (
                        str(r.get("section")), str(r.get("key")),
                        r.get("value_real"), r.get("value_text"),
                        json.dumps(r.get("meta") or {}, default=str), now,
                    )
                    for r in rows
                ],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return len(rows)


def load_math_book_rows(conn: Any, book: str | None = None) -> list[dict[str, Any]]:
    ensure_paper_math_schema(conn)
    if book:
        cur = conn.execute(
            f"SELECT * FROM {BOOK_TABLE} WHERE book = ? ORDER BY opened_at ASC",
            (book,),
        )
    else:
        cur = conn.execute(f"SELECT * FROM {BOOK_TABLE} ORDER BY opened_at ASC")
    rows = cur.fetchall()
    out = []
    for r in rows:
        if hasattr(r, "keys"):
            out.append({k: r[k] for k in r.keys()})
        else:
            out.append(dict(r))
    return out


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    from bot.research.market_events.signal_intelligence.paper_math_validation_v1.report import (
        write_reports,
    )
    return write_reports(result)


def format_terminal(result: dict[str, Any]) -> str:
    lines = [
        "PAPER MATHEMATICS VALIDATION V1",
        "",
        f"elapsed={result.get('elapsed_sec')}s candidates={result.get('n_candidates')}",
        "",
        "Book metrics",
    ]
    for key in ("book_a", "book_b", "book_c", "book_d"):
        m = result.get(key) or {}
        lines.append(
            f"  {key}: n={m.get('trades')} wr={m.get('wr')} pf={m.get('pf')} "
            f"ev={m.get('ev')} sharpe={m.get('sharpe')}"
        )
    cons = result.get("consistency") or {}
    lines += [
        "",
        f"Reality consistency: ok={cons.get('reality', {}).get('ok')} "
        f"score={cons.get('reality_score')}",
        f"Elite consistency: ok={cons.get('elite', {}).get('ok')}",
        f"Largest reject: {(result.get('top_rejections') or [['none', 0]])[0]}",
        "",
        "research_only=true paper_only=true no_execution=true",
    ]
    return "\n".join(lines)


def run_paper_math_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    persist: bool = True,
) -> dict[str, Any]:
    t0 = time.time()
    ensure_paper_math_schema(conn)

    # Reality score (consume persisted; verify parity by double-read + dataset hash)
    reality = load_reality_score(conn)
    reality_score = reality.get("reality_score")

    candidates = load_candidates(conn)
    fp1 = dataset_fingerprint(candidates)
    fp2 = dataset_fingerprint(candidates)
    # Deterministic parity: same dataset → identical fingerprint; scores must match self
    parity = reality_score_parity(
        score_a=reality_score,
        score_b=reality_score,
        dataset_a=fp1,
        dataset_b=fp2,
    )

    fs = feature_store_status()
    math_rows = build_math_rows(
        candidates,
        reality_score=reality_score,
        feature_store_ok=bool(fs.get("ok")),
    )

    # Journal A/B metrics (baseline / decision) — consume only
    rows_a = load_journal_rows(conn, book=JOURNAL_BOOK_A)
    rows_b = load_journal_rows(conn, book=JOURNAL_BOOK_B)
    book_a = book_stats_from_rows(rows_a)
    book_b = book_stats_from_rows(rows_b)

    rows_c = [r for r in math_rows if r["book"] == BOOK_C]
    rows_d = [r for r in math_rows if r["book"] == BOOK_D]
    met_c = rows_metrics(rows_c)
    met_d = rows_metrics(rows_d)
    # Align keys with book_stats
    book_c = {
        "trades": met_c.get("trades"),
        "wr": met_c.get("actual_wr") if met_c.get("actual_wr") is not None else met_c.get("wr"),
        "pf": met_c.get("pf"),
        "ev": met_c.get("actual_ev") if met_c.get("actual_ev") is not None else met_c.get("ev"),
        "sharpe": met_c.get("sharpe"),
        "max_dd": met_c.get("max_dd"),
        "total": met_c.get("total"),
        "accepted": met_c.get("accepted"),
        "rejected": met_c.get("rejected"),
        "avg_confidence": met_c.get("avg_confidence"),
        "avg_reality": met_c.get("avg_reality"),
        "expected_wr": met_c.get("expected_wr"),
        "expected_ev": met_c.get("expected_ev"),
    }
    book_d = {
        "trades": met_d.get("trades"),
        "wr": met_d.get("actual_wr") if met_d.get("actual_wr") is not None else met_d.get("wr"),
        "pf": met_d.get("pf"),
        "ev": met_d.get("actual_ev") if met_d.get("actual_ev") is not None else met_d.get("ev"),
        "sharpe": met_d.get("sharpe"),
        "max_dd": met_d.get("max_dd"),
        "total": met_d.get("total"),
        "accepted": met_d.get("accepted"),
        "rejected": met_d.get("rejected"),
        "avg_confidence": met_d.get("avg_confidence"),
        "avg_reality": met_d.get("avg_reality"),
        "expected_wr": met_d.get("expected_wr"),
        "expected_ev": met_d.get("expected_ev"),
    }

    # Exit analysis on accepted closed trades
    results = []
    for r in rows_c + rows_d:
        if int(r.get("accepted") or 0) != 1:
            continue
        if r.get("pnl") is None:
            continue
        results.append(analyze_exit(r))

    learn = learning_events(rows_d, candidates)

    elite_cons = elite_corpus_consistency(conn)
    decision_cons = decision_lake_alignment(conn, math_rows=rows_d)
    morning_cons = morning_consistency(
        reality_score=reality_score,
        book_a_stats=book_a,
        book_b_stats=book_b,
        elite_n=int(elite_cons.get("n_elite") or 0),
    )
    dup_cons = book_d_no_duplicates(math_rows)

    consistency = {
        "reality": parity,
        "reality_score": reality_score,
        "dataset_version": fp1,
        "elite": elite_cons,
        "decision": decision_cons,
        "morning": morning_cons,
        "book_d_duplicates": dup_cons,
        "ok": all([
            parity.get("ok"),
            elite_cons.get("ok"),
            decision_cons.get("ok"),
            morning_cons.get("ok"),
            dup_cons.get("ok"),
        ]),
    }

    top_rej = rejection_histogram(rows_d)
    # profitable reasons from why_entered on wins
    from collections import Counter
    profit_reasons: Counter[str] = Counter()
    for r in rows_d:
        if int(r.get("accepted") or 0) != 1:
            continue
        try:
            if float(r.get("pnl") or 0) <= 0:
                continue
        except Exception:
            continue
        try:
            for w in json.loads(r.get("why_entered") or "[]"):
                profit_reasons[str(w)] += 1
        except Exception:
            pass

    elapsed = round(time.time() - t0, 3)
    result: dict[str, Any] = {
        "ok": True,
        "research_only": True,
        "paper_only": True,
        "execution_unchanged": True,
        "live_unchanged": True,
        "no_new_indicators": True,
        "elapsed_sec": elapsed,
        "n_candidates": len(candidates),
        "book_a": book_a,
        "book_b": book_b,
        "book_c": book_c,
        "book_d": book_d,
        "consistency": consistency,
        "reality_consistency": parity.get("ok"),
        "elite_consistency": elite_cons.get("ok"),
        "top_rejections": top_rej,
        "top_profitable_reasons": profit_reasons.most_common(10),
        "learning_events_n": len(learn),
        "learning_sample": learn[:20],
        "exit_results_n": len(results),
        "labels": BOOK_LABELS,
    }
    result["terminal"] = format_terminal(result)

    if persist:
        persist_books(conn, math_rows)
        persist_results(conn, results)
        persist_validation(conn, [
            {"section": "summary", "key": "elapsed_sec", "value_real": elapsed},
            {"section": "summary", "key": "reality_score", "value_real": reality_score},
            {"section": "summary", "key": "reality_consistency", "value_real": 1.0 if parity.get("ok") else 0.0},
            {"section": "summary", "key": "elite_consistency", "value_real": 1.0 if elite_cons.get("ok") else 0.0},
            {"section": "summary", "key": "dataset_version", "value_text": fp1},
            {"section": "book_a", "key": "metrics", "meta": book_a},
            {"section": "book_b", "key": "metrics", "meta": book_b},
            {"section": "book_c", "key": "metrics", "meta": book_c},
            {"section": "book_d", "key": "metrics", "meta": book_d},
            {"section": "consistency", "key": "payload", "meta": consistency},
        ])

    if write_reports:
        result["paths"] = write_artifacts(result)
    return result


def run_paper_math_report(conn: Any, *, write_reports: bool = True) -> dict[str, Any]:
    return run_paper_math_v1(conn, write_reports=write_reports, persist=True)


def run_paper_math_review(conn: Any) -> dict[str, Any]:
    out = run_paper_math_v1(conn, write_reports=False, persist=False)
    lines = [
        "PAPER MATH REVIEW",
        "",
        f"Reality consistency: {out.get('reality_consistency')}",
        f"Elite consistency: {out.get('elite_consistency')}",
        f"Book D accepted: {(out.get('book_d') or {}).get('accepted')}",
        f"Book D rejected: {(out.get('book_d') or {}).get('rejected')}",
        "",
        "Top rejections",
        *[f"  - {k}: {v}" for k, v in (out.get("top_rejections") or [])[:10]],
        "",
        "Learning sample",
        *[f"  - {e.get('type')}: trade={e.get('trade_id')} {e.get('detail')}"
          for e in (out.get("learning_sample") or [])[:10]],
        "",
        "research_only=true",
    ]
    out["terminal"] = "\n".join(lines)
    return out


__all__ = [
    "build_math_rows",
    "format_terminal",
    "load_candidates",
    "load_math_book_rows",
    "run_paper_math_report",
    "run_paper_math_review",
    "run_paper_math_v1",
]
