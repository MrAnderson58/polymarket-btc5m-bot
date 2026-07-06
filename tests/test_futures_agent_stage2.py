"""Stage 2 market snapshot and BTC context tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.research.futures_agent.config import (
    ALIGNMENT_ALIGNED,
    BTC_REGIME_UP,
    DATA_QUALITY_COMPLETE,
    DATA_QUALITY_SYMBOL_UNAVAILABLE,
    RESEARCH_SUPPORTIVE,
    STATUS_COMPLETE,
    STATUS_PENDING_SNAPSHOT,
)
from bot.research.futures_agent.context_report import format_context_report
from bot.research.futures_agent.db import agent_connection
from bot.research.futures_agent.env_bootstrap import reset_bootstrap_for_tests
from bot.research.futures_agent.features import (
    AssetSnapshot,
    build_asset_snapshot,
    candles_at_or_before,
    close_at,
    correlation_beta,
    return_over,
)
from bot.research.futures_agent.ingestion import ingest_forwarded_signal
from bot.research.futures_agent.market_provider import MarketDataProvider, symbol_pair
from bot.research.futures_agent.pipeline import process_input
from bot.research.futures_agent.regime import (
    alignment_label,
    btc_market_regime,
    preliminary_research_label,
    volatility_regime,
)
from bot.research.futures_agent.schema import apply_migrations
from bot.research.futures_agent.snapshot import snapshot_signal

EXPLICIT_LONG = (
    "SUI LONG\n"
    "Entry: 2.14-2.18\n"
    "SL: 2.05\n"
    "TP1: 2.32\n"
    "TP2: 2.45\n"
)


def _kline(open_ts_sec: int, close: float, *, high: float | None = None, low: float | None = None) -> list:
    h = high if high is not None else close * 1.001
    l = low if low is not None else close * 0.999
    return [
        open_ts_sec * 1000, str(close * 0.999), str(h), str(l), str(close), "1000",
        (open_ts_sec + 60) * 1000, "5000", "10", "500", "250", "0",
    ]


def _synthetic_candles(base_ts: int, start_price: float, n: int = 120) -> list[list]:
    candles = []
    price = start_price
    for i in range(n):
        ts = base_ts - (n - i) * 60
        price *= 1.0005
        candles.append(_kline(ts, price))
    return candles


class MockMarketProvider(MarketDataProvider):
    def __init__(self, candles: dict[str, list[list]], *, available: set[str] | None = None) -> None:
        self._candles = candles
        self._available = available or set(candles.keys())

    def fetch_spot_klines(self, pair: str, interval: str, end_ts: int, *, limit: int = 500) -> list[list]:
        return self._candles.get(pair, [])

    def fetch_futures_klines(self, pair: str, interval: str, end_ts: int, *, limit: int = 500) -> list[list]:
        return self._candles.get(pair, [])

    def fetch_funding_rate(self, pair: str, end_ts: int) -> tuple[float | None, str]:
        return 0.0001, "available"

    def symbol_available(self, pair: str, end_ts: int) -> bool:
        return pair in self._available


class FuturesAgentStage2FeatureTestCase(unittest.TestCase):
    def test_no_lookahead_candle_filter(self) -> None:
        ts = 1_700_000_000
        candles = [_kline(ts - 120, 100), _kline(ts, 101), _kline(ts + 60, 999)]
        filtered = candles_at_or_before(candles, ts)
        self.assertEqual(len(filtered), 2)
        self.assertEqual(close_at(filtered, ts), 101.0)

    def test_return_uses_only_data_at_or_before_T(self) -> None:
        ts = 1_700_000_000
        candles = _synthetic_candles(ts, 100.0)
        ret = return_over(candles, ts, 300)
        self.assertIsNotNone(ret)
        future_candles = candles + [_kline(ts + 3600, 200.0)]
        ret2 = return_over(future_candles, ts, 300)
        self.assertEqual(ret, ret2)

    def test_correlation_and_beta(self) -> None:
        alt = [0.01, 0.02, -0.01, 0.015, 0.005, 0.01]
        btc = [0.005, 0.01, -0.005, 0.008, 0.002, 0.005]
        corr, beta, _ = correlation_beta(alt, btc)
        self.assertIsNotNone(corr)
        self.assertIsNotNone(beta)
        self.assertGreater(corr, 0.9)
        self.assertGreater(beta, 1.0)

    def test_btc_regime_labels(self) -> None:
        snap = AssetSnapshot(symbol="BTC", pair="BTCUSDT", snapshot_ts=1)
        snap.return_1h = 0.5
        snap.return_4h = 0.3
        self.assertEqual(btc_market_regime(snap), BTC_REGIME_UP)

    def test_alignment_label_long_with_btc_up(self) -> None:
        alt = AssetSnapshot(symbol="SUI", pair="SUIUSDT", snapshot_ts=1)
        btc = AssetSnapshot(symbol="BTC", pair="BTCUSDT", snapshot_ts=1)
        btc.return_1h = 1.0
        btc.return_4h = 0.5
        label = alignment_label(
            signal_direction="LONG", alt_snap=alt, btc_snap=btc, excess_1h=0.1,
        )
        self.assertEqual(label, ALIGNMENT_ALIGNED)

    def test_unavailable_symbol_handling(self) -> None:
        provider = MockMarketProvider({}, available=set())
        snap = build_asset_snapshot(provider, "FAKECOIN", 1_700_000_000)
        self.assertEqual(snap.data_quality, DATA_QUALITY_SYMBOL_UNAVAILABLE)

    def test_api_timeout_returns_unavailable(self) -> None:
        provider = MockMarketProvider({})
        snap = build_asset_snapshot(provider, "SUI", 1_700_000_000)
        self.assertIn(snap.data_quality, ("API_UNAVAILABLE", "SYMBOL_UNAVAILABLE"))


class FuturesAgentStage2PipelineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        reset_bootstrap_for_tests()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "stage2.db"
        self.db_url = f"sqlite:///{self.db_path}"
        self.ts = 1_700_000_000

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        reset_bootstrap_for_tests()

    def _provider(self) -> MockMarketProvider:
        sui = _synthetic_candles(self.ts, 2.15)
        btc = _synthetic_candles(self.ts, 65000.0)
        return MockMarketProvider({
            "SUIUSDT": sui,
            "BTCUSDT": btc,
            "ETHUSDT": _synthetic_candles(self.ts, 3500.0),
        })

    def _seed_gated_signal(self, conn) -> int:
        apply_migrations(conn, postgres=False)
        ing = ingest_forwarded_signal(
            conn, raw_text=EXPLICIT_LONG, telegram_message_id="st2-1",
        )
        proc = process_input(conn, ing.input_id)
        conn.execute(
            "UPDATE futures_agent_inputs SET received_at = ? WHERE id = ?",
            (self.ts, ing.input_id),
        )
        return int(proc.signal_id)

    def test_snapshot_synchronized_alt_and_btc(self) -> None:
        with agent_connection(self.db_url) as conn:
            signal_id = self._seed_gated_signal(conn)
            result = snapshot_signal(conn, signal_id, provider=self._provider())
            self.assertTrue(result.success)
            alt = conn.execute(
                "SELECT snapshot_ts FROM futures_agent_market_snapshots WHERE signal_id = ?",
                (signal_id,),
            ).fetchone()
            btc = conn.execute(
                "SELECT snapshot_ts FROM futures_agent_btc_context WHERE signal_id = ?",
                (signal_id,),
            ).fetchone()
        self.assertEqual(alt["snapshot_ts"], self.ts)
        self.assertEqual(btc["snapshot_ts"], self.ts)

    def test_snapshot_idempotent(self) -> None:
        with agent_connection(self.db_url) as conn:
            signal_id = self._seed_gated_signal(conn)
            r1 = snapshot_signal(conn, signal_id, provider=self._provider())
            r2 = snapshot_signal(conn, signal_id, provider=self._provider())
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM futures_agent_btc_context WHERE signal_id = ?",
                (signal_id,),
            ).fetchone()["n"]
        self.assertTrue(r1.success)
        self.assertTrue(r2.skipped)
        self.assertEqual(n, 1)

    def test_snapshot_failure_does_not_rollback_stage1_signal(self) -> None:
        with agent_connection(self.db_url) as conn:
            signal_id = self._seed_gated_signal(conn)
            failing = MockMarketProvider({})
            result = snapshot_signal(conn, signal_id, provider=failing)
            n_sig = conn.execute(
                "SELECT COUNT(*) AS n FROM futures_agent_signals WHERE id = ?",
                (signal_id,),
            ).fetchone()["n"]
            status = conn.execute(
                "SELECT processing_status FROM futures_agent_inputs i "
                "JOIN futures_agent_signals s ON s.input_id = i.id WHERE s.id = ?",
                (signal_id,),
            ).fetchone()["processing_status"]
        self.assertFalse(result.success)
        self.assertEqual(n_sig, 1)
        self.assertEqual(status, STATUS_PENDING_SNAPSHOT)

    def test_full_snapshot_persists_all_tables(self) -> None:
        with agent_connection(self.db_url) as conn:
            signal_id = self._seed_gated_signal(conn)
            snapshot_signal(conn, signal_id, provider=self._provider())
            snaps = conn.execute(
                "SELECT COUNT(*) AS n FROM futures_agent_market_snapshots WHERE signal_id = ?",
                (signal_id,),
            ).fetchone()["n"]
            btc = conn.execute(
                "SELECT COUNT(*) AS n FROM futures_agent_btc_context WHERE signal_id = ?",
                (signal_id,),
            ).fetchone()["n"]
            rs = conn.execute(
                "SELECT COUNT(*) AS n FROM futures_agent_relative_strength WHERE signal_id = ?",
                (signal_id,),
            ).fetchone()["n"]
            status = conn.execute(
                "SELECT processing_status FROM futures_agent_inputs i "
                "JOIN futures_agent_signals s ON s.input_id = i.id WHERE s.id = ?",
                (signal_id,),
            ).fetchone()["processing_status"]
        self.assertGreaterEqual(snaps, 1)
        self.assertEqual(btc, 1)
        self.assertEqual(rs, 1)
        self.assertEqual(status, STATUS_COMPLETE)

    def test_context_report_generation(self) -> None:
        with agent_connection(self.db_url) as conn:
            signal_id = self._seed_gated_signal(conn)
            snapshot_signal(conn, signal_id, provider=self._provider())
            report = format_context_report(conn, signal_id)
        self.assertIn("SIGNAL", report)
        self.assertIn("SUI", report)
        self.assertIn("ALT MARKET", report)
        self.assertIn("BTC CONTEXT", report)
        self.assertIn("RELATIVE STRENGTH", report)

    def test_research_label_deterministic(self) -> None:
        label = preliminary_research_label(
            data_quality=DATA_QUALITY_COMPLETE,
            alignment=ALIGNMENT_ALIGNED,
            btc_regime=BTC_REGIME_UP,
            vol_regime="NORMAL_VOL",
            passes_gate=True,
        )
        self.assertEqual(label, RESEARCH_SUPPORTIVE)

    def test_stage2_migration_applies(self) -> None:
        with agent_connection(self.db_url) as conn:
            applied = apply_migrations(conn, postgres=False)
            applied2 = apply_migrations(conn, postgres=False)
        self.assertTrue(any("stage2" in a for a in applied))
        self.assertEqual(applied2, [])


if __name__ == "__main__":
    unittest.main()
