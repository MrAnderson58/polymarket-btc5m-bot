"""Tests for Market Timeline Intelligence Engine V1."""

from __future__ import annotations

import unittest
from unittest import mock

import numpy as np

from bot.research.market_events.signal_intelligence.market_timeline_v1.chains import (
    cluster_histories,
    mine_chains,
)
from bot.research.market_events.signal_intelligence.market_timeline_v1.engine import (
    run_market_timeline_v1,
)
from bot.research.market_events.signal_intelligence.market_timeline_v1.labels import (
    chain_key,
    label_chain,
    label_window,
)
from bot.research.market_events.signal_intelligence.market_timeline_v1.report import (
    format_terminal,
)
from bot.research.market_events.signal_intelligence.market_timeline_v1.windows import (
    CHAIN_WINDOWS,
    TIMELINE_WINDOWS,
    timeline_trade,
)


def _fake_book(n: int = 500) -> dict:
    from bot.research.market_events.signal_intelligence.market_fingerprint_v1 import (
        snapshots as sn,
    )

    ts = np.arange(1_700_000_000, 1_700_000_000 + n * 300, 300, dtype=np.int64)
    close = 100 + np.cumsum(np.random.default_rng(1).normal(0, 0.25, size=len(ts)))
    high = close + 0.6
    low = close - 0.6
    vol = np.full(len(ts), 1200.0)
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


def _rows(n: int = 240) -> list[dict]:
    book = _fake_book()
    rows = []
    base = 1_700_000_000 + 400 * 300
    for i in range(n):
        opened = base + (i % 80) * 300
        pnl = 1.2 if i % 3 else -0.7
        trade = {
            "trade_id": i + 1,
            "symbol": "BTC",
            "direction": "LONG" if i % 2 == 0 else "SHORT",
            "opened_at": opened,
            "closed_at": opened + 900,
            "pnl": pnl,
            "result": "WIN" if pnl > 0 else "LOSS",
            "regime": ["BULL", "RANGE", "BEAR"][i % 3],
            "holding_seconds": 900,
            "exit_reason": "tp" if pnl > 0 else "sl",
            "features_json": "{}",
        }
        row = timeline_trade(trade, book)
        assert row is not None
        rows.append(row)
    return rows


class TestLabels(unittest.TestCase):
    def test_label_window_bear_bull_range(self):
        self.assertEqual(label_window({"ret": -2.0}), "Bear")
        self.assertEqual(label_window({"ret": 2.0}), "Bull")
        self.assertEqual(label_window({"ret": 0.1}), "Range")

    def test_atr_compression_transition(self):
        prior = {"atr_pct": 2.0, "ret": -0.2}
        cur = {"atr_pct": 1.4, "ret": -0.1}
        self.assertEqual(label_window(cur, prior=prior), "ATR_compression")

    def test_chain_ends_with_direction(self):
        rows = _rows(20)
        labs = label_chain(rows[0])
        self.assertEqual(len(labs), len(CHAIN_WINDOWS) + 1)
        self.assertIn(labs[-1], ("LONG", "SHORT"))
        self.assertIn("→", chain_key(labs))


class TestChains(unittest.TestCase):
    def test_mine_and_cluster(self):
        rows = _rows(240)
        mined = mine_chains(rows, top_n=50, min_n=5)
        self.assertGreater(mined["n_chains"], 0)
        self.assertTrue(mined["top_chains"])
        clustered = cluster_histories(rows, k=8, min_n=20)
        self.assertGreaterEqual(len(clustered.get("clusters") or []), 1)

    def test_terminal_summary_shape(self):
        text = format_terminal(
            {
                "n_timelines": 100,
                "n_clusters": 5,
                "timeline_chains": 40,
                "n_profitable_chains": 3,
                "n_ready_chains": 1,
                "top_chain": {
                    "id": 1,
                    "wr": 70,
                    "pf": 2.1,
                    "ev": 1.5,
                    "confidence": 0.8,
                    "common_duration": "4h → 1h → 15m",
                },
                "similarity": {
                    "similarity_pct": 80,
                    "closest_chain": 1,
                    "recommendation": "RESEARCH ONLY",
                },
                "elapsed_sec": 1.2,
            }
        )
        self.assertIn("MARKET TIMELINE SUMMARY", text)
        self.assertIn("READY chains", text)
        self.assertIn("RESEARCH ONLY", text)


class TestEngine(unittest.TestCase):
    def test_run_with_mocks(self):
        book = _fake_book()
        fake_trades = []
        base = 1_700_000_000 + 400 * 300
        for i in range(120):
            fake_trades.append(
                {
                    "trade_id": i + 1,
                    "symbol": "BTC",
                    "direction": "LONG" if i % 2 == 0 else "SHORT",
                    "opened_at": base + (i % 60) * 300,
                    "closed_at": base + (i % 60) * 300 + 600,
                    "pnl": 1.0 if i % 3 else -0.5,
                    "result": "WIN" if i % 3 else "LOSS",
                    "regime": "RANGE",
                    "holding_seconds": 600,
                    "exit_reason": "tp",
                    "features_json": "{}",
                }
            )

        conn = mock.MagicMock()
        with mock.patch(
            "bot.research.market_events.signal_intelligence.market_timeline_v1.engine._load_trades",
            return_value=(fake_trades, {"source": "test"}),
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_timeline_v1.engine.load_candle_book",
            return_value=book,
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_timeline_v1.engine.write_artifacts",
            return_value={"report_text": "ok"},
        ):
            out = run_market_timeline_v1(conn, write_reports=True)
        self.assertTrue(out.get("ok"))
        self.assertGreater(out.get("n_timelines"), 0)
        self.assertIn("MARKET TIMELINE SUMMARY", out.get("terminal") or "")
        self.assertEqual(len(TIMELINE_WINDOWS), 13)


if __name__ == "__main__":
    unittest.main()
