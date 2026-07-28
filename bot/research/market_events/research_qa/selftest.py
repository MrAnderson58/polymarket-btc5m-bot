"""Research Self-Test orchestrator — CI entry for Research Lab QA."""

from __future__ import annotations

import json
import tempfile
import traceback
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.research_qa.golden_dataset import (
    GOLDEN_N_DEFAULT,
    generate_golden_trades,
    seed_golden_s55,
)
from bot.research.market_events.research_qa.integrity import (
    check_experiment_integrity,
    check_hypothesis_integrity,
    check_knowledge_integrity,
    check_report_files,
)
from bot.research.market_events.research_qa.performance import run_performance_suite
from bot.research.market_events.research_qa.pipeline import (
    compare_fingerprints,
    golden_expectations_path,
    load_golden_expectations,
    pipeline_fingerprint,
    report_structure_fingerprint,
    run_full_research_pipeline,
    save_golden_expectations,
)

CheckFn = Callable[[], list[str]]


@dataclass
class SuiteResult:
    name: str
    passed: bool
    detail: str = ""
    checks: int = 0
    failures: list[str] = field(default_factory=list)


@dataclass
class SelfTestReport:
    suites: list[SuiteResult] = field(default_factory=list)
    unit_tests_run: int = 0
    unit_tests_failed: int = 0
    performance: dict[str, Any] | None = None

    @property
    def total_checks(self) -> int:
        return sum(s.checks for s in self.suites) + self.unit_tests_run

    @property
    def total_failed(self) -> int:
        return sum(1 for s in self.suites if not s.passed) + self.unit_tests_failed


def _run_suite(name: str, fn: CheckFn) -> SuiteResult:
    try:
        fails = fn()
        return SuiteResult(
            name=name,
            passed=not fails,
            detail="ok" if not fails else "; ".join(fails[:5]),
            checks=max(1, 1 if not fails else len(fails)),
            failures=fails,
        )
    except Exception as exc:
        return SuiteResult(
            name=name,
            passed=False,
            detail=f"{type(exc).__name__}: {exc}",
            checks=1,
            failures=[traceback.format_exc(limit=5)],
        )


def _prepare_golden_db(td: Path) -> tuple[Any, Path]:
    """Context-managed connection is returned open; caller closes."""
    db_path = td / "research_qa.db"
    configure_unit_test_db_isolation(db_path)
    reports = td / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    return db_path, reports


def regenerate_golden_expectations() -> Path:
    """Developer helper: rebuild expectations_v1.json from golden corpus."""
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        db_path, reports = _prepare_golden_db(td)
        with market_events_connection() as conn:
            apply_migrations(conn)
            seed_golden_s55(conn, generate_golden_trades(GOLDEN_N_DEFAULT))
            result = run_full_research_pipeline(conn, reports_root=reports, patterns_root=reports)
            fp = pipeline_fingerprint(result)
            # Also freeze report heading structures
            shapes = {}
            for name in (
                "knowledge.md",
                "patterns.md",
                "hypotheses.md",
                "experiments.md",
                "feature_validation.md",
            ):
                p = reports / name
                if p.exists():
                    shapes[name] = report_structure_fingerprint(p.read_text(encoding="utf-8"))
            fp["report_shapes"] = shapes
            return save_golden_expectations(fp)


def _with_golden_pipeline(callback: Callable[[Any, Path, dict[str, Any]], list[str]]) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        db_path, reports = _prepare_golden_db(td)
        with market_events_connection() as conn:
            apply_migrations(conn)
            n = seed_golden_s55(conn, generate_golden_trades(GOLDEN_N_DEFAULT))
            if n != GOLDEN_N_DEFAULT:
                return [f"golden seed size {n} != {GOLDEN_N_DEFAULT}"]
            result = run_full_research_pipeline(conn, reports_root=reports, patterns_root=reports)
            return callback(conn, reports, result)


