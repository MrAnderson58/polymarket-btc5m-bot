"""Research Integrity Fix V1 engine + INTEGRITY_REPORT.md."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.research_write_manager import research_replace_table
from bot.research.market_events.signal_intelligence.research_integrity_v1.fixes import (
    fix_book_d_feature_store,
    fix_elite_canonical,
    fix_reality_dataset_parity,
    fix_s55_audit,
    fix_s55_infrastructure,
    fix_timestamp_reconcile,
)
from bot.research.market_events.signal_intelligence.research_integrity_v1.schema import (
    TABLE,
    ensure_research_integrity_schema,
)

OUT_DIR = BASE_DIR / "reports" / "research" / "research_integrity_v1"


def persist_integrity(conn: Any, *, rows: list[dict[str, Any]]) -> int:
    ensure_research_integrity_schema(conn)
    now = int(time.time())
    payload = [
        (
            str(r.get("section")),
            str(r.get("key")),
            r.get("value_real"),
            r.get("value_text"),
            json.dumps(r.get("meta") or {}, default=str),
            now,
        )
        for r in rows
    ]
    return research_replace_table(
        conn,
        delete_sql=f"DELETE FROM {TABLE}",
        insert_sql=f"""
            INSERT INTO {TABLE}(section, key, value_real, value_text, meta_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
        rows=payload,
    )


