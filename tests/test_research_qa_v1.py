"""Tests for Research QA & Regression Suite V1."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.pattern_discovery_v1 import KMEANS_SEED
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
from bot.research.market_events.research_qa.pipeline import (
    compare_fingerprints,
    golden_expectations_path,
    load_golden_expectations,
    pipeline_fingerprint,
    report_structure_fingerprint,
    run_full_research_pipeline,
)
from bot.research.market_events.research_qa.selftest import (
    check_determinism,
    check_migrations,
    check_regression,
    format_selftest_report,
)


class ResearchQAV1Tests(unittest.TestCase):
    def test_kmeans_seed_fixed(self) -> None:
        self.assertEqual(KMEANS_SEED, 42)

    def test_golden_dataset_deterministic(self) -> None:
        a = generate_golden_trades(GOLDEN_N_DEFAULT)
        b = generate_golden_trades(GOLDEN_N_DEFAULT)
        self.assertEqual(len(a), GOLDEN_N_DEFAULT)
        self.assertEqual(a[0]["pnl_pct"], b[0]["pnl_pct"])
        self.assertEqual(a[-1]["funding"], b[-1]["funding"])

    def test_golden_expectations_file_exists(self) -> None:
        path = golden_expectations_path()
        self.assertTrue(path.exists(), msg="commit expectations_v1.json")
        data = load_golden_expectations()
        self.assertEqual(data["n_trades"], GOLDEN_N_DEFAULT)
        self.assertIn("top_features", data)
        self.assertIn("report_shapes", data)

    def test_migrations_tables(self) -> None:
        fails = check_migrations()
        self.assertEqual(fails, [])

    def test_e2e_pipeline_and_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            configure_unit_test_db_isolation(Path(tmp) / "qa.db")
            reports = Path(tmp) / "reports"
            with market_events_connection() as conn:
                apply_migrations(conn)
                seed_golden_s55(conn, generate_golden_trades(GOLDEN_N_DEFAULT))
                result = run_full_research_pipeline(
                    conn, reports_root=reports, patterns_root=reports
                )
                self.assertEqual(result["feature_validation"]["n_trades"], GOLDEN_N_DEFAULT)
                self.assertTrue(result["patterns"]["clusters"])
                self.assertTrue(result["hypotheses"])
                self.assertGreater(result["experiments"]["ran"], 0)

                self.assertEqual(check_report_files(reports), [])
                self.assertEqual(check_knowledge_integrity(conn), [])
                self.assertEqual(check_hypothesis_integrity(conn), [])
                self.assertEqual(check_experiment_integrity(conn), [])

                fp = pipeline_fingerprint(result)
                expected = load_golden_expectations()
                exp_core = {k: v for k, v in expected.items() if k != "report_shapes"}
                fails = compare_fingerprints(fp, exp_core)
                self.assertEqual(fails, [], msg="\n".join(fails))

                # Snapshot headings
                for name, heads in (expected.get("report_shapes") or {}).items():
                    got = report_structure_fingerprint(
                        (reports / name).read_text(encoding="utf-8")
                    )
                    self.assertEqual(got, heads, msg=name)

    def test_regression_and_determinism_helpers(self) -> None:
        self.assertEqual(check_regression(), [])
        self.assertEqual(check_determinism(), [])

    def test_selftest_format(self) -> None:
        from bot.research.market_events.research_qa.selftest import SelfTestReport, SuiteResult

        report = SelfTestReport(
            suites=[
                SuiteResult(name="Regression", passed=True, checks=1),
                SuiteResult(name="Knowledge", passed=True, checks=1),
                SuiteResult(name="Performance", passed=True, checks=2),
            ],
            unit_tests_run=10,
            unit_tests_failed=0,
        )
        text = format_selftest_report(report)
        self.assertIn("Research Self-Test", text)
        self.assertIn("Regression", text)
        self.assertIn("PASS", text)
        self.assertIn("Total:", text)
        self.assertEqual(report.total_failed, 0)


if __name__ == "__main__":
    unittest.main()
