"""Persist + report + engine for Elite Profile Audit V1."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Sequence

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.research_db_session import research_write_lock
from bot.research.market_events.signal_intelligence.elite_candidate_v1.context import (
    load_decision_book_rows,
)
from bot.research.market_events.signal_intelligence.research_integrity_v1.canonical import (
    load_canonical_elite,
)
from bot.research.market_events.signal_intelligence.elite_profile_audit_v1.audits import (
    coin_audit,
    corpus_audit,
    cross_validate,
    detect_biases,
    direction_audit,
    distribution_audit,
    explain_extremes,
    leakage_test,
    sampling_audit,
    time_audit,
    weekday_audit,
)
from bot.research.market_events.signal_intelligence.elite_profile_audit_v1.schema import (
    AUDIT_TABLE,
    BIAS_TABLE,
    ensure_elite_profile_audit_schema,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.loader import (
    load_research_lake_rows,
)

OUT_DIR = BASE_DIR / "reports" / "research" / "elite_profile_audit_v1"


def _merge_lake(row: dict[str, Any], lake: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(lake or {})
    out.update({k: v for k, v in row.items() if v is not None})
    if out.get("pnl") is None and lake and lake.get("pnl") is not None:
        out["pnl"] = lake["pnl"]
    return out


def persist_audit(
    conn: Any,
    *,
    sections: Sequence[dict[str, Any]],
    biases: Sequence[dict[str, Any]],
) -> dict[str, int]:
    ensure_elite_profile_audit_schema(conn)
    now = int(time.time())
    with research_write_lock():
        try:
            conn.execute("BEGIN IMMEDIATE")
        except Exception:
            pass
        try:
            conn.execute(f"DELETE FROM {AUDIT_TABLE}")
            conn.execute(f"DELETE FROM {BIAS_TABLE}")
            a_sql = f"""
            INSERT INTO {AUDIT_TABLE} (
                section, key, value_json, n, pct, p_value, lift, meta_json, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """
            a_rows = [(
                s.get("section"),
                s.get("key"),
                json.dumps(s.get("value"), ensure_ascii=False, default=str),
                s.get("n"),
                s.get("pct"),
                s.get("p_value"),
                s.get("lift"),
                json.dumps(s.get("meta") or {}, ensure_ascii=False, default=str),
                now,
            ) for s in sections]
            if a_rows:
                conn.executemany(a_sql, a_rows)
            b_sql = f"""
            INSERT INTO {BIAS_TABLE} (
                bias_type, severity, score, evidence, largest, meta_json, updated_at
            ) VALUES (?,?,?,?,?,?,?)
            """
            b_rows = [(
                b.get("bias_type"),
                b.get("severity"),
                b.get("score"),
                b.get("evidence"),
                int(b.get("largest") or 0),
                json.dumps({}, ensure_ascii=False),
                now,
            ) for b in biases]
            if b_rows:
                conn.executemany(b_sql, b_rows)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
    return {"sections": len(a_rows), "biases": len(b_rows)}


def format_terminal(result: dict[str, Any]) -> str:
    c = result.get("corpus") or {}
    bias = (result.get("biases") or [{}])[0] if result.get("biases") else {}
    ext = result.get("extremes") or {}
    lines = [
        "ELITE PROFILE AUDIT V1",
        "",
        f"elapsed={result.get('elapsed_sec')}s",
        "",
        "Corpus",
        f"  closed={c.get('total_closed')} elite={c.get('total_elite')} "
        f"A+={c.get('total_aplus')} A={c.get('total_a')} ignore={c.get('total_ignore')}",
        f"  stored={c.get('total_stored')} book_b_accepted={c.get('book_b_accepted')} "
        f"equal={c.get('stored_equals_accepted')}",
        f"  {c.get('explain')}",
        "",
        "Largest bias",
        f"  {bias.get('bias_type')} [{bias.get('severity')}] score={bias.get('score')}",
        f"  {bias.get('evidence')}",
        "",
        "Extremes",
        f"  SOL share={ext.get('sol_share_pct')}% WR={ext.get('sol_wr')}",
        f"  SHORT={ext.get('short_pct')}% Friday={ext.get('friday_pct')}% elite_WR={ext.get('elite_wr')}",
        "",
        "Validated",
    ]
    for v in (result.get("validated") or [])[:6]:
        lines.append(f"  - {v}")
    lines.extend(["", "research_only=true"])
    return "\n".join(lines)


def write_artifacts(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    c = result.get("corpus") or {}
    samp = result.get("sampling") or {}
    biases = result.get("biases") or []
    coins = [r for r in (result.get("coins") or []) if r.get("significant")]
    direction = result.get("direction") or {}
    weekday = result.get("weekday") or {}
    time_a = result.get("time") or {}
    leak = result.get("leakage") or {}
    extremes = result.get("extremes") or {}

    audit_md = "\n".join([
        "# ELITE_PROFILE_AUDIT",
        "",
        "_Elite Profile Audit V1 — research only._",
        "",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- closed: {c.get('total_closed')}",
        f"- elite/A+/A: {c.get('total_elite')}/{c.get('total_aplus')}/{c.get('total_a')}",
        f"- ignore: {c.get('total_ignore')}",
        f"- stored_equals_accepted: {c.get('stored_equals_accepted')}",
        "",
        "## Explain",
        c.get("explain") or "",
        "",
        "## Extremes",
        f"- SOL: {extremes.get('sol')}",
        f"- SHORT: {extremes.get('short')}",
        f"- Friday: {extremes.get('friday')}",
        f"- WR: {extremes.get('wr')}",
        "",
        "## Sampling",
        f"```json\n{json.dumps(samp, indent=2)}\n```",
        "",
    ])
    bias_md = "\n".join([
        "# SELECTION_BIAS",
        "",
        *[
            f"- **{b.get('bias_type')}** [{b.get('severity')}] score={b.get('score')}"
            f"{' ← LARGEST' if b.get('largest') else ''}\n  {b.get('evidence')}"
            for b in biases
        ],
        "",
    ])
    coin_md = "\n".join([
        "# COIN_AUDIT",
        "",
        "| coin | trades | elite | elite% | WR | PF | EV | expected% | lift | p | sig |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|",
        *[
            f"| {r.get('coin')} | {r.get('trades')} | {r.get('elite')} | {r.get('elite_pct')} | "
            f"{r.get('wr')} | {r.get('pf')} | {r.get('ev')} | {r.get('expected_elite_pct')} | "
            f"{r.get('lift')} | {r.get('p_value')} | {r.get('significant')} |"
            for r in (result.get("coins") or [])[:40]
        ],
        "",
        "## Significant lifts only",
        *[f"- {r.get('coin')}: lift={r.get('lift')} p={r.get('p_value')}" for r in coins],
        "",
    ])
    dir_md = "\n".join([
        "# DIRECTION_AUDIT",
        "",
        f"- cause: **{direction.get('cause')}**",
        f"- {direction.get('explain')}",
        "",
        f"```json\n{json.dumps(direction, indent=2, default=str)}\n```",
        "",
    ])
    time_md = "\n".join([
        "# TIME_AUDIT",
        "",
        f"- recent_only: {time_a.get('recent_only')}",
        f"- {time_a.get('explain')}",
        "",
        "## Elite by month",
        *[f"- {r.get('key')}: n={r.get('n')} ({r.get('pct')}%)" for r in (time_a.get('elite_by_month') or [])],
        "",
        "## Weekday",
        f"- Friday elite %: {weekday.get('friday_pct')}",
        f"- {weekday.get('explain')}",
        "",
    ])
    leak_md = "\n".join([
        "# LEAKAGE_REPORT",
        "",
        f"- ok: {leak.get('ok')}",
        f"- promoted_by_outcome_learning: {leak.get('promoted_by_outcome_learning')}",
        "",
        *[
            f"- {c.get('module')}: pass={c.get('pass')} future_pnl={c.get('uses_future_pnl')} — {c.get('evidence')}"
            for c in (leak.get("checks") or [])
        ],
        "",
    ])

    files = {
        "ELITE_PROFILE_AUDIT.md": audit_md,
        "SELECTION_BIAS.md": bias_md,
        "COIN_AUDIT.md": coin_md,
        "DIRECTION_AUDIT.md": dir_md,
        "TIME_AUDIT.md": time_md,
        "LEAKAGE_REPORT.md": leak_md,
    }
    paths: dict[str, str] = {}
    for name, text in files.items():
        (BASE_DIR / name).write_text(text, encoding="utf-8")
        (OUT_DIR / name).write_text(text, encoding="utf-8")
        paths[name] = str(BASE_DIR / name)
    slim = {k: result.get(k) for k in (
        "ok", "elapsed_sec", "corpus", "sampling", "biases", "coins", "direction",
        "weekday", "time", "cv", "leakage", "extremes", "validated", "largest_anomaly",
    )}
    jp = OUT_DIR / "elite_profile_audit.json"
    jp.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    paths["json"] = str(jp)
    return paths


def run_elite_profile_audit_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    persist: bool = True,
) -> dict[str, Any]:
    t0 = time.time()
    ensure_elite_profile_audit_schema(conn)

    lake = load_research_lake_rows(conn, require_pnl=False)
    journal = load_decision_book_rows(conn)
    elite_store = load_canonical_elite(conn)
    lake_by = {int(r.get("trade_id") or 0): r for r in lake if int(r.get("trade_id") or 0)}
    journal_by = {int(r.get("trade_id") or 0): r for r in journal if int(r.get("trade_id") or 0)}

    def enrich(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            tid = int(r.get("trade_id") or 0)
            merged = _merge_lake(r, lake_by.get(tid))
            j = journal_by.get(tid) or {}
            for k in (
                "timeline_similarity", "fingerprint_similarity", "replay", "dna",
                "brain", "edge", "confidence", "rules", "accepted", "pnl", "result",
            ):
                if merged.get(k) is None and j.get(k) is not None:
                    merged[k] = j.get(k)
            if not merged.get("symbol"):
                merged["symbol"] = j.get("symbol") or r.get("symbol")
            if not merged.get("direction"):
                merged["direction"] = j.get("direction") or r.get("direction")
            if not merged.get("opened_at"):
                merged["opened_at"] = j.get("opened_at") or r.get("opened_at")
            out.append(merged)
        return out

    elite = enrich(list(elite_store))
    # Corpus = lake if available else journal
    corpus = enrich(list(lake if lake else journal))
    decision = enrich(list(journal))

    corp = corpus_audit(
        lake_rows=lake,
        journal_rows=journal,
        elite_store=elite_store,
    )
    sampling = sampling_audit()
    dist = distribution_audit(elite, corpus)
    biases = detect_biases(
        corpus=corpus, decision=decision, elite=elite, corpus_audit_res=corp
    )
    coins = coin_audit(elite, corpus)
    direction = direction_audit(corpus, decision, elite)
    weekday = weekday_audit(corpus, decision, elite)
    time_a = time_audit(corpus, elite)
    cv = cross_validate(elite, folds=5)
    leak = leakage_test(elite_store)
    extremes = explain_extremes(
        elite=elite,
        corpus=corpus,
        decision=decision,
        coin_rows=coins,
        direction=direction,
        weekday=weekday,
    )

    validated = []
    if corp.get("stored_equals_accepted"):
        validated.append("Elite+A++A matches Book B accepted count")
    else:
        validated.append(f"Stored≠accepted: {corp.get('explain')}")
    validated.append(direction.get("explain") or "direction audited")
    validated.append(weekday.get("explain") or "weekday audited")
    validated.append(time_a.get("explain") or "time audited")
    validated.append(
        f"leakage_ok={leak.get('ok')} promoted_by_learn={leak.get('promoted_by_outcome_learning')}"
    )
    if cv.get("ok"):
        validated.append(f"CV folds={len(cv.get('folds') or [])} stable={cv.get('stable')}")
    sig_coins = [r["coin"] for r in coins if r.get("significant")][:5]
    if sig_coins:
        validated.append(f"significant coin lifts: {', '.join(sig_coins)}")

    largest = biases[0] if biases else {"bias_type": "none", "evidence": "no bias flagged"}
    # largest anomaly among extremes
    anomaly = "none"
    if float(extremes.get("short_pct") or 0) >= 90:
        anomaly = f"SHORT={extremes.get('short_pct')}%"
    if float(weekday.get("friday_pct") or 0) >= 95:
        anomaly = f"Friday={weekday.get('friday_pct')}%"
    if float(extremes.get("sol_share_pct") or 0) >= 50:
        anomaly = f"SOL_share={extremes.get('sol_share_pct')}%"

    sections = [
        {"section": "corpus", "key": k, "value": v, "n": v if isinstance(v, int) else None}
        for k, v in corp.items() if k != "explain"
    ]
    for r in coins[:50]:
        sections.append({
            "section": "coin",
            "key": r["coin"],
            "value": r,
            "n": r.get("elite"),
            "pct": r.get("elite_pct"),
            "p_value": r.get("p_value"),
            "lift": r.get("lift"),
        })

    stored = {"sections": 0, "biases": 0}
    if persist:
        stored = persist_audit(conn, sections=sections, biases=biases)

    elapsed = round(time.time() - t0, 3)
    result = {
        "ok": True,
        "research_only": True,
        "corpus": corp,
        "sampling": sampling,
        "distribution": dist,
        "biases": biases,
        "coins": coins,
        "direction": direction,
        "weekday": weekday,
        "time": time_a,
        "cv": cv,
        "leakage": leak,
        "extremes": extremes,
        "validated": validated,
        "largest_bias": largest,
        "largest_anomaly": anomaly,
        "bias_detected": bool(biases),
        "stored": stored,
        "elapsed_sec": elapsed,
        "gate_unchanged": True,
        "strategy_unchanged": True,
        "execution_unchanged": True,
        "paper_unchanged": True,
        "decision_unchanged": True,
        "brain_unchanged": True,
    }
    result["terminal"] = format_terminal(result)
    if write_reports:
        result["paths"] = write_artifacts(result)
    return result


def run_elite_profile_verify(conn: Any) -> dict[str, Any]:
    out = run_elite_profile_audit_v1(conn, write_reports=True, persist=False)
    if not out.get("ok"):
        return out
    lines = [
        "ELITE PROFILE VERIFY V1",
        "",
        f"stored_equals_accepted={ (out.get('corpus') or {}).get('stored_equals_accepted') }",
        f"bias_detected={out.get('bias_detected')}",
        f"largest_anomaly={out.get('largest_anomaly')}",
        f"leakage_ok={ (out.get('leakage') or {}).get('ok') }",
        f"cv_stable={ (out.get('cv') or {}).get('stable') }",
        "",
        f"elapsed={out.get('elapsed_sec')}s research_only=true",
    ]
    out["terminal"] = "\n".join(lines)
    return out


def run_elite_profile_bias(conn: Any) -> dict[str, Any]:
    out = run_elite_profile_audit_v1(conn, write_reports=False, persist=False)
    if not out.get("ok"):
        return out
    lines = ["ELITE PROFILE BIAS V1", ""]
    for b in out.get("biases") or []:
        mark = " ← LARGEST" if b.get("largest") else ""
        lines.append(f"{b.get('bias_type')} [{b.get('severity')}]{mark}")
        lines.append(f"  score={b.get('score')} {b.get('evidence')}")
        lines.append("")
    lines.append(f"elapsed={out.get('elapsed_sec')}s research_only=true")
    out["terminal"] = "\n".join(lines)
    return out


__all__ = [
    "run_elite_profile_audit_v1",
    "run_elite_profile_bias",
    "run_elite_profile_verify",
]