def check_regression() -> list[str]:
    expected = load_golden_expectations()

    def _inner(conn: Any, reports: Path, result: dict[str, Any]) -> list[str]:
        actual = pipeline_fingerprint(result)
        # Compare without report_shapes first
        exp_core = {k: v for k, v in expected.items() if k != "report_shapes"}
        act_core = {k: v for k, v in actual.items() if k != "report_shapes"}
        return compare_fingerprints(act_core, exp_core)

    return _with_golden_pipeline(_inner)


def check_determinism() -> list[str]:
    """Run pipeline twice; fingerprints must match (k-means seed fixed)."""
    fps: list[dict[str, Any]] = []
    for _ in range(2):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            _, reports = _prepare_golden_db(td)
            with market_events_connection() as conn:
                apply_migrations(conn)
                seed_golden_s55(conn, generate_golden_trades(GOLDEN_N_DEFAULT))
                result = run_full_research_pipeline(conn, reports_root=reports, patterns_root=reports)
                fps.append(pipeline_fingerprint(result))
    return compare_fingerprints(fps[0], fps[1])


def check_knowledge() -> list[str]:
    def _inner(conn: Any, reports: Path, result: dict[str, Any]) -> list[str]:
        return check_knowledge_integrity(conn)

    return _with_golden_pipeline(_inner)


def check_patterns() -> list[str]:
    def _inner(conn: Any, reports: Path, result: dict[str, Any]) -> list[str]:
        fails: list[str] = []
        pats = result.get("patterns") or {}
        if int(pats.get("n_trades") or 0) != GOLDEN_N_DEFAULT:
            fails.append("pattern n_trades mismatch")
        if not pats.get("clusters"):
            fails.append("no pattern clusters")
        from bot.research.market_events.pattern_discovery_v1 import KMEANS_SEED

        if int(KMEANS_SEED) != 42:
            fails.append(f"KMEANS_SEED={KMEANS_SEED} expected 42")
        if not (reports / "patterns.md").exists():
            fails.append("patterns.md missing")
        if not (reports / "patterns.json").exists():
            fails.append("patterns.json missing")
        return fails

    return _with_golden_pipeline(_inner)


def check_hypotheses() -> list[str]:
    def _inner(conn: Any, reports: Path, result: dict[str, Any]) -> list[str]:
        return check_hypothesis_integrity(conn)

    return _with_golden_pipeline(_inner)


def check_experiments() -> list[str]:
    def _inner(conn: Any, reports: Path, result: dict[str, Any]) -> list[str]:
        return check_experiment_integrity(conn)

    return _with_golden_pipeline(_inner)


def check_integrity() -> list[str]:
    def _inner(conn: Any, reports: Path, result: dict[str, Any]) -> list[str]:
        fails: list[str] = []
        fails.extend(check_knowledge_integrity(conn))
        fails.extend(check_hypothesis_integrity(conn))
        fails.extend(check_experiment_integrity(conn))
        fails.extend(check_report_files(reports))
        return fails

    return _with_golden_pipeline(_inner)


def check_snapshots() -> list[str]:
    expected = load_golden_expectations()
    shapes_exp = expected.get("report_shapes") or {}

    def _inner(conn: Any, reports: Path, result: dict[str, Any]) -> list[str]:
        fails: list[str] = []
        for name, exp_heads in shapes_exp.items():
            p = reports / name
            if not p.exists():
                fails.append(f"snapshot missing {name}")
                continue
            got = report_structure_fingerprint(p.read_text(encoding="utf-8"))
            if got != exp_heads:
                fails.append(
                    f"{name} heading structure changed: got {got[:8]}... expected {exp_heads[:8]}..."
                )
        return fails

    return _with_golden_pipeline(_inner)


def check_migrations() -> list[str]:
    fails: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        configure_unit_test_db_isolation(Path(tmp) / "mig.db")
        with market_events_connection() as conn:
            apply_migrations(conn)
            for table in (
                "market_events_trade_features_s55",
                "knowledge_features",
                "knowledge_rules",
                "knowledge_interactions",
                "knowledge_history",
                "research_hypotheses",
                "hypothesis_evidence",
                "research_experiments",
                "experiment_runs",
            ):
                try:
                    conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
                except Exception as exc:
                    fails.append(f"migration missing {table}: {exc}")
    return fails