def write_integrity_report(result: dict[str, Any]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    reality = result.get("reality") or {}
    elite = result.get("elite") or {}
    s55 = result.get("s55") or {}
    ts = result.get("timestamp") or {}
    book_d = result.get("book_d") or {}

    lines = [
        "# INTEGRITY_REPORT",
        "",
        "_Research Integrity Fix V1 — ONE canonical dataset before forward validation._",
        "",
        f"- runtime: **{result.get('elapsed_sec')}s**",
        f"- all_ok: **{result.get('all_ok')}**",
        "",
        "## FIX 1 — Reality Validation (dataset parity)",
        f"- ok: {reality.get('ok')}",
        f"- dataset_version: `{((reality.get('current') or {}).get('dataset_version'))}`",
        f"- lake_rows: {(reality.get('current') or {}).get('lake_rows')}",
        f"- build_ts: {(reality.get('current') or {}).get('build_ts')}",
        f"- hash: `{((reality.get('current') or {}).get('hash') or '')[:16]}...`",
        f"- stored_score: {(reality.get('stored') or {}).get('reality_score')}",
        f"- recompute_score: {reality.get('recompute_score')}",
        f"- mismatches: {reality.get('mismatches') or []}",
        "",
        "## FIX 2 — Elite canonical table",
        f"- ok: {elite.get('ok')}",
        f"- table: `{elite.get('canonical_table')}`",
        f"- n_elite: {elite.get('n_elite')}",
        f"- issues: {elite.get('issues') or []}",
        "",
        "## FIX 3 — S55 / Feature Store audit",
        f"- NO_S55_RECORD: **{s55.get('NO_S55_RECORD')}**",
        f"- TIMESTAMP_MISMATCH: {s55.get('TIMESTAMP_MISMATCH')}",
        f"- unexpected_s55: **{s55.get('unexpected_s55')}**",
        f"- n_s55 rows: {s55.get('n_s55')}",
        f"- feature_store_ok: {(s55.get('feature_store') or {}).get('ok')}",
        f"- root_causes: {s55.get('root_causes') or []}",
        "",
        "### Pipeline",
        f"```json\n{json.dumps(s55.get('pipeline') or {}, indent=2)}\n```",
        "",
        "## FIX 4 — TIMESTAMP_MISMATCH reconcile (<60s)",
        f"- before: {ts.get('before')}",
        f"- reconciled: {ts.get('reconciled')}",
        f"- after: {ts.get('after')}",
        f"- remaining: {ts.get('remaining')}",
        "",
        "## FIX 5 — Book D Feature Store gate",
        f"- ok: {book_d.get('ok')}",
        f"- book_d_will_refuse: {book_d.get('book_d_will_refuse')}",
        f"- n_samples: {(book_d.get('feature_store') or {}).get('n_samples')}",
        "",
        "research_freeze=true observe_only=true",
        "",
    ]
    text = "\n".join(lines)
    (BASE_DIR / "INTEGRITY_REPORT.md").write_text(text, encoding="utf-8")
    (OUT_DIR / "INTEGRITY_REPORT.md").write_text(text, encoding="utf-8")
    jp = OUT_DIR / "research_integrity.json"
    slim = {k: result.get(k) for k in (
        "ok", "all_ok", "elapsed_sec", "reality", "elite", "s55",
        "timestamp", "book_d", "reality_fixed", "elite_fixed", "unexpected_s55",
    )}
    jp.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    return {"INTEGRITY_REPORT.md": str(BASE_DIR / "INTEGRITY_REPORT.md"), "json": str(jp)}


def format_terminal(result: dict[str, Any]) -> str:
    return "\n".join([
        "RESEARCH INTEGRITY FIX V1",
        "",
        f"elapsed={result.get('elapsed_sec')}s all_ok={result.get('all_ok')}",
        f"Reality fixed: {result.get('reality_fixed')}",
        f"Elite fixed: {result.get('elite_fixed')}",
        f"Unexpected S55: {result.get('unexpected_s55')}",
        f"NO_S55_RECORD: {(result.get('s55') or {}).get('NO_S55_RECORD')}",
        f"TIMESTAMP reconciled: {(result.get('timestamp') or {}).get('reconciled')}",
        f"Book D refuse (no FS): {(result.get('book_d') or {}).get('book_d_will_refuse')}",
        "",
        "research_freeze=true",
    ])


def run_research_integrity_v1(
    conn: Any,
    *,
    write_reports: bool = True,
    persist: bool = True,
    reconcile_timestamps: bool = True,
) -> dict[str, Any]:
    t0 = time.time()
    ensure_research_integrity_schema(conn)

    s55_infra = fix_s55_infrastructure(conn, apply=True)
    reality = fix_reality_dataset_parity(conn)
    elite = fix_elite_canonical(conn)
    s55 = fix_s55_audit(conn)
    ts = fix_timestamp_reconcile(conn, apply=False)
    book_d = fix_book_d_feature_store(conn)

    all_ok = all([
        reality.get("ok"),
        elite.get("ok"),
        book_d.get("ok"),
        s55_infra.get("ok"),
    ])

    elapsed = round(time.time() - t0, 3)
    result: dict[str, Any] = {
        "ok": True,
        "research_only": True,
        "observe_only": True,
        "execution_unchanged": True,
        "elapsed_sec": elapsed,
        "all_ok": all_ok,
        "reality": reality,
        "elite": elite,
        "s55": s55,
        "s55_infra": s55_infra,
        "timestamp": ts,
        "book_d": book_d,
        "reality_fixed": bool(reality.get("ok")),
        "elite_fixed": bool(elite.get("ok")),
        "unexpected_s55": int(s55_infra.get("unexpected_s55") or 0),
        "impossible_explanation": s55_infra.get("impossible_explanation") or [],
    }
    result["terminal"] = format_terminal(result)

    if persist:
        persist_integrity(conn, rows=[
            {"section": "summary", "key": "all_ok", "value_real": 1.0 if all_ok else 0.0},
            {"section": "summary", "key": "unexpected_s55", "value_real": float(s55.get("unexpected_s55") or 0)},
            {"section": "reality", "key": "payload", "meta": reality},
            {"section": "elite", "key": "payload", "meta": elite},
            {"section": "s55", "key": "payload", "meta": {k: s55.get(k) for k in (
                "NO_S55_RECORD", "TIMESTAMP_MISMATCH", "unexpected_s55", "root_causes", "pipeline",
            )}},
            {"section": "timestamp", "key": "payload", "meta": ts},
            {"section": "book_d", "key": "payload", "meta": book_d},
        ])
    if write_reports:
        result["paths"] = write_integrity_report(result)
    return result


__all__ = [
    "format_terminal",
    "persist_integrity",
    "run_research_integrity_v1",
    "write_integrity_report",
]
