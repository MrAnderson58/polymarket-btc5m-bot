"""V7.0.2 Morning Brief + V7.0.3 AI Research tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.terminal import __version__
from bot.terminal.brief import (
    MorningBriefService,
    build_morning_brief,
    format_morning_brief,
    reset_morning_brief_service,
)
from bot.terminal.decision import LearningHint, build_from_scan
from bot.terminal.instruments.models import AssetClass
from bot.terminal.research import (
    build_research_context,
    get_research_service,
    reset_research_service,
    template_review,
)
from bot.terminal.scanner import ScannerRegistry, ScannerService, StaticScannerProvider
from bot.terminal.scanner.models import RankComponents, ScannerResult
from bot.terminal.telegram.terminal_router import dispatch_terminal_command, is_terminal_command
from bot.terminal.watchlist import WatchlistService, WatchlistStore, reset_watchlist_service


class TestMorningBriefV702(unittest.TestCase):
    def setUp(self) -> None:
        reset_morning_brief_service()
        reset_watchlist_service()
        self._tmp = tempfile.TemporaryDirectory()
        self.watch = WatchlistService(WatchlistStore(Path(self._tmp.name) / "w.json"))
        self.watch.add("u1", "BTC")
        self.watch.add("u1", "ETH")

    def tearDown(self) -> None:
        reset_morning_brief_service()
        reset_watchlist_service()
        self._tmp.cleanup()

    def test_version(self) -> None:
        self.assertEqual(__version__, "7.1.4")

    def test_brief_sections(self) -> None:
        reg = ScannerRegistry()
        reg.register(StaticScannerProvider())
        with patch(
            "bot.terminal.brief.builder.get_scanner_service",
            return_value=ScannerService(reg),
        ), patch(
            "bot.terminal.brief.builder.get_watchlist_service",
            return_value=self.watch,
        ), patch(
            "bot.terminal.decision.service.get_scanner_service",
            return_value=ScannerService(reg),
        ):
            brief = build_morning_brief(user_id="u1")
        text = format_morning_brief(brief)
        for needle in (
            "Good Morning",
            "Markets",
            "Stocks",
            "Crypto",
            "Macro",
            "Top Opportunities",
            "Portfolio Advice",
            "Watchlist Updates",
            "AI Summary",
        ):
            self.assertIn(needle, text)

    def test_telegram_brief(self) -> None:
        self.assertTrue(is_terminal_command("/brief"))
        with patch(
            "bot.terminal.brief.get_morning_brief_service",
            return_value=MorningBriefService(),
        ), patch(
            "bot.terminal.brief.builder.get_scanner_service",
            return_value=ScannerService(ScannerRegistry()),
        ):
            # empty registry → still builds
            reply = dispatch_terminal_command("/brief", user_id="9")
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertIn("Good Morning", reply.text)


class TestResearchV703(unittest.TestCase):
    def setUp(self) -> None:
        reset_research_service()

    def tearDown(self) -> None:
        reset_research_service()

    def test_context_packs_decision(self) -> None:
        scan = ScannerResult(
            symbol="BTC",
            asset_class=AssetClass.CRYPTO_FUTURE,
            direction="SHORT",
            confidence=9.0,
            score=91.0,
            reasons=("accepted",),
            provider="static",
            components=RankComponents(trend=90, volume=80, ai=85, learning=70, pattern=90),
        )
        card = build_from_scan(scan, learning=LearningHint(confirms=True, winrate_pct=55), price=95000)
        with patch(
            "bot.terminal.research.context.get_decision_service",
        ) as gd, patch(
            "bot.terminal.research.context.get_scanner_service",
        ) as gs:
            gd.return_value.for_symbol.return_value = card
            gs.return_value.by_symbol.return_value = scan
            ctx = build_research_context("BTC", decision=card, scan=scan)
        block = ctx.as_prompt_block()
        self.assertIn("DECISION CARD", block)
        self.assertIn("SCANNER", block)
        self.assertIn("PORTFOLIO", block)
        self.assertIn("NEWS", block)
        self.assertIn("LEARNING", block)
        self.assertIn("BTC", block)

    def test_template_review_not_blank_slate(self) -> None:
        scan = ScannerResult(
            symbol="BTC",
            asset_class=AssetClass.CRYPTO_FUTURE,
            direction="SHORT",
            confidence=9.0,
            score=91.0,
            reasons=("accepted",),
            provider="static",
            components=RankComponents(trend=90, volume=80),
        )
        card = build_from_scan(scan, price=95000)
        ctx = build_research_context("BTC", decision=card, scan=scan)
        review = template_review(ctx)
        self.assertEqual(review.symbol, "BTC")
        self.assertIn(review.verdict, {"confirm", "caution", "reject", "unknown"})
        self.assertEqual(review.provider, "template")
        self.assertTrue(review.summary)

    def test_research_service_template_path(self) -> None:
        review = get_research_service().review_symbol("BTC", use_claude=False)
        self.assertEqual(review.provider, "template")

    def test_telegram_research(self) -> None:
        self.assertTrue(is_terminal_command("/research BTC"))
        with patch(
            "bot.terminal.research.get_research_service",
        ) as gr:
            gr.return_value.review_symbol.return_value = template_review(
                build_research_context(
                    "BTC",
                    decision=build_from_scan(
                        ScannerResult(
                            symbol="BTC",
                            asset_class=AssetClass.CRYPTO_FUTURE,
                            direction="SHORT",
                            confidence=9.0,
                            score=91.0,
                            reasons=("ok",),
                            provider="static",
                            components=RankComponents(trend=90),
                        ),
                        price=95000,
                    ),
                )
            )
            reply = dispatch_terminal_command("/research BTC template")
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertIn("Research Review", reply.text)


if __name__ == "__main__":
    unittest.main()
