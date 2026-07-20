"""S46.1 — normalized live context enrichment tests."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from bot.research.ai_analyst.context_builder import (
    build_market_context,
    compute_context_completeness,
)
from bot.research.ai_analyst.market_data_fetch import (
    _parse_farside_daily_flows,
    metric_from_values,
)


class TestNormalizeMetricS461(unittest.TestCase):
    def test_metric_shape(self) -> None:
        m = metric_from_values(98.42, 98.11, source="yahoo:DX-Y.NYB", unit="index")
        assert m is not None
        self.assertEqual(m["value"], 98.42)
        self.assertAlmostEqual(m["change_24h"], 0.31, places=5)
        self.assertEqual(m["trend"], "Bullish")
        self.assertNotIn("title", m)


class TestFarsideParseS461(unittest.TestCase):
    def test_daily_from_cumulative(self) -> None:
        html = "totalData = [100.0, 110.0, 105.0, 125.0]"
        daily = _parse_farside_daily_flows(html)
        self.assertEqual(daily, [100.0, 10.0, -5.0, 20.0])


class TestCompletenessS461(unittest.TestCase):
    def test_score(self) -> None:
        ctx = {
            "btc": {"price": 1, "change_24h_pct": 1, "dominance": 50},
            "sp500": {"value": 1},
            "nasdaq": {"value": 1},
            "vix": {"value": 1},
            "macro": {
                "dxy": {"value": 1},
                "us10y": {"value": 1},
                "us02y": {"value": 1},
                "gold": {"value": 1},
                "oil": {"value": 1},
            },
            "etf": {
                "btc_etf": {"netflow_5d": 1},
                "eth_etf": {"netflow_5d": 1},
            },
            "funding": {"current": 0.01},
            "open_interest": {"current": 1},
            "fear_greed": {"current": 40},
            "intelligence": {"top_events": [{"title": "x"}]},
            "polymarket": {"top_markets": [{"probability": 0.5}]},
        }
        c = compute_context_completeness(ctx)
        self.assertEqual(c["context_completeness"], 100)
        self.assertEqual(c["missing_fields"], [])


class TestLiveEnrichOptionalS461(unittest.TestCase):
    @unittest.skipUnless(
        __import__("os").getenv("S46_LIVE_NET") == "1",
        "set S46_LIVE_NET=1 to hit Yahoo/Farside",
    )
    def test_live_network(self) -> None:
        from bot.research.ai_analyst.market_data_fetch import fetch_all_live_enrichment
        payload = fetch_all_live_enrichment()
        self.assertIn("spx", payload["quotes"])
        self.assertIn("value", payload["quotes"]["spx"])
        self.assertTrue(
            payload["etf"].get("btc_etf", {}).get("netflow_5d") is not None
            or payload["etf"].get("eth_etf", {}).get("netflow_5d") is not None
        )


if __name__ == "__main__":
    unittest.main()
