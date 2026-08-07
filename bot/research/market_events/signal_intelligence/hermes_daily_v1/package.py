"""Hermes Daily Research Pipeline V1 — compact RESEARCH_PACKAGE only (research-only)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.research_write_manager import research_write_batch

PACKAGE_NAME = "RESEARCH_PACKAGE.json"
CONCLUSION_NAME = "RESEARCH_CONCLUSION.md"
MAX_PACKAGE_BYTES = 500 * 1024
TARGET_INPUT_TOKENS = 50_000
TARGET_OUTPUT_TOKENS = 10_000

OUT_DIR = BASE_DIR / "reports" / "research" / "hermes_daily_v1"
DOCS = (
    "HERMES_SYSTEM_PROMPT.md",
    "PROJECT_STATE.md",
    "RESEARCH_RULES.md",
    "ARCHITECTURE.md",
)

# Slim fields only — never dump full lake rows / feature blobs.
_TRADE_KEYS = (
    "trade_id", "symbol", "direction", "opened_at", "decision", "accepted",
    "confidence", "replay", "fingerprint_similarity", "timeline_similarity",
    "dna", "rules", "edge", "brain", "causality", "decision_rank",
    "historical_wr", "historical_ev", "historical_pf", "result", "pnl", "regime",
)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token)."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _slim_trade(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in _TRADE_KEYS:
        if row.get(k) is not None:
            out[k] = row.get(k)
    return out


def _read_report_head(path: Path, *, max_chars: int = 4000) -> str | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    if len(text) > max_chars:
        return text[:max_chars] + "\n…[truncated]"
    return text


def _load_json_if_small(path: Path, *, max_bytes: int = 80_000) -> Any | None:
    if not path.exists():
        return None
    try:
        if path.stat().st_size > max_bytes:
            return {"_skipped": True, "reason": "too_large", "bytes": path.stat().st_size}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_error": str(exc)[:120]}


def _book_stats_compact(conn: Any) -> dict[str, Any]:
    from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
        BOOK_A,
        BOOK_B,
        BOOK_C,
    )
    from bot.research.market_events.signal_intelligence.paper_decision_books_v1.journal import (
        load_journal_rows,
    )
    from bot.research.market_events.signal_intelligence.paper_decision_books_v1.metrics import (
        book_stats_from_rows,
    )
    from bot.research.market_events.signal_intelligence.paper_math_validation_v1.books import (
        BOOK_C as MATH_C,
        BOOK_D,
    )

    out: dict[str, Any] = {}
    for label, book in (("A", BOOK_A), ("B", BOOK_B), ("C_decision", BOOK_C)):
        rows = load_journal_rows(conn, book=book)
        out[label] = book_stats_from_rows(rows)
        out[label]["n_rows"] = len(rows)
        out[label]["n_accepted"] = sum(1 for r in rows if int(r.get("accepted") or 0) == 1)

    try:
        from bot.research.market_events.signal_intelligence.paper_math_validation_v1.engine import (
            load_math_book_rows,
        )
        from bot.research.market_events.signal_intelligence.paper_math_validation_v1.metrics import (
            rows_metrics,
        )

        for label, book in (("C_math", MATH_C), ("D_math", BOOK_D)):
            rows = load_math_book_rows(conn, book=book)
            out[label] = rows_metrics(rows)
    except Exception as exc:
        out["math_books_error"] = str(exc)[:120]
    return out


def _journal_samples(conn: Any) -> dict[str, Any]:
    from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import (
        BOOK_A,
        BOOK_B,
    )
    from bot.research.market_events.signal_intelligence.paper_decision_books_v1.journal import (
        load_journal_rows,
    )

    rows_a = load_journal_rows(conn, book=BOOK_A)
    # closed = has pnl
    closed = [r for r in rows_a if r.get("pnl") is not None]
    closed.sort(key=lambda r: int(r.get("opened_at") or 0))
    last_closed = [_slim_trade(r) for r in closed[-100:]]

    rows_b = load_journal_rows(conn, book=BOOK_B)
    rows_b.sort(key=lambda r: int(r.get("opened_at") or 0))
    accepted = [_slim_trade(r) for r in rows_b if int(r.get("accepted") or 0) == 1][-30:]
    # rejected relative to Book A TRADE decisions not in B, or B accepted=0
    rejected_src = [r for r in rows_a if str(r.get("decision") or "") != "TRADE" or int(r.get("accepted") or 0) == 0]
    if not rejected_src:
        # fallback: Book A rows with low replay
        rejected_src = [r for r in rows_a if (r.get("replay") is None or float(r.get("replay") or 0) < 0.5)]
    rejected_src.sort(key=lambda r: int(r.get("opened_at") or 0))
    rejected = [_slim_trade(r) for r in rejected_src[-30:]]

    return {
        "last_100_closed": last_closed,
        "last_30_accepted": accepted,
        "last_30_rejected": rejected,
        "n_closed_available": len(closed),
        "n_book_b": len(rows_b),
        "report_md": _read_report_head(BASE_DIR / "DECISION_JOURNAL_REPORT.md", max_chars=2000),
    }


def _funnel_compact(conn: Any) -> dict[str, Any]:
    try:
        from bot.research.market_events.signal_intelligence.math_decision_funnel_v1.schema import (
            FUNNEL_TABLE,
            REJECTIONS_TABLE,
        )

        stages = []
        for r in conn.execute(
            f"SELECT stage, stage_order, input_n, accepted_n, rejected_n, acceptance_pct, wr, pf, ev, sharpe "
            f"FROM {FUNNEL_TABLE} ORDER BY stage_order"
        ).fetchall():
            stages.append(dict(r) if hasattr(r, "keys") else {
                "stage": r[0], "stage_order": r[1], "input_n": r[2], "accepted_n": r[3],
                "rejected_n": r[4], "acceptance_pct": r[5], "wr": r[6], "pf": r[7],
                "ev": r[8], "sharpe": r[9],
            })
        top = []
        for r in conn.execute(
            f"""
            SELECT first_rejector, COUNT(*) AS n
            FROM {REJECTIONS_TABLE}
            GROUP BY first_rejector
            ORDER BY n DESC
            LIMIT 10
            """
        ).fetchall():
            top.append({"module": r[0] if not hasattr(r, "keys") else r["first_rejector"],
                        "rejected": int(r[1] if not hasattr(r, "keys") else r["n"])})
        return {"stages": stages, "top_rejectors": top, "source": "sqlite"}
    except Exception:
        pass
    # fallback report heads
    return {
        "source": "reports",
        "funnel_md": _read_report_head(BASE_DIR / "DECISION_FUNNEL.md", max_chars=2500),
        "waterfall_md": _read_report_head(BASE_DIR / "DECISION_WATERFALL.md", max_chars=1500),
        "rejectors_md": _read_report_head(BASE_DIR / "TOP_REJECTORS.md", max_chars=1500),
    }


def _replay_compact(conn: Any) -> dict[str, Any]:
    try:
        from bot.research.market_events.signal_intelligence.replay_recovery_v1.schema import TABLE

        out: dict[str, Any] = {"source": "sqlite"}
        for r in conn.execute(
            f"SELECT section, key, value_real, value_text, meta_json FROM {TABLE}"
        ).fetchall():
            section = r["section"] if hasattr(r, "keys") else r[0]
            key = r["key"] if hasattr(r, "keys") else r[1]
            vr = r["value_real"] if hasattr(r, "keys") else r[2]
            vt = r["value_text"] if hasattr(r, "keys") else r[3]
            mj = r["meta_json"] if hasattr(r, "keys") else r[4]
            if section == "summary":
                out[key] = vr if vr is not None else vt
            elif key == "payload" and mj:
                try:
                    out[section] = json.loads(mj)
                except Exception:
                    out[section] = mj[:500]
        return out
    except Exception:
        return {
            "source": "reports",
            "recovery_md": _read_report_head(BASE_DIR / "REPLAY_RECOVERY.md", max_chars=2500),
            "recoverable_md": _read_report_head(BASE_DIR / "REPLAY_RECOVERABLE.md", max_chars=2000),
        }


def _reality_compact(conn: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"source": "sqlite"}
    try:
        row = conn.execute(
            """
            SELECT value_real FROM reality_validation_v1
            WHERE section='summary' AND key='reality_score'
            """
        ).fetchone()
        if row:
            out["reality_score"] = float(row[0] if not hasattr(row, "keys") else row["value_real"])
        for key in ("dataset_version", "lake_rows", "build_ts", "hash"):
            r = conn.execute(
                """
                SELECT value_real, value_text FROM reality_validation_v1
                WHERE section='dataset' AND key=?
                """,
                (key,),
            ).fetchone()
            if r:
                vr = r[0] if not hasattr(r, "keys") else r["value_real"]
                vt = r[1] if not hasattr(r, "keys") else r["value_text"]
                out[key] = vt or vr
    except Exception as exc:
        out["sqlite_error"] = str(exc)[:80]
    out["report_md"] = _read_report_head(BASE_DIR / "REALITY_REPORT.md", max_chars=2500)
    return out


def _elite_compact(conn: Any) -> dict[str, Any]:
    from bot.research.market_events.signal_intelligence.research_integrity_v1.canonical import (
        load_canonical_elite,
    )

    elite = load_canonical_elite(conn)
    from collections import Counter

    cats = Counter(str(e.get("category") or "") for e in elite)
    pnls = []
    for e in elite:
        if e.get("pnl") is None:
            continue
        try:
            pnls.append(float(e["pnl"]))
        except Exception:
            pass
    from bot.research.market_events.signal_intelligence.paper_decision_books_v1.metrics import (
        book_stats_from_pnls,
    )

    top = sorted(elite, key=lambda e: float(e.get("score") or 0), reverse=True)[:15]
    return {
        "n_elite": len(elite),
        "categories": dict(cats),
        "stats": book_stats_from_pnls(pnls),
        "top15_slim": [
            {
                "trade_id": e.get("trade_id"),
                "symbol": e.get("symbol"),
                "category": e.get("category"),
                "score": e.get("score"),
                "pnl": e.get("pnl"),
                "historical_wr": e.get("historical_wr"),
                "historical_ev": e.get("historical_ev"),
            }
            for e in top
        ],
        "report_md": _read_report_head(BASE_DIR / "ELITE_CANDIDATE_REPORT.md", max_chars=2000),
    }


def _forward_compact() -> dict[str, Any]:
    return {
        "report_md": _read_report_head(BASE_DIR / "FORWARD_VALIDATION_REPORT.md", max_chars=2500),
        "json": _load_json_if_small(
            BASE_DIR / "reports" / "research" / "forward_validation_v1" / "forward_validation.json",
            max_bytes=60_000,
        ),
    }


def _morning_compact() -> dict[str, Any]:
    # morning report path varies; try common locations
    candidates = [
        BASE_DIR / "MORNING_REPORT.md",
        BASE_DIR / "reports" / "research" / "morning" / "MORNING_REPORT.md",
    ]
    for p in BASE_DIR.glob("reports/**/MORNING*.md"):
        candidates.append(p)
        break
    text = None
    for p in candidates:
        text = _read_report_head(p, max_chars=3000)
        if text:
            break
    return {"report_md": text}


def _current_market_compact(conn: Any) -> dict[str, Any]:
    """Latest journal / lake regime snapshot — statistics only."""
    from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import BOOK_A
    from bot.research.market_events.signal_intelligence.paper_decision_books_v1.journal import (
        load_journal_rows,
    )

    rows = load_journal_rows(conn, book=BOOK_A)
    rows.sort(key=lambda r: int(r.get("opened_at") or 0))
    last = rows[-1] if rows else {}
    return {
        "last_trade_id": last.get("trade_id"),
        "symbol": last.get("symbol"),
        "opened_at": last.get("opened_at"),
        "regime": last.get("regime"),
        "decision": last.get("decision"),
        "confidence": last.get("confidence"),
        "n_journal_a": len(rows),
    }


def _fingerprint_timeline_compact(conn: Any) -> dict[str, Any]:
    # Report heads / summaries only — never re-run full engines (cost + latency).
    del conn  # reserved for future sqlite summaries
    fp = {
        "report_md": _read_report_head(
            BASE_DIR / "reports" / "research" / "market_fingerprint_v1" / "MARKET_FINGERPRINT.md",
            max_chars=1500,
        )
        or _read_report_head(BASE_DIR / "MARKET_FINGERPRINT.md", max_chars=1500),
    }
    tl = {
        "report_md": _read_report_head(
            BASE_DIR / "reports" / "research" / "market_timeline_v1" / "MARKET_TIMELINE.md",
            max_chars=1500,
        )
        or _read_report_head(BASE_DIR / "MARKET_TIMELINE.md", max_chars=1500),
    }
    # compact json summaries if present and small
    fp_json = _load_json_if_small(
        BASE_DIR / "reports" / "research" / "market_fingerprint_v1" / "fingerprint_summary.json",
        max_bytes=20_000,
    )
    if fp_json and not (isinstance(fp_json, dict) and fp_json.get("_skipped")):
        fp["summary"] = fp_json
    tl_json = _load_json_if_small(
        BASE_DIR / "reports" / "research" / "market_timeline_v1" / "timeline_summary.json",
        max_bytes=20_000,
    )
    if tl_json and not (isinstance(tl_json, dict) and tl_json.get("_skipped")):
        tl["summary"] = tl_json
    return {"fingerprint": fp, "timeline": tl}


def _integrity_compact() -> dict[str, Any]:
    return {
        "report_md": _read_report_head(BASE_DIR / "INTEGRITY_REPORT.md", max_chars=2000),
    }


def build_research_package(conn: Any) -> dict[str, Any]:
    """Assemble stats-only RESEARCH_PACKAGE (never full lake)."""
    t0 = time.time()
    package: dict[str, Any] = {
        "schema": "hermes_daily_research_package_v1",
        "generated_at": int(time.time()),
        "research_only": True,
        "cost_rules": {
            "max_package_bytes": MAX_PACKAGE_BYTES,
            "target_input_tokens": TARGET_INPUT_TOKENS,
            "target_output_tokens": TARGET_OUTPUT_TOKENS,
            "never_read_full_lake": True,
            "never_request_raw_sql_tables": True,
        },
        "morning": _morning_compact(),
        "forward": _forward_compact(),
        "reality": _reality_compact(conn),
        "decision_funnel": _funnel_compact(conn),
        "replay_recovery": _replay_compact(conn),
        "elite": _elite_compact(conn),
        "decision_journal_samples": _journal_samples(conn),
        "book_statistics": _book_stats_compact(conn),
        "current_market": _current_market_compact(conn),
        "current_fingerprint_timeline": _fingerprint_timeline_compact(conn),
        "integrity": _integrity_compact(),
    }
    package["elapsed_sec_build"] = round(time.time() - t0, 3)
    return package


def enforce_package_size(package: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Trim samples until JSON ≤ MAX_PACKAGE_BYTES."""
    pkg = dict(package)
    raw = json.dumps(pkg, default=str, separators=(",", ":")).encode("utf-8")
    if len(raw) <= MAX_PACKAGE_BYTES:
        return pkg, len(raw)

    # progressive trim
    samples = pkg.get("decision_journal_samples") or {}
    for key, keep in (
        ("last_100_closed", 50),
        ("last_100_closed", 25),
        ("last_30_accepted", 15),
        ("last_30_rejected", 15),
        ("last_100_closed", 10),
        ("last_30_accepted", 5),
        ("last_30_rejected", 5),
    ):
        if key in samples and isinstance(samples[key], list):
            samples[key] = samples[key][-keep:]
        pkg["decision_journal_samples"] = samples
        elite = pkg.get("elite") or {}
        if "top15_slim" in elite:
            elite["top15_slim"] = (elite.get("top15_slim") or [])[:8]
            pkg["elite"] = elite
        # drop heavy md blobs
        for section in ("morning", "forward", "reality", "integrity"):
            sec = pkg.get(section)
            if isinstance(sec, dict) and sec.get("report_md"):
                sec["report_md"] = str(sec["report_md"])[:1200]
        raw = json.dumps(pkg, default=str, separators=(",", ":")).encode("utf-8")
        if len(raw) <= MAX_PACKAGE_BYTES:
            pkg["trimmed"] = True
            return pkg, len(raw)

    # last resort: drop report markdown
    for section in ("morning", "forward", "reality", "integrity", "decision_funnel", "replay_recovery"):
        sec = pkg.get(section)
        if isinstance(sec, dict):
            sec.pop("report_md", None)
            sec.pop("funnel_md", None)
            sec.pop("waterfall_md", None)
            sec.pop("rejectors_md", None)
            sec.pop("recovery_md", None)
            sec.pop("recoverable_md", None)
            sec.pop("json", None)
    raw = json.dumps(pkg, default=str, separators=(",", ":")).encode("utf-8")
    pkg["trimmed"] = True
    pkg["hard_trimmed"] = True
    return pkg, len(raw)


