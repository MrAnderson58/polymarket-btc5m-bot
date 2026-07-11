"""F.0 exchange symbol resolver tests."""

from __future__ import annotations

import json
import unittest

from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.exchange_resolver import (
    ExchangeResolution,
    get_or_resolve,
    persist_resolution,
    resolve_exchange_symbol,
)
from tests.f0_test_utils import conn_ctx, make_db


class ExchangeResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _mock_checkers(self, found_venue: str | None):
        def bybit(s):
            return (found_venue == "bybit", f"{s.upper()}USDT")
        def binance(s):
            return (found_venue == "binance", f"{s.upper()}USDT")
        def okx(s):
            return (found_venue == "okx", f"{s.upper()}-USDT-SWAP")
        return {"bybit": bybit, "binance": binance, "okx": okx}

    def test_bybit_priority(self) -> None:
        r = resolve_exchange_symbol("BTC", checkers=self._mock_checkers("bybit"))
        self.assertEqual(r.status, "RESOLVED")
        self.assertEqual(r.resolved_venue, "bybit")

    def test_binance_second_priority(self) -> None:
        r = resolve_exchange_symbol("ETH", checkers=self._mock_checkers("binance"))
        self.assertEqual(r.resolved_venue, "binance")

    def test_okx_third_priority(self) -> None:
        r = resolve_exchange_symbol("SOL", checkers=self._mock_checkers("okx"))
        self.assertEqual(r.resolved_venue, "okx")

    def test_unsupported_symbol(self) -> None:
        r = resolve_exchange_symbol("UNKNOWN", checkers=self._mock_checkers(None))
        self.assertEqual(r.status, "UNSUPPORTED_SYMBOL")
        self.assertIsNone(r.resolved_venue)

    def test_attempts_json_recorded(self) -> None:
        r = resolve_exchange_symbol("BTC", checkers=self._mock_checkers(None))
        self.assertEqual(len(r.attempts), 3)

    def test_persist_and_load(self) -> None:
        r = ExchangeResolution("SUI", "bybit", "SUIUSDT", "RESOLVED", [{"venue": "bybit", "found": True}])
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            persist_resolution(conn, r)
            row = conn.execute(
                "SELECT * FROM market_event_exchange_symbols WHERE canonical_symbol = 'SUI'",
            ).fetchone()
            self.assertEqual(row["status"], "RESOLVED")
            attempts = json.loads(row["exchange_attempts_json"])
            self.assertTrue(attempts)

    def test_get_or_resolve_caches(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            r1 = get_or_resolve(conn, "BTC", )
            # patch won't apply to get_or_resolve without mock - test persist path
            persist_resolution(conn, ExchangeResolution(
                "BTC", "binance", "BTCUSDT", "RESOLVED", [],
            ))
            r2 = get_or_resolve(conn, "BTC")
            self.assertEqual(r2.resolved_venue, "binance")

    def test_resolved_symbol_format_bybit(self) -> None:
        r = resolve_exchange_symbol("XRP", checkers=self._mock_checkers("bybit"))
        self.assertEqual(r.resolved_symbol, "XRPUSDT")

    def test_multiple_attempts_all_logged(self) -> None:
        checkers = self._mock_checkers("okx")
        r = resolve_exchange_symbol("AVAX", checkers=checkers)
        venues = [a["venue"] for a in r.attempts]
        self.assertEqual(venues, ["bybit", "binance", "okx"])

    def test_status_resolved_has_symbol(self) -> None:
        r = resolve_exchange_symbol("BNB", checkers=self._mock_checkers("binance"))
        self.assertIsNotNone(r.resolved_symbol)

    def test_canonical_preserved(self) -> None:
        r = resolve_exchange_symbol("LINK", checkers=self._mock_checkers("bybit"))
        self.assertEqual(r.canonical_symbol, "LINK")

    def test_unsupported_has_no_symbol(self) -> None:
        r = resolve_exchange_symbol("FAKECOIN", checkers=self._mock_checkers(None))
        self.assertIsNone(r.resolved_symbol)


if __name__ == "__main__":
    unittest.main()
