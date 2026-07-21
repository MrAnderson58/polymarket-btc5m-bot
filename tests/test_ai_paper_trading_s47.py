"""S47 — AI Paper Trading Engine tests."""

from __future__ import annotations

import sqlite3
import time
import unittest

from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.ai_analyst.paper_trading.engine import PaperTradingEngine
from bot.research.ai_analyst.paper_trading.models import (
    EXIT_STOP,
    EXIT_TP1,
    EXIT_TP2,
    EXIT_TP3,
    STATUS_CLOSED,
    STATUS_OPEN,
    compute_stats,
)
from bot.research.ai_analyst.paper_trading.reports import (
    format_closed_trades,
    format_full_report,
    format_open_trades,
    format_strategy_stats,
)
from bot.research.ai_analyst.paper_trading.signal_builder import build_signal_from_context
from bot.research.ai_analyst.paper_trading.signals import TradingSignal
from bot.research.ai_analyst.paper_trading.store import load_engine, persist_engine
from bot.research.ai_analyst.cli import main as cli_main
from bot.research.ai_analyst.config import load_agent_profiles
from bot.research.ai_analyst.prompt_builder import load_prompt


def _long_signal(**kwargs) -> TradingSignal:
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
        reasons=["ETF inflows", "Risk-on equities"],
        strategy="ai_test",
    )
    base.update(kwargs)
    return TradingSignal(**base)


def _mem() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    return conn


class TestSignalModelS47(unittest.TestCase):
    def test_required_fields_and_validation(self) -> None:
        sig = _long_signal()
        self.assertEqual(sig.validate(), [])
        self.assertTrue(sig.signal_id.startswith("s47-"))
        d = sig.to_dict()
        for key in (
            "entry_low", "entry_high", "stop_loss", "tp1", "tp2", "tp3",
            "risk_pct", "confidence", "reasons",
        ):
            self.assertIn(key, d)

    def test_invalid_long_stop(self) -> None:
        sig = _long_signal(stop_loss=101.0)
        self.assertTrue(any("stop" in e.lower() for e in sig.validate()))


class TestPaperEngineS47(unittest.TestCase):
    def test_open_on_entry_range(self) -> None:
        eng = PaperTradingEngine(initial_equity=10_000)
        eng.submit_signal(_long_signal())
        self.assertEqual(len(eng.open_trades()), 0)
        eng.tick("BTC", 99.0, ts=1)  # below range
        self.assertEqual(len(eng.open_trades()), 0)
        events = eng.tick("BTC", 101.0, ts=2)
        self.assertTrue(any(e["type"] == "opened" for e in events))
        self.assertEqual(len(eng.open_trades()), 1)
        trade = eng.open_trades()[0]
        self.assertEqual(trade.status, STATUS_OPEN)
        self.assertEqual(trade.entry, 101.0)

    def test_scale_out_tp1_tp2_tp3(self) -> None:
        eng = PaperTradingEngine(initial_equity=10_000)
        eng.submit_signal(_long_signal())
        eng.tick("BTC", 101.0, ts=10)
        trade = eng.open_trades()[0]
        eng.tick("BTC", 110.0, ts=20)
        self.assertTrue(trade.tp1_hit)
        self.assertAlmostEqual(trade.size_remaining, 2.0 / 3.0, places=5)
        eng.tick("BTC", 120.0, ts=30)
        self.assertTrue(trade.tp2_hit)
        eng.tick("BTC", 130.0, ts=40)
        self.assertEqual(trade.status, STATUS_CLOSED)
        self.assertEqual(trade.exit_reason, EXIT_TP3)
        self.assertGreater(trade.pnl_usd, 0)
        self.assertGreater(trade.r_multiple, 0)
        self.assertEqual(trade.holding_seconds, 30)
        self.assertEqual([f.level for f in trade.fills], [EXIT_TP1, EXIT_TP2, EXIT_TP3])

    def test_stop_loss_after_partial_tp1(self) -> None:
        eng = PaperTradingEngine(initial_equity=10_000)
        eng.submit_signal(_long_signal())
        eng.tick("BTC", 101.0, ts=10)
        trade = eng.open_trades()[0]
        eng.tick("BTC", 110.0, ts=20)  # TP1
        eng.tick("BTC", 95.0, ts=30)   # stop remainder
        self.assertEqual(trade.status, STATUS_CLOSED)
        self.assertEqual(trade.exit_reason, EXIT_STOP)
        self.assertTrue(trade.tp1_hit)
        self.assertLess(trade.size_remaining, 1e-9)

    def test_mfe_mae_tracked(self) -> None:
        eng = PaperTradingEngine()
        eng.submit_signal(_long_signal())
        eng.tick("BTC", 101.0, ts=1)
        trade = eng.open_trades()[0]
        eng.tick("BTC", 108.0, ts=2)  # favorable
        eng.tick("BTC", 98.0, ts=3)   # adverse but above stop
        self.assertGreater(trade.mfe_pct, 0)
        self.assertLess(trade.mae_pct, 0)

    def test_short_stop_and_tp(self) -> None:
        eng = PaperTradingEngine()
        sig = TradingSignal(
            symbol="BTC",
            direction="SHORT",
            entry_low=100.0,
            entry_high=102.0,
            stop_loss=110.0,
            tp1=90.0,
            tp2=80.0,
            tp3=70.0,
            risk_pct=1.0,
            confidence=65,
            reasons=["Bearish"],
            strategy="short_test",
        )
        eng.submit_signal(sig)
        eng.tick("BTC", 101.0, ts=1)
        eng.tick("BTC", 90.0, ts=2)
        eng.tick("BTC", 80.0, ts=3)
        eng.tick("BTC", 70.0, ts=4)
        closed = eng.closed_trades()[0]
        self.assertEqual(closed.exit_reason, EXIT_TP3)
        self.assertGreater(closed.pnl_usd, 0)


