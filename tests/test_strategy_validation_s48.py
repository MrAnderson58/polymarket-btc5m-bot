"""S48 — Strategy Validation & AI Signal Ranking tests."""

from __future__ import annotations

import sqlite3
import unittest

from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
    SUPPORTED_COMMANDS,
)
from bot.research.ai_analyst.cli import main as cli_main
from bot.research.ai_analyst.paper_trading.signals import TradingSignal
from bot.research.ai_analyst.strategy_validation.dashboard import build_dashboard
from bot.research.ai_analyst.strategy_validation.history import history_from_trading_signal
from bot.research.ai_analyst.strategy_validation.outcomes import (
    aggregate_exit_breakdown,
    outcome_from_trade,
)
from bot.research.ai_analyst.strategy_validation.ranking import (
    build_ranking_report,
    format_leaderboard,
)
from bot.research.ai_analyst.strategy_validation.service import StrategyValidationService
from bot.research.ai_analyst.strategy_validation.store import (
    load_outcomes,
    load_signal_history,
)
from bot.research.ai_analyst.strategy_validation.telegram_views import (
    S48_COMMANDS,
    handle_s48_command,
)
from bot.research.ai_analyst.telegram_terminal import AI_RESEARCH_COMMANDS, is_ai_research_command
from bot.research.ai_analyst.prompt_builder import load_prompt


def _mem() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    return conn


def _sig(**kw) -> TradingSignal:
    base = dict(
        symbol="BTC",
        direction="LONG",
        entry_low=100.0,
        entry_high=102.0,
        stop_loss=95.0,
        tp1=110.0,
        tp2=120.0,
        tp3=130.0,
        risk_pct=1.0,
        confidence=70.0,
        reasons=["Positive ETF inflows", "AI narrative"],
        strategy="etf_ai",
    )
    base.update(kw)
    return TradingSignal(**base)


class TestSignalHistoryS48(unittest.TestCase):
    def test_history_fields(self) -> None:
        sig = _sig()
        rec = history_from_trading_signal(sig)
        d = rec.to_dict()
        for key in (
            "signal_id", "created_at", "market", "direction", "confidence",
            "score", "reasoning", "entry_low", "entry_high", "stop_loss",
            "tp1", "tp2", "tp3",
        ):
            self.assertIn(key, d)
        self.assertIn("ETF", d["tags"])


class TestOutcomeAggregationS48(unittest.TestCase):
    def test_auto_outcome_on_close(self) -> None:
        svc = StrategyValidationService()
        svc.submit_signal(_sig(signal_id="o1"))
        svc.tick("BTC", 101, ts=1)
        svc.tick("BTC", 110, ts=2)
        svc.tick("BTC", 120, ts=3)
        svc.tick("BTC", 130, ts=4)
        outcomes = svc.list_outcomes()
        self.assertEqual(len(outcomes), 1)
        o = outcomes[0]
        self.assertEqual(o["result"], "WIN")
        self.assertEqual(o["tp_level_reached"], "TP3")
        self.assertGreater(o["r_multiple"], 0)
        self.assertIn("hold_time_sec", o)
        self.assertIn("mfe_pct", o)
        self.assertIn("mae_pct", o)

    def test_stop_outcome(self) -> None:
        svc = StrategyValidationService()
        svc.submit_signal(_sig(
            signal_id="o2",
            reasons=["Macro headwinds", "High funding"],
            strategy="macro_funding",
        ))
        svc.tick("BTC", 101, ts=1)
        svc.tick("BTC", 95, ts=2)
        o = svc.list_outcomes()[0]
        self.assertEqual(o["result"], "LOSS")
        self.assertEqual(o["tp_level_reached"], "STOP")


class TestDashboardS48(unittest.TestCase):
    def test_stats_fields(self) -> None:
        svc = StrategyValidationService()
        svc.submit_signal(_sig(signal_id="w1", strategy="A"))
        svc.tick("BTC", 101, ts=1)
        svc.tick("BTC", 110, ts=2)
        svc.tick("BTC", 120, ts=3)
        svc.tick("BTC", 130, ts=4)
        svc.submit_signal(_sig(
            signal_id="l1", strategy="A",
            entry_low=200, entry_high=202, stop_loss=190,
            tp1=210, tp2=220, tp3=230,
            reasons=["Macro", "High funding"],
        ))
        svc.tick("BTC", 201, ts=10)
        svc.tick("BTC", 190, ts=11)
        dash = svc.dashboard()
        for key in (
            "win_rate", "profit_factor", "expectancy", "avg_r",
            "avg_hold_seconds", "tp1_pct", "tp2_pct", "tp3_pct", "stop_pct",
            "max_drawdown_pct", "sharpe",
        ):
            self.assertIn(key, dash)
        self.assertEqual(dash["trades"], 2)
        self.assertEqual(dash["wins"], 1)
        self.assertEqual(dash["losses"], 1)
        self.assertGreater(dash["stop_pct"], 0)
        self.assertGreater(dash["tp3_pct"], 0)


