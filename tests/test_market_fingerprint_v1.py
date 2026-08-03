"""Tests for Market State Fingerprint Engine V1."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from bot.research.market_events.signal_intelligence.market_fingerprint_v1.clusters import (
    build_clusters,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.engine import (
    run_market_fingerprint_v1,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.regime import (
    regime_transitions,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.report import (
    format_report,
    format_terminal,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.sequence import (
    sequence_analysis,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.similarity import (
    build_similarity_index,
    format_similarity,
    query_similarity,
)
from bot.research.market_events.signal_intelligence.market_fingerprint_v1.snapshots import (
    VECTOR_KEYS,
    snapshot_trade,
    vector_matrix,
)


def _fake_book(n: int = 400) -> dict:
    ts = np.arange(1_700_000_000, 1_700_000_000 + n * 300, 300, dtype=np.int64)
    close = 100 + np.cumsum(np.random.default_rng(0).normal(0, 0.2, size=len(ts)))
    high = close + 0.5
    low = close - 0.5
    vol = np.full(len(ts), 1000.0)
    from bot.research.market_events.signal_intelligence.market_fingerprint_v1 import snapshots as sn

    # Build via public helpers by constructing arrays like load_candle_book
    atr = sn._atr(high, low, close, 14)
    macd_l, macd_s, macd_h = sn._macd(close)
    bb_m, bb_u, bb_l = sn._bb(close, 20)
    with np.errstate(divide="ignore", invalid="ignore"):
        atr_pct = np.where(close > 0, 100.0 * atr / close, np.nan)
        bb_width = np.where(bb_m > 0, (bb_u - bb_l) / bb_m, np.nan)
        bb_pos = np.where((bb_u - bb_l) > 1e-12, (close - bb_l) / (bb_u - bb_l), np.nan)
    return {
        "BTC": {
            "ts": ts,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
            "ema20": sn._ema(close, 20),
            "ema50": sn._ema(close, 50),
            "ema200": sn._ema(close, 200),
            "rsi": sn._rsi(close, 14),
            "atr": atr,
            "atr_pct": atr_pct,
            "macd": macd_l,
            "macd_signal": macd_s,
            "macd_hist": macd_h,
            "stoch": sn._stoch(high, low, close, 14),
            "bb_mid": bb_m,
            "bb_width": bb_width,
            "bb_pos": bb_pos,
        }
    }


def _rows(n: int = 300) -> list[dict]:
    book = _fake_book()
    rows = []
    base = 1_700_000_000 + 250 * 300
    for i in range(n):
        opened = base + i * 300
        pnl = 1.0 if i % 3 else -0.6
        trade = {
            "trade_id": i + 1,
            "symbol": "BTC",
            "direction": "LONG" if i % 2 == 0 else "SHORT",
            "opened_at": opened,
            "closed_at": opened + 600,
            "pnl": pnl,
            "result": "WIN" if pnl > 0 else "LOSS",
            "regime": ["BULL", "RANGE", "BEAR"][i % 3],
            "mae_pct": -0.3,
            "mfe_pct": 0.8,
            "holding_seconds": 600,
            "features_json": "{}",
        }
        snap = snapshot_trade(trade, book)
        if snap:
            rows.append(snap)
    return rows


class TestSnapshots(unittest.TestCase):
    def test_snapshot_vector(self) -> None:
        rows = _rows(20)
        self.assertGreater(len(rows), 10)
        self.assertEqual(len(rows[0]["vector"]), len(VECTOR_KEYS))
        mat = vector_matrix(rows)
        self.assertEqual(mat.shape[0], len(rows))
        self.assertEqual(mat.shape[1], len(VECTOR_KEYS))


class TestClustersSimilarity(unittest.TestCase):
    def test_clusters_and_knn(self) -> None:
        rows = _rows(240)
        clustered = build_clusters(rows, k_per_bucket=4, min_n=20)
        self.assertGreaterEqual(len(clustered.get("clusters") or []), 1)
        idx = build_similarity_index(rows)
        self.assertTrue(idx.get("ok"))
        out = query_similarity(idx, rows[-1]["vector"], k=50, assignments=clustered.get("assignments"))
        self.assertTrue(out.get("ok"))
        self.assertIsNotNone(out.get("similarity_pct"))
        text = format_similarity(out)
        self.assertIn("Similarity", text)
        self.assertIn("RESEARCH ONLY", text)


class TestSequenceRegime(unittest.TestCase):
    def test_sequence_and_regime(self) -> None:
        rows = _rows(200)
        seq = sequence_analysis(rows)
        self.assertIn("3", seq)
        tr = regime_transitions(rows)
        self.assertIn("probs", tr)
        self.assertIn("BULL", tr["probs"])


class TestEngine(unittest.TestCase):
    def test_empty(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as td:
            conn = sqlite3.connect(str(Path(td) / "e.db"))
            out = run_market_fingerprint_v1(conn, write_reports=False, limit=5)
            self.assertFalse(out["ok"])
            conn.close()

    def test_mocked(self) -> None:
        rows = [
            {
                "trade_id": i + 1,
                "symbol": "BTC",
                "direction": "LONG",
                "opened_at": 1_700_080_000 + i * 300,
                "closed_at": 1_700_080_600 + i * 300,
                "pnl": 1.0 if i % 2 == 0 else -0.5,
                "regime": "RANGE",
                "features_json": "{}",
                "mae_pct": -0.2,
                "mfe_pct": 0.5,
                "holding_seconds": 600,
            }
            for i in range(80)
        ]
        book = _fake_book()
        with tempfile.TemporaryDirectory() as td:
            import sqlite3

            conn = sqlite3.connect(str(Path(td) / "m.db"))
            with mock.patch(
                "bot.research.market_events.signal_intelligence.market_fingerprint_v1.engine._load_trades",
                return_value=(rows, {"source": "mock"}),
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.market_fingerprint_v1.engine.load_candle_book",
                return_value=book,
            ):
                out = run_market_fingerprint_v1(conn, write_reports=False)
            self.assertTrue(out["ok"])
            self.assertIn("terminal", out)
            self.assertIn("MARKET FINGERPRINT", out["terminal"])
            self.assertLess(len(format_report(out).splitlines()), 201)
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
        blob = r.stdout + r.stderr
        self.assertIn("market-fingerprint", blob)
        self.assertIn("market-fingerprint-report", blob)
        self.assertIn("market-similarity", blob)


if __name__ == "__main__":
    unittest.main()
