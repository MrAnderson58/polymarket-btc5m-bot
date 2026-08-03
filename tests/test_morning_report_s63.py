"""S63 Morning Trading Report tests."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence import morning_report_s63 as s63
from bot.research.market_events.signal_intelligence.research_lake_v1.schema import (
    ensure_research_lake_schema,
)
from tests.research_db_helpers import ensure_research_schema


class TestMorningReportS63(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "s63.db"
        configure_unit_test_db_isolation(self.db_path)
        self.now = int(time.time())
        self.out = Path(self.tmp.name) / "morning"
        with market_events_connection() as conn:
            apply_migrations(conn)
            ensure_research_lake_schema(conn)
            conn.commit()
        ensure_research_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed_analytics(self, conn, *, n: int = 80) -> None:
        ensure_research_lake_schema(conn)
        for i in range(n):
            ts = self.now - (i % 20) * 1800  # within ~10h
            direction = "LONG" if i % 2 == 0 else "SHORT"
            pnl = 1.0 if direction == "SHORT" else -0.4
            conn.execute(
                """
                INSERT INTO market_events_paper_trades_s42 (
                  s40_signal_type, s40_signal_id, symbol, direction, entry,
                  status, result, pnl_usd, pnl_pct, created_at, closed_at, updated_at,
                  capital_usd, leverage
                ) VALUES (?, ?, ?, ?, 100, 'CLOSED', ?, ?, ?, ?, ?, ?, 100, 1)
                """,
                (
                    "hist:er_v2" if i % 2 else "hist:er_v3",
                    i + 1,
                    "BTC" if i % 3 else "ETH",
                    direction,
                    "WIN" if pnl > 0 else "LOSS",
                    pnl,
                    pnl,
                    ts,
                    ts,
                    ts,
                ),
            )
            tid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute(
                """
                INSERT INTO market_events_trade_features_s55 (
                  paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
                  pnl_usd, pnl_pct, result, created_at, closed_at, funding, ai_score,
                  hour, weekday, market_regime
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tid,
                    "hist:er_v2" if i % 2 else "hist:er_v3",
                    i + 1,
                    "BTC" if i % 3 else "ETH",
                    direction,
                    pnl,
                    pnl,
                    "WIN" if pnl > 0 else "LOSS",
                    ts,
                    ts,
                    -0.001 if direction == "LONG" else 0.001,
                    0.6,
                    14 + (i % 4),
                    i % 7,
                    "RANGE" if i % 2 else "WEAK_BEAR",
                ),
            )
            s55_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute(
                """
                INSERT INTO market_events_research_lake_v1 (
                  trade_id, symbol, direction, result, pnl, pnl_pct, status,
                  opened_at, closed_at, dataset_version, feature_version, schema_version,
                  row_hash, updated_at, built_at, s55_id
                ) VALUES (?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, 't', 't', 't', ?, ?, ?, ?)
                """,
                (
                    tid,
                    "BTC" if i % 3 else "ETH",
                    direction,
                    "WIN" if pnl > 0 else "LOSS",
                    pnl,
                    pnl,
                    ts,
                    ts,
                    f"h{tid}",
                    ts,
                    ts,
                    s55_id,
                ),
            )
            # Keep S56 seed for load_lab_trades preference path
            try:
                conn.execute(
                    """
                    INSERT INTO market_events_trade_snapshots_s56 (
                      paper_trade_id, s40_signal_type, s40_signal_id, symbol, direction,
                      entry, exit_price, pnl_usd, pnl_pct, duration_sec, exit_reason,
                      funding, ai_score, hour, weekday, market_regime,
                      created_at, timestamp
                    ) VALUES (?, ?, ?, ?, ?, 100, 101, ?, ?, 200, 'TP1',
                      ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tid,
                        "hist:er_v2" if i % 2 else "hist:er_v3",
                        i + 1,
                        "BTC" if i % 3 else "ETH",
                        direction,
                        pnl,
                        pnl,
                        -0.001 if direction == "LONG" else 0.001,
                        0.6,
                        14 + (i % 4),
                        i % 7,
                        "RANGE" if i % 2 else "WEAK_BEAR",
                        ts,
                        ts,
                    ),
                )
            except Exception:
                pass
        conn.commit()

    def test_morning_report_exports(self) -> None:
        with market_events_connection() as conn:
            self._seed_analytics(conn)
            out = s63.run_morning_report(
                conn,
                now=self.now,
                report_dir=self.out,
                root=Path(self.tmp.name),
                db_path=self.db_path,
                db_source="unit_test",
            )
        self.assertTrue(out.get("ok"))
        self.assertEqual((out.get("db") or {}).get("status"), "OK")
        self.assertEqual((out.get("db") or {}).get("rows"), 80)
        self.assertEqual(out.get("stage"), "S63")
        self.assertIn("system", out)
        self.assertIn("trading", out)
        self.assertGreater((out.get("trading") or {}).get("trades_window", 0), 0)

        summary = s63.format_morning_summary(out)
        self.assertIn("DB\nOK", summary)
        self.assertIn("rows\n80", summary)
        self.assertIn("Expected", summary)
        self.assertIn("Unexpected", summary)
        self.assertIn("Status\n  OK", summary)
        self.assertNotIn("[HIGH] missing_s55=", summary)
        self.assertNotIn("missing_s55=", summary)

        md = Path(out["export_paths"]["markdown"])
        js = Path(out["export_paths"]["json"])
        self.assertTrue(md.exists())
        self.assertTrue(js.exists())

    def test_wrong_database_self_check(self) -> None:
        empty = Path(self.tmp.name) / "wrong.db"
        conn = sqlite3.connect(str(empty))
        out = s63.run_morning_report(
            conn,
            now=self.now,
            report_dir=self.out,
            root=Path(self.tmp.name),
            db_path=empty,
            db_source="wrong",
        )
        conn.close()
        self.assertFalse(out.get("ok"))
        self.assertEqual((out.get("db") or {}).get("status"), "ERROR")
        self.assertEqual((out.get("db") or {}).get("error"), "wrong database")
        text = s63.format_morning_summary(out)
        self.assertIn("ERROR", text)
        self.assertIn("wrong database", text)
        self.assertNotIn("n=0", text)

    def test_cli_uses_analytics_resolver(self) -> None:
        from bot.research.market_events import __main__ as m

        src = Path(m.__file__).read_text(encoding="utf-8")
        # morning-report / rule-health / research-lake-health share the same resolver
        for cmd in ("morning-report", "rule-health", "research-lake-health"):
            self.assertIn(f'args.command == "{cmd}"', src)
        self.assertIn("resolve_research_analytics_sqlite_path", src)
        # morning-report must not open via research_connection()
        block_start = src.index('if args.command == "morning-report":')
        block_end = src.index('if args.command == "pnl-killers":', block_start)
        block = src[block_start:block_end]
        self.assertIn("resolve_research_analytics_sqlite_path", block)
        self.assertIn("research_migrate_then_readonly", block)
        self.assertNotIn("research_connection", block)

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
        self.assertIn("morning-report", proc.stdout)


if __name__ == "__main__":
    unittest.main()
