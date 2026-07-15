"""Phase S3.2 — Pattern Evidence Engine (SELECT-only)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.decision_engine_s20 import (
    run_decision_engine_s20,
)
from bot.research.market_events.signal_intelligence.market_data_source_g01 import (
    SymbolMarketDataG01,
)
from bot.research.market_events.signal_intelligence.candles import CandleBar
from bot.research.market_events.signal_intelligence.pattern_agent_s31 import (
    format_pattern_report_s31,
    run_pattern_agent_s31,
)
from bot.research.market_events.signal_intelligence.pattern_evidence_s32 import (
    compute_pattern_quality_s32,
    pattern_explorer_dashboard_s32,
)


def _bars(n: int = 24) -> list[CandleBar]:
    out = []
    px = 100.0
    ts = 1_700_000_000
    for i in range(n):
        nxt = px * 1.002
        out.append(CandleBar(ts + i * 300, px, nxt * 1.001, px * 0.999, nxt, 1000 + i))
        px = nxt
    return out


def _seed_rich(conn, *, symbol: str, direction: str, n: int = 25) -> None:
    ts = 1_700_200_000
    for i in range(n):
        win = i % 3 != 2
        funding = 70 if i % 5 != 0 else 40
        oi = 75 if i % 4 != 0 else 45
        volume = 60 if i % 3 != 0 else 30
        atr = 40 if i % 2 == 0 else 70
        fg = 25 if i % 2 == 0 else 55
        cur = conn.execute(
            """
            INSERT INTO market_candidate_g31 (
              snapshot_id, candidate_ts, symbol, trend_score, market_score, liquidity_score,
              confidence, rr, btc_alignment, funding_score, oi_score, volume_score, atr_score,
              fear_greed, candidate_state, rejection_reason, direction, created_at
            ) VALUES (NULL, ?, ?, 60, 70, 75, 8.0, 2.5, 'Aligned', ?, ?, ?, ?, ?,
                      'rejected', 'No reversal confirmation', ?, ?)
            """,
            (ts + i, symbol, funding, oi, volume, atr, fg, direction, ts + i),
        )
        cid = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO market_candidate_outcomes_g32 (
              candidate_id, symbol, direction, created_at, price_entry, price_4h,
              max_profit_pct, max_drawdown_pct, would_hit_tp, would_hit_sl,
              best_rr, replay_status, updated_at
            ) VALUES (?, ?, ?, ?, 100, ?, ?, 0.5, ?, 0, 2.4, 'COMPLETE', ?)
            """,
            (
                cid, symbol, direction, ts + i,
                102.0 if win else 98.0,
                2.0 if win else -1.5,
                1 if win else 0,
                ts + i + 7200,
            ),
        )


class TestPatternEvidenceS32(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(str(Path(self.tmp.name) / "me.db"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_examples_common_features_quality(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            _seed_rich(conn, symbol="BTC", direction="LONG", n=30)
            conn.commit()
            result = run_pattern_agent_s31(
                conn, symbol="BTC", direction="LONG", timeframe="60m",
                market_snapshot={"direction": "LONG", "meta": {}, "reasons": []},
            )
        examples = result["pattern_examples"]
        self.assertLessEqual(len(examples), 10)
        self.assertGreaterEqual(len(examples), 1)
        ex = examples[0]
        for key in ("symbol", "date", "direction", "entry", "exit", "PnL",
                    "hold", "funding", "OI", "volume", "ATR", "reason"):
            self.assertIn(key, ex)
        self.assertTrue(result["common_features"])
        self.assertIn(result["pattern_quality"], ("High", "Medium", "Low"))
        text = format_pattern_report_s31(result, show_examples=True)
        self.assertIn("Evidence", text)
        self.assertIn("Common", text)
        self.assertIn("Examples", text)
        self.assertIn("Quality", text)

    def test_quality_tiers(self) -> None:
        self.assertEqual(
            compute_pattern_quality_s32(sample_size=50, confidence=0.8, variance=1.0),
            "High",
        )
        self.assertEqual(
            compute_pattern_quality_s32(sample_size=25, confidence=0.6, variance=4.0),
            "Medium",
        )
        self.assertEqual(
            compute_pattern_quality_s32(sample_size=3, confidence=0.2, variance=10.0),
            "Low",
        )

    def test_decision_receives_evidence(self) -> None:
        data = SymbolMarketDataG01(
            symbol="BTC", price=100.0, funding=-0.0002, open_interest=1.0,
            volume_24h=10.0, bars=_bars(), source="bybit",
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            _seed_rich(conn, symbol="BTC", direction="LONG", n=20)
            conn.commit()
            result = run_decision_engine_s20(
                conn, "BTC", persist=False, force_fallback=True,
                headlines=[{"title": "Bitcoin ETF inflows surge", "summary": "", "source": "coindesk"}],
                market_data=data,
            )
            runs = conn.execute("SELECT COUNT(*) AS n FROM market_decision_runs_s20").fetchone()["n"]
        self.assertEqual(runs, 0)
        pat = result["pattern"]
        self.assertIn("pattern_examples", pat)
        self.assertIn("common_features", pat)
        self.assertIn("pattern_quality", pat)
        self.assertIn("Evidence", result["telegram"])
        self.assertIn("Quality", result["telegram"])

    def test_pattern_explorer_dashboard(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            _seed_rich(conn, symbol="ETH", direction="SHORT", n=12)
            conn.commit()
            dash = pattern_explorer_dashboard_s32(conn, symbol="ETH", direction="SHORT")
        self.assertEqual(dash["tab"], "Pattern Explorer")
        self.assertIn("examples", dash)
        self.assertIn("common_features", dash)


if __name__ == "__main__":
    unittest.main()
