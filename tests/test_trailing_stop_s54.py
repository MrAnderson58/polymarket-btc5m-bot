"""S54 — Experimental trailing stop after TP1."""

from __future__ import annotations

import sqlite3
import time
import unittest
from unittest.mock import patch

from bot.research.market_events.event_schema import SCHEMA_VERSION, apply_migrations
from bot.research.market_events.signal_intelligence import signal_paper_performance_s42 as s42


def _mem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    return conn


def _insert_open(
    conn: sqlite3.Connection,
    *,
    signal_id: int = 1,
    direction: str = "LONG",
    entry: float = 100.0,
    stop: float = 95.0,
    tp1: float = 105.0,
    tp2: float = 110.0,
) -> None:
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO market_events_paper_trades_s42 (
          s40_signal_type, s40_signal_id, symbol, direction,
          entry, stop, tp1, tp2, created_at, status,
          mfe_pct, mae_pct, capital_usd, leverage, updated_at
        ) VALUES ('g3', ?, 'BTC', ?, ?, ?, ?, ?, ?, 'OPEN', 0, 0, 100, 20, ?)
        """,
        (signal_id, direction, entry, stop, tp1, tp2, now, now),
    )
    conn.commit()


class TestTrailingMathS54(unittest.TestCase):
    def test_ratchet_never_loosens_long(self) -> None:
        hi, lo, stop, moved = s42.ratchet_trailing_stop(
            is_long=True, price=110, distance=5, trailing_stop=100, highest=105, lowest=100,
        )
        self.assertEqual(hi, 110)
        self.assertEqual(stop, 105)
        self.assertTrue(moved)
        # Price dips — stop must not move down
        hi2, lo2, stop2, moved2 = s42.ratchet_trailing_stop(
            is_long=True, price=106, distance=5, trailing_stop=stop, highest=hi, lowest=lo,
        )
        self.assertEqual(stop2, 105)
        self.assertFalse(moved2)
        self.assertEqual(hi2, 110)

    def test_ratchet_never_loosens_short(self) -> None:
        hi, lo, stop, moved = s42.ratchet_trailing_stop(
            is_long=False, price=90, distance=5, trailing_stop=100, highest=100, lowest=95,
        )
        self.assertEqual(lo, 90)
        self.assertEqual(stop, 95)
        self.assertTrue(moved)
        hi2, lo2, stop2, moved2 = s42.ratchet_trailing_stop(
            is_long=False, price=94, distance=5, trailing_stop=stop, highest=hi, lowest=lo,
        )
        self.assertEqual(stop2, 95)
        self.assertFalse(moved2)


class TestSimulatePathS54(unittest.TestCase):
    def test_long_tp1_trailing_exit(self) -> None:
        # entry 100, tp1 105, distance 5 → initial stop 100
        # path: hit tp1, run to 112 (stop→107), then back to 107 → trail exit
        prices = [100, 102, 105, 108, 112, 107]
        out = s42.simulate_exit_on_path(
            prices,
            entry=100, stop=95, tp1=105, tp2=120,
            direction="LONG", trail_after_tp1=True, multiplier=1.0,
        )
        self.assertEqual(out["exit_reason"], s42.EXIT_TRAILING)
        self.assertGreater(out["pnl_usd"], 0)

    def test_short_tp1_trailing_exit(self) -> None:
        prices = [100, 98, 95, 92, 88, 93]
        out = s42.simulate_exit_on_path(
            prices,
            entry=100, stop=105, tp1=95, tp2=85,
            direction="SHORT", trail_after_tp1=True, multiplier=1.0,
        )
        self.assertEqual(out["exit_reason"], s42.EXIT_TRAILING)

    def test_price_reverses_immediately_after_tp1(self) -> None:
        # TP1 hit then immediately back to entry (= initial trail stop) → trail exit ~BE
        prices = [100, 105, 100]
        out = s42.simulate_exit_on_path(
            prices,
            entry=100, stop=95, tp1=105, tp2=120,
            direction="LONG", trail_after_tp1=True, multiplier=1.0,
        )
        self.assertEqual(out["exit_reason"], s42.EXIT_TRAILING)
        self.assertAlmostEqual(out["pnl_pct"], 0.0, places=4)

    def test_multiple_high_updates(self) -> None:
        prices = [100, 105, 106, 108, 111, 109, 106]
        out = s42.simulate_exit_on_path(
            prices,
            entry=100, stop=95, tp1=105, tp2=200,
            direction="LONG", trail_after_tp1=True, multiplier=1.0,
        )
        self.assertEqual(out["exit_reason"], s42.EXIT_TRAILING)
        self.assertGreater(out["max_run_after_tp1"], 0)

    def test_classic_identical_when_flag_false(self) -> None:
        prices = [100, 102, 105, 112]
        classic = s42.simulate_exit_on_path(
            prices, entry=100, stop=95, tp1=105, tp2=120,
            direction="LONG", trail_after_tp1=False,
        )
        self.assertEqual(classic["exit_reason"], s42.EXIT_TP1)
        # Same path with trail would continue — classic stops at TP1
        trail = s42.simulate_exit_on_path(
            prices, entry=100, stop=95, tp1=105, tp2=120,
            direction="LONG", trail_after_tp1=True,
        )
        self.assertNotEqual(trail["exit_reason"], s42.EXIT_TP1)
        cmp_ = s42.compare_classic_vs_trailing(
            prices, entry=100, stop=95, tp1=105, tp2=120, direction="LONG",
        )
        self.assertEqual(cmp_["classic"]["exit_reason"], s42.EXIT_TP1)


class TestTickClassicParityS54(unittest.TestCase):
    def test_trail_flag_false_closes_tp1(self) -> None:
        conn = _mem_conn()
        self.assertGreaterEqual(SCHEMA_VERSION, 64)
        _insert_open(conn)
        with patch.object(s42, "TRAIL_AFTER_TP1", False), patch.object(
            s42, "_current_price", return_value=105.0,
        ):
            s42.tick_open_paper_trades_s42(conn)
            conn.commit()
        row = conn.execute("SELECT * FROM market_events_paper_trades_s42").fetchone()
        self.assertEqual(row["status"], "CLOSED")
        self.assertEqual(row["exit_reason"], "TP1")
        self.assertEqual(int(row["trailing_active"] or 0), 0)

    def test_trail_flag_true_arms_and_exits(self) -> None:
        conn = _mem_conn()
        _insert_open(conn, tp2=200.0)
        prices = iter([105.0, 110.0, 105.0])  # arm at 105, run, then hit trail (~105)

        def _price(_conn, _symbol):
            return next(prices)

        with patch.object(s42, "TRAIL_AFTER_TP1", True), patch.object(
            s42, "_current_price", side_effect=_price,
        ), patch.object(s42, "LOG_TRAILING_EVENTS", False):
            s42.tick_open_paper_trades_s42(conn)
            conn.commit()
            row = conn.execute("SELECT * FROM market_events_paper_trades_s42").fetchone()
            self.assertEqual(row["status"], "OPEN")
            self.assertEqual(int(row["trailing_active"]), 1)
            self.assertIsNotNone(row["trailing_stop"])

            s42.tick_open_paper_trades_s42(conn)
            conn.commit()
            s42.tick_open_paper_trades_s42(conn)
            conn.commit()

        row = conn.execute("SELECT * FROM market_events_paper_trades_s42").fetchone()
        self.assertEqual(row["status"], "CLOSED")
        self.assertEqual(row["exit_reason"], s42.EXIT_TRAILING)
        self.assertEqual(row["trailing_exit_reason"], "trailing_stop")

    def test_short_tick_trailing(self) -> None:
        conn = _mem_conn()
        _insert_open(conn, direction="SHORT", entry=100, stop=105, tp1=95, tp2=80)
        prices = iter([95.0, 90.0, 95.0])

        def _price(_conn, _symbol):
            return next(prices)

        with patch.object(s42, "TRAIL_AFTER_TP1", True), patch.object(
            s42, "_current_price", side_effect=_price,
        ), patch.object(s42, "LOG_TRAILING_EVENTS", False):
            s42.tick_open_paper_trades_s42(conn)
            conn.commit()
            self.assertEqual(
                int(conn.execute("SELECT trailing_active FROM market_events_paper_trades_s42").fetchone()[0]),
                1,
            )
            s42.tick_open_paper_trades_s42(conn)
            conn.commit()
            s42.tick_open_paper_trades_s42(conn)
            conn.commit()
        row = conn.execute("SELECT exit_reason FROM market_events_paper_trades_s42").fetchone()
        self.assertEqual(row["exit_reason"], s42.EXIT_TRAILING)

    def test_migration_columns_present(self) -> None:
        conn = _mem_conn()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(market_events_paper_trades_s42)")}
        for col in (
            "trailing_active",
            "trailing_stop",
            "highest_price_after_tp1",
            "lowest_price_after_tp1",
            "trailing_exit_reason",
        ):
            self.assertIn(col, cols)

    def test_default_config_off(self) -> None:
        import os
        old = os.environ.pop("TRAIL_AFTER_TP1", None)
        try:
            s42.refresh_trailing_config_from_env()
            self.assertFalse(s42.TRAIL_AFTER_TP1)
        finally:
            if old is not None:
                os.environ["TRAIL_AFTER_TP1"] = old
            s42.refresh_trailing_config_from_env()


class TestAbReportS54(unittest.TestCase):
    def test_ab_format(self) -> None:
        text = s42.format_classic_vs_trailing_ab([
            {
                "id": "L1",
                "prices": [100, 105, 112, 107],
                "entry": 100,
                "stop": 95,
                "tp1": 105,
                "tp2": 120,
                "direction": "LONG",
            },
            {
                "id": "S1",
                "prices": [100, 95, 88, 93],
                "entry": 100,
                "stop": 105,
                "tp1": 95,
                "tp2": 80,
                "direction": "SHORT",
            },
        ])
        self.assertIn("Classic vs Trailing A/B", text)
        self.assertIn("Classic total PnL", text)
        self.assertIn("Trailing total PnL", text)


if __name__ == "__main__":
    unittest.main()
