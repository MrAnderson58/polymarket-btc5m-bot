"""G3.6 Telegram Vision tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.futures_agent.telegram_inbound import handle_update
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
    handle_market_events_command,
)
from bot.research.market_events.signal_intelligence.telegram_photo_g36 import is_image_message
from bot.research.market_events.signal_intelligence.telegram_vision_g36 import (
    analyze_telegram_chart_g36,
    format_vision_telegram_g36,
)
from bot.research.market_events.signal_intelligence.visual_platform_f4 import detect_platform


class TelegramVisionG36Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "g36.db"
        configure_unit_test_db_isolation(self.db_path)
        self._env = patch.dict(os.environ, {
            "ME_G36_VISION_MEMORY": "true",
            "TELEGRAM_AGENT_ALLOWED_CHAT_IDS": "12345",
            "TELEGRAM_BOT_TOKEN": "test-token",
        }, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()

    def test_schema_v40_vision_table(self) -> None:
        with market_events_connection() as conn:
            applied = apply_migrations(conn)
            self.assertIn("v41", applied)
            self.assertEqual(SCHEMA_VERSION, 42)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
            self.assertIn("market_telegram_vision_g36", tables)

    def test_is_image_message_photo(self) -> None:
        msg = {"photo": [{"file_id": "abc", "width": 100}], "document": None}
        self.assertTrue(is_image_message(msg))

    def test_is_image_message_document(self) -> None:
        msg = {"document": {"mime_type": "image/png", "file_name": "chart.png"}}
        self.assertTrue(is_image_message(msg))

    def test_platform_detection(self) -> None:
        self.assertEqual(detect_platform("TradingView chart SOL 15m"), "TradingView")
        self.assertEqual(detect_platform("bybit.com futures"), "Bybit")

    def test_analyze_chart_with_bos(self) -> None:
        caption = "SOL 15m BOS liquidity sweep demand zone long entry 180 sl 175"
        result = analyze_telegram_chart_g36(caption=caption, filename="tradingview.png")
        self.assertTrue(result.chart_detected)
        self.assertIn("BOS", result.detected)
        text = format_vision_telegram_g36(result)
        self.assertIn("Chart detected", text)
        self.assertIn("Platform", text)

    def test_analyze_command_btc(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.execute(
                """
                INSERT INTO market_snapshots_g3 (
                  snapshot_uuid, snapshot_ts, funding, open_interest, fear_greed,
                  recorder_status, created_at
                ) VALUES ('g36', 1, -0.01, 1e9, 45, 'ok', 1)
                """,
            )
            conn.commit()
        result = handle_market_events_command("/analyze BTC", chat_id=12345)
        self.assertTrue(result.ok)
        self.assertIn("BTC", result.reply_text)
        self.assertIn("No signal yet", result.reply_text)

    def test_vision_command_help(self) -> None:
        result = handle_market_events_command("/vision")
        self.assertIn("chart", result.reply_text.lower())

    @patch("bot.research.market_events.signal_intelligence.telegram_vision_g36.handle_telegram_photo_message")
    @patch("bot.research.futures_agent.telegram_inbound.send_telegram_reply", return_value=True)
    def test_photo_routes_to_vision_not_parser(
        self,
        _mock_reply: unittest.mock.MagicMock,
        mock_vision: unittest.mock.MagicMock,
    ) -> None:
        mock_vision.return_value = "Chart detected"
        update = {
            "update_id": 1,
            "message": {
                "message_id": 99,
                "chat": {"id": 12345},
                "photo": [{"file_id": "f1", "width": 800, "height": 600}],
                "caption": "TradingView SOL 15m BOS",
            },
        }
        result = handle_update(update)
        self.assertIsNotNone(result)
        mock_vision.assert_called_once()
        self.assertEqual(result.reply_text, "Chart detected")


if __name__ == "__main__":
    unittest.main()
