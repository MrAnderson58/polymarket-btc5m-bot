"""Phase S1.1 — Validation Signal pipeline tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.candidate_g31 import CandidateG31
from bot.research.market_events.signal_intelligence.trend_windows_g3 import TrendWindowG3
from bot.research.market_events.signal_intelligence.validation_signal_s11 import (
    SIGNAL_TYPE,
    check_validation_followups_s11,
    emit_validation_signal_s11,
    format_validation_open_s11,
    format_validation_telegram_s11,
    pick_validation_candidate_s11,
)


def _cand(
    symbol: str,
    *,
    conf: float,
    ms: float,
    rr: float,
    vol: float = 35.0,
    direction: str = "SHORT",
) -> CandidateG31:
    trend = TrendWindowG3(
        symbol=symbol,
        window_minutes=60,
        pattern_type="test",
        consecutive_candles=3,
        trend_score=50.0,
        direction=direction,
        details={},
    )
    return CandidateG31(
        symbol=symbol,
        trend_score=50.0,
        market_score=ms,
        liquidity_score=40.0,
        confidence=conf,
        rr=rr,
        btc_alignment="ALIGNED",
        funding_score=55.0,
        oi_score=50.0,
        volume_score=vol,
        atr_score=50.0,
        fear_greed=50.0,
        candidate_state="WATCH",
        rejection_reason=None,
        direction=direction,
        trend=trend,
    )


class ValidationSignalS11Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "s11.db"
        configure_unit_test_db_isolation(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_pick_sorts_conf_ms_rr(self) -> None:
        cands = [
            _cand("AAA", conf=5.0, ms=40, rr=2.0),
            _cand("BBB", conf=6.0, ms=30, rr=1.5),
            _cand("CCC", conf=6.0, ms=36, rr=1.9),
        ]
        best = pick_validation_candidate_s11(cands)
        assert best is not None
        self.assertEqual(best.symbol, "CCC")

    def test_pick_falls_back_when_no_shadow_pass(self) -> None:
        # Weak candidate still selected when nothing passes shadow gates
        weak = _cand("SOL", conf=3.0, ms=20, rr=1.0, vol=10.0)
        best = pick_validation_candidate_s11([weak])
        assert best is not None
        self.assertEqual(best.symbol, "SOL")

    def test_telegram_card_mentions_validation(self) -> None:
        c = _cand("SOL", conf=5.8, ms=36, rr=1.9, vol=35.0)
        text = format_validation_telegram_s11(
            candidate=c, signal_id=42, entry=100, sl=102, tp1=98, tp2=96,
        )
        self.assertIn("VALIDATION SIGNAL", text)
        self.assertIn("SOL", text)
        self.assertIn("Volume", text)
        self.assertIn("FAIL", text)
        self.assertIn("Signal ID", text)
        self.assertIn("42", text)

    def test_emit_and_open_report(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            now = int(time.time())
            conn.execute(
                """
                INSERT INTO market_snapshots_g3 (
                  snapshot_uuid, snapshot_ts, btc_price, eth_price, sol_price, bnb_price,
                  funding, open_interest, fear_greed, collector_latency_ms, recorder_status, created_at
                ) VALUES ('s', ?, 65000, 3000, 150, 500, 0.0001, 1e9, 50, 100, 'ok', ?)
                """,
                (now, now),
            )
            snap_id = conn.execute("SELECT id FROM market_snapshots_g3 ORDER BY id DESC LIMIT 1").fetchone()["id"]
            with patch(
                "bot.research.market_events.signal_intelligence.validation_signal_s11.send_validation_telegram_s11",
                return_value=True,
            ):
                sig = emit_validation_signal_s11(
                    conn,
                    snapshot_id=int(snap_id),
                    candidates=[_cand("SOL", conf=5.8, ms=36, rr=1.9)],
                    force=True,
                )
            self.assertIsNotNone(sig)
            assert sig is not None
            row = conn.execute(
                "SELECT signal_type, symbol, status FROM market_validation_signals WHERE id = ?",
                (sig.signal_id,),
            ).fetchone()
            self.assertEqual(row["signal_type"], SIGNAL_TYPE)
            self.assertEqual(row["symbol"], "SOL")
            self.assertEqual(row["status"], "OPEN")
            open_txt = format_validation_open_s11(conn)
            self.assertIn("Signals today", open_txt)
            self.assertIn("1", open_txt)

            # daily cap without force
            with patch(
                "bot.research.market_events.signal_intelligence.validation_signal_s11.send_validation_telegram_s11",
                return_value=True,
            ):
                again = emit_validation_signal_s11(
                    conn,
                    snapshot_id=int(snap_id),
                    candidates=[_cand("ETH", conf=6.0, ms=40, rr=2.0)],
                    force=False,
                )
            self.assertIsNone(again)

    def test_followup_writes_horizon(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            now = int(time.time())
            conn.execute(
                """
                INSERT INTO market_snapshots_g3 (
                  snapshot_uuid, snapshot_ts, sol_price, created_at
                ) VALUES ('s2', ?, 150, ?)
                """,
                (now, now),
            )
            snap_id = conn.execute("SELECT id FROM market_snapshots_g3 ORDER BY id DESC LIMIT 1").fetchone()["id"]
            with patch(
                "bot.research.market_events.signal_intelligence.validation_signal_s11.send_validation_telegram_s11",
                return_value=False,
            ):
                sig = emit_validation_signal_s11(
                    conn,
                    snapshot_id=int(snap_id),
                    candidates=[_cand("SOL", conf=5.8, ms=36, rr=1.9)],
                    force=True,
                )
            assert sig is not None
            # age signal to 15m+
            conn.execute(
                "UPDATE market_validation_signals SET created_at = ? WHERE id = ?",
                (now - 1000, sig.signal_id),
            )
            with patch(
                "bot.research.market_events.signal_intelligence.validation_signal_s11._current_price",
                return_value=148.0,
            ):
                n = check_validation_followups_s11(conn)
            self.assertGreaterEqual(n, 1)
            hz = conn.execute(
                "SELECT horizon_label FROM market_validation_horizons WHERE signal_id = ?",
                (sig.signal_id,),
            ).fetchall()
            labels = {r["horizon_label"] for r in hz}
            self.assertIn("15m", labels)


if __name__ == "__main__":
    unittest.main()
