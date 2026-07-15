"""Phase S2.0 — Trading Decision Engine MVP tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.candles import CandleBar
from bot.research.market_events.signal_intelligence.decision_engine_s20.decision_agent import (
    run_decision_agent_s20,
)
from bot.research.market_events.signal_intelligence.decision_engine_s20.market_agent import (
    run_market_agent_s20,
)
from bot.research.market_events.signal_intelligence.decision_engine_s20.news_agent import (
    run_news_agent_s20,
    score_headlines_s20,
)
from bot.research.market_events.signal_intelligence.decision_engine_s20.orchestrator import (
    format_decision_telegram_s20,
    run_decision_engine_s20,
)
from bot.research.market_events.signal_intelligence.decision_engine_s20.x_twitter import (
    DEFAULT_X_ACCOUNT_WHITELIST,
    XClientInterface,
    XTweetStub,
)
from bot.research.market_events.signal_intelligence.market_data_source_g01 import (
    SymbolMarketDataG01,
)


def _bars(*, start: float = 100.0, up: bool = True, n: int = 20) -> list[CandleBar]:
    out: list[CandleBar] = []
    px = start
    ts = 1_700_000_000
    for i in range(n):
        nxt = px * (1.004 if up else 0.996)
        h, l = max(px, nxt) * 1.001, min(px, nxt) * 0.999
        out.append(
            CandleBar(
                open_ts=ts + i * 300,
                open=px,
                high=h,
                low=l,
                close=nxt,
                volume=1000.0 + i * 10,
            )
        )
        px = nxt
    return out


class TestMarketAgentS20(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "me.db")
        configure_unit_test_db_isolation(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_market_agent_long_geometry(self) -> None:
        data = SymbolMarketDataG01(
            symbol="BTC",
            price=110.0,
            funding=-0.0003,
            open_interest=1_000_000.0,
            volume_24h=50_000.0,
            bars=_bars(up=True),
            source="bybit",
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            out = run_market_agent_s20(conn, "BTC", market_data=data)
        self.assertEqual(out["direction"], "LONG")
        self.assertGreater(out["confidence"], 0.3)
        self.assertGreater(out["entry"], 0)
        self.assertLess(out["stop"], out["entry"])
        self.assertGreater(out["tp1"], out["entry"])
        self.assertGreater(out["tp2"], out["tp1"])
        self.assertTrue(out["reasons"])


class TestNewsAgentS20(unittest.TestCase):
    def test_bullish_headlines(self) -> None:
        headlines = [
            {"title": "Bitcoin ETF inflows surge to record high", "summary": "", "source": "coindesk"},
            {"title": "BTC breaks out as bulls accumulate", "summary": "", "source": "decrypt"},
        ]
        out = score_headlines_s20(headlines, symbol="BTC")
        self.assertEqual(out["sentiment"], "bullish")
        self.assertIn(out["importance"], ("high", "medium", "low"))
        self.assertTrue(out["reasons"])

    def test_news_agent_with_injected_headlines_and_x(self) -> None:
        class FakeX(XClientInterface):
            def fetch_recent(self, *, symbol: str, limit: int = 20):
                return [XTweetStub(account="woonomic", text="Bitcoin rally looks strong")]

            def is_configured(self) -> bool:
                return True

        out = run_news_agent_s20(
            "BTC",
            headlines=[{"title": "SEC approval fuels crypto rally", "summary": "", "source": "cryptopanic"}],
            x_client=FakeX(),
        )
        self.assertEqual(out["sentiment"], "bullish")
        self.assertTrue(DEFAULT_X_ACCOUNT_WHITELIST)
        self.assertIn("whitelist", out["meta"]["x_status"])


class TestDecisionAgentS20(unittest.TestCase):
    def test_fallback_merge_aligned(self) -> None:
        market = {
            "direction": "LONG",
            "confidence": 0.8,
            "rr": 2.4,
            "entry": 100,
            "stop": 98,
            "tp1": 103,
            "tp2": 105,
            "reasons": ["Trend UP"],
        }
        news = {
            "sentiment": "bullish",
            "confidence": 0.75,
            "importance": "medium",
            "reasons": ["ETF inflows"],
        }
        out = run_decision_agent_s20(market, news, force_fallback=True)
        self.assertEqual(out["decision"], "LONG")
        self.assertGreaterEqual(out["probability"], 50)
        self.assertGreaterEqual(out["confidence"], 3.0)
        self.assertTrue(out["summary"])
        self.assertTrue(out["risks"])
        self.assertFalse(out["meta"]["claude"])


class TestDecisionEngineIntegrationS20(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "me.db")
        configure_unit_test_db_isolation(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_full_cycle_persist_and_telegram(self) -> None:
        data = SymbolMarketDataG01(
            symbol="BTC",
            price=100_000.0,
            funding=-0.0004,
            open_interest=2_000_000.0,
            volume_24h=80_000.0,
            bars=_bars(start=99_000.0, up=True, n=24),
            source="bybit",
        )
        headlines = [
            {"title": "Bitcoin ETF inflows surge", "summary": "record high", "source": "coindesk"},
            {"title": "BTC adoption upgrade sparks rally", "summary": "", "source": "theblock"},
        ]
        with market_events_connection() as conn:
            apply_migrations(conn)
            result = run_decision_engine_s20(
                conn,
                "BTC",
                persist=True,
                force_fallback=True,
                headlines=headlines,
                market_data=data,
            )
            conn.commit()
            self.assertIsNotNone(result["run_id"])
            n_runs = conn.execute("SELECT COUNT(*) AS n FROM market_decision_runs_s20").fetchone()["n"]
            n_out = conn.execute(
                "SELECT COUNT(*) AS n FROM market_decision_agent_outputs_s20"
            ).fetchone()["n"]
            self.assertEqual(n_runs, 1)
            self.assertEqual(n_out, 3)
            agents = {
                r["agent_name"]
                for r in conn.execute(
                    "SELECT agent_name FROM market_decision_agent_outputs_s20 WHERE run_id = ?",
                    (result["run_id"],),
                ).fetchall()
            }
            self.assertEqual(agents, {"market", "news", "decision"})

        card = result["telegram"]
        self.assertIn("BTC", card)
        self.assertIn("Probability:", card)
        self.assertIn("Confidence:", card)
        self.assertIn("Entry", card)
        self.assertIn("Stop", card)
        self.assertIn("TP1", card)
        self.assertIn("TP2", card)
        self.assertIn("Market Agent", card)
        self.assertIn("News Agent", card)
        self.assertIn("Decision Agent", card)
        self.assertIn("Risk", card)

        formatted = format_decision_telegram_s20(
            symbol="BTC",
            market=result["market"],
            news=result["news"],
            decision=result["decision"],
        )
        self.assertEqual(formatted, card)

    def test_telegram_command_decision(self) -> None:
        from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
            handle_market_events_command,
        )

        data = SymbolMarketDataG01(
            symbol="BTC",
            price=50_000.0,
            funding=0.0,
            open_interest=1.0,
            volume_24h=10.0,
            bars=_bars(start=49_000.0, up=True),
            source="okx",
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()

        with patch(
            "bot.research.market_events.signal_intelligence.decision_engine_s20.market_agent.fetch_symbol_market_data_g01",
            return_value=data,
        ), patch(
            "bot.research.market_events.signal_intelligence.decision_engine_s20.news_agent.fetch_rss_headlines_s20",
            return_value=[{"title": "Bitcoin bull rally", "summary": "", "source": "decrypt"}],
        ), patch(
            "bot.research.market_events.signal_intelligence.decision_engine_s20.news_agent.fetch_cryptopanic_s20",
            return_value=[],
        ), patch(
            "bot.research.market_events.signal_intelligence.claude_client_g2.is_claude_configured",
            return_value=False,
        ):
            result = handle_market_events_command("/decision BTC")
        self.assertTrue(result.ok)
        self.assertIn("BTC", result.reply_text)
        self.assertIn("Market Agent", result.reply_text)


if __name__ == "__main__":
    unittest.main()
