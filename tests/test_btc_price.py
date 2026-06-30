"""Tests for BTC price fallback chain."""

from __future__ import annotations

import unittest
from unittest import mock

import requests

from bot.btc_price import (
    BtcPriceError,
    clear_price_caches,
    get_current_btc_price,
    get_strike_price,
)


def _mock_response(*, status_code: int = 200, json_data: dict | None = None) -> mock.MagicMock:
    response = mock.MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data or {}
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(
            f"{status_code} error",
            response=response,
        )
    else:
        response.raise_for_status.return_value = None
    return response


class BtcPriceFallbackTestCase(unittest.TestCase):
    def setUp(self) -> None:
        clear_price_caches()

    @mock.patch("bot.btc_price.requests.get")
    def test_binance_ok(self, mock_get: mock.MagicMock) -> None:
        mock_get.return_value = _mock_response(json_data={"price": "65000.5"})

        with self.assertLogs("bot.btc_price", level="INFO") as logs:
            price = get_current_btc_price()

        self.assertEqual(price, 65000.5)
        mock_get.assert_called_once()
        self.assertTrue(any("BTC fetch | source=binance" in msg for msg in logs.output))

    @mock.patch("bot.btc_price.requests.get")
    def test_binance_451_falls_back_to_coinbase(self, mock_get: mock.MagicMock) -> None:
        mock_get.side_effect = [
            _mock_response(status_code=451),
            _mock_response(json_data={"data": {"amount": "64000.25"}}),
        ]

        with self.assertLogs("bot.btc_price", level="INFO") as logs:
            price = get_current_btc_price()

        self.assertEqual(price, 64000.25)
        self.assertEqual(mock_get.call_count, 2)
        self.assertTrue(
            any("Binance unavailable, falling back to Coinbase" in msg for msg in logs.output)
        )
        self.assertTrue(any("BTC fetch | source=coinbase" in msg for msg in logs.output))

    @mock.patch("bot.btc_price.requests.get")
    def test_binance_and_coinbase_fail_kraken_ok(self, mock_get: mock.MagicMock) -> None:
        mock_get.side_effect = [
            requests.Timeout("binance timeout"),
            requests.ConnectionError("coinbase down"),
            _mock_response(
                json_data={
                    "error": [],
                    "result": {
                        "XXBTZUSD": {
                            "c": ["63000.75", "0.01"],
                        }
                    },
                }
            ),
        ]

        with self.assertLogs("bot.btc_price", level="INFO") as logs:
            price = get_current_btc_price()

        self.assertEqual(price, 63000.75)
        self.assertEqual(mock_get.call_count, 3)
        self.assertTrue(any("BTC fetch | source=kraken" in msg for msg in logs.output))

    @mock.patch("bot.btc_price.requests.get")
    def test_all_sources_fail_raises_btc_price_error(self, mock_get: mock.MagicMock) -> None:
        mock_get.side_effect = [
            requests.HTTPError("451", response=_mock_response(status_code=451)),
            requests.Timeout("coinbase timeout"),
            requests.ConnectionError("kraken down"),
        ]

        with self.assertRaises(BtcPriceError) as ctx:
            get_current_btc_price()

        self.assertIn("Failed to fetch current BTC price from all sources", str(ctx.exception))
        self.assertEqual(mock_get.call_count, 3)