class TestStatsAndReportsS47(unittest.TestCase):
    def test_win_rate_pf_expectancy(self) -> None:
        eng = PaperTradingEngine(initial_equity=10_000)
        # Winner
        eng.submit_signal(_long_signal(signal_id="w1", strategy="A"))
        eng.tick("BTC", 101, ts=1)
        eng.tick("BTC", 110, ts=2)
        eng.tick("BTC", 120, ts=3)
        eng.tick("BTC", 130, ts=4)
        # Loser (stop)
        eng.submit_signal(_long_signal(signal_id="l1", strategy="A",
                                       entry_low=200, entry_high=202, stop_loss=190,
                                       tp1=210, tp2=220, tp3=230))
        eng.tick("BTC", 201, ts=10)
        eng.tick("BTC", 190, ts=11)

        st = eng.stats(strategy="A")
        self.assertEqual(st["trades"], 2)
        self.assertEqual(st["wins"], 1)
        self.assertEqual(st["losses"], 1)
        self.assertEqual(st["win_rate"], 50.0)
        self.assertIsNotNone(st["profit_factor"])
        self.assertIn("expectancy", st)

        report = format_full_report(eng)
        self.assertIn("OPEN TRADES", report)
        self.assertIn("CLOSED TRADES", report)
        self.assertIn("STRATEGY STATS", report)
        self.assertIn("WinRate=", format_strategy_stats(eng))
        self.assertIn("CLOSED", format_closed_trades(eng))

    def test_compute_stats_empty(self) -> None:
        st = compute_stats([])
        self.assertEqual(st["trades"], 0)
        self.assertEqual(st["win_rate"], 0.0)


class TestPersistenceS47(unittest.TestCase):
    def test_schema_v62_and_roundtrip(self) -> None:
        self.assertGreaterEqual(SCHEMA_VERSION, 62)
        conn = _mem()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ai_paper_trades_s47'",
        ).fetchone()
        self.assertIsNotNone(row)

        eng = PaperTradingEngine(initial_equity=10_000)
        eng.submit_signal(_long_signal())
        eng.tick("BTC", 101.0, ts=5)
        persist_engine(conn, eng, now=5)
        conn.commit()

        eng2 = load_engine(conn)
        self.assertEqual(len(eng2.open_trades()), 1)
        self.assertEqual(eng2.open_trades()[0].symbol, "BTC")


class TestSignalBuilderAndCLI(unittest.TestCase):
    def test_builder_from_context(self) -> None:
        ctx = {
            "btc": {"price": 65000, "trend": "Bullish", "change_24h_pct": 1.2},
            "analysis_quality": {"confidence": 72},
            "etf": {"btc_etf": {"netflow_5d": 100}},
        }
        sig = build_signal_from_context(ctx)
        self.assertIsNotNone(sig)
        assert sig is not None
        self.assertEqual(sig.direction, "LONG")
        self.assertEqual(sig.validate(), [])

    def test_prompt_and_agent_profile(self) -> None:
        text = load_prompt("paper_signal")
        self.assertIn("tp3", text.lower())
        profiles = load_agent_profiles()
        self.assertIn("s47_paper_signal", profiles)

    def test_cli_paper_help(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            cli_main(["paper", "--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_cli_paper_demo(self) -> None:
        code = cli_main(["paper", "--demo", "--json"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
