"""Tests for Trading DNA Discovery V1."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from bot.research.market_events.signal_intelligence.trading_dna_v1.coins import coin_dna
from bot.research.market_events.signal_intelligence.trading_dna_v1.engine import (
    run_trading_dna_v1,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.features import (
    _ema,
    _rsi,
    enrich_trade,
    load_candle_book,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.metrics import (
    pf_sort_key,
    trade_metrics,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.report import (
    format_morning_summary,
    format_report,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.rules import (
    find_forbidden,
    find_minimal_rules,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.segments import (
    common_factors,
    profile_all,
    split_segments,
)
from bot.research.market_events.signal_intelligence.trading_dna_v1.setups import (
    mine_setups,
)


def _rows(n: int = 200) -> list[dict]:
    rows = []
    for i in range(n):
        pnl = 2.0 if i % 5 == 0 else (-0.8 if i % 3 == 0 else 0.4)
        rows.append({
            "trade_id": i + 1,
            "symbol": ["BTC", "ETH", "SOL", "XRP"][i % 4],
            "direction": "LONG" if i % 2 == 0 else "SHORT",
            "pnl": pnl,
            "rsi": 25 + (i % 50),
            "atr_pct": 0.2 + (i % 10) * 0.08,
            "atr": 100.0,
            "funding": 0.0001 if i % 3 else -0.0002,
            "oi_delta": 1.0 if i % 2 else -1.0,
            "funding_sign": "+" if i % 3 else "-",
            "oi_sign": "+" if i % 2 else "-",
            "ema20": 100 + i * 0.01,
            "ema50": 99 + i * 0.01,
            "ema200": 95.0,
            "vwap": 100.0,
            "adx": 15 + (i % 20),
            "macd": 0.5 if i % 2 else -0.4,
            "hour": i % 24,
            "weekday": i % 7,
            "regime": "RANGE" if i % 2 else "TREND",
            "gate": "PASS",
            "pattern": "g31" if i % 4 else "shock",
            "confidence": 0.4 + (i % 10) * 0.05,
            "news_score": 0.1 * (i % 5),
            "candle_hit": True,
        })
    return rows


class TestMetrics(unittest.TestCase):
    def test_trade_metrics(self) -> None:
        m = trade_metrics([1.0, -0.5, 1.0, 0.5])
        self.assertEqual(m["n"], 4)
        self.assertGreater(m["pf"], 1)
        self.assertIsNotNone(m["ev"])

    def test_empty(self) -> None:
        self.assertEqual(trade_metrics([])["n"], 0)

    def test_pf_inf(self) -> None:
        m = trade_metrics([1.0, 2.0])
        self.assertTrue(m.get("pf_inf") or m["pf"] is None or m["pf"] > 0)

    def test_sort_key(self) -> None:
        a = {"pf": 2.0, "ev": 1.0, "n": 10, "confidence": 0.5}
        b = {"pf": 1.5, "ev": 2.0, "n": 100, "confidence": 0.9}
        self.assertGreater(pf_sort_key(a), pf_sort_key(b))


class TestIndicators(unittest.TestCase):
    def test_ema_len(self) -> None:
        x = np.arange(50, dtype=float)
        e = _ema(x, 10)
        self.assertEqual(len(e), 50)
        self.assertTrue(np.isfinite(e[-1]))

    def test_rsi_bounds(self) -> None:
        x = np.linspace(100, 120, 40)
        r = _rsi(x, 14)
        self.assertTrue(np.nanmax(r[14:]) <= 100.0)
        self.assertTrue(np.nanmin(r[14:]) >= 0.0)


class TestSegments(unittest.TestCase):
    def test_split_keys(self) -> None:
        segs = split_segments(_rows(100))
        for k in ("TOP_5", "TOP_10", "TOP_20", "MIDDLE", "BOTTOM_20", "BOTTOM_10", "BOTTOM_5"):
            self.assertIn(k, segs)

    def test_top_better_than_bottom(self) -> None:
        rows = _rows(200)
        segs = split_segments(rows)
        top = np.mean([r["pnl"] for r in segs["TOP_10"]])
        bot = np.mean([r["pnl"] for r in segs["BOTTOM_10"]])
        self.assertGreater(top, bot)

    def test_profiles(self) -> None:
        segs = split_segments(_rows(120))
        profiles = profile_all(segs)
        self.assertEqual(len(profiles), 7)
        self.assertIn("numeric", profiles[0])

    def test_common_factors(self) -> None:
        segs = split_segments(_rows(150))
        cf = common_factors(profile_all(segs))
        self.assertIn("numeric_deltas", cf)


class TestSetups(unittest.TestCase):
    def test_mine_returns_lists(self) -> None:
        out = mine_setups(_rows(200), top_n=20, min_n=10, max_combo=2)
        self.assertIn("profitable", out)
        self.assertIn("losing", out)
        self.assertGreater(out["n_atomics"], 10)

    def test_profitable_sorted(self) -> None:
        out = mine_setups(_rows(250), top_n=30, min_n=10, max_combo=2)
        p = out["profitable"]
        if len(p) >= 2:
            self.assertGreaterEqual(pf_sort_key(p[0]), pf_sort_key(p[1]))


class TestRules(unittest.TestCase):
    def test_minimal(self) -> None:
        rows = _rows(250)
        mined = mine_setups(rows, top_n=20, min_n=15, max_combo=2)
        rules = find_minimal_rules(rows, mined["atomic_rules"], mined["masks"], min_n=20)
        self.assertIsInstance(rules, list)

    def test_forbidden(self) -> None:
        losing = [
            {"setup": "bad", "pf": 0.4, "ev": -1.0, "n": 80, "wr": 20, "confidence": 0.5, "n_losses": 40},
            {"setup": "okish", "pf": 0.9, "ev": -0.1, "n": 80, "wr": 40, "confidence": 0.4, "n_losses": 40},
        ]
        f = find_forbidden(losing, max_pf=0.7, min_n=40)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["action"], "BLOCK")


class TestCoins(unittest.TestCase):
    def test_coin_dna(self) -> None:
        out = coin_dna(_rows(200), top_n=4)
        self.assertLessEqual(len(out), 4)
        self.assertIn("best", out[0])
        self.assertIn("worst", out[0])


class TestFeatures(unittest.TestCase):
    def test_enrich_without_candles(self) -> None:
        t = enrich_trade({
            "trade_id": 1, "symbol": "BTC", "direction": "LONG", "pnl": 1.0,
            "opened_at": 1_700_000_000, "funding": 0.001, "oi_delta": -2,
        }, {})
        self.assertEqual(t["funding_sign"], "+")
        self.assertEqual(t["oi_sign"], "-")
        self.assertIn(t["hour"], range(24))
        self.assertIn(t["weekday"], range(7))

    def test_load_candle_book_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite3.connect(str(Path(td) / "e.db"))
            self.assertEqual(load_candle_book(conn), {})
            conn.close()

    def test_load_candle_book_with_data(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite3.connect(str(Path(td) / "c.db"))
            conn.execute("""
                CREATE TABLE market_events_historical_candles (
                  id INTEGER PRIMARY KEY, venue TEXT, symbol TEXT, timeframe TEXT,
                  open_ts INTEGER, open REAL, high REAL, low REAL, close REAL,
                  volume REAL, source TEXT, fetched_at INTEGER
                )
            """)
            base = 1_700_000_000
            for i in range(80):
                px = 100 + i * 0.1
                conn.execute(
                    "INSERT INTO market_events_historical_candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (i + 1, "x", "BTC", "5m", base + i * 300, px, px + 1, px - 1, px + 0.2, 10, "t", base),
                )
            conn.commit()
            book = load_candle_book(conn)
            self.assertIn("BTC", book)
            self.assertEqual(len(book["BTC"]["rsi"]), 80)
            conn.close()


class TestReport(unittest.TestCase):
    def _result(self) -> dict:
        rows = _rows(180)
        segs = split_segments(rows)
        profiles = profile_all(segs)
        mined = mine_setups(rows, top_n=15, min_n=10, max_combo=2)
        return {
            "ok": True,
            "n_trades": len(rows),
            "elapsed_sec": 1.2,
            "feature_stats": {"candle_hit_rate": 0.9},
            "profiles": profiles,
            "common_factors": common_factors(profiles),
            "profitable": mined["profitable"],
            "losing": mined["losing"],
            "minimal_rules": find_minimal_rules(rows, mined["atomic_rules"], mined["masks"], min_n=15)[:5],
            "forbidden": find_forbidden(mined["losing"], max_pf=0.85, min_n=10),
            "coins": coin_dna(rows, top_n=4),
            "research_only": True,
            "paper_unchanged": True,
            "execution_unchanged": True,
            "strategy_unchanged": True,
            "gate_unchanged": True,
            "optimizer_unchanged": True,
            "brain_unchanged": True,
        }

    def test_morning_plain(self) -> None:
        text = format_morning_summary(self._result())
        self.assertIn("MORNING SUMMARY", text)
        self.assertIn("TOP 10 SETUPS", text)
        self.assertNotIn("```", text)
        self.assertNotIn("{", text.split("\n")[0])

    def test_report_has_sections(self) -> None:
        md = format_report(self._result())
        self.assertIn("TRADING_DNA_REPORT", md)
        self.assertIn("Minimal rule", md)
        self.assertIn("Forbidden", md)

    def test_write(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.trading_dna_v1.report.BASE_DIR", base
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.trading_dna_v1.report.REPORT_MD",
                base / "TRADING_DNA_REPORT.md",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.trading_dna_v1.report.OUT_DIR",
                base / "out",
            ):
                paths = write_artifacts(self._result())
                self.assertTrue((base / "TRADING_DNA_REPORT.md").exists())
                self.assertIn("TRADING_DNA_REPORT.md", paths)


class TestEngine(unittest.TestCase):
    def test_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite3.connect(str(Path(td) / "e.db"))
            out = run_trading_dna_v1(conn, write_reports=False, limit=10)
            self.assertFalse(out["ok"])
            self.assertTrue(out["research_only"])
            conn.close()

    def test_mocked(self) -> None:
        rows = [
            {
                "trade_id": i + 1,
                "symbol": "BTC",
                "direction": "LONG" if i % 2 == 0 else "SHORT",
                "pnl": 1.0 if i % 3 else -0.6,
                "opened_at": 1_700_000_000 + i * 300,
                "confidence": 0.5,
                "features_json": "{}",
            }
            for i in range(80)
        ]
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite3.connect(str(Path(td) / "m.db"))
            with mock.patch(
                "bot.research.market_events.signal_intelligence.trading_dna_v1.engine._load_trades",
                return_value=(rows, {"source": "mock"}),
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.trading_dna_v1.features.load_candle_book",
                return_value={},
            ):
                out = run_trading_dna_v1(conn, write_reports=False, limit=80)
            self.assertTrue(out["ok"])
            self.assertEqual(out["n_trades"], 80)
            self.assertIn("morning_summary", out)
            self.assertIn("TOP 10 SETUPS", out["morning_summary"])
            conn.close()


class TestCli(unittest.TestCase):
    def test_help(self) -> None:
        import subprocess
        import sys

        r = subprocess.run(
            [sys.executable, "-m", "bot.research.market_events", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("trading-dna", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
