"""S52 — Report polish tests (no new product modules)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from bot.research.ai_analyst.paper_trading.signals import TradingSignal
from bot.research.ai_analyst.signal_consistency.reasons import (
    aggregate_and_rank_reasons,
    is_banned_reason,
)
from bot.research.ai_analyst.strategy_validation.dashboard import (
    build_dashboard,
    format_dashboard,
)
from bot.research.ai_analyst.strategy_validation.daily_report import format_daily_report
from bot.research.ai_analyst.strategy_validation.telegram_views import (
    format_open_telegram,
    format_signals_telegram,
    format_stats_telegram,
)
from bot.research.ai_analyst.trader_report import (
    format_top_news_compact,
    format_trader_report_html,
    merge_similar_news,
    polish_headline,
    shorten_news_title,
)


class TestHeadlinePolishS52(unittest.TestCase):
    def test_polymarket_war_war(self) -> None:
        self.assertEqual(polish_headline("Polymarket War: war"), "Polymarket: War")

    def test_title_length_cap(self) -> None:
        long = "A" * 200
        self.assertLessEqual(len(shorten_news_title(long)), 64)

    def test_merge_similar_etf_news(self) -> None:
        merged = merge_similar_news([
            {"title": "BlackRock ETF +420M", "polarity": "positive"},
            {"title": "BlackRock ETF inflows +420M today", "polarity": "positive"},
            {"title": "CPI tomorrow", "polarity": "neutral"},
        ])
        titles = [m["title"] for m in merged]
        self.assertEqual(len(merged), 2)
        self.assertTrue(any("CPI" in t for t in titles))
        html = format_top_news_compact([
            {"title": "ETF +420M", "polarity": "positive"},
            {"title": "ETF inflows +420 million", "polarity": "positive"},
        ])
        self.assertEqual(html.count("🟢"), 1)


class TestReasonAggregationS52(unittest.TestCase):
    def test_aggregate_duplicate_etf_and_rank(self) -> None:
        out = aggregate_and_rank_reasons([
            "ETF +50M (5d)",
            "ETF +420M (1d)",
            "Funding neutral",
            "Current trend",
            "Market looks bullish",
            "OI rising +2.1%",
        ])
        self.assertTrue(any("ETF" in r for r in out))
        self.assertEqual(sum(1 for r in out if "ETF" in r), 1)
        self.assertIn("Funding neutral", out)
        self.assertTrue(any("OI" in r for r in out))
        self.assertNotIn("Current trend", out)
        self.assertFalse(any(is_banned_reason(r) for r in out))
        # ETF should rank before weaker items
        self.assertTrue(out[0].startswith("ETF") or "ETF" in out[0])


class TestConfidenceSplitS52(unittest.TestCase):
    def test_market_vs_trade_confidence(self) -> None:
        sig = TradingSignal(
            symbol="BTC",
            direction="LONG",
            entry_low=1,
            entry_high=2,
            stop_loss=0.5,
            tp1=3,
            tp2=4,
            tp3=5,
            risk_pct=1,
            confidence=40,
            reasons=["ETF +203M", "Funding neutral"],
        )
        html = format_trader_report_html(
            {"analysis_quality": {"confidence": 75}, "intelligence": {"top_events": []}},
            signal=sig,
            open_count=1,
        )
        self.assertIn("MARKET CONDITIONS", html)
        self.assertIn("TRADE CONFIDENCE", html)
        self.assertIn("below market conditions", html)


class TestEmptyHistoryUXS52(unittest.TestCase):
    def test_dashboard_empty_message(self) -> None:
        text = format_dashboard(build_dashboard([], open_count=0))
        self.assertIn("Статистика появится после первой закрытой сделки", text)
        self.assertNotIn("Win Rate: 0%", text)

    def test_daily_empty_message(self) -> None:
        text = format_daily_report([], signals_today=0, open_count=0)
        self.assertIn("Статистика появится после первой закрытой сделки", text)

    def test_stats_telegram_empty(self) -> None:
        with patch(
            "bot.research.ai_analyst.strategy_validation.telegram_views.get_repository",
        ) as mock_repo:
            mock_repo.return_value.paper_stats_dashboard.return_value = {
                "open_trades": 0,
                "closed_trades": 0,
                "winrate_pct": 0.0,
                "today_pnl_usd": 0.0,
                "current_equity": 0.0,
                "weekly_pnl_usd": 0.0,
            }
            text = format_stats_telegram()
        self.assertIn("Статистика появится", text)

    def test_signals_empty_message(self) -> None:
        with patch(
            "bot.research.ai_analyst.strategy_validation.telegram_views.get_repository",
        ) as mock_repo:
            mock_repo.return_value.list_signals.return_value = []
            text = format_signals_telegram()
        self.assertIn("пуста", text.lower())


class TestPendingSignalWhenNoOpenS52(unittest.TestCase):
    def test_report_shows_pending_when_no_open(self) -> None:
        sig = TradingSignal(
            symbol="BTC",
            direction="LONG",
            entry_low=64000,
            entry_high=64500,
            stop_loss=63000,
            tp1=65000,
            tp2=66000,
            tp3=68000,
            risk_pct=1,
            confidence=60,
            reasons=["ETF +203M"],
            status="PENDING",
        )
        html = format_trader_report_html(
            {"analysis_quality": {"confidence": 70}, "intelligence": {"top_events": []}},
            signal=sig,
            open_count=0,
        )
        self.assertIn("CURRENT AI SIGNAL", html)
        self.assertIn("pending", html.lower())

    def test_open_shows_pending_signal(self) -> None:
        pending = {
            "market": "BTC",
            "direction": "LONG",
            "status": "PENDING",
            "confidence": 61,
            "entry_low": 1,
            "entry_high": 2,
            "stop_loss": 0.5,
            "tp1": 3,
            "tp2": 4,
            "tp3": 5,
        }
        with patch(
            "bot.research.ai_analyst.strategy_validation.telegram_views.get_repository",
        ) as mock_repo:
            mock_repo.return_value.list_paper_open_trades.return_value = []
            mock_repo.return_value.list_open_trades.return_value = []
            mock_repo.return_value.latest_signal.return_value = pending
            text = format_open_telegram()
        self.assertIn("Нет открытых", text)
        self.assertIn("AI SIGNAL", text.upper())
        self.assertIn("BTC", text)
        self.assertIn("LONG", text)


if __name__ == "__main__":
    unittest.main()
