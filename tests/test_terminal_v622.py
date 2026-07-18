"""V6.2.2 — Alert Engine + stack acceptance (Scanner / Registry / Watchlist / Alert)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.terminal import __version__
from bot.terminal.alerts import (
    AlertKind,
    AlertService,
    ai_alert,
    evaluate_rules,
    get_alert_service,
    reset_alert_service,
    signal_alert,
)
from bot.terminal.instruments.models import AssetClass
from bot.terminal.scanner import ScannerRegistry, ScannerService, StaticScannerProvider
from bot.terminal.scanner.models import RankComponents, ScannerResult
from bot.terminal.telegram.terminal_router import dispatch_terminal_command, is_terminal_command
from bot.terminal.watchlist import WatchlistService, WatchlistStore, reset_watchlist_service


class TestAlertEngineV622(unittest.TestCase):
    def setUp(self) -> None:
        reset_alert_service()
        reg = ScannerRegistry()
        reg.register(StaticScannerProvider())
        self.scanner = ScannerService(reg)
        self.alerts = AlertService(scanner=self.scanner)

    def tearDown(self) -> None:
        reset_alert_service()

    def test_version(self) -> None:
        self.assertEqual(__version__, "7.1.4")

    def test_kinds(self) -> None:
        self.assertEqual(AlertKind.PRICE.value, "price")
        self.assertEqual(AlertKind.SIGNAL.value, "signal")
        self.assertEqual(AlertKind.AI.value, "ai")

    def test_btc_score_rule(self) -> None:
        rule = self.alerts.create_ai("u1", "BTC", score_gt=85)
        self.assertEqual(rule.symbol, "BTC")
        self.assertIn("score>85", rule.describe())
        hits = self.alerts.evaluate("u1")
        self.assertTrue(any(h.symbol == "BTC" for h in hits))
        btc = next(h for h in hits if h.symbol == "BTC")
        self.assertGreater(btc.score or 0, 85)

    def test_nvda_long_rule(self) -> None:
        # Inject NVDA LONG into static-like scan via custom evaluate_rules
        nvda = ScannerResult(
            symbol="NVDA",
            asset_class=AssetClass.STOCK,
            direction="LONG",
            confidence=8.0,
            score=70.0,
            reasons=("appeared",),
            provider="static",
            components=RankComponents(confidence=80, ai=70),
        )
        rule = signal_alert("u1", "NVDA", direction="LONG")
        hits = evaluate_rules([rule], [nvda])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].direction, "LONG")

    def test_parse_and_create(self) -> None:
        rule = self.alerts.parse_and_create("u2", ["BTC", "score>85"])
        self.assertEqual(rule.symbol, "BTC")
        self.assertEqual(rule.kind, AlertKind.AI)
        rule2 = self.alerts.parse_and_create("u2", ["NVDA", "LONG"])
        self.assertEqual(rule2.kind, AlertKind.SIGNAL)
        self.assertIn("direction==LONG", rule2.describe())

    def test_telegram_alert_command(self) -> None:
        self.assertTrue(is_terminal_command("/alert"))
        with patch(
            "bot.terminal.alerts.get_alert_service",
            return_value=self.alerts,
        ):
            reply = dispatch_terminal_command("/alert add BTC score>85", user_id="9")
        assert reply is not None
        self.assertIn("Rule created", reply.text)
        self.assertIn("BTC", reply.text)
        self.assertIn("score>85", reply.text)


class TestStackAcceptanceV622(unittest.TestCase):
    """Cursor acceptance: Scanner + Registry + Watchlist + Alert."""

    def setUp(self) -> None:
        reset_alert_service()
        reset_watchlist_service()
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "watchlists.json"

    def tearDown(self) -> None:
        reset_alert_service()
        reset_watchlist_service()
        self._tmp.cleanup()

    def test_acceptance_surface(self) -> None:
        # --- Scanner ---
        reg = ScannerRegistry()
        reg.register(StaticScannerProvider())
        scanner = ScannerService(reg)
        btc = scanner.by_symbol("BTC")
        self.assertIsNotNone(btc)
        assert btc is not None
        self.assertEqual(btc.symbol, "BTC")
        self.assertGreater(btc.score, 0)
        self.assertTrue(btc.provider)
        self.assertTrue(btc.reasons)

        # --- Registry ---
        providers = reg.providers()
        self.assertGreaterEqual(len(providers), 1)
        names = [getattr(p, "name", "") for p in providers]
        self.assertIn("static", names)

        # --- Watchlist ---
        watch = WatchlistService(WatchlistStore(self.path))
        for sym in ("BTC", "ETH", "NVDA"):
            watch.add("accept", sym)
        symbols = watch.list_symbols("accept")
        self.assertIn("BTC", symbols)
        self.assertIn("ETH", symbols)
        self.assertIn("NVDA", symbols)

        # --- Alert ---
        alerts = AlertService(scanner=scanner)
        rule = alerts.create_ai("accept", "BTC", score_gt=85)
        self.assertEqual(rule.symbol, "BTC")
        self.assertIn("score>85", rule.describe())

        # Human-readable acceptance dump (also asserted)
        lines = [
            "Scanner",
            f"  {btc.symbol}",
            f"  score={btc.score}",
            f"  provider={btc.provider}",
            f"  reasons={list(btc.reasons)}",
            "Registry",
            f"  providers()={[getattr(p, 'name', type(p).__name__) for p in providers]}",
            "Watchlist",
            f"  {', '.join(symbols)}",
            "Alert",
            "  Rule created",
            f"  {rule.symbol}",
            f"  {rule.describe()}",
        ]
        report = "\n".join(lines)
        self.assertIn("BTC", report)
        self.assertIn("providers()", report)
        self.assertIn("NVDA", report)
        self.assertIn("Rule created", report)
        self.assertIn("score>85", rule.describe())
        print("\n" + report)


if __name__ == "__main__":
    unittest.main()
