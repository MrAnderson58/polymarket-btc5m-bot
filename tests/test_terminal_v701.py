"""V7.0.1 — Portfolio Intelligence tests."""

from __future__ import annotations

import unittest

from bot.terminal import __version__
from bot.terminal.models.dto import PortfolioCard, PositionCard
from bot.terminal.portfolio import (
    PortfolioIntelligenceService,
    reset_portfolio_intelligence_service,
)
from bot.terminal.telegram.render import render_portfolio_intelligence


class TestPortfolioIntelligenceV701(unittest.TestCase):
    def setUp(self) -> None:
        reset_portfolio_intelligence_service()
        self.svc = PortfolioIntelligenceService()

    def tearDown(self) -> None:
        reset_portfolio_intelligence_service()

    def test_version(self) -> None:
        self.assertEqual(__version__, "7.1.4")

    def test_crypto_heavy_advice(self) -> None:
        summary = PortfolioCard(
            equity=100_000.0,
            cash=28_000.0,
            used_margin=72_000.0,
            open_risk=72_000.0,
            open_positions=2,
            trades=10,
        )
        positions = [
            PositionCard(symbol="BTC", side="LONG", size=45_000.0, status="open"),
            PositionCard(symbol="ETH", side="LONG", size=27_000.0, status="open"),
        ]
        intel = self.svc.analyze(summary=summary, positions=positions)

        self.assertAlmostEqual(intel.crypto_exposure_pct, 72.0, places=0)
        self.assertIsNotNone(intel.open_risk)
        self.assertIsNotNone(intel.correlation)
        self.assertIsNotNone(intel.expected_dd_pct)
        self.assertTrue(any(s.label == "Crypto" for s in intel.sector_exposure))

        assert intel.advice is not None
        blob = " ".join(intel.advice.bullets + intel.advice.actions)
        self.assertIn("72%", blob)
        self.assertIn("Crypto", blob)
        self.assertIn("BTC", blob)
        self.assertIn("Gold", blob)

        text = render_portfolio_intelligence(intel)
        for needle in (
            "Open Risk",
            "Correlation",
            "Sector Exposure",
            "Crypto Exposure",
            "Cash",
            "Expected DD",
            "Portfolio Advice",
            "Gold",
        ):
            self.assertIn(needle, text)

    def test_diversified_book(self) -> None:
        summary = PortfolioCard(equity=100_000.0, cash=40_000.0, open_risk=60_000.0)
        positions = [
            PositionCard(symbol="BTC", side="LONG", size=20_000.0),
            PositionCard(symbol="NVDA", side="LONG", size=20_000.0),
            PositionCard(symbol="GOLD", side="LONG", size=20_000.0),
        ]
        intel = self.svc.analyze(summary=summary, positions=positions)
        self.assertLess(intel.crypto_exposure_pct, 40.0)
        self.assertGreaterEqual(len(intel.sector_exposure), 2)


if __name__ == "__main__":
    unittest.main()
