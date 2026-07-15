"""Phase S3.1 — Pattern Intelligence tests (SELECT-only)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
    build_pattern_index_s31,
    classify_outcome_s31,
    format_pattern_report_s31,
    run_pattern_agent_s31,
)
from bot.research.market_events.signal_intelligence.pattern_keys_s31 import (
    canonical_pattern_key_s31,
    expand_pattern_key_aliases_s31,
    normalize_family_s31,
    pattern_key_match_s31,
)
from bot.research.market_events.sqlite_manager_g05 import PURE_READONLY_COMMANDS


def _bars(n: int = 24) -> list[CandleBar]:
    out = []
    px = 100.0
    ts = 1_700_000_000
    for i in range(n):
        nxt = px * 1.002
        out.append(CandleBar(ts + i * 300, px, nxt * 1.001, px * 0.999, nxt, 1000 + i))
        px = nxt
    return out


def _seed_outcomes(conn, *, symbol: str, direction: str, wins: int, losses: int) -> None:
    ts = 1_700_100_000
    for i in range(wins + losses):
        win = i < wins
        cur = conn.execute(
            """
            INSERT INTO market_candidate_g31 (
              snapshot_id, candidate_ts, symbol, trend_score, market_score, liquidity_score,
              confidence, rr, btc_alignment, funding_score, oi_score, volume_score, atr_score,
              fear_greed, candidate_state, rejection_reason, direction, created_at
            ) VALUES (NULL, ?, ?, 60, 70, 75, 8.0, 2.5, 'Aligned', 50, 50, 55, 50, 50,
                      'rejected', 'No reversal confirmation', ?, ?)
            """,
            (ts + i, symbol, direction, ts + i),
        )
        cid = int(cur.lastrowid)
        conn.execute(
            """
            INSERT INTO market_candidate_outcomes_g32 (
              candidate_id, symbol, direction, created_at, price_entry,
              max_profit_pct, max_drawdown_pct, would_hit_tp, would_hit_sl,
              best_rr, replay_status, updated_at
            ) VALUES (?, ?, ?, ?, 100, ?, 0.5, ?, 0, 2.4, 'COMPLETE', ?)
            """,
            (cid, symbol, direction, ts + i, 1.5 if win else -1.0, 1 if win else 0, ts + i + 7200),
        )


class TestOutcomeClassifyS31(unittest.TestCase):
    def test_win_loss_unknown(self) -> None:
        self.assertEqual(
            classify_outcome_s31(would_hit_tp=1, would_hit_sl=0, max_profit_pct=0),
            "WIN",
        )
        self.assertEqual(
            classify_outcome_s31(would_hit_tp=0, would_hit_sl=1, max_profit_pct=0),
            "LOSS",
        )
        self.assertEqual(
            classify_outcome_s31(would_hit_tp=0, would_hit_sl=0, max_profit_pct=0),
            "UNKNOWN",
        )
        self.assertEqual(
            classify_outcome_s31(would_hit_tp=0, would_hit_sl=0, max_profit_pct=-1.2),
            "LOSS",
        )


class TestPatternKeysS31(unittest.TestCase):
    def test_unknown_maps_to_trend_reversal(self) -> None:
        self.assertEqual(normalize_family_s31("unknown"), "trend_reversal")
        key = canonical_pattern_key_s31(symbol="BTC", family="unknown", timeframe=60)
        self.assertEqual(key, "BTC|trend_reversal|60m")

    def test_aliases_include_legacy(self) -> None:
        key = canonical_pattern_key_s31(symbol="BTC", family="trend_reversal", timeframe="60m")
        aliases = expand_pattern_key_aliases_s31(key)
        self.assertIn("BTC|unknown|60m", aliases)
        self.assertTrue(
            pattern_key_match_s31(
                "BTC|unknown|60m",
                want_symbol="BTC",
                want_family="trend_reversal",
                want_tf="60m",
            )
        )


class TestPatternAgentS31(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(str(Path(self.tmp.name) / "me.db"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_pattern_found_from_outcomes(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            _seed_outcomes(conn, symbol="BTC", direction="LONG", wins=40, losses=20)
            _seed_outcomes(conn, symbol="ETH", direction="LONG", wins=10, losses=5)
            conn.commit()
            result = run_pattern_agent_s31(
                conn,
                symbol="BTC",
                direction="LONG",
                timeframe="60m",
                market_snapshot={"direction": "LONG", "meta": {"trend": "UP"}, "reasons": []},
            )
        self.assertTrue(result["pattern_found"])
        self.assertGreaterEqual(result["sample_size"], 5)
        self.assertEqual(result["pattern_key"], "BTC|trend_continuation|60m")
        self.assertIn("BTC", result["similar_symbols"])
        text = format_pattern_report_s31(result)
        self.assertIn("WR", text)
        self.assertIn("Samples", text)
        self.assertIn("READ ONLY", text)

    def test_pattern_build_select_only(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            _seed_outcomes(conn, symbol="SOL", direction="SHORT", wins=8, losses=4)
            conn.commit()
            index = build_pattern_index_s31(conn)
        self.assertGreaterEqual(index["buckets"], 1)

    def test_decision_uses_pattern_no_insert(self) -> None:
        data = SymbolMarketDataG01(
            symbol="BTC", price=100.0, funding=-0.0002, open_interest=1.0,
            volume_24h=10.0, bars=_bars(), source="bybit",
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            _seed_outcomes(conn, symbol="BTC", direction="LONG", wins=30, losses=10)
            conn.commit()
            result = run_decision_engine_s20(
                conn,
                "BTC",
                persist=False,
                force_fallback=True,
                headlines=[{"title": "Bitcoin ETF inflows surge", "summary": "", "source": "coindesk"}],
                market_data=data,
            )
            runs = conn.execute("SELECT COUNT(*) AS n FROM market_decision_runs_s20").fetchone()["n"]
        self.assertIsNone(result["run_id"])
        self.assertEqual(runs, 0)
        self.assertIn("pattern", result)
        self.assertIn("Pattern", result["telegram"])
        self.assertIn("WR", result["telegram"])

    def test_pattern_in_pure_ro(self) -> None:
        self.assertIn("/pattern", PURE_READONLY_COMMANDS)


if __name__ == "__main__":
    unittest.main()
