"""Forward Validation Monitor V1 — observe-only incremental trade tracking."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.research_db_session import research_write_lock
from bot.research.market_events.signal_intelligence.elite_candidate_v1.score import (
    STORE_CATEGORIES,
)
from bot.research.market_events.signal_intelligence.elite_candidate_v1.store import (
    load_stored_candidates,
)
from bot.research.market_events.signal_intelligence.forward_validation_v1.books import (
    BOOK_A,
    BOOK_B,
    BOOK_C,
    BOOK_D,
    BOOK_LABELS,
)
from bot.research.market_events.signal_intelligence.forward_validation_v1.metrics import (
    closed_metrics,
    detect_alerts,
)
from bot.research.market_events.signal_intelligence.forward_validation_v1.schema import (
    STATE_TABLE,
    TABLE,
    ensure_forward_validation_schema,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
    BOOK_A as JA,
    BOOK_B as JB,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.journal import (
    load_journal_rows,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.books import (
    BOOK_C as MC,
    BOOK_D as MD,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.schema import (
    BOOK_TABLE as MATH_BOOK_TABLE,
)

OUT_DIR = BASE_DIR / "reports" / "research" / "forward_validation_v1"

WEEK_SEC = 7 * 86400


def _now() -> int:
    return int(time.time())


def _load_reality_score(conn: Any) -> float | None:
    try:
        row = conn.execute(
            """
            SELECT value_real FROM reality_validation_v1
            WHERE section='summary' AND key='reality_score'
            """
        ).fetchone()
        if row is None:
            return None
        v = row["value_real"] if hasattr(row, "keys") else row[0]
        return float(v) if v is not None else None
    except Exception:
        return None


def _elite_score_map(conn: Any) -> dict[int, float]:
    out: dict[int, float] = {}
    try:
        for r in load_stored_candidates(conn, categories=list(STORE_CATEGORIES)):
            tid = int(r.get("trade_id") or 0)
            if not tid:
                continue
            sc = r.get("score")
            if sc is None:
                sc = r.get("decision_confidence")
            try:
                out[tid] = float(sc)
            except Exception:
                continue
    except Exception:
        pass
    return out


def _math_book_rows(conn: Any, book: str) -> list[dict[str, Any]]:
    try:
        ensure_forward_validation_schema(conn)
        cur = conn.execute(
            f"SELECT * FROM {MATH_BOOK_TABLE} WHERE book = ? AND accepted = 1",
            (book,),
        )
        rows = cur.fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        if hasattr(r, "keys"):
            out.append({k: r[k] for k in r.keys()})
        else:
            out.append(dict(r))
    return out


def _signal_from_journal(row: dict[str, Any], book: str, *, reality: float | None, elite: float | None) -> dict[str, Any]:
    return {
        "trade_id": int(row.get("trade_id") or 0),
        "book": book,
        "opened_at": int(row.get("opened_at") or 0),
        "symbol": row.get("symbol"),
        "direction": row.get("direction"),
        "decision": row.get("decision"),
        "confidence": row.get("confidence") if row.get("confidence") is not None else row.get("decision_score"),
        "brain_score": row.get("brain") if row.get("brain") is not None else row.get("brain_score"),
        "timeline_similarity": row.get("timeline_similarity"),
        "fingerprint_similarity": row.get("fingerprint_similarity"),
        "historical_wr": row.get("historical_wr"),
        "historical_pf": row.get("historical_pf"),
        "historical_ev": row.get("historical_ev"),
        "reality_score": reality,
        "elite_score": elite,
        "entry_price": row.get("entry_px") or row.get("entry_price"),
        "expected_stop": row.get("stop_px") or row.get("expected_stop"),
        "expected_target": row.get("target_px") or row.get("expected_target"),
        "result": row.get("result"),
        "pnl": row.get("pnl"),
        "expected_wr": row.get("historical_wr"),
    }


def collect_source_signals(conn: Any) -> list[dict[str, Any]]:
    """Gather accepted signals from A/B journal + C/D math books. No rebuild."""
    reality = _load_reality_score(conn)
    elite_map = _elite_score_map(conn)
    out: list[dict[str, Any]] = []

    for book_id, jbook in ((BOOK_A, JA), (BOOK_B, JB)):
        rows = load_journal_rows(conn, book=jbook, accepted_only=True)
        for r in rows:
            tid = int(r.get("trade_id") or 0)
            out.append(_signal_from_journal(
                r, book_id, reality=reality, elite=elite_map.get(tid),
            ))

    for book_id, mbook in ((BOOK_C, MC), (BOOK_D, MD)):
        for r in _math_book_rows(conn, mbook):
            tid = int(r.get("trade_id") or 0)
            out.append(_signal_from_journal(
                r, book_id, reality=reality, elite=elite_map.get(tid),
            ))
    return out


def _existing_keys(conn: Any) -> dict[tuple[int, str], dict[str, Any]]:
    ensure_forward_validation_schema(conn)
    cur = conn.execute(f"SELECT * FROM {TABLE}")
    out: dict[tuple[int, str], dict[str, Any]] = {}
    for r in cur.fetchall():
        d = {k: r[k] for k in r.keys()} if hasattr(r, "keys") else dict(r)
        out[(int(d["trade_id"]), str(d["book"]))] = d
    return out


def _is_closed(sig: dict[str, Any]) -> bool:
    res = str(sig.get("result") or "").upper()
    if res in ("WIN", "LOSS", "FLAT", "CLOSED", "TIMEOUT"):
        return True
    if res == "REJECTED":
        return False
    # pnl present ⇒ treated as closed outcome for paper journals
    if sig.get("pnl") is not None:
        return True
    return False


def _close_fields(sig: dict[str, Any], opened_at: int) -> dict[str, Any]:
    pnl = None
    try:
        if sig.get("pnl") is not None:
            pnl = float(sig["pnl"])
    except Exception:
        pnl = None
    entry = None
    try:
        if sig.get("entry_price") is not None:
            entry = float(sig["entry_price"])
    except Exception:
        entry = None
    pnl_pct = None
    if pnl is not None and entry is not None and abs(entry) > 1e-12:
        pnl_pct = round(100.0 * pnl / abs(entry), 6)
    elif pnl is not None:
        # unit-notional research approx ($100)
        pnl_pct = round(pnl, 6)

    closed_at = int(sig.get("closed_at") or sig.get("opened_at") or opened_at or _now())
    holding = None
    oa = int(opened_at or 0)
    if oa > 0 and closed_at >= oa:
        holding = float(closed_at - oa)
    else:
        holding = 300.0  # default expected hold proxy when close ts missing

    # MAE / MFE from available fields only (no new indicators)
    mae = abs(pnl) if pnl is not None and pnl < 0 else 0.0
    mfe = abs(pnl) if pnl is not None and pnl > 0 else 0.0
    if sig.get("expected_stop") is not None and entry is not None:
        try:
            mae = max(mae, abs(float(sig["expected_stop"]) - entry))
        except Exception:
            pass
    if sig.get("expected_target") is not None and entry is not None:
        try:
            mfe = max(mfe, abs(float(sig["expected_target"]) - entry))
        except Exception:
            pass

    actual = sig.get("result")
    if actual is None and pnl is not None:
        actual = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "FLAT")
    actual_u = str(actual or "").upper()

    exp_wr = sig.get("expected_wr") if sig.get("expected_wr") is not None else sig.get("historical_wr")
    pred_correct = None
    try:
        if exp_wr is not None:
            e = float(exp_wr)
            if e > 1.5:
                e = e / 100.0
            # predict win if expected WR >= 50%
            predicted_win = e >= 0.5
            actual_win = actual_u == "WIN"
            pred_correct = 1 if predicted_win == actual_win else 0
    except Exception:
        pred_correct = None

    return {
        "status": "closed",
        "closed_at": closed_at,
        "result": actual_u or None,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "holding_time_sec": holding,
        "mae": round(mae, 6) if mae is not None else None,
        "mfe": round(mfe, 6) if mfe is not None else None,
        "expected_wr": exp_wr,
        "actual_result": actual_u or None,
        "prediction_correct": pred_correct,
    }


def incremental_sync(conn: Any) -> dict[str, Any]:
    """
    Incremental only: insert NEW open trades; update OPEN→CLOSED when outcome appears.
    Never rebuilds lake / replay.
    """
    ensure_forward_validation_schema(conn)
    existing = _existing_keys(conn)
    signals = collect_source_signals(conn)
    now = _now()
    new_n = 0
    closed_n = 0
    inserts: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []

    for sig in signals:
        tid = int(sig.get("trade_id") or 0)
        book = str(sig.get("book") or "")
        if not tid or not book:
            continue
        key = (tid, book)
        closed = _is_closed(sig)
        if key not in existing:
            row = {
                "trade_id": tid,
                "book": book,
                "status": "open",
                "opened_at": int(sig.get("opened_at") or now),
                "closed_at": None,
                "symbol": sig.get("symbol"),
                "direction": sig.get("direction"),
                "decision": sig.get("decision"),
                "confidence": sig.get("confidence"),
                "brain_score": sig.get("brain_score"),
                "timeline_similarity": sig.get("timeline_similarity"),
                "fingerprint_similarity": sig.get("fingerprint_similarity"),
                "historical_wr": sig.get("historical_wr"),
                "historical_pf": sig.get("historical_pf"),
                "historical_ev": sig.get("historical_ev"),
                "reality_score": sig.get("reality_score"),
                "elite_score": sig.get("elite_score"),
                "entry_price": sig.get("entry_price"),
                "expected_stop": sig.get("expected_stop"),
                "expected_target": sig.get("expected_target"),
                "result": None,
                "pnl": None,
                "pnl_pct": None,
                "holding_time_sec": None,
                "mae": None,
                "mfe": None,
                "expected_wr": sig.get("expected_wr"),
                "actual_result": None,
                "prediction_correct": None,
                "meta_json": json.dumps({"research_only": True, "observe_only": True}),
                "created_at": now,
                "updated_at": now,
            }
            if closed:
                row.update(_close_fields(sig, int(row["opened_at"])))
                closed_n += 1
            new_n += 1
            inserts.append(row)
            continue

        prev = existing[key]
        if str(prev.get("status") or "") == "open" and closed:
            u = dict(prev)
            u.update(_close_fields(sig, int(prev.get("opened_at") or now)))
            u["updated_at"] = now
            # refresh scores if newly available
            for k in ("reality_score", "elite_score", "confidence", "brain_score"):
                if sig.get(k) is not None:
                    u[k] = sig.get(k)
            updates.append(u)
            closed_n += 1

    with research_write_lock():
        conn.execute("BEGIN IMMEDIATE")
        try:
            if inserts:
                conn.executemany(
                    f"""
                    INSERT OR IGNORE INTO {TABLE} (
                        trade_id, book, status, opened_at, closed_at, symbol, direction,
                        decision, confidence, brain_score, timeline_similarity,
                        fingerprint_similarity, historical_wr, historical_pf, historical_ev,
                        reality_score, elite_score, entry_price, expected_stop, expected_target,
                        result, pnl, pnl_pct, holding_time_sec, mae, mfe, expected_wr,
                        actual_result, prediction_correct, meta_json, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        (
                            r["trade_id"], r["book"], r["status"], r.get("opened_at"), r.get("closed_at"),
                            r.get("symbol"), r.get("direction"), r.get("decision"), r.get("confidence"),
                            r.get("brain_score"), r.get("timeline_similarity"), r.get("fingerprint_similarity"),
                            r.get("historical_wr"), r.get("historical_pf"), r.get("historical_ev"),
                            r.get("reality_score"), r.get("elite_score"), r.get("entry_price"),
                            r.get("expected_stop"), r.get("expected_target"), r.get("result"),
                            r.get("pnl"), r.get("pnl_pct"), r.get("holding_time_sec"), r.get("mae"),
                            r.get("mfe"), r.get("expected_wr"), r.get("actual_result"),
                            r.get("prediction_correct"), r.get("meta_json"), r["created_at"], r["updated_at"],
                        )
                        for r in inserts
                    ],
                )
            for u in updates:
                conn.execute(
                    f"""
                    UPDATE {TABLE} SET
                        status=?, closed_at=?, result=?, pnl=?, pnl_pct=?, holding_time_sec=?,
                        mae=?, mfe=?, expected_wr=?, actual_result=?, prediction_correct=?,
                        reality_score=?, elite_score=?, confidence=?, brain_score=?, updated_at=?
                    WHERE trade_id=? AND book=?
                    """,
                    (
                        u["status"], u.get("closed_at"), u.get("result"), u.get("pnl"), u.get("pnl_pct"),
                        u.get("holding_time_sec"), u.get("mae"), u.get("mfe"), u.get("expected_wr"),
                        u.get("actual_result"), u.get("prediction_correct"), u.get("reality_score"),
                        u.get("elite_score"), u.get("confidence"), u.get("brain_score"), u["updated_at"],
                        u["trade_id"], u["book"],
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {"new_tracked": new_n, "closed_tracked": closed_n, "sources": len(signals)}


def load_tracked(conn: Any, *, since: int | None = None, book: str | None = None) -> list[dict[str, Any]]:
    ensure_forward_validation_schema(conn)
    q = f"SELECT * FROM {TABLE} WHERE 1=1"
    args: list[Any] = []
    if since is not None:
        q += " AND opened_at >= ?"
        args.append(int(since))
    if book is not None:
        q += " AND book = ?"
        args.append(book)
    q += " ORDER BY opened_at ASC"
    rows = conn.execute(q, args).fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def _get_state(conn: Any, key: str) -> dict[str, Any] | None:
    ensure_forward_validation_schema(conn)
    row = conn.execute(
        f"SELECT value_text, value_real FROM {STATE_TABLE} WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    text = row["value_text"] if hasattr(row, "keys") else row[0]
    if text:
        try:
            return json.loads(text)
        except Exception:
            return {"value_text": text}
    real = row["value_real"] if hasattr(row, "keys") else row[1]
    return {"value_real": real}


def _set_state(conn: Any, key: str, payload: dict[str, Any]) -> None:
    ensure_forward_validation_schema(conn)
    now = _now()
    with research_write_lock():
        conn.execute(
            f"""
            INSERT INTO {STATE_TABLE}(key, value_text, value_real, updated_at)
            VALUES (?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET
                value_text=excluded.value_text,
                value_real=excluded.value_real,
                updated_at=excluded.updated_at
            """,
            (key, json.dumps(payload, default=str), payload.get("reality_score"), now),
        )
        try:
            conn.commit()
        except Exception:
            pass


def write_daily_report(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    m = result.get("metrics") or {}
    alert_lines = [f"- {a.get('type')}: {a}" for a in (result.get("alerts") or [])]
    if not alert_lines:
        alert_lines = ["- none"]
    lines = [
        "# FORWARD_VALIDATION_REPORT",
        "",
        "_Forward Validation Monitor V1 — observe only. Research freeze._",
        "",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- new_tracked: **{result.get('new_tracked')}**",
        f"- closed_tracked: **{result.get('closed_tracked')}**",
        f"- open: **{m.get('n_open')}**",
        f"- closed: **{m.get('n_closed')}**",
        "",
        "## Metrics (tracked forward set)",
        f"- Win Rate: {m.get('wr')}",
        f"- PF: {m.get('pf')}",
        f"- EV: {m.get('ev')}",
        f"- Sharpe: {m.get('sharpe')}",
        f"- Average hold (sec): {m.get('avg_hold_sec')}",
        f"- Average DD (MAE): {m.get('avg_dd')}",
        f"- Prediction Accuracy: {m.get('prediction_accuracy')}",
        f"- Expected WR vs Actual WR: {m.get('expected_wr')} vs {m.get('actual_wr')}",
        f"- Expected EV vs Actual EV: {m.get('expected_ev')} vs {m.get('actual_ev')}",
        "",
        "## Alerts",
        *alert_lines,
        "",
        "observe_only=true no_strategy_change=true",
        "",
    ]
    text = "\n".join(lines)
    paths = {}
    for name in ("FORWARD_VALIDATION_REPORT.md",):
        (BASE_DIR / name).write_text(text, encoding="utf-8")
        (OUT_DIR / name).write_text(text, encoding="utf-8")
        paths[name] = str(BASE_DIR / name)
    jp = OUT_DIR / "forward_validation.json"
    jp.write_text(json.dumps({
        k: result.get(k) for k in (
            "ok", "elapsed_sec", "new_tracked", "closed_tracked", "metrics", "alerts", "by_book",
        )
    }, indent=2, default=str), encoding="utf-8")
    paths["json"] = str(jp)
    return paths


def write_weekly_report(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    alert_lines = [f"- {a.get('type')}: {a}" for a in (result.get("alerts") or [])]
    if not alert_lines:
        alert_lines = ["- none"]
    lines = [
        "# FORWARD_WEEKLY",
        "",
        "_Compare Book A/B/C/D using NEW trades only. Historical forbidden._",
        "",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- window_start: {result.get('window_start')}",
        f"- n_new_in_window: {result.get('n_new_in_window')}",
        "",
        "| Book | Closed | WR | PF | EV | Sharpe | PredAcc |",
        "|------|--------|----|----|----|--------|---------|",
    ]
    for key, label in (
        ("book_a", "A"), ("book_b", "B"), ("book_c", "C"), ("book_d", "D"),
    ):
        m = (result.get("by_book") or {}).get(key) or {}
        lines.append(
            f"| {label} | {m.get('n_closed')} | {m.get('wr')} | {m.get('pf')} | "
            f"{m.get('ev')} | {m.get('sharpe')} | {m.get('prediction_accuracy')} |"
        )
    lines += [
        "",
        "## Alerts",
        *alert_lines,
        "",
    ]
    text = "\n".join(lines)
    paths = {}
    (BASE_DIR / "FORWARD_WEEKLY.md").write_text(text, encoding="utf-8")
    (OUT_DIR / "FORWARD_WEEKLY.md").write_text(text, encoding="utf-8")
    paths["FORWARD_WEEKLY.md"] = str(BASE_DIR / "FORWARD_WEEKLY.md")
    return paths


def format_terminal(result: dict[str, Any]) -> str:
    m = result.get("metrics") or {}
    return "\n".join([
        "FORWARD VALIDATION MONITOR V1",
        "",
        f"elapsed={result.get('elapsed_sec')}s",
        f"new_tracked={result.get('new_tracked')} closed_tracked={result.get('closed_tracked')}",
        f"open={m.get('n_open')} closed={m.get('n_closed')}",
        f"WR={m.get('wr')} PF={m.get('pf')} EV={m.get('ev')} Sharpe={m.get('sharpe')}",
        f"pred_acc={m.get('prediction_accuracy')}",
        f"alerts={len(result.get('alerts') or [])}",
        "",
        "observe_only=true research_freeze=true",
    ])


def run_forward_monitor_v1(
    conn: Any,
    *,
    write_reports: bool = True,
) -> dict[str, Any]:
    t0 = time.time()
    sync = incremental_sync(conn)
    rows = load_tracked(conn)
    metrics = closed_metrics(rows)
    by_book = {}
    for book, key in (
        (BOOK_A, "book_a"), (BOOK_B, "book_b"), (BOOK_C, "book_c"), (BOOK_D, "book_d"),
    ):
        by_book[key] = closed_metrics([r for r in rows if r.get("book") == book])

    reality = _load_reality_score(conn)
    current_snap = {
        **metrics,
        "reality_score": reality,
    }
    prev = _get_state(conn, "daily_snapshot")
    alerts = detect_alerts(current=current_snap, previous=prev, book_metrics=by_book)
    _set_state(conn, "daily_snapshot", current_snap)

    elapsed = round(time.time() - t0, 3)
    result = {
        "ok": True,
        "research_only": True,
        "observe_only": True,
        "execution_unchanged": True,
        "no_new_indicators": True,
        "elapsed_sec": elapsed,
        "new_tracked": sync.get("new_tracked"),
        "closed_tracked": sync.get("closed_tracked"),
        "metrics": metrics,
        "by_book": by_book,
        "alerts": alerts,
        "labels": BOOK_LABELS,
    }
    result["terminal"] = format_terminal(result)
    if write_reports:
        result["paths"] = write_daily_report(result)
    return result


def run_forward_report(conn: Any, *, write_reports: bool = True) -> dict[str, Any]:
    return run_forward_monitor_v1(conn, write_reports=write_reports)


def run_forward_weekly(conn: Any, *, write_reports: bool = True) -> dict[str, Any]:
    t0 = time.time()
    # Ensure incremental catch-up first (still no rebuild)
    sync = incremental_sync(conn)
    now = _now()
    since = now - WEEK_SEC
    rows = load_tracked(conn, since=since)
    # NEW trades only = opened in window
    by_book = {}
    for book, key in (
        (BOOK_A, "book_a"), (BOOK_B, "book_b"), (BOOK_C, "book_c"), (BOOK_D, "book_d"),
    ):
        by_book[key] = closed_metrics([r for r in rows if r.get("book") == book])

    overall = closed_metrics(rows)
    reality = _load_reality_score(conn)
    current_snap = {**overall, "reality_score": reality}
    prev = _get_state(conn, "weekly_snapshot")
    alerts = detect_alerts(current=current_snap, previous=prev, book_metrics=by_book)
    _set_state(conn, "weekly_snapshot", current_snap)

    elapsed = round(time.time() - t0, 3)
    result = {
        "ok": True,
        "research_only": True,
        "observe_only": True,
        "elapsed_sec": elapsed,
        "window_start": since,
        "n_new_in_window": len(rows),
        "new_tracked": sync.get("new_tracked"),
        "closed_tracked": sync.get("closed_tracked"),
        "by_book": by_book,
        "metrics": overall,
        "alerts": alerts,
    }
    result["terminal"] = "\n".join([
        "FORWARD WEEKLY V1",
        "",
        f"elapsed={elapsed}s window_trades={len(rows)}",
        *[
            f"  {k}: closed={ (by_book.get(k) or {}).get('n_closed') } "
            f"WR={(by_book.get(k) or {}).get('wr')} EV={(by_book.get(k) or {}).get('ev')}"
            for k in ("book_a", "book_b", "book_c", "book_d")
        ],
        f"alerts={len(alerts)}",
        "",
        "new_trades_only=true historical_forbidden=true",
    ])
    if write_reports:
        result["paths"] = write_weekly_report(result)
    return result


__all__ = [
    "collect_source_signals",
    "incremental_sync",
    "load_tracked",
    "run_forward_monitor_v1",
    "run_forward_report",
    "run_forward_weekly",
]
