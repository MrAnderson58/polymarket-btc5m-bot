"""Tests for ER_ENTRY_PRICE_OFFSET configuration."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from bot.early_reversion import log_er_entry_threshold_config


class ErEntryThresholdTestCase(unittest.TestCase):
    def test_effective_threshold_with_offset(self) -> None:
        with mock.patch.dict(os.environ, {"ER_ENTRY_PRICE_OFFSET": "-0.01"}, clear=False):
            import importlib
            import bot.config as config

            importlib.reload(config)
            self.assertAlmostEqual(config.effective_entry_threshold(0.40), 0.39)

    def test_effective_threshold_default_offset(self) -> None:
        with mock.patch.dict(os.environ, {"ER_ENTRY_PRICE_OFFSET": "0"}, clear=False):
            import importlib
            import bot.config as config

            importlib.reload(config)
            self.assertAlmostEqual(config.effective_entry_threshold(0.40), 0.40)

    def test_startup_log_format(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"ENABLED_STRATEGIES": "NO_C", "ER_ENTRY_PRICE_OFFSET": "-0.01"},
            clear=False,
        ):
            import importlib
            import bot.config as config
            import bot.early_reversion as early_reversion

            importlib.reload(config)
            importlib.reload(early_reversion)
            with self.assertLogs("bot.early_reversion", level="INFO") as logs:
                early_reversion.log_er_entry_threshold_config()
        output = "\n".join(logs.output)
        self.assertIn("Entry threshold: 0.40", output)
        self.assertIn("Offset: -0.01", output)
        self.assertIn("Effective threshold: 0.39", output)


if __name__ == "__main__":
    unittest.main()
