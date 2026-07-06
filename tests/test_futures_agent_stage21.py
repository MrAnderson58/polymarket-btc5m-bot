"""Stage 2.1 production validation and hardening tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bot.research.futures_agent.config import (
    ALIGNMENT_ALT_RS,
    ALIGNMENT_NEUTRAL,
    CANONICAL_ALIGNMENT_LABELS,
    SIGNAL_MARKET_ENTRY_NEAR,
    SIGNAL_MARKET_ENTRY_PENDING,
    SIGNAL_MARKET_ENTRY_ALREADY_PASSED,
    SIGNAL_MARKET_STALE,
)
from bot.research.futures_agent.context_report import format_context_report
from bot.research.futures_agent.db import agent_connection
from bot.research.futures_agent.env_bootstrap import reset_bootstrap_for_tests
from bot.research.futures_agent.features import (
    candles_at_or_before,
    correlation_beta,
    has_history_for_window,
    synchronized_minute_returns,
)
from bot.research.futures_agent.ingestion import ingest_forwarded_signal
from bot.research.futures_agent.market_provider import MarketDataProvider
from bot.research.futures_agent.pipeline import process_input
from bot.research.futures_agent.regime import canonical_relative_strength_label, ensure_canonical_alignment
from bot.research.futures_agent.schema import apply_migrations
from bot.research.futures_agent.signal_sanity import compute_signal_market_sanity
from bot.research.futures_agent.snapshot import snapshot_signal
from bot.research.futures_agent.snapshot_audit import format_snapshot_audit

EXPLICIT_LONG = (
    "SUI LONG\n"
    "Entry: 2.14-2.18\n"
    "SL: 2.05\n"
    "TP1: 2.32\n"
    "TP2: 2.45\n"
)


def _kline(open_ts_sec: int, close: float) -> list:
    return [
        open_ts_sec * 1000, str(close * 0.999), str(close * 1.001), str(close * 0.999),
        str(close), "1000", (open_ts_sec + 60) * 1000, "5000", "10", "500", "250", "0",
    ]


def _synthetic_candles(base_ts: int, start_price: float, n: int = 300) -> list[list]:
    candles = []
    price = start_price
    for i in range(n):
        ts = base_ts - (n - i) * 60
        price *= 1.0002
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


class SignalSanityTestCase(unittest.TestCase):
    def test_stale_synthetic_signal_detected(self) -> None:
        r = compute_signal_market_sanity(
            direction="LONG",
            entry_low=2.14,
            entry_high=2.18,
            stop_loss=2.05,
            take_profits=[2.32, 2.45],
            market_price=0.7423,
        )
        self.assertEqual(r["signal_market_status"], SIGNAL_MARKET_STALE)
        self.assertLess(r["entry_distance_pct"], -50)

    def test_entry_near_market(self) -> None:
        r = compute_signal_market_sanity(
            direction="LONG",
            entry_low=2.14,
            entry_high=2.18,
            stop_loss=2.05,
            take_profits=[2.32],
            market_price=2.16,
        )
        self.assertEqual(r["signal_market_status"], SIGNAL_MARKET_ENTRY_NEAR)

    def test_long_entry_pending(self) -> None:
        r = compute_signal_market_sanity(
            direction="LONG",
            entry_low=2.14,
            entry_high=2.18,
            stop_loss=2.05,
            take_profits=[2.32],
            market_price=2.25,
        )
        self.assertEqual(r["signal_market_status"], SIGNAL_MARKET_ENTRY_PENDING)

    def test_short_entry_pending(self) -> None:
        r = compute_signal_market_sanity(
            direction="SHORT",
            entry_low=2.14,
            entry_high=2.18,
            stop_loss=2.25,
            take_profits=[2.0],
            market_price=2.10,
        )
        self.assertEqual(r["signal_market_status"], SIGNAL_MARKET_ENTRY_PENDING)

    def test_long_entry_already_passed(self) -> None:
        r = compute_signal_market_sanity(
            direction="LONG",
            entry_low=2.14,
            entry_high=2.18,
            stop_loss=2.05,
            take_profits=[2.32],
            market_price=2.10,
        )
        self.assertEqual(r["signal_market_status"], SIGNAL_MARKET_ENTRY_ALREADY_PASSED)


class AlignmentLabelTestCase(unittest.TestCase):
    def test_canonical_relative_strength_never_inline(self) -> None:
        label = canonical_relative_strength_label(0.0, 0.0)
        self.assertEqual(label, ALIGNMENT_NEUTRAL)
        self.assertNotEqual(label, "INLINE")

    def test_outperform_maps_to_alt_rs(self) -> None:
        self.assertEqual(canonical_relative_strength_label(0.1, 0.6), ALIGNMENT_ALT_RS)

    def test_ensure_canonical_rejects_inline(self) -> None:
        self.assertEqual(ensure_canonical_alignment("INLINE"), ALIGNMENT_NEUTRAL)

    def test_all_canonical_labels(self) -> None:
        for label in CANONICAL_ALIGNMENT_LABELS:
            self.assertNotEqual(label, "INLINE")


class FeatureIntegrityTestCase(unittest.TestCase):
    def test_candle_timestamp_at_or_before_signal_T(self) -> None:
        ts = 1_700_000_000
        candles = [_kline(ts - 120, 1.0), _kline(ts, 1.1), _kline(ts + 60, 9.9)]
        filtered = candles_at_or_before(candles, ts)
        self.assertEqual(len(filtered), 2)
        for c in filtered:
            self.assertLessEqual(int(c[0] // 1000), ts)

    def test_synchronized_correlation_beta(self) -> None:
        ts = 1_700_000_000
        alt = _synthetic_candles(ts, 2.0, n=80)
        btc = _synthetic_candles(ts, 60000.0, n=80)
        a, b, n = synchronized_minute_returns(alt, btc, ts)
        corr, beta, paired = correlation_beta(a, b)
        self.assertGreaterEqual(n, 5)
        self.assertEqual(paired, n)
        self.assertIsNotNone(corr)
        self.assertIsNotNone(beta)

    def test_insufficient_paired_sample(self) -> None:
        corr, beta, n = correlation_beta([0.01], [0.02], min_samples=5)
        self.assertIsNone(corr)
        self.assertIsNone(beta)
        self.assertLess(n, 5)

    def test_4h_requires_sufficient_history(self) -> None:
        ts = 1_700_000_000
        short = _synthetic_candles(ts, 100.0, n=30)
        self.assertFalse(has_history_for_window(short, ts, 14400))


class Stage21IntegrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        reset_bootstrap_for_tests()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_url = f"sqlite:///{Path(self._tmpdir.name) / 's21.db'}"
        self.ts = 1_700_000_000

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        reset_bootstrap_for_tests()

    def _provider_stale_entry(self) -> MockMarketProvider:
        return MockMarketProvider({
            "SUIUSDT": _synthetic_candles(self.ts, 0.74, n=300),
            "BTCUSDT": _synthetic_candles(self.ts, 65000.0, n=300),
        })

    def test_snapshot_persists_canonical_alignment_not_inline(self) -> None:
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            ing = ingest_forwarded_signal(
                conn, raw_text=EXPLICIT_LONG, telegram_message_id="s21-1",
            )
            conn.execute(
                "UPDATE futures_agent_inputs SET received_at = ? WHERE id = ?",
                (self.ts, ing.input_id),
            )
            proc = process_input(conn, ing.input_id)
            snapshot_signal(conn, proc.signal_id, provider=self._provider_stale_entry())
            rs = conn.execute(
                "SELECT relative_strength_label FROM futures_agent_relative_strength WHERE signal_id = ?",
                (proc.signal_id,),
            ).fetchone()
            alt = conn.execute(
                "SELECT raw_metadata_json FROM futures_agent_market_snapshots WHERE signal_id = ?",
                (proc.signal_id,),
            ).fetchone()
            meta = json.loads(alt["raw_metadata_json"])
            report = format_context_report(conn, proc.signal_id)
        self.assertIn(rs["relative_strength_label"], CANONICAL_ALIGNMENT_LABELS)
        self.assertNotIn("INLINE", rs["relative_strength_label"])
        self.assertEqual(meta["signal_market_status"], SIGNAL_MARKET_STALE)
        self.assertIn("alignment:", report.lower())
        self.assertNotIn("INLINE", report)

    def test_snapshot_audit_command_output(self) -> None:
        with agent_connection(self.db_url) as conn:
            apply_migrations(conn)
            ing = ingest_forwarded_signal(
                conn, raw_text=EXPLICIT_LONG, telegram_message_id="s21-audit",
            )
            conn.execute(
                "UPDATE futures_agent_inputs SET received_at = ? WHERE id = ?",
                (self.ts, ing.input_id),
            )
            proc = process_input(conn, ing.input_id)
            snapshot_signal(conn, proc.signal_id, provider=self._provider_stale_entry())
            audit = format_snapshot_audit(conn, proc.signal_id, provider=self._provider_stale_entry())
        self.assertIn("SIGNAL T:", audit)
        self.assertIn("ALT latest data T:", audit)
        self.assertIn("LOOKAHEAD VIOLATIONS", audit)
        self.assertIn("STALE DATA FLAGS", audit)


if __name__ == "__main__":
    unittest.main()
