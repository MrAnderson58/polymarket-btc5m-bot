"""Research Infrastructure self-test — Integrity / Lake / Reality / Paper / Morning."""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.research_db_session import (
    research_migrate_then_readonly,
    research_write_connection,
)


@dataclass
class InfraCheck:
    name: str
    passed: bool
    elapsed_sec: float = 0.0
    detail: str = ""
    error: str = ""


@dataclass
class InfraSelfTestReport:
    checks: list[InfraCheck] = field(default_factory=list)
    elapsed_sec: float = 0.0

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    def by_name(self) -> dict[str, InfraCheck]:
        return {c.name: c for c in self.checks}


def _run_check(name: str, fn) -> InfraCheck:
    t0 = time.time()
    try:
        ok, detail = fn()
        return InfraCheck(
            name=name,
            passed=bool(ok),
            elapsed_sec=round(time.time() - t0, 3),
            detail=str(detail or "")[:400],
        )
    except Exception as exc:
        return InfraCheck(
            name=name,
            passed=False,
            elapsed_sec=round(time.time() - t0, 3),
            detail="exception",
            error=traceback.format_exc(limit=8),
        )


def run_research_infrastructure_selftest(db_path: Path) -> InfraSelfTestReport:
    t0 = time.time()
    report = InfraSelfTestReport()

    def _integrity() -> tuple[bool, str]:
        from bot.research.market_events.signal_intelligence.research_integrity_v1 import (
            run_research_integrity_v1,
        )

        with research_write_connection(db_path) as conn:
            apply_migrations(conn)
            out = run_research_integrity_v1(conn, write_reports=False, persist=True)
        unexpected = int(out.get("unexpected_s55") or 0)
        ok = bool(out.get("ok")) and unexpected == 0 and bool(out.get("all_ok"))
        return ok, f"all_ok={out.get('all_ok')} unexpected_s55={unexpected}"

    def _lake() -> tuple[bool, str]:
        from bot.research.market_events.signal_intelligence.research_lake_v1.health import (
            research_lake_health_v1,
        )

        with research_migrate_then_readonly(db_path) as conn:
            health = research_lake_health_v1(conn)
        return bool(health.get("ok")), f"verdict={health.get('verdict')}"

    def _reality() -> tuple[bool, str]:
        from bot.research.market_events.signal_intelligence.reality_validation_v1.engine import (
            _flat_rows,
            persist_reality,
            run_reality_validation_v1,
        )

        with research_migrate_then_readonly(db_path) as conn:
            out = run_reality_validation_v1(conn, write_reports=False, persist=False, mc_sims=200)
        with research_write_connection(db_path) as wconn:
            apply_migrations(wconn)
            persist_reality(wconn, rows=_flat_rows(out))
        return bool(out.get("ok")), f"score={out.get('reality_score')}"

    def _paper() -> tuple[bool, str]:
        from bot.research.market_events.signal_intelligence.paper_math_validation_v1 import (
            run_paper_math_v1,
        )

        with research_migrate_then_readonly(db_path) as conn:
            out = run_paper_math_v1(conn, write_reports=False, persist=False)
        return bool(out.get("ok")), f"book_d_accepted={(out.get('book_d') or {}).get('accepted')}"

    def _morning() -> tuple[bool, str]:
        from bot.research.market_events.signal_intelligence.morning_report_s63 import (
            run_morning_report,
        )

        with research_migrate_then_readonly(db_path) as conn:
            out = run_morning_report(conn, db_path=db_path, db_source="selftest")
        return bool(out.get("ok")), f"stage={out.get('stage')}"

    for name, fn in (
        ("Integrity", _integrity),
        ("Lake", _lake),
        ("Reality", _reality),
        ("Paper", _paper),
        ("Morning", _morning),
    ):
        report.checks.append(_run_check(name, fn))

    report.elapsed_sec = round(time.time() - t0, 3)
    return report


def format_infrastructure_selftest(report: InfraSelfTestReport) -> str:
    lines = [
        "RESEARCH INFRASTRUCTURE SELF-TEST",
        f"elapsed={report.elapsed_sec}s ok={report.ok}",
        "",
    ]
    for c in report.checks:
        status = "PASS" if c.passed else "FAIL"
        lines.append(f"{c.name:<10} {status}  ({c.elapsed_sec}s) {c.detail}")
        if c.error:
            lines.append(c.error[:500])
    return "\n".join(lines)


__all__ = [
    "InfraCheck",
    "InfraSelfTestReport",
    "format_infrastructure_selftest",
    "run_research_infrastructure_selftest",
]
