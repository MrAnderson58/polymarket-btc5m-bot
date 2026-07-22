"""S51 — Signal consistency: single source of truth across surfaces."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from bot.research.ai_analyst.paper_trading.signal_builder import build_signal_from_context
from bot.research.ai_analyst.signal_consistency.confidence import (
    calibrate_confidence,
    historical_hit_rates,
)
from bot.research.ai_analyst.signal_consistency.direction import (
    lock_direction,
    score_direction_from_context,
)
from bot.research.ai_analyst.signal_consistency.reasons import (
    is_banned_reason,
    sanitize_reasons,
)
from bot.research.ai_analyst.signal_consistency.repository import (
    SignalTruthRepository,
    day_start_local_ts,
)
from bot.research.ai_analyst.intelligence_compression import (
    dedupe_events,
    normalize_event,
)
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations


class TestReasonsAndDirectionS51(unittest.TestCase):
    def test_banned_generic_reasons(self) -> None:
        self.assertTrue(is_banned_reason("Current trend"))
        self.assertTrue(is_banned_reason("Bullish structure"))
        self.assertFalse(is_banned_reason("ETF +203M"))
        cleaned = sanitize_reasons([
            "Current trend",
            "ETF +203M",
            "Funding neutral",
            "OI rising",
        ])
        self.assertEqual(cleaned, ["ETF +203M", "Funding neutral", "OI rising"])

    def test_machine_direction_locks_llm(self) -> None:
        ctx = {
            "btc": {"price": 65000, "change_24h_pct": 1.5},
            "etf": {"btc_etf": {"netflow_1d": 420}},
            "funding": {"current": 0.00004},
            "open_interest": {"delta_24h": 3.0},
        }
        decision = score_direction_from_context(ctx)
        self.assertEqual(decision.direction, "LONG")
        self.assertEqual(lock_direction("SHORT", decision), "LONG")

        sig = build_signal_from_context(
            ctx,
            proposed_direction="SHORT",
            llm_reasons=["Current trend", "ETF +420M"],
            calibrate=False,
        )
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.direction, "LONG")
        self.assertNotIn("Current trend", sig.reasons)
        self.assertTrue(any("ETF" in r for r in sig.reasons))

    def test_wait_blocks_llm_direction(self) -> None:
        ctx = {"btc": {"price": 65000, "change_24h_pct": 0.0}}
        decision = score_direction_from_context(ctx)
        self.assertEqual(decision.direction, "WAIT")
        self.assertEqual(lock_direction("LONG", decision), "WAIT")
        self.assertIsNone(build_signal_from_context(ctx, proposed_direction="LONG", calibrate=False))


class TestConfidenceCalibrationS51(unittest.TestCase):
    def test_calibrate_with_history(self) -> None:
        outcomes = [
            {"confidence": 80, "pnl_usd": 10, "r_multiple": 1},
            {"confidence": 75, "pnl_usd": -5, "r_multiple": -0.5},
            {"confidence": 72, "pnl_usd": 8, "r_multiple": 0.8},
            {"confidence": 78, "pnl_usd": 3, "r_multiple": 0.4},
            {"confidence": 81, "pnl_usd": -2, "r_multiple": -0.2},
            {"confidence": 70, "pnl_usd": 1, "r_multiple": 0.1},
            {"confidence": 85, "pnl_usd": 12, "r_multiple": 1.2},
            {"confidence": 77, "pnl_usd": 4, "r_multiple": 0.3},
            {"confidence": 79, "pnl_usd": -1, "r_multiple": -0.1},
            {"confidence": 74, "pnl_usd": 2, "r_multiple": 0.2},
        ]
        rates = historical_hit_rates(outcomes)
        self.assertGreater(rates["high"]["n"], 0)
        cal = calibrate_confidence(90, outcomes=outcomes, min_samples=5)
        self.assertGreaterEqual(cal, 5)
        self.assertLessEqual(cal, 95)
        # Without enough samples, raw returned
        self.assertEqual(calibrate_confidence(66, outcomes=[], min_samples=8), 66.0)


class TestDedupMultiKeyS51(unittest.TestCase):
    def test_dedupe_by_url_and_cluster(self) -> None:
        a = normalize_event({"title": "Story A", "url": "https://news.example/1"})
        b = normalize_event({"title": "Different wording", "url": "https://news.example/1"})
        assert a and b
        self.assertEqual(len(dedupe_events([a, b])), 1)

        c = normalize_event({"title": "ETF flow", "cluster_id": "evt-9"})
        d = normalize_event({"title": "ETF flows again", "cluster_id": "evt-9"})
        assert c and d
        self.assertEqual(len(dedupe_events([c, d])), 1)

    def test_dedupe_by_text_hash(self) -> None:
        a = normalize_event({"title": "Whale moved 18k BTC", "summary": "same body"})
        b = normalize_event({"title": "Whale moved 18k BTC", "summary": "same body"})
        assert a and b
        self.assertEqual(a["text_hash"], b["text_hash"])
        self.assertEqual(len(dedupe_events([a, b])), 1)


class TestRepositoryConsistencyS51(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "s51.db"
        configure_unit_test_db_isolation(self.db)
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
            now = int(__import__("time").time())
            # one open trade (S47 schema)
            conn.execute(
                """
                INSERT INTO ai_paper_trades_s47 (
                  trade_id, signal_id, symbol, direction, strategy,
                  entry, stop_loss, tp1, tp2, tp3, risk_pct, confidence,
                  reasons_json, opened_at, status, size_remaining,
                  fills_json, account_equity_at_open, updated_at
                ) VALUES (
                  't1','s1','BTC','LONG','ai',
                  100,95,105,110,115,1.0,70,
                  '[]',?,'OPEN',1.0,
                  '[]',10000,?
                )
                """,
                (now, now),
            )
            # legacy S42 opens must NOT inflate trader open count
            try:
                for i in range(5):
                    conn.execute(
                        """
                        INSERT INTO market_events_paper_trades_s42 (
                          s40_signal_type, s40_signal_id, symbol, direction,
                          entry, stop, tp1, tp2, created_at, status,
                          capital_usd, leverage, updated_at
                        ) VALUES ('test', ?, 'BTC', 'LONG', 1, 0.9, 1.1, 1.2, ?, 'OPEN', 100, 20, ?)
                        """,
                        (i, now, now),
                    )
            except Exception:
                pass
            # signals today
            for i in range(3):
                conn.execute(
                    """
                    INSERT INTO ai_signal_history_s48 (
                      signal_id, created_at, market, direction, confidence, score,
                      reasoning, reasons_json, strategy, entry_low, entry_high,
                      stop_loss, tp1, tp2, tp3, risk_pct, status, tags_json
                    ) VALUES (?, ?, 'BTC', 'LONG', 70, 71, '', '[]', 'ai',
                      100, 101, 95, 105, 110, 115, 1.0, 'PENDING', '[]')
                    """,
                    (f"sig-{i}", now),
                )
            # closed outcome for stats (FK → history row)
            conn.execute(
                """
                INSERT INTO ai_signal_history_s48 (
                  signal_id, created_at, market, direction, confidence, score,
                  reasoning, reasons_json, strategy, entry_low, entry_high,
                  stop_loss, tp1, tp2, tp3, risk_pct, status, tags_json
                ) VALUES ('out-1', ?, 'BTC', 'LONG', 70, 71, '', '[]', 'ai',
                  100, 101, 95, 105, 110, 115, 1.0, 'CLOSED', '[]')
                """,
                (now - 10,),
            )
            conn.execute(
                """
                INSERT INTO ai_signal_outcomes_s48 (
                  signal_id, trade_id, closed_at, result, r_multiple, pnl_usd,
                  mfe_pct, mae_pct, hold_time_sec, tp_level_reached, exit_reason,
                  strategy, market, direction, confidence, score, reasons_json, tags_json
                ) VALUES (
                  'out-1','t-closed',?, 'WIN', 1.2, 50,
                  2, 1, 600, 'TP1', 'tp1',
                  'ai', 'BTC', 'LONG', 70, 71, '[]', '[]'
                )
                """,
                (now,),
            )
            conn.commit()

        @contextmanager
        def factory():
            with market_events_connection() as c:
                yield c

        self.repo = SignalTruthRepository(conn_factory=factory)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_snapshot_counts(self) -> None:
        snap = self.repo.snapshot()
        self.assertEqual(snap.open_count, 1)
        self.assertEqual(snap.signals_today, 4)  # 3 pending + 1 closed history
        self.assertEqual(len(snap.open_trades), 1)
        self.assertEqual(snap.stats["open_trades"], 1)
        self.assertEqual(snap.stats["trades"], 1)

    def test_doctor_matches_telegram_surfaces(self) -> None:
        """doctor open/signals == /open == /stats == /signals book."""
        snap = self.repo.snapshot()

        with patch(
            "bot.research.ai_analyst.signal_consistency.repository.get_repository",
            return_value=self.repo,
        ), patch(
            "bot.research.market_events.doctor.get_repository",
            create=True,
        ):
            # Patch the import sites used by doctor / telegram
            import bot.research.market_events.doctor as doctor
            import bot.research.ai_analyst.strategy_validation.telegram_views as views

            with patch.object(doctor, "_proc_running", return_value=True), patch(
                "bot.research.ai_analyst.signal_consistency.repository.get_repository",
                return_value=self.repo,
            ), patch(
                "bot.research.ai_analyst.strategy_validation.telegram_views.get_repository",
                return_value=self.repo,
            ):
                paper, open_n = doctor._check_paper_trading()
                signals_today = doctor._signals_today()
                open_rows = self.repo.list_open_trades()
                stats = self.repo.stats_dashboard()
                signals = self.repo.list_signals(limit=50)

                self.assertEqual(open_n, snap.open_count)
                self.assertEqual(open_n, len(open_rows))
                self.assertEqual(open_n, stats["open_trades"])
                self.assertEqual(signals_today, snap.signals_today)
                self.assertGreaterEqual(signals_today, 3)
                self.assertGreaterEqual(len(signals), 3)

                # Telegram formatters use same repo — open section not empty
                open_html = views.format_open_telegram()
                self.assertIn("BTC", open_html)
                self.assertNotIn("(none open)", open_html)
                stats_html = views.format_stats_telegram()
                self.assertIn("1", stats_html)  # open or trades

    def test_s42_not_mixed_into_open_count(self) -> None:
        """Regression: doctor must not sum S42 into trader open count."""
        self.assertEqual(self.repo.count_open_trades(), 1)


class TestDoctorCollectUsesRepoS51(unittest.TestCase):
    def test_collect_doctor_open_from_repo(self) -> None:
        from bot.research.market_events.doctor import Check, collect_doctor

        class FakeRepo:
            def count_open_trades(self) -> int:
                return 7

            def count_signals_today(self, now=None) -> int:
                return 11

        with patch(
            "bot.research.ai_analyst.signal_consistency.repository.get_repository",
            return_value=FakeRepo(),
        ), patch(
            "bot.research.market_events.doctor._check_sqlite",
            return_value=Check("SQLite", True),
        ), patch(
            "bot.research.market_events.doctor._check_postgres",
            return_value=Check("PostgreSQL", True),
        ), patch(
            "bot.research.market_events.doctor._check_news",
            return_value=[],
        ), patch(
            "bot.research.market_events.doctor._check_ai",
            return_value=[],
        ), patch(
            "bot.research.market_events.doctor._check_telegram_bot",
            return_value=Check("Connected", True),
        ), patch(
            "bot.research.market_events.doctor._proc_running",
            return_value=True,
        ), patch(
            "bot.research.market_events.doctor._git_sha",
            return_value="abc",
        ), patch(
            "bot.research.market_events.doctor._last_report_utc",
            return_value="—",
        ):
            data = collect_doctor(skip_network=True)
        self.assertEqual(data["open_trades"], 7)
        self.assertEqual(data["signals_today"], 11)


if __name__ == "__main__":
    unittest.main()
