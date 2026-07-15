"""Phase S2.2 — Core stabilization: RO decision, reversal explain, no provider_state writes."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.candles import CandleBar
from bot.research.market_events.signal_intelligence.decision_engine_s20 import (
    run_decision_engine_s20,
)
from bot.research.market_events.signal_intelligence.explain_decision_s22 import (
    evaluate_reversal_confirmation_s22,
    format_explain_decision_s22,
    format_reversal_conditions_s22,
)
from bot.research.market_events.signal_intelligence.market_data_source_g01 import (
    SymbolMarketDataG01,
    _allow_provider_state_persist,
    fetch_symbol_market_data_g01,
)
from bot.research.market_events.signal_intelligence.trend_windows_g3 import TrendWindowG3
from bot.research.market_events.sqlite_manager_g05 import PURE_READONLY_COMMANDS


def _bars(n: int = 40, up: bool = True) -> list[CandleBar]:
    out: list[CandleBar] = []
    px = 100.0
    ts = 1_700_000_000
    for i in range(n):
        nxt = px * (1.004 if up else 0.996)
        out.append(
            CandleBar(
                open_ts=ts + i * 300,
                open=px,
                high=max(px, nxt) * 1.001,
                low=min(px, nxt) * 0.999,
                close=nxt,
                volume=1000 + i,
            )
        )
        px = nxt
    return out


class TestDecisionReadOnlyS22(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "me.db")
        configure_unit_test_db_isolation(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_decision_default_no_insert(self) -> None:
        data = SymbolMarketDataG01(
            symbol="BTC",
            price=100.0,
            funding=-0.0001,
            open_interest=1.0,
            volume_24h=10.0,
            bars=_bars(),
            source="bybit",
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
            result = run_decision_engine_s20(
                conn,
                "BTC",
                force_fallback=True,
                headlines=[{"title": "Bitcoin rally", "summary": "", "source": "coindesk"}],
                market_data=data,
            )
            n = conn.execute("SELECT COUNT(*) AS n FROM market_decision_runs_s20").fetchone()["n"]
        self.assertIsNone(result["run_id"])
        self.assertEqual(n, 0)
        self.assertIn("BTC", result["telegram"])

    def test_decision_in_pure_readonly(self) -> None:
        self.assertIn("/decision", PURE_READONLY_COMMANDS)
        self.assertIn("/explain-decision", PURE_READONLY_COMMANDS)
        self.assertIn("/status", PURE_READONLY_COMMANDS)

    def test_telegram_decision_does_not_write(self) -> None:
        from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
            handle_market_events_command,
        )

        data = SymbolMarketDataG01(
            symbol="BTC", price=50_000.0, funding=0.0, open_interest=1.0,
            volume_24h=10.0, bars=_bars(), source="okx",
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()

        with patch(
            "bot.research.market_events.signal_intelligence.decision_engine_s20.market_agent.fetch_symbol_market_data_g01",
            return_value=data,
        ), patch(
            "bot.research.market_events.signal_intelligence.decision_engine_s20.news_agent.fetch_rss_headlines_s20",
            return_value=[{"title": "Bitcoin bull", "summary": "", "source": "decrypt"}],
        ), patch(
            "bot.research.market_events.signal_intelligence.decision_engine_s20.news_agent.fetch_cryptopanic_s20",
            return_value=[],
        ), patch(
            "bot.research.market_events.signal_intelligence.claude_client_g2.is_claude_configured",
            return_value=False,
        ):
            result = handle_market_events_command("/decision BTC")

        self.assertTrue(result.ok)
        with market_events_connection() as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM market_decision_runs_s20").fetchone()["n"]
            traces = conn.execute(
                "SELECT COUNT(*) AS n FROM market_events_command_trace_g351 WHERE command = '/decision'"
            ).fetchone()["n"]
        self.assertEqual(n, 0)
        self.assertEqual(traces, 0)


class TestProviderStateReadS22(unittest.TestCase):
    def test_persist_state_false_blocks_db_writes(self) -> None:
        from bot.research.market_events.signal_intelligence import market_data_source_g01 as m

        m._allow_provider_state_persist = True
        m._allow_provider_state_persist = False
        try:
            with patch(
                "bot.research.market_events.signal_intelligence.health_g3.set_g3_ops_state",
            ) as set_ops:
                m._save_provider_state_g03({"dummy": True})
                set_ops.assert_not_called()
        finally:
            m._allow_provider_state_persist = True
        self.assertTrue(m._allow_provider_state_persist)


class TestReversalExplainS22(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "me.db")
        configure_unit_test_db_isolation(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_all_conditions_literal(self) -> None:
        trend = TrendWindowG3(
            symbol="BTC",
            window_minutes=60,
            pattern_type="stair_step",
            consecutive_candles=3,
            trend_score=40.0,
            direction="UP",
            details={},
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            ev = evaluate_reversal_confirmation_s22(conn, symbol="BTC", trend=trend)
            text = format_reversal_conditions_s22(ev)

        self.assertFalse(ev.passed)
        self.assertEqual(len(ev.conditions), 5)
        self.assertIn("FALSE", text)
        self.assertIn("потому что", text)
        self.assertIn("Condition 1", text)
        self.assertIn("Condition 5", text)
        for c in ev.conditions:
            self.assertIn(c.label, ("PASS", "FAIL"))

    def test_pattern_rule_passes(self) -> None:
        trend = TrendWindowG3(
            symbol="ETH",
            window_minutes=30,
            pattern_type="capitulation",
            consecutive_candles=2,
            trend_score=10.0,
            direction="DOWN",
            details={},
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            ev = evaluate_reversal_confirmation_s22(conn, symbol="ETH", trend=trend)
        self.assertTrue(ev.passed)
        self.assertTrue(ev.conditions[4].passed)  # C5 pattern

    def test_explain_decision_format(self) -> None:
        data = SymbolMarketDataG01(
            symbol="BTC", price=100.0, funding=0.0001, open_interest=1.0,
            volume_24h=10.0, bars=_bars(), source="bybit",
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
            with patch(
                "bot.research.market_events.signal_intelligence.market_data_source_g01.fetch_symbol_market_data_g01",
                return_value=data,
            ):
                text = format_explain_decision_s22(conn, "BTC")

        self.assertIn("Trend", text)
        self.assertIn("Funding", text)
        self.assertIn("MACD", text)
        self.assertIn("EMA", text)
        self.assertIn("RSI", text)
        self.assertIn("Reversal", text)
        self.assertIn("READ ONLY", text)
        self.assertTrue("PASS" in text or "FAIL" in text)


class TestStatusNoHeartbeatWriteS22(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "me.db")
        configure_unit_test_db_isolation(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_status_does_not_touch_reader(self) -> None:
        from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
            handle_market_events_command,
        )

        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
            before = conn.execute(
                "SELECT value FROM market_events_g3_ops_state WHERE key = 'heartbeat_reader_ts'"
            ).fetchone()

        result = handle_market_events_command("/status")
        self.assertTrue(result.ok)

        with market_events_connection() as conn:
            after = conn.execute(
                "SELECT value FROM market_events_g3_ops_state WHERE key = 'heartbeat_reader_ts'"
            ).fetchone()
        self.assertEqual(
            None if before is None else before["value"],
            None if after is None else after["value"],
        )


if __name__ == "__main__":
    unittest.main()
