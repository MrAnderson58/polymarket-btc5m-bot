"""Tests for open-trade recovery after bot restart."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db, insert_early_reversion_v2_trade
from bot.early_reversion_v2 import process_early_reversion_v2
from bot.market_scanner import Btc5mMarket, TokenQuotes
from bot.recovery import close_expired_open_trades, count_open_trades, recover_open_trades


def _current_window_start(now_ts: int) -> int:
    return (now_ts // 300) * 300


def _make_market(*, window_start: int, slug: str | None = None) -> Btc5mMarket:
    return Btc5mMarket(
        slug=slug or f"btc-updown-5m-{window_start}",
        title="BTC 5m test",
        condition_id="cond-test",
        window_start_ts=window_start,
        end_ts=window_start + 300,
        yes_token_id="yes-token",
        no_token_id="no-token",
        yes_outcome="Up",
        no_outcome="Down",
        yes_quotes=TokenQuotes("yes-token", 0.44, 0.46),
        no_quotes=TokenQuotes("no-token", 0.54, 0.56),
    )


class RecoveryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        init_db(self.db_path)
        self._env_patch = mock.patch.dict(
            os.environ,
            {
                "TRADING_MODE": "paper",
                "LIVE_EXIT_ENABLED": "false",
            },
            clear=False,
        )
        self._env_patch.start()
        import importlib
        import bot.config as config
        import bot.execution as execution
        import bot.early_reversion_v2 as er_v2

        importlib.reload(config)
        importlib.reload(execution)
        importlib.reload(er_v2)

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _insert_open_v2_trade(
        self,
        *,
        entry_ts: int,
        end_ts: int,
        window_start: int,
        last_bid: float | None = None,
    ) -> int:
        slug = f"btc-updown-5m-{window_start}"
        with connect(self.db_path) as conn:
            trade_id = insert_early_reversion_v2_trade(
                conn,
                market_slug=slug,
                window_start_ts=window_start,
                end_ts=end_ts,
                side="YES",
                strategy_name="YES_B",
                entry_price=0.40,
                entry_ts=entry_ts,
            )
            if last_bid is not None:
                conn.execute(
                    """
                    UPDATE early_reversion_v2_trades
                    SET last_bid = ?, max_price_seen = ?
                    WHERE id = ?
                    """,
                    (last_bid, max(last_bid, 0.40), trade_id),
                )
            conn.commit()
            return trade_id

    def test_open_trade_persisted_and_counted(self) -> None:
        now = int(time.time())
        window_start = _current_window_start(now)
        self._insert_open_v2_trade(
            entry_ts=now - 10,
            end_ts=window_start + 300,
            window_start=window_start,
        )

        with connect(self.db_path) as conn:
            self.assertEqual(count_open_trades(conn), 1)
            row = conn.execute(
                "SELECT status FROM early_reversion_v2_trades WHERE id = 1"
            ).fetchone()
            self.assertEqual(row["status"], "open")

    def test_expired_trade_closed_on_startup_recovery(self) -> None:
        now = int(time.time())
        window_start = _current_window_start(now) - 600
        trade_id = self._insert_open_v2_trade(
            entry_ts=window_start + 10,
            end_ts=window_start + 300,
            window_start=window_start,
            last_bid=0.42,
        )

        with connect(self.db_path) as conn:
            recovered = recover_open_trades(conn, now_ts=now)
            conn.commit()
            self.assertEqual(recovered, 1)

            row = conn.execute(
                "SELECT status, exit_reason, exit_price FROM early_reversion_v2_trades WHERE id = ?",
                (trade_id,),
            ).fetchone()
            self.assertEqual(row["status"], "closed")
            self.assertIsNotNone(row["exit_reason"])
            self.assertEqual(row["exit_price"], 0.42)

    def test_active_trade_survives_recovery_and_closes_after_restart(self) -> None:
        now = int(time.time())
        window_start = _current_window_start(now)
        trade_id = self._insert_open_v2_trade(
            entry_ts=now - 95,
            end_ts=window_start + 300,
            window_start=window_start,
            last_bid=0.45,
        )

        with connect(self.db_path) as conn:
            recovered = recover_open_trades(conn, now_ts=now)
            conn.commit()
            self.assertEqual(recovered, 1)
            row = conn.execute(
                "SELECT status FROM early_reversion_v2_trades WHERE id = ?",
                (trade_id,),
            ).fetchone()
            self.assertEqual(row["status"], "open")

        market = _make_market(window_start=window_start)
        quotes = {
            "yes_bid": 0.45,
            "yes_ask": 0.46,
            "no_bid": 0.54,
            "no_ask": 0.56,
        }

        with connect(self.db_path) as conn:
            with mock.patch("bot.early_reversion_v2.time.time", return_value=float(now)):
                process_early_reversion_v2(conn, market, quotes)
            conn.commit()

            row = conn.execute(
                """
                SELECT status, exit_reason, holding_time_seconds
                FROM early_reversion_v2_trades WHERE id = ?
                """,
                (trade_id,),
            ).fetchone()
            self.assertEqual(row["status"], "closed")
            self.assertEqual(row["exit_reason"], "TIME_STOP")
            self.assertGreaterEqual(float(row["holding_time_seconds"]), 90.0)

    def test_simulated_stop_and_restart_closes_open_trade(self) -> None:
        """Open trade -> stop bot -> wait -> restart main loop cycle -> closed."""
        now = int(time.time())
        window_start = _current_window_start(now)
        trade_id = self._insert_open_v2_trade(
            entry_ts=now - 30,
            end_ts=window_start + 300,
            window_start=window_start,
            last_bid=0.38,
        )

        # Bot stopped; 45 seconds pass.
        restarted_at = now + 45

        with connect(self.db_path) as conn:
            recovered = recover_open_trades(conn, now_ts=restarted_at)
            conn.commit()
            self.assertEqual(recovered, 1)

        market = _make_market(window_start=window_start)
        quotes = {
            "yes_bid": 0.35,
            "yes_ask": 0.36,
            "no_bid": 0.64,
            "no_ask": 0.66,
        }

        with connect(self.db_path) as conn:
            expired_closed = close_expired_open_trades(conn, restarted_at)
            self.assertEqual(expired_closed, 0)

            with mock.patch("bot.early_reversion_v2.time.time", return_value=float(restarted_at)):
                process_early_reversion_v2(conn, market, quotes)
            conn.commit()

            row = conn.execute(
                "SELECT status, exit_reason FROM early_reversion_v2_trades WHERE id = ?",
                (trade_id,),
            ).fetchone()
            self.assertEqual(row["status"], "closed")
            self.assertEqual(row["exit_reason"], "STOP_LOSS")


if __name__ == "__main__":
    unittest.main()
