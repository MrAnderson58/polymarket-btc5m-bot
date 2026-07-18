"""V6.2.1 — Watchlist Engine tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.terminal import __version__
from bot.terminal.models.dto import SignalCard
from bot.terminal.telegram.keyboards import (
    favorite_button,
    parse_favorite_symbol,
    signals_keyboard,
)
from bot.terminal.telegram.terminal_router import (
    dispatch_terminal_command,
    handle_terminal_callback,
    is_terminal_command,
)
from bot.terminal.watchlist import (
    DEFAULT_FAVORITES,
    WatchlistService,
    WatchlistStore,
    normalize_symbol,
    reset_watchlist_service,
)


class TestWatchlistCoreV621(unittest.TestCase):
    def setUp(self) -> None:
        reset_watchlist_service()
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "watchlists.json"
        self.svc = WatchlistService(WatchlistStore(self.path))

    def tearDown(self) -> None:
        reset_watchlist_service()
        self._tmp.cleanup()

    def test_version(self) -> None:
        self.assertEqual(__version__, "7.1.4")

    def test_normalize(self) -> None:
        self.assertEqual(normalize_symbol("btc"), "BTC")
        self.assertEqual(normalize_symbol("Gold"), "GOLD")
        self.assertEqual(normalize_symbol("XAUUSD"), "GOLD")

    def test_default_seed(self) -> None:
        wl = self.svc.get("u1")
        self.assertEqual(wl.symbols(), DEFAULT_FAVORITES)
        self.assertTrue(self.path.is_file())

    def test_add_remove(self) -> None:
        self.svc.add("u1", "SOL")
        self.assertTrue(self.svc.is_favorite("u1", "SOL"))
        self.svc.remove("u1", "ETH")
        self.assertFalse(self.svc.is_favorite("u1", "ETH"))
        self.assertIn("SOL", self.svc.list_symbols("u1"))

    def test_toggle(self) -> None:
        _, on = self.svc.toggle("u2", "AAA")
        self.assertTrue(on)
        _, off = self.svc.toggle("u2", "AAA")
        self.assertFalse(off)

    def test_persistence_reload(self) -> None:
        self.svc.add("u3", "NVDA")
        reloaded = WatchlistService(WatchlistStore(self.path))
        self.assertTrue(reloaded.is_favorite("u3", "NVDA"))


class TestWatchlistTelegramV621(unittest.TestCase):
    def setUp(self) -> None:
        reset_watchlist_service()
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "watchlists.json"
        self.svc = WatchlistService(WatchlistStore(self.path))

    def tearDown(self) -> None:
        reset_watchlist_service()
        self._tmp.cleanup()

    def test_is_terminal_watch(self) -> None:
        self.assertTrue(is_terminal_command("/watch"))
        self.assertTrue(is_terminal_command("/watch add BTC"))

    def test_watch_list_command(self) -> None:
        with patch(
            "bot.terminal.watchlist.get_watchlist_service",
            return_value=self.svc,
        ):
            reply = dispatch_terminal_command("/watch", user_id="42")
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertIn("Watchlist", reply.text)
        self.assertIn("BTC", reply.text)
        self.assertIn("GOLD", reply.text)

    def test_watch_add_remove(self) -> None:
        with patch(
            "bot.terminal.watchlist.get_watchlist_service",
            return_value=self.svc,
        ):
            add = dispatch_terminal_command("/watch add SOL", user_id="7")
            rem = dispatch_terminal_command("/watch remove ETH", user_id="7")
        assert add is not None and rem is not None
        self.assertIn("Added SOL", add.text)
        self.assertIn("Removed ETH", rem.text)
        self.assertTrue(self.svc.is_favorite("7", "SOL"))
        self.assertFalse(self.svc.is_favorite("7", "ETH"))

    def test_favorite_button(self) -> None:
        btn = favorite_button("BTC", favorited=False)
        self.assertIn("⭐", btn["text"])
        self.assertEqual(btn["callback_data"], "term:fav:BTC")
        self.assertEqual(parse_favorite_symbol("term:fav:ETH"), "ETH")

    def test_signals_keyboard_has_favorite(self) -> None:
        kb = signals_keyboard(["BTC", "ETH"], favorited={"BTC"})
        flat = [b["text"] for row in kb["inline_keyboard"] for b in row]
        self.assertTrue(any("BTC" in t for t in flat))
        self.assertTrue(any(t.startswith("⭐") or t.startswith("★") for t in flat))

    def test_fav_callback_toggles(self) -> None:
        fake_signals = [
            SignalCard(symbol="BTC", direction="LONG", status="ok", confidence=8.0),
        ]
        with patch(
            "bot.terminal.watchlist.get_watchlist_service",
            return_value=self.svc,
        ), patch(
            "bot.terminal.services.signals_service.get_signal_service",
        ) as gs:
            gs.return_value.get_top.return_value = fake_signals
            # BTC is default favorite → toggle removes
            reply = handle_terminal_callback("term:fav:BTC", user_id="9")
        assert reply is not None
        self.assertTrue(reply.edit)
        self.assertIn("Removed BTC", reply.text)
        self.assertFalse(self.svc.is_favorite("9", "BTC"))


if __name__ == "__main__":
    unittest.main()