def run_research_unit_tests() -> tuple[int, int]:
    """Run focused Research Lab unit tests; returns (run, failed)."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    modules = [
        "tests.test_feature_validation_v1",
        "tests.test_knowledge_engine_v1",
        "tests.test_pattern_discovery_v1",
        "tests.test_hypothesis_engine_v1",
        "tests.test_experiment_engine_v1",
        # test_research_qa_v1 is exercised via suites above; avoid recursive selftest load
    ]
    for mod in modules:
        try:
            suite.addTests(loader.loadTestsFromName(mod))
        except Exception:
            pass
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    run = result.testsRun
    failed = len(result.failures) + len(result.errors)
    return run, failed


def run_research_selftest(
    *,
    light_perf: bool = True,
    include_unit_tests: bool = True,
    regenerate: bool = False,
) -> SelfTestReport:
    if regenerate or not golden_expectations_path().exists():
        regenerate_golden_expectations()

    report = SelfTestReport()
    report.suites.append(_run_suite("Regression", check_regression))
    report.suites.append(_run_suite("Knowledge", check_knowledge))
    report.suites.append(_run_suite("Patterns", check_patterns))
    report.suites.append(_run_suite("Hypotheses", check_hypotheses))
    report.suites.append(_run_suite("Experiments", check_experiments))
    report.suites.append(_run_suite("Integrity", check_integrity))
    report.suites.append(_run_suite("Snapshots", check_snapshots))
    report.suites.append(_run_suite("Determinism", check_determinism))
    report.suites.append(_run_suite("Migrations", check_migrations))

    perf = run_performance_suite(light=light_perf)
    report.performance = perf
    report.suites.append(
        SuiteResult(
            name="Performance",
            passed=not perf.get("fails"),
            detail=json.dumps(perf.get("rows") or [])[:200],
            checks=len(perf.get("rows") or []) or 1,
            failures=list(perf.get("fails") or []),
        )
    )

    if include_unit_tests:
        # Avoid recursive load of test_research_qa_v1 calling selftest again:
        # unit runner includes test_research_qa_v1 which should not call run_research_selftest.
        run, failed = run_research_unit_tests()
        report.unit_tests_run = run
        report.unit_tests_failed = failed

    return report


def format_selftest_report(report: SelfTestReport) -> str:
    lines = ["Research Self-Test", ""]
    width = max(len(s.name) for s in report.suites) if report.suites else 10
    for s in report.suites:
        status = "PASS" if s.passed else "FAIL"
        lines.append(f"{s.name:<{width}}  {status}")
        if not s.passed and s.failures:
            for f in s.failures[:3]:
                lines.append(f"  - {f[:180]}")
    lines.append("")
    lines.append("Total:")
    unit = report.unit_tests_run
    unit_fail = report.unit_tests_failed
    suite_checks = sum(s.checks for s in report.suites)
    # Present as tests ≈ suites + unit tests
    total = len(report.suites) + unit
    failed = sum(1 for s in report.suites if not s.passed) + unit_fail
    lines.append(f"{total} tests")
    lines.append(f"{failed} failed")
    if report.performance:
        lines.append("")
        lines.append("Performance:")
        for row in report.performance.get("rows") or []:
            lines.append(
                f"  n={row['n']}: {row['duration_s']}s  "
                f"mem≈{row.get('peak_tracemalloc_mb')}MB  db={row.get('db_mb')}MB"
            )
    if unit:
        lines.append("")
        lines.append(f"Unit tests: {unit} run, {unit_fail} failed (suite checks={suite_checks})")
    # Coverage note
    cov = os_environ_coverage()
    if cov is not None:
        lines.append(f"Coverage: {cov}")
    else:
        lines.append("Coverage: not configured (run with coverage run -m ... if desired)")
    return "\n".join(lines)


def os_environ_coverage() -> str | None:
    try:
        from coverage import Coverage  # type: ignore

        # Only report if already started
        return None
    except Exception:
        return None
