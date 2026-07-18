"""V6.1.1 Instrument Registry + V6.1.2 Market Profile Engine tests."""

from __future__ import annotations

import time
import unittest

from bot.terminal.instruments import (
    AssetClass,
    CryptoFutureProfile,
    InstrumentRegistry,
    MarketProfile,
    StaticProvider,
    StockProfile,
    get_instrument_registry,
    get_market_profile,
    get_market_profile_for_instrument,
    list_market_profiles,
)


class TestInstrumentRegistryV611(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = InstrumentRegistry([StaticProvider()])

    def test_get_btcusdt(self) -> None:
        inst = self.reg.get("BTCUSDT")
        self.assertIsNotNone(inst)
        assert inst is not None
        self.assertEqual(inst.symbol, "BTCUSDT")
        self.assertEqual(inst.asset_class, AssetClass.CRYPTO_FUTURE)

    def test_get_nvda(self) -> None:
        inst = self.reg.get("NVDA")
        self.assertIsNotNone(inst)
        assert inst is not None
        self.assertEqual(inst.name, "NVIDIA Corp")
        self.assertEqual(inst.asset_class, AssetClass.STOCK)

    def test_list_stock(self) -> None:
        stocks = self.reg.list("stock")
        symbols = {s.symbol for s in stocks}
        self.assertEqual(symbols, {"AAPL", "META", "NVDA"})

    def test_list_all_and_exists(self) -> None:
        all_inst = self.reg.list()
        self.assertGreaterEqual(len(all_inst), 12)
        self.assertTrue(self.reg.exists("ETHUSDT"))
        self.assertFalse(self.reg.exists("NOPE"))

    def test_default_registry(self) -> None:
        reg = get_instrument_registry()
        self.assertTrue(reg.exists("EURUSD"))


class TestMarketProfilesV612(unittest.TestCase):
    def test_crypto_future_profile(self) -> None:
        p = CryptoFutureProfile().profile()
        self.assertTrue(p.is_24_7)
        self.assertTrue(p.has_funding)
        self.assertTrue(p.uses_liquidations)
        self.assertFalse(p.uses_earnings)

    def test_stock_profile(self) -> None:
        p = StockProfile().profile()
        self.assertFalse(p.is_24_7)
        self.assertTrue(p.has_premarket)
        self.assertTrue(p.has_postmarket)
        self.assertTrue(p.uses_earnings)
        self.assertTrue(p.uses_options)

    def test_get_by_asset_class(self) -> None:
        fx = get_market_profile("forex")
        self.assertIsInstance(fx, MarketProfile)
        self.assertEqual(fx.asset_class, AssetClass.FOREX)
        self.assertTrue(fx.macro_sensitive)

    def test_profile_for_instrument(self) -> None:
        reg = InstrumentRegistry([StaticProvider()])
        btc = reg.get("BTCUSDT")
        assert btc is not None
        p = get_market_profile_for_instrument(btc)
        self.assertTrue(p.has_funding)

    def test_list_profiles_covers_all_classes(self) -> None:
        profiles = list_market_profiles()
        classes = {p.asset_class for p in profiles}
        self.assertEqual(classes, set(AssetClass))


class TestPerfAndArchitectureSmoke(unittest.TestCase):
    def test_registry_lookup_under_100ms(self) -> None:
        reg = InstrumentRegistry([StaticProvider()])
        t0 = time.perf_counter()
        for _ in range(1000):
            reg.get("BTCUSDT")
            reg.list("stock")
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        # 1000 iterations local — far under 100ms screen budget
        self.assertLess(elapsed_ms, 100.0)

    def test_no_sql_in_instruments_package(self) -> None:
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1] / "bot" / "terminal" / "instruments"
        for path in root.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("SELECT ", text.upper())
            self.assertNotIn("sqlite", text.lower())


if __name__ == "__main__":
    unittest.main()