class TestRankingS48(unittest.TestCase):
    def test_best_and_worst_setups(self) -> None:
        svc = StrategyValidationService()
        svc.submit_signal(_sig(
            signal_id="w",
            reasons=["ETF demand", "AI infrastructure", "Neutral funding"],
            strategy="best",
        ))
        svc.tick("BTC", 101, ts=1)
        svc.tick("BTC", 110, ts=2)
        svc.tick("BTC", 120, ts=3)
        svc.tick("BTC", 130, ts=4)

        svc.submit_signal(_sig(
            signal_id="l",
            entry_low=200, entry_high=202, stop_loss=190,
            tp1=210, tp2=220, tp3=230,
            reasons=["Macro shock", "High funding"],
            strategy="worst",
        ))
        svc.tick("BTC", 201, ts=10)
        svc.tick("BTC", 190, ts=11)

        payload = svc.ranking()
        self.assertGreaterEqual(payload["n_outcomes"], 2)
        self.assertIsNotNone(payload["best_setup"])
        self.assertIsNotNone(payload["worst_setup"])
        self.assertTrue(payload["best_setup"]["avg_r"] >= payload["worst_setup"]["avg_r"])
        board = format_leaderboard(payload)
        self.assertIn("LEADERBOARD", board)


class TestPersistenceS48(unittest.TestCase):
    def test_schema_and_roundtrip(self) -> None:
        self.assertGreaterEqual(SCHEMA_VERSION, 63)
        conn = _mem()
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        self.assertIn("ai_signal_history_s48", tables)
        self.assertIn("ai_signal_outcomes_s48", tables)

        svc = StrategyValidationService(conn=conn)
        svc.submit_signal(_sig(signal_id="persist1"))
        svc.tick("BTC", 101, ts=1)
        svc.tick("BTC", 110, ts=2)
        svc.tick("BTC", 120, ts=3)
        svc.tick("BTC", 130, ts=4)
        conn.commit()

        hist = load_signal_history(conn)
        outs = load_outcomes(conn)
        self.assertEqual(len(hist), 1)
        self.assertEqual(len(outs), 1)
        self.assertEqual(outs[0]["signal_id"], "persist1")


class TestTelegramS48(unittest.TestCase):
    def test_commands_registered(self) -> None:
        for cmd in ("/signals", "/open", "/closed", "/stats", "/leaderboard", "/daily"):
            self.assertIn(cmd, SUPPORTED_COMMANDS)
            self.assertIn(cmd, AI_RESEARCH_COMMANDS)
            self.assertIn(cmd, S48_COMMANDS)
            self.assertTrue(is_ai_research_command(cmd))

    def test_handlers_return_html(self) -> None:
        for cmd in S48_COMMANDS:
            text = handle_s48_command(cmd)
            self.assertIsInstance(text, str)
            self.assertGreater(len(text), 5)


class TestCLIS48(unittest.TestCase):
    def test_prompt_exists(self) -> None:
        self.assertIn("Best Strategies", load_prompt("signal_ranking"))

    def test_validate_cli(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            cli_main(["validate", "--help"])
        self.assertEqual(cm.exception.code, 0)
        code = cli_main(["validate", "--demo", "--json"])
        self.assertEqual(code, 0)


class TestDailyReportS48(unittest.TestCase):
    def test_daily_format(self) -> None:
        svc = StrategyValidationService()
        svc.submit_signal(_sig(signal_id="d1", reasons=["ETF", "AI", "Neutral funding"]))
        svc.tick("BTC", 101, ts=1)
        svc.tick("BTC", 110, ts=2)
        svc.tick("BTC", 120, ts=3)
        svc.tick("BTC", 130, ts=4)
        text = svc.daily_report(now=100)
        self.assertIn("Signals today", text)
        self.assertIn("WinRate:", text)
        self.assertIn("PF:", text)
        self.assertIn("Expectancy:", text)
        self.assertIn("Best setup:", text)


if __name__ == "__main__":
    unittest.main()
