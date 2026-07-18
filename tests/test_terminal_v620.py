"""V6.2.0 — Universal Scanner tests."""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from bot.terminal.instruments.models import AssetClass
from bot.terminal.scanner import (
    CryptoScannerProvider,
    ScannerRegistry,
    ScannerResult,
    ScannerService,
    StaticScannerProvider,
    compute_score,
)
from bot.terminal.scanner.models import RankComponents
from bot.terminal.scanner.ranking import normalize_confidence
from bot.terminal.scanner.registry import default_scanner_registry


class TestRankingV620(unittest.TestCase):
    def test_score_0_100(self) -> None:
        score = compute_score(
            RankComponents(
                confidence=80, ai=90, trend=70, volume=60, news=50, pattern=40, learning=30,
            )
        )
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_normalize_confidence(self) -> None:
        self.assertEqual(normalize_confidence(8.0), 80.0)
        self.assertEqual(normalize_confidence(80.0), 80.0)


class TestScannerV620(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = ScannerRegistry()
        self.reg.register(StaticScannerProvider())
        self.svc = ScannerService(self.reg)

    def test_scan_crypto(self) -> None:
        rows = self.svc.scan("crypto")
        symbols = [r.symbol for r in rows]
        self.assertIn("BTC", symbols)
        self.assertIn("ETH", symbols)
        self.assertIn("SOL", symbols)

    def test_top5(self) -> None:
        top = self.svc.top(5)
        self.assertEqual(len(top), 5)
        scores = [r.score for r in top]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_by_symbol(self) -> None:
        hit = self.svc.by_symbol("BTC")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.symbol, "BTC")

    def test_registry_provider(self) -> None:
        p = self.reg.provider("crypto")
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p.name, "static")

    def test_crypto_provider_supports(self) -> None:
        p = CryptoScannerProvider()
        self.assertTrue(p.supports("crypto"))
        self.assertTrue(p.supports(AssetClass.CRYPTO_FUTURE))
        self.assertFalse(p.supports("stock"))

    def test_default_registry_has_g31(self) -> None:
        reg = default_scanner_registry(include_static=False)
        names = [getattr(p, "name", "") for p in reg.providers()]
        self.assertIn("g31_crypto", names)

    def test_crypto_provider_maps_g31_rows(self) -> None:
        class _Row(dict):
            pass

        row = _Row(
            symbol="BTC",
            direction="LONG",
            confidence=8.0,
            market_score=70.0,
            trend_coverage_pct=60.0,
            liquidity_score=5.0,
            candidate_state="accepted",
            rejection_reason=None,
        )

        @contextmanager
        def _ro():
            yield object()

        with patch(
            "bot.research.market_events.signal_intelligence.candidate_g31.fetch_top_candidates_g31",
            return_value=[row],
        ), patch(
            "bot.terminal.services._db.market_events_ro",
            _ro,
        ):
            results = list(CryptoScannerProvider().scan(limit=5))

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].symbol, "BTC")
        self.assertEqual(results[0].provider, "g31_crypto")
        self.assertGreater(results[0].score, 0)

    def test_scanner_result_dto(self) -> None:
        r = ScannerResult(
            symbol="BTC",
            asset_class=AssetClass.CRYPTO_FUTURE,
            direction="LONG",
            confidence=8.0,
            score=80.0,
            reasons=("ok",),
            provider="static",
        )
        self.assertEqual(r.symbol, "BTC")


if __name__ == "__main__":
    unittest.main()
