"""Tests for CLOB healthcheck."""

from __future__ import annotations

import unittest
from unittest import mock

from bot import clob_healthcheck


class ClobHealthcheckTestCase(unittest.TestCase):
    @mock.patch("bot.clob_healthcheck.wallet_configured", return_value=False)
    def test_all_fail_without_wallet(self, _configured: mock.MagicMock) -> None:
        results = clob_healthcheck.run_healthchecks()
        self.assertEqual(len(results), 5)
        self.assertFalse(any(item.passed for item in results))
        report = clob_healthcheck.format_report(results)
        self.assertIn("Wallet: FAIL", report)
        self.assertIn("OVERALL: FAIL", report)

    @mock.patch("bot.clob_healthcheck.find_active_btc_5m_market")
    @mock.patch("bot.clob_healthcheck.get_authenticated_clob_client")
    @mock.patch("bot.clob_healthcheck.wallet_configured", return_value=True)
    def test_all_pass_with_mocked_clob(
        self,
        _configured: mock.MagicMock,
        mock_client_factory: mock.MagicMock,
        mock_find_market: mock.MagicMock,
    ) -> None:
        from bot.market_scanner import Btc5mMarket, TokenQuotes

        client = mock.MagicMock()
        client.get_address.return_value = "0xabc123"
        client.creds = mock.MagicMock(
            api_key="key123456",
            api_secret="secret",
            api_passphrase="pass",
        )
        client.get_balance_allowance.return_value = {
            "balance": "5000000",
            "allowance": "5000000",
        }
        client.create_order.return_value = {"signed": True}
        mock_client_factory.return_value = client

        mock_find_market.return_value = Btc5mMarket(
            slug="btc-updown-5m-test",
            title="test",
            condition_id="cond",
            window_start_ts=1,
            end_ts=301,
            yes_token_id="yes",
            no_token_id="no",
            yes_outcome="Up",
            no_outcome="Down",
            yes_quotes=TokenQuotes("yes", 0.4, 0.41),
            no_quotes=TokenQuotes("no", 0.59, 0.6),
        )

        results = clob_healthcheck.run_healthchecks()
        self.assertTrue(all(item.passed for item in results))
        self.assertIn("OVERALL: PASS", clob_healthcheck.format_report(results))


if __name__ == "__main__":
    unittest.main()
