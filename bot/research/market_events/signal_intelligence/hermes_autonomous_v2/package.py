"""Hermes Autonomous Research V2 — package builder (≤100KB, research-only)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.research_write_manager import research_write_batch
from bot.research.market_events.signal_intelligence.hermes_daily_v1 import package as v1

PACKAGE_NAME = "RESEARCH_PACKAGE.json"
CONCLUSION_NAME = "RESEARCH_CONCLUSION.md"
NEXT_RESEARCH_NAME = "NEXT_RESEARCH.md"
SCORECARD_NAME = "DAILY_SCORECARD.md"

MAX_PACKAGE_BYTES = 100 * 1024
TARGET_INPUT_TOKENS = 40_000
TARGET_OUTPUT_TOKENS = 8_000

OUT_DIR = BASE_DIR / "reports" / "research" / "hermes_autonomous_v2"
SCHEMA_TABLE = "hermes_autonomous_v2"

# Already-shipped research — Hermes must not re-propose these as "new".
IMPLEMENTED_RESEARCH = [
    "Research Lake V1",
    "Paper Decision Books A/B/C + journal",
    "Decision Threshold Optimizer",
    "Decision Error Learning",
    "Regime Transition / Markov",
    "Elite Candidate Engine",
    "Elite Market Profile",
    "Elite Profile Audit",
    "Portfolio Simulator V1",
    "Reality Validation Engine V1",
    "Paper Mathematics Validation V1 (Books C/D)",
    "Forward Validation Monitor V1",
    "Research Integrity Fix V1",
    "Research Infrastructure Finalization V1",
    "Math Decision Funnel V1",
    "Replay Recovery Investigation V1",
    "Documentation System V1",
    "Hermes Daily Research Pipeline V1",
    "Market Fingerprint V1",
    "Market Timeline V1",
    "Market Decision V1",
    "Trading DNA / Rules",
    "Feature Store / Feature Lab",
    "Alpha Discovery / Alpha Validation",
    "Quant Research / Edge Discovery",
]

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


def estimate_tokens(text: str) -> int:
    return v1.estimate_tokens(text)


def ensure_hermes_autonomous_schema(conn: Any) -> None:
    conn.executescript(SCHEMA_DDL)
    try:
        conn.commit()
    except Exception:
        pass


def run_self_check(conn: Any) -> dict[str, Any]:
    """Python-side gates: research-integrity + health + status (Hermes never opens SQLite)."""
    checks: dict[str, Any] = {
        "research_integrity": {"ok": False, "detail": "not_run"},
        "health": {"ok": False, "detail": "not_run"},
        "status": {"ok": False, "detail": "not_run"},
    }
    fails: list[str] = []

    try:
        from bot.research.market_events.signal_intelligence.research_integrity_v1 import (
            run_research_integrity_v1,
        )

        integrity = run_research_integrity_v1(conn, write_reports=False)
        ok = bool(integrity.get("all_ok") or integrity.get("ok"))
        checks["research_integrity"] = {
            "ok": ok,
            "all_ok": integrity.get("all_ok"),
            "detail": "PASS" if ok else "FAIL",
            "elapsed_sec": integrity.get("elapsed_sec"),
        }
        if not ok:
            fails.append("research-integrity FAIL")
    except Exception as exc:
        checks["research_integrity"] = {"ok": False, "detail": f"error:{type(exc).__name__}:{exc}"[:160]}
        fails.append(f"research-integrity error: {exc}")

    try:
        from bot.research.market_events.process_manager import system_health_report

        report = system_health_report()
        text = report if isinstance(report, str) else str(report)
        # FAIL if report explicitly says FAIL / CRITICAL (case-insensitive)
        lowered = text.lower()
        ok = ("fail" not in lowered) and ("critical" not in lowered)
        # also accept explicit OK lines
        if "overall: ok" in lowered or "health: ok" in lowered or "status: ok" in lowered:
            ok = True
        if "overall: fail" in lowered or "health: fail" in lowered:
            ok = False
        checks["health"] = {
            "ok": ok,
            "detail": "PASS" if ok else "FAIL",
            "head": text[:800],
        }
        if not ok:
            fails.append("health FAIL")
    except Exception as exc:
        checks["health"] = {"ok": False, "detail": f"error:{type(exc).__name__}:{exc}"[:160]}
        fails.append(f"health error: {exc}")

    try:
        from bot.research.market_events.process_manager import status_report

        report = status_report()
        text = report if isinstance(report, str) else str(report)
        # status is informational; FAIL only if empty / exception
        ok = bool(text.strip())
        checks["status"] = {
            "ok": ok,
            "detail": "PASS" if ok else "FAIL",
            "head": text[:800],
        }
        if not ok:
            fails.append("status FAIL (empty)")
    except Exception as exc:
        checks["status"] = {"ok": False, "detail": f"error:{type(exc).__name__}:{exc}"[:160]}
        fails.append(f"status error: {exc}")

    # Integrity is hard-fail; health/status soft-warn unless integrity also failed
    hard_ok = bool(checks["research_integrity"].get("ok"))
    return {
        "ok": hard_ok,
        "hard_ok": hard_ok,
        "checks": checks,
        "fails": fails,
        "stopped_reason": None if hard_ok else "; ".join(fails) or "self_check FAIL",
    }


def _scorecard_inputs(conn: Any, package_core: dict[str, Any]) -> dict[str, Any]:
    elite = package_core.get("elite") or {}
    cats = elite.get("categories") or {}
    books = package_core.get("book_statistics") or {}
    book_b = books.get("B") or {}
    reality = package_core.get("reality") or {}
    funnel = package_core.get("decision_funnel") or {}
    top = funnel.get("top_rejectors") or []
    best = top[-1]["module"] if top else None
    worst = top[0]["module"] if top else None
    # prefer Book B stats for live paper quality
    return {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total_trades": book_b.get("n_rows") or book_b.get("n") or (books.get("A") or {}).get("n_rows"),
        "elite": cats.get("ELITE") or elite.get("n_elite"),
        "a_plus": cats.get("A+") or 0,
        "a": cats.get("A") or 0,
        "ignore": cats.get("IGNORE") or cats.get("Ignore") or 0,
        "wr": book_b.get("wr"),
        "pf": book_b.get("pf"),
        "ev": book_b.get("ev"),
        "sharpe": book_b.get("sharpe"),
        "reality_score": reality.get("reality_score"),
        "overfitting": (package_core.get("integrity") or {}).get("overfitting"),
        "best_module": best,
        "worst_module": worst,
    }


def _lean_journal(conn: Any) -> dict[str, Any]:
    samples = v1._journal_samples(conn)
    return {
        "last_50_closed": (samples.get("last_100_closed") or [])[-50:],
        "last_15_accepted": (samples.get("last_30_accepted") or [])[-15:],
        "last_15_rejected": (samples.get("last_30_rejected") or [])[-15:],
        "n_closed_available": samples.get("n_closed_available"),
        "n_book_b": samples.get("n_book_b"),
    }


def _short_md(path_name: str, max_chars: int = 600) -> str | None:
    return v1._read_report_head(BASE_DIR / path_name, max_chars=max_chars)


def build_research_package_v2(conn: Any, *, run_checks: bool = True) -> dict[str, Any]:
    t0 = time.time()
    self_check = run_self_check(conn) if run_checks else {
        "ok": True, "hard_ok": True, "checks": {}, "fails": [], "stopped_reason": None,
    }

    elite = v1._elite_compact(conn)
    if isinstance(elite.get("top15_slim"), list):
        elite["top15_slim"] = elite["top15_slim"][:8]
    elite.pop("report_md", None)

    funnel = v1._funnel_compact(conn)
    for k in ("funnel_md", "waterfall_md", "rejectors_md"):
        funnel.pop(k, None)

    replay = v1._replay_compact(conn)
    for k in ("recovery_md", "recoverable_md"):
        replay.pop(k, None)

    reality = v1._reality_compact(conn)
    reality["report_md"] = (reality.get("report_md") or "")[:600] or None

    package: dict[str, Any] = {
        "schema": "hermes_autonomous_research_package_v2",
        "generated_at": int(time.time()),
        "research_only": True,
        "hermes_rules": {
            "read_only": "RESEARCH_PACKAGE.json",
            "never_open": ["sqlite", "research_lake", "logs", "replay_db", "optimizer"],
            "max_input_tokens": TARGET_INPUT_TOKENS,
            "if_over_budget": "ask_python_to_aggregate_first",
            "outputs": [CONCLUSION_NAME, NEXT_RESEARCH_NAME, SCORECARD_NAME],
            "autonomous": True,
            "no_gate_strategy_execution_changes": True,
        },
        "cost_rules": {
            "max_package_bytes": MAX_PACKAGE_BYTES,
            "target_input_tokens": TARGET_INPUT_TOKENS,
            "target_output_tokens": TARGET_OUTPUT_TOKENS,
            "never_read_full_lake": True,
            "never_request_raw_sql_tables": True,
        },
        "self_check": self_check,
        "implemented_research": IMPLEMENTED_RESEARCH,
        "morning": {"report_md": _short_md("MORNING_REPORT.md")},
        "forward": {"report_md": _short_md("FORWARD_VALIDATION_REPORT.md")},
        "reality": reality,
        "decision_funnel": funnel,
        "replay_recovery": replay,
        "elite": elite,
        "decision_journal_samples": _lean_journal(conn),
        "book_statistics": v1._book_stats_compact(conn),
        "current_market": v1._current_market_compact(conn),
        "current_fingerprint_timeline": v1._fingerprint_timeline_compact(conn),
        "integrity": {"report_md": _short_md("INTEGRITY_REPORT.md", 500)},
    }
    package["scorecard_inputs"] = _scorecard_inputs(conn, package)
    package["elapsed_sec_build"] = round(time.time() - t0, 3)
    return package


def enforce_package_size_v2(package: dict[str, Any]) -> tuple[dict[str, Any], int]:
    pkg = dict(package)
    raw = json.dumps(pkg, default=str, separators=(",", ":")).encode("utf-8")
    if len(raw) <= MAX_PACKAGE_BYTES:
        return pkg, len(raw)

    samples = pkg.get("decision_journal_samples") or {}
    for key, keep in (
        ("last_50_closed", 25),
        ("last_50_closed", 10),
        ("last_15_accepted", 8),
        ("last_15_rejected", 8),
        ("last_50_closed", 5),
        ("last_15_accepted", 3),
        ("last_15_rejected", 3),
    ):
        if key in samples and isinstance(samples[key], list):
            samples[key] = samples[key][-keep:]
        pkg["decision_journal_samples"] = samples
        elite = pkg.get("elite") or {}
        if "top15_slim" in elite:
            elite["top15_slim"] = (elite.get("top15_slim") or [])[:5]
            pkg["elite"] = elite
        for section in ("morning", "forward", "reality", "integrity"):
            sec = pkg.get(section)
            if isinstance(sec, dict) and sec.get("report_md"):
                sec["report_md"] = str(sec["report_md"])[:400]
        # shrink fingerprint/timeline md
        ft = pkg.get("current_fingerprint_timeline") or {}
        for side in ("fingerprint", "timeline"):
            s = ft.get(side)
            if isinstance(s, dict) and s.get("report_md"):
                s["report_md"] = str(s["report_md"])[:300]
                s.pop("summary", None)
        raw = json.dumps(pkg, default=str, separators=(",", ":")).encode("utf-8")
        if len(raw) <= MAX_PACKAGE_BYTES:
            pkg["trimmed"] = True
            return pkg, len(raw)

    for section in (
        "morning", "forward", "reality", "integrity",
        "decision_funnel", "replay_recovery", "current_fingerprint_timeline",
    ):
        sec = pkg.get(section)
        if isinstance(sec, dict):
            for k in list(sec.keys()):
                if k.endswith("_md") or k in ("json", "summary", "report_md"):
                    sec.pop(k, None)
        elif section == "current_fingerprint_timeline" and isinstance(sec, dict):
            pkg[section] = {}
    # drop implemented list detail if still huge
    if len(json.dumps(pkg, default=str, separators=(",", ":")).encode("utf-8")) > MAX_PACKAGE_BYTES:
        pkg["implemented_research"] = IMPLEMENTED_RESEARCH[:12]
    raw = json.dumps(pkg, default=str, separators=(",", ":")).encode("utf-8")
    pkg["trimmed"] = True
    pkg["hard_trimmed"] = True
    return pkg, len(raw)


def write_research_package(package: dict[str, Any], *, size_bytes: int) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(package, default=str, separators=(",", ":"))
    root = BASE_DIR / PACKAGE_NAME
    outp = OUT_DIR / PACKAGE_NAME
    root.write_text(text, encoding="utf-8")
    outp.write_text(text, encoding="utf-8")
    return {"root": str(root), "out_dir": str(outp), "bytes": str(size_bytes)}


def persist_package_meta(conn: Any, *, size_bytes: int, tokens_est: int, elapsed: float) -> None:
    ensure_hermes_autonomous_schema(conn)
    now = int(time.time())

    def _write(c: Any) -> int:
        rows = [
            ("summary", "package_bytes", float(size_bytes), None, None, now),
            ("summary", "estimated_input_tokens", float(tokens_est), None, None, now),
            ("summary", "elapsed_sec", float(elapsed), None, None, now),
            ("summary", "max_package_bytes", float(MAX_PACKAGE_BYTES), None, None, now),
            ("summary", "schema_version", None, "v2", None, now),
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
    run_checks: bool = True,
) -> dict[str, Any]:
    t0 = time.time()
    ensure_hermes_autonomous_schema(conn)
    package = build_research_package_v2(conn, run_checks=run_checks)
    package, size_bytes = enforce_package_size_v2(package)
    tokens_est = estimate_tokens(json.dumps(package, default=str))
    elapsed = round(time.time() - t0, 3)
    package["package_bytes"] = size_bytes
    package["estimated_input_tokens"] = tokens_est
    package["elapsed_sec"] = elapsed

    paths = {}
    if write_files:
        paths = write_research_package(package, size_bytes=size_bytes)
    if persist:
        try:
            persist_package_meta(conn, size_bytes=size_bytes, tokens_est=tokens_est, elapsed=elapsed)
        except Exception:
            pass

    ok_size = size_bytes <= MAX_PACKAGE_BYTES
    ok_tokens = tokens_est <= TARGET_INPUT_TOKENS
    terminal = "\n".join([
        "HERMES AUTONOMOUS RESEARCH PACKAGE V2",
        "",
        f"elapsed={elapsed}s",
        f"package_bytes={size_bytes} max={MAX_PACKAGE_BYTES} ok={ok_size}",
        f"estimated_input_tokens={tokens_est} target<{TARGET_INPUT_TOKENS} ok={ok_tokens}",
        f"self_check_ok={bool((package.get('self_check') or {}).get('ok'))}",
        "never_full_lake=true research_only=true package_only=true",
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
    "IMPLEMENTED_RESEARCH",
    "MAX_PACKAGE_BYTES",
    "PACKAGE_NAME",
    "TARGET_INPUT_TOKENS",
    "build_research_package_v2",
    "enforce_package_size_v2",
    "estimate_tokens",
    "run_daily_research_package",
    "run_self_check",
]
