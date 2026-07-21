"""Tests for live market data refresh and data timestamps."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.research.ai_analyst.context_builder import build_market_context
from bot.research.ai_analyst.data_timestamps import (
    build_data_timestamps,
    format_data_timestamp_block,
)
from bot.research.ai_analyst.report_generator import run_ai_analyst
from bot.research.ai_analyst.telegram_terminal import run_interactive_report


class TestBtcLivePreference(unittest.TestCase):
    def test_btc_prefers_yahoo_over_snapshot(self) -> None:
        live = {
            "quotes": {
                "btc": {
                    "value": 98500.0,
                    "change_24h_pct": 1.2,
                    "trend": "Bullish",
                    "source": "yahoo:BTC-USD",
                    "asof_ts": 1_700_000_100,
                },
                "spx": {
                    "value": 5300.0,
                    "change_24h_pct": 0.5,
                    "trend": "Bullish",
                    "source": "yahoo:^GSPC",
                    "asof_ts": 1_700_000_100,
                },
            },
            "etf": {},
            "fetched_at": 1_700_000_100,
        }
        ctx = build_market_context(now=1_700_000_000, live_enrich=False, live_payload=live)
        self.assertEqual(ctx["btc"]["price"], 98500.0)
        self.assertEqual(ctx["btc"]["source"], "yahoo:BTC-USD")
        self.assertEqual(ctx["sp500"]["source"], "yahoo:^GSPC")
        self.assertIn("data_timestamps", ctx)
        self.assertIn("yahoo", ctx["data_timestamps"]["sources"]["btc"])


class TestDataTimestamps(unittest.TestCase):
    def test_format_block(self) -> None:
        ctx = {
            "data_timestamps": {
                "market": "2026-01-01 12:00:00 UTC",
                "macro": "2026-01-01 11:55:00 UTC",
                "intelligence": "2026-01-01 10:00:00 UTC",
                "context": "2026-01-01 12:01:00 UTC",
            },
        }
        block = format_data_timestamp_block(ctx)
        self.assertIn("## Data Timestamp", block)
        self.assertIn("Market:", block)
        self.assertIn("Intelligence:", block)

    def test_build_from_context(self) -> None:
        now = int(time.time())
        ctx = {
            "btc": {"price": 1, "source": "yahoo:BTC-USD", "asof_ts": now - 60},
            "sp500": {"value": 2, "source": "yahoo:^GSPC", "asof_ts": now - 120},
            "macro": {"dxy": {"value": 3, "asof_ts": now - 300}},
            "live_enrichment": {"fetched_at": now, "elapsed_ms": 10},
        }
        ts = build_data_timestamps(ctx, now_ts=now, latest_snapshot_ts=now - 3600, events_raw=[])
        self.assertIn("market", ts)
        self.assertEqual(ts["context_age_sec"], 0)


class TestRefreshPipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.reports = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @patch("bot.research.ai_analyst.market_data_fetch.refresh_market_data")
    @patch("bot.research.ai_analyst.report_generator.build_market_context")
    @patch("bot.research.ai_analyst.report_generator.get_llm_client")
    def test_run_ai_analyst_refresh_live(
        self,
        mock_client: MagicMock,
        mock_ctx: MagicMock,
        mock_refresh: MagicMock,
    ) -> None:
        mock_refresh.return_value = {"quotes": {"btc": {"value": 1}}, "etf": {}, "fetched_at": 1}
        mock_ctx.return_value = {"btc": {"price": 1}, "intelligence": {"top_events": []}}
        mock_client.return_value.complete.return_value = MagicMock(
            text='{"market_bias":"neutral"}',
            provider="template",
            model="template",
            latency_ms=1,
            error=None,
        )
        run_ai_analyst(
            flags=["json"],
            reports_dir=self.reports,
            force_template=True,
            refresh_live_data=True,
        )
        mock_refresh.assert_called_once()
        _args, kwargs = mock_ctx.call_args
        self.assertIsNotNone(kwargs.get("live_payload"))

    @patch("bot.research.ai_analyst.telegram_terminal._regenerate_full")
    def test_report_always_regenerates(self, mock_regen: MagicMock) -> None:
        mock_regen.return_value = {"ok": True}
        edits: list[str] = []

        def edit(body: str, _markup: dict | None) -> bool:
            edits.append(body)
            return True

        with patch(
            "bot.research.ai_analyst.telegram_terminal.build_report_completion_message",
            return_value="done",
        ):
            run_interactive_report(cmd="/report", edit_message=edit, reports_dir=self.reports)
        mock_regen.assert_called_once()
        _args, kwargs = mock_regen.call_args
        self.assertTrue(kwargs.get("refresh_live_data"))


if __name__ == "__main__":
    unittest.main()
