"""S62.3 Pattern Discovery Engine tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence import pattern_discovery_s623 as s623
from bot.research.market_events.signal_intelligence.research_repository_s60 import (
    research_connection,
)
from tests.research_db_helpers import ensure_research_schema


class TestPatternDiscoveryS623(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "s623.db")
        self.now = int(time.time())
        self.out = Path(self.tmp.name) / "reports"
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
        ensure_research_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self, conn, *, n: int = 400) -> None:
        for i in range(n):
            ts = self.now - (i % 200) * 1800
            direction = "LONG" if i % 2 == 0 else "SHORT"
            # Strong BTC SHORT edge; weak ETH LONG
            if direction == "SHORT" and i % 2 == 1:
                pnl = 1.2 + (i % 5) * 0.05
                sym = "BTC"
                regime = "WEAK_BEAR"
            else:
                pnl = -0.4 - (i % 4) * 0.05
                sym = "ETH" if i % 3 == 0 else "BTC"
                regime = "RANGE"
            conn.execute(
                """
                INSERT INTO market_events_trade_snapshots_s56 (
                  paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
                  entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
                  funding, ai_score, hour, weekday, market_regime,
                  created_at, timestamp
                ) VALUES (?, 'hist:er_v2', ?, ?, ?, 100, 101, ?, ?, 300, 'TP1',
                  ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    i + 1, i + 1, sym, direction, pnl, pnl,
                    -0.001 if direction == "LONG" else 0.001,
                    0.7 if direction == "SHORT" else 0.3,
                    i % 24, i % 7, regime, ts, ts,
                ),
            )
            conn.execute(
                """
                INSERT INTO market_events_trade_decisions_s58 (
                  paper_trade_id, opened_at, symbol, direction, entry_price,
                  rsi, gate_result, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 100, ?, 'PASS', ?, ?)
                """,
                (i + 1, ts, sym, direction, 25 + (i % 50), ts, ts),
            )
        conn.commit()

    def test_discover_patterns_exports(self) -> None:
        with research_connection() as conn:
            self._seed(conn)
            out = s623.run_pattern_discovery(
                conn,
                now=self.now,
                report_dir=self.out,
                min_trades=30,
                periods=["lifetime", "24h"],
                last_trades=[100],
            )
        self.assertTrue(out.get("ok"))
        self.assertLess(out.get("elapsed_sec") or 99, 15)
        self.assertIn("lifetime", out.get("universes") or {})
        self.assertIn("last_100", out.get("universes") or {})
        life = (out.get("universes") or {})["lifetime"]
        self.assertGreater(len(life.get("top_best") or []), 0)
        self.assertGreater(len(life.get("top_worst") or []), 0)
        self.assertLessEqual(len(life.get("top_best") or []), 100)

        for path_key in ("markdown", "json", "candidates"):
            p = Path(out["export_paths"][path_key])
            self.assertTrue(p.exists(), path_key)
        md = Path(out["export_paths"]["markdown"]).read_text(encoding="utf-8")
        self.assertIn("TOP Best Patterns", md)
        self.assertIn("Biggest Recent Deterioration", md)
        self.assertIn("Data Quality", md)
        self.assertIn("Most Robust Patterns", md)
        self.assertIn("Root Causes", md)
        self.assertIn("Duplicate patterns removed", md)
        cand = Path(out["export_paths"]["candidates"]).read_text(encoding="utf-8")
        self.assertIn("Candidate Disables", cand)
        self.assertIn("Candidate Enables", cand)
        self.assertIn("Confidence", cand)

        # Patterns have stability and period deltas
        pats = out.get("patterns") or []
        self.assertGreater(len(pats), 0)
        sample = pats[0]
        self.assertIn("stability", sample)
        self.assertIn("by_period", sample)
        self.assertIn("period_deltas", sample)

        # Consolidation: unique <= raw; no permutation dupes in unique list
        cons = out.get("consolidation") or {}
        self.assertGreaterEqual(int(cons.get("raw_count") or 0), int(cons.get("unique_retained") or 0))
        unique = out.get("patterns_unique") or []
        self.assertEqual(len(unique), int(cons.get("unique_retained") or 0))
        canon_keys = [p.get("canonical_key") or p.get("key") for p in unique]
        self.assertEqual(len(canon_keys), len(set(canon_keys)))
        # Canonical label order: Coin before Hour when both present
        for p in unique:
            dims = p.get("dims") or []
            if "coin" in dims and "hour" in dims:
                self.assertLess(dims.index("coin"), dims.index("hour"))
                self.assertTrue(str(p.get("label") or "").startswith("Coin="))

        dq = out.get("data_quality") or {}
        self.assertIn("rows_analysed", dq)
        self.assertIn("duplicate_patterns_removed", dq)
        self.assertEqual(out.get("stage"), "S62.3.1")

        # Ranked lists should not contain Hour=… + Coin=… when Coin=… + Hour=… exists
        life = (out.get("universes") or {})["lifetime"]
        labels = [c.get("label") for c in (life.get("top_best") or [])]
        for lab in labels:
            if lab and lab.startswith("Hour=") and " + Coin=" in lab:
                self.fail(f"non-canonical label in top_best: {lab}")

    def test_consolidate_permutations(self) -> None:
        a = {
            "dims": ["coin", "hour"],
            "values": ["BTC", "H22"],
            "key": "coin=BTC|hour=H22",
            "label": "Coin=BTC + Hour=H22",
            "metrics": {"trades": 80, "profit_factor": 2.0, "expectancy": 0.1},
            "stability": 75,
        }
        b = {
            "dims": ["hour", "coin"],
            "values": ["H22", "BTC"],
            "key": "hour=H22|coin=BTC",
            "label": "Hour=H22 + Coin=BTC",
            "metrics": {"trades": 80, "profit_factor": 2.0, "expectancy": 0.1},
            "stability": 75,
        }
        out = s623.consolidate_patterns([a, b])
        self.assertEqual(out["duplicates_removed"], 1)
        self.assertEqual(out["unique_retained"], 1)
        kept = out["patterns"][0]
        self.assertEqual(kept["label"], "Coin=BTC + Hour=H22")
        self.assertEqual(kept["dims"], ["coin", "hour"])


    def test_cli_registered(self) -> None:
        import subprocess
        import sys

        proc = subprocess.run(
            [sys.executable, "-m", "bot.research.market_events", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("discover-patterns", proc.stdout)
        self.assertIn("--last-trades", proc.stdout)


if __name__ == "__main__":
    unittest.main()