class BtcPriceCacheTestCase(unittest.TestCase):
    def setUp(self) -> None:
        clear_price_caches()

    @mock.patch("bot.btc_price.requests.get")
    def test_btc_price_cache_hit_within_ttl(self, mock_get: mock.MagicMock) -> None:
        mock_get.return_value = _mock_response(json_data={"price": "65000.5"})

        with self.assertLogs("bot.btc_price", level="INFO") as logs:
            first = get_current_btc_price()
            second = get_current_btc_price()

        self.assertEqual(first, 65000.5)
        self.assertEqual(second, 65000.5)
        mock_get.assert_called_once()
        self.assertTrue(any("BTC cache MISS" in msg for msg in logs.output))
        self.assertTrue(any("BTC cache HIT" in msg for msg in logs.output))

    @mock.patch("bot.btc_price.time.monotonic")
    @mock.patch("bot.btc_price.requests.get")
    def test_btc_price_cache_hit_across_poll_interval(
        self,
        mock_get: mock.MagicMock,
        mock_monotonic: mock.MagicMock,
    ) -> None:
        mock_get.return_value = _mock_response(json_data={"price": "65000.5"})
        mock_monotonic.side_effect = [0.0, 2.0]

        with self.assertLogs("bot.btc_price", level="INFO") as logs:
            first = get_current_btc_price()
            second = get_current_btc_price()

        self.assertEqual(first, second)
        mock_get.assert_called_once()
        self.assertTrue(any("BTC cache MISS" in msg for msg in logs.output))
        self.assertTrue(any("BTC cache HIT" in msg for msg in logs.output))

    @mock.patch("bot.btc_price.time.monotonic")
    @mock.patch("bot.btc_price.requests.get")
    def test_btc_price_cache_miss_after_ttl_expires(
        self,
        mock_get: mock.MagicMock,
        mock_monotonic: mock.MagicMock,
    ) -> None:
        mock_get.return_value = _mock_response(json_data={"price": "65000.5"})
        mock_monotonic.side_effect = [0.0, 3.0]

        with self.assertLogs("bot.btc_price", level="INFO") as logs:
            get_current_btc_price()
            get_current_btc_price()

        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(sum("BTC cache MISS" in msg for msg in logs.output), 2)


class StrikePriceFallbackTestCase(unittest.TestCase):
    WINDOW_START = 1_782_307_200

    def setUp(self) -> None:
        clear_price_caches()

    @mock.patch("bot.btc_price.requests.get")
    def test_strike_binance_ok(self, mock_get: mock.MagicMock) -> None:
        mock_get.return_value = _mock_response(
            json_data=[[self.WINDOW_START * 1000, "62100.0", "62200.0", "62000.0", "62150.0", "1.0"]]
        )

        with self.assertLogs("bot.btc_price", level="INFO") as logs:
            strike = get_strike_price(self.WINDOW_START)

        self.assertEqual(strike, 62100.0)
        mock_get.assert_called_once()
        self.assertTrue(any("Strike source=binance" in msg for msg in logs.output))

    @mock.patch("bot.btc_price.requests.get")
    def test_strike_binance_fail_coinbase_ok(self, mock_get: mock.MagicMock) -> None:
        mock_get.side_effect = [
            _mock_response(status_code=451),
            _mock_response(
                json_data=[
                    [self.WINDOW_START, 62080.0, 62120.0, 62090.5, 62110.0, 2.0],
                ]
            ),
        ]

        with self.assertLogs("bot.btc_price", level="WARNING") as logs:
            strike = get_strike_price(self.WINDOW_START + 30)

        self.assertEqual(strike, 62090.5)
        self.assertEqual(mock_get.call_count, 2)
        self.assertTrue(
            any("Binance unavailable for strike, falling back to Coinbase" in msg for msg in logs.output)
        )

    @mock.patch("bot.btc_price.requests.get")
    def test_strike_all_sources_fail(self, mock_get: mock.MagicMock) -> None:
        mock_get.side_effect = [
            requests.HTTPError("451", response=_mock_response(status_code=451)),
            _mock_response(json_data=[]),
            _mock_response(json_data=[]),
            _mock_response(json_data=[]),
        ]

        with self.assertRaises(BtcPriceError) as ctx:
            get_strike_price(self.WINDOW_START)

        self.assertIn("Failed to fetch strike price", str(ctx.exception))

    @mock.patch("bot.btc_price.requests.get")
    def test_strike_cache_hit_same_window(self, mock_get: mock.MagicMock) -> None:
        mock_get.return_value = _mock_response(
            json_data=[[self.WINDOW_START * 1000, "62100.0", "62200.0", "62000.0", "62150.0", "1.0"]]
        )

        with self.assertLogs("bot.btc_price", level="INFO") as logs:
            first = get_strike_price(self.WINDOW_START)
            second = get_strike_price(self.WINDOW_START + 30)

        self.assertEqual(first, 62100.0)
        self.assertEqual(second, 62100.0)
        mock_get.assert_called_once()
        self.assertTrue(any("Strike cache MISS" in msg for msg in logs.output))
        self.assertTrue(any("Strike cache HIT" in msg for msg in logs.output))


if __name__ == "__main__":
    unittest.main()
