"""Phase D.1.1 robustness and outlier audit tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bot.research.futures_agent.env_bootstrap import (
    configure_unit_test_db_isolation,
    reset_bootstrap_for_tests,
)
from bot.research.futures_agent.db import agent_connection, insert_returning_id
from bot.research.futures_agent.schema import apply_migrations
from bot.research.futures_agent.signal_outcome_constants import ENGINE_VERSION
from bot.research.futures_agent.signal_outcome_outlier_audit import (
    run_outlier_audit,
    validate_trade_math,
)
from bot.research.futures_agent.signal_outcome_report import run_outcome_report
from bot.research.futures_agent.signal_outcome_robustness import (
    VerdictInputs,
    compute_research_verdict,
    compute_robust_stats,
    trimmed_mean,
    winsorized_mean,
)


class StageD11Tests(unittest.TestCase):
    def setUp(self) -> None:
        reset_bootstrap_for_tests()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmpdir.name) / "agent_d11_test.db")
        self.agent_url = f"sqlite:///{self.db_path}"
        configure_unit_test_db_isolation(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        reset_bootstrap_for_tests()

    def _conn(self):
        return agent_connection(self.agent_url)

    def _seed_outcome(
        self,
        conn,
        *,
        post_id: str,
        ret: float,
        direction: str = "LONG",
        message_ts: int = 1_700_000_000,
    ) -> int:
        post_row_id = insert_returning_id(
            conn,
            """
            INSERT INTO futures_agent_trader_posts (
              source_message_id, channel_name, message_ts, raw_text,
              content_hash, content_type, symbols_json, deterministic_confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                post_id, "signalyp", message_ts,
                "BTC LONG\nEntry: 100\nSL: 95\nTP: 110",
                f"h{post_id}", "EXPLICIT_SIGNAL", json.dumps(["BTC"]), 0.9,
            ),
        )
        thesis_id = insert_returning_id(
            conn,
            """
            INSERT INTO futures_agent_trader_theses (
              post_id, symbol, direction, thesis_text, confidence
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (post_row_id, "BTC", direction, "test", 0.9),
        )
        for lt, price, ordinal in (
            ("ENTRY_LOW", 100.0, 0),
            ("ENTRY_HIGH", 100.0, 0),
            ("STOP", 95.0, 0),
            ("TARGET", 110.0, 1),
        ):
            conn.execute(
                """
                INSERT INTO futures_agent_trader_levels (thesis_id, level_type, price, ordinal)
                VALUES (?, ?, ?, ?)
                """,
                (thesis_id, lt, price, ordinal),
            )
        policy_json = {
            p: {"return_pct": ret, "exit_event": "TP1", "notes": ""}
            for p in ("P1", "P2", "P3", "P4", "P5", "P6")
        }
        outcome_id = insert_returning_id(
            conn,
            """
            INSERT INTO futures_agent_research_signal_outcomes (
              thesis_id, post_id, channel, symbol, exchange_symbol, direction,
              decision_ts, entry_mode, entry_status, entry_ts, entry_price, entry_fill_model,
              stop_mode, stop_price, outcome_status, first_terminal_event, first_terminal_ts,
              max_target_reached, mfe_pct, mae_pct, ambiguous_intrabar,
              conservative_terminal, optimistic_terminal, raw_return_pct,
              data_quality_status, symbol_resolve_status, candle_meta_json,
              policy_results_json, engine_version, created_at
            ) VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                thesis_id, post_row_id, "signalyp", "BTC", "BTCUSDT", direction, message_ts,
                "NUMERIC_ZONE", "ENTERED", message_ts + 60, 100.0, "conservative_boundary",
                "NUMERIC_STOP", 95.0, "ENTERED", "TP1", message_ts + 120,
                1, max(ret, 0.0) + 1.0, -1.0, 0,
                "TP1", "TP1", ret,
                "COMPLETE", "resolved",
                json.dumps({"targets": [{"ordinal": 1, "price": 110.0, "validity": "VALID"}]}),
                json.dumps(policy_json), ENGINE_VERSION, message_ts,
            ),
        )
        conn.execute(
            """
            INSERT INTO futures_agent_research_signal_events (
              outcome_id, event_type, target_index, event_ts, event_price,
              candle_open_ts, ambiguity_flag, ordinal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (outcome_id, "ENTRY", None, message_ts + 60, 100.0, message_ts + 60, 0, 0),
        )
        conn.execute(
            """
            INSERT INTO futures_agent_research_signal_events (
              outcome_id, event_type, target_index, event_ts, event_price,
              candle_open_ts, ambiguity_flag, ordinal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (outcome_id, "TP", 1, message_ts + 120, 110.0, message_ts + 120, 0, 1),
        )
        conn.commit()
        return outcome_id

    def test_robust_stats_trim_and_winsor(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 100.0]
        self.assertAlmostEqual(trimmed_mean(values, 0.2) or 0.0, 3.0, places=3)
        self.assertLess(winsorized_mean(values, 0.2) or 0.0, 25.0)
        rs = compute_robust_stats(values)
        self.assertIn("top_1", rs.top_contribution_pct)
        self.assertGreater(rs.top_contribution_pct["top_1"], 0.8)

    def test_verdict_rejects_concentrated_edge(self) -> None:
        verdict, _ = compute_research_verdict(VerdictInputs(
            entered_n=200,
            complete_data_pct=0.99,
            headline_mean=7.0,
            bootstrap_mean_ci=(-0.3, 22.0),
            winsorized_5pct_mean=1.0,
            top10_concentration=0.80,
            top25_concentration=0.90,
            cost_adjusted_mean=5.0,
            risk_sized_max_dd=20.0,
            oos_years_positive=2,
            oos_years_total=3,
            rolling_blocks_positive=3,
            rolling_blocks_total=4,
            rolling_3m_positive=2,
            rolling_3m_total=4,
            recent_250_mean=2.0,
            p4_mean=-1.0,
            p5_mean=-1.0,
            p6_mean=-1.0,
        ))
        self.assertIn(verdict, ("CONDITIONAL_EDGE", "NOT_STABLE"))

    def test_outlier_audit_on_seeded_trades(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            for i in range(5):
                self._seed_outcome(conn, post_id=f"m{i}", ret=float(i), message_ts=1_700_000_000 + i * 3600)
            self._seed_outcome(conn, post_id="outlier", ret=50.0, message_ts=1_700_100_000)
            audit = run_outlier_audit(conn, channel="signalyp", policy="P1", validate_top_n=3)
        self.assertEqual(audit.trade_count, 6)
        self.assertIn("P1", audit.attribution.get("policies", {}))
        self.assertGreater(len(audit.attribution["policies"]["P1"]["top_10"]), 0)

    def test_validate_trade_math_consistent(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            oid = self._seed_outcome(conn, post_id="v1", ret=10.0)
            row = conn.execute(
                "SELECT * FROM futures_agent_research_signal_outcomes WHERE id = ?",
                (oid,),
            ).fetchone()
            findings = validate_trade_math(conn, row, policy="P1", candle_provider=None)
            errors = [f for f in findings if f.severity == "ERROR"]
        self.assertEqual(errors, [])

    def test_report_verdict_not_persistent_on_outlier_sample(self) -> None:
        with self._conn() as conn:
            apply_migrations(conn)
            for i in range(40):
                self._seed_outcome(
                    conn,
                    post_id=f"b{i}",
                    ret=0.5,
                    message_ts=1_700_000_000 + i * 86400,
                )
            self._seed_outcome(conn, post_id="huge", ret=200.0, message_ts=1_701_000_000)
            report = run_outcome_report(conn, channel="signalyp", policy="P1")
        self.assertIn(report.verdict, (
            "CONDITIONAL_EDGE",
            "NOT_STABLE",
            "INSUFFICIENT_DATA",
        ))


if __name__ == "__main__":
    unittest.main()