def write_research_package(package: dict[str, Any], *, size_bytes: int) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # compact JSON so on-disk size matches enforce_package_size bytes
    text = json.dumps(package, default=str, separators=(",", ":"))
    root = BASE_DIR / PACKAGE_NAME
    outp = OUT_DIR / PACKAGE_NAME
    root.write_text(text, encoding="utf-8")
    outp.write_text(text, encoding="utf-8")
    return {"root": str(root), "out_dir": str(outp), "bytes": str(size_bytes)}


def load_docs_for_hermes(*, max_chars_each: int = 12_000) -> dict[str, str]:
    docs_dir = BASE_DIR / "docs"
    out: dict[str, str] = {}
    for name in DOCS:
        p = docs_dir / name
        if not p.exists():
            out[name] = f"[missing {name}]"
            continue
        text = p.read_text(encoding="utf-8")
        if len(text) > max_chars_each:
            text = text[:max_chars_each] + "\n…[truncated for token budget]"
        out[name] = text
    return out


def estimate_hermes_input_tokens(package: dict[str, Any], docs: dict[str, str]) -> int:
    blob = json.dumps(package, default=str) + "\n".join(docs.values())
    return estimate_tokens(blob)


SCHEMA_TABLE = "hermes_daily_pipeline_v1"
SCHEMA_DDL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section TEXT NOT NULL,
    key TEXT NOT NULL,
    value_real REAL,
    value_text TEXT,
    meta_json TEXT,
    updated_at INTEGER NOT NULL,
    UNIQUE(section, key)
);
"""


def ensure_hermes_daily_schema(conn: Any) -> None:
    conn.executescript(SCHEMA_DDL)
    try:
        conn.commit()
    except Exception:
        pass


def persist_package_meta(conn: Any, *, size_bytes: int, tokens_est: int, elapsed: float) -> None:
    ensure_hermes_daily_schema(conn)
    now = int(time.time())

    def _write(c: Any) -> int:
        rows = [
            ("summary", "package_bytes", float(size_bytes), None, None, now),
            ("summary", "estimated_input_tokens", float(tokens_est), None, None, now),
            ("summary", "elapsed_sec", float(elapsed), None, None, now),
            ("summary", "max_package_bytes", float(MAX_PACKAGE_BYTES), None, None, now),
        ]
        c.execute(f"DELETE FROM {SCHEMA_TABLE} WHERE section='summary'")
        c.executemany(
            f"""
            INSERT INTO {SCHEMA_TABLE}(section, key, value_real, value_text, meta_json, updated_at)
            VALUES (?,?,?,?,?,?)
            """,
            rows,
        )
        return len(rows)

    research_write_batch(conn, _write)


def run_daily_research_package(
    conn: Any,
    *,
    write_files: bool = True,
    persist: bool = True,
) -> dict[str, Any]:
    t0 = time.time()
    ensure_hermes_daily_schema(conn)
    package = build_research_package(conn)
    package, size_bytes = enforce_package_size(package)
    docs = load_docs_for_hermes()
    tokens_est = estimate_hermes_input_tokens(package, docs)
    elapsed = round(time.time() - t0, 3)
    package["package_bytes"] = size_bytes
    package["estimated_input_tokens_with_docs"] = tokens_est
    package["elapsed_sec"] = elapsed

    paths = {}
    if write_files:
        paths = write_research_package(package, size_bytes=size_bytes)
    if persist:
        persist_package_meta(conn, size_bytes=size_bytes, tokens_est=tokens_est, elapsed=elapsed)

    ok_size = size_bytes <= MAX_PACKAGE_BYTES
    ok_tokens = tokens_est <= TARGET_INPUT_TOKENS
    terminal = "\n".join([
        "HERMES DAILY RESEARCH PACKAGE V1",
        "",
        f"elapsed={elapsed}s",
        f"package_bytes={size_bytes} max={MAX_PACKAGE_BYTES} ok={ok_size}",
        f"estimated_input_tokens={tokens_est} target<{TARGET_INPUT_TOKENS} ok={ok_tokens}",
        f"never_full_lake=true research_only=true",
        "",
    ])
    return {
        "ok": ok_size,
        "research_only": True,
        "elapsed_sec": elapsed,
        "package_bytes": size_bytes,
        "estimated_tokens": tokens_est,
        "estimated_input_tokens": tokens_est,
        "package": package,
        "paths": paths,
        "terminal": terminal,
    }


__all__ = [
    "MAX_PACKAGE_BYTES",
    "PACKAGE_NAME",
    "build_research_package",
    "enforce_package_size",
    "estimate_tokens",
    "run_daily_research_package",
]
