"""V7.0.0 — Decision Card tests + BTC SHORT acceptance."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from bot.terminal import __version__
from bot.terminal.decision import (
    DecisionService,
    LearningHint,
    build_from_scan,
    reset_decision_service,
)
from bot.terminal.instruments.models import AssetClass
from bot.terminal.instruments.profiles import get_market_profile
from bot.terminal.scanner import ScannerRegistry, ScannerService, StaticScannerProvider
from bot.terminal.scanner.models import RankComponents, ScannerResult
from bot.terminal.telegram.render import render_decision
from bot.terminal.telegram.terminal_router import dispatch_terminal_command, is_terminal_command


def _btc_short_scan() -> ScannerResult:
    return ScannerResult(
        symbol="BTC",
        asset_class=AssetClass.CRYPTO_FUTURE,
        direction="SHORT",
        confidence=9.0,
        score=91.0,
        reasons=("accepted",),
        provider="static",
        components=RankComponents(
            confidence=90,
            ai=88,
            learning=70,
            trend=92,
            volume=80,
            news=55,
            pattern=90,
        ),
        extra={"price": 95000.0},
    )


class TestDecisionCardV700(unittest.TestCase):
    def setUp(self) -> None:
        reset_decision_service()

    def tearDown(self) -> None:
        reset_decision_service()

    def test_version(self) -> None:
        self.assertEqual(__version__, "7.1.4")

    def test_btc_short_acceptance(self) -> None:
        scan = _btc_short_scan()
        profile = get_market_profile(AssetClass.CRYPTO_FUTURE)
        card = build_from_scan(
            scan,
            profile=profile,
            learning=LearningHint(confirms=True, winrate_pct=58.0),
            price=95000.0,
        )

        self.assertEqual(card.symbol, "BTC")
        self.assertEqual(card.direction, "SHORT")
        self.assertIsNotNone(card.entry)
        self.assertIsNotNone(card.stop)
        self.assertIsNotNone(card.tp1)
        self.assertIsNotNone(card.tp2)
        assert card.entry and card.stop and card.tp1
        self.assertGreater(card.stop, card.entry)
        self.assertLess(card.tp1, card.entry)

        self.assertTrue(any("нисходящий" in r for r in card.reasons))
        self.assertIn("pattern #18", " ".join(card.reasons))
        self.assertTrue(card.ai_summary)
        self.assertIn("Вероятность продолжения", card.ai_summary)
        self.assertIn("Основные риски", card.ai_summary)
        self.assertIn("Рекомендуемый риск", card.ai_summary)

        text = render_decision(card)
        self.assertIn("Direction", text)
        self.assertIn("SHORT", text)
        self.assertIn("Entry", text)
        self.assertIn("Stop", text)
        self.assertIn("TP", text)
        self.assertIn("Reason", text)
        self.assertIn("Summary", text)

    def test_service_from_static_scanner(self) -> None:
        reg = ScannerRegistry()
        reg.register(StaticScannerProvider())
        svc = DecisionService(scanner=ScannerService(reg))
        card = svc.for_symbol("BTC", price=100000.0)
        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card.symbol, "BTC")

    def test_telegram_decision(self) -> None:
        self.assertTrue(is_terminal_command("/decision BTC"))
        fake = build_from_scan(_btc_short_scan(), price=95000.0)

        class _Svc:
            def for_symbol(self, symbol, *, price=None):
                return fake

            def from_scan(self, symbol=None, *, price=None):
                return fake

        with patch("bot.terminal.decision.get_decision_service", return_value=_Svc()):
            reply = dispatch_terminal_command("/decision BTC")
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertIn("SHORT", reply.text)
        self.assertIn("Decision", reply.text)


if __name__ == "__main__":
    unittest.main()
