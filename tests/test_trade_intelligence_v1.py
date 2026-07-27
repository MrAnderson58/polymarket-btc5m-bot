"""Trade Intelligence V1 — knowledge layer foundation tests."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import LIVE_SCHEMA_VERSION, apply_migrations
from bot.research.market_events.event_types import SHOCK_DIRECTION_DOWN
from bot.research.market_events.trade_intelligence.cli import run_trade_cli
from bot.research.market_events.trade_intelligence.importers.csv_import import import_csv_trades
from bot.research.market_events.trade_intelligence.importers.manual import import_manual_trade
from bot.research.market_events.trade_intelligence.importers.paper import import_paper_trades
from bot.research.market_events.trade_intelligence.knowledge import build_trade_knowledge
from bot.research.market_events.trade_intelligence.models import MarketSnapshot, NewsItem
from bot.research.market_events.trade_intelligence.repository import TradeRepository
from bot.research.market_events.trade_intelligence.schema import ensure_trade_intelligence_schema


class TestTradeIntelligenceV1(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        configure_unit_test_db_isolation(Path(self.tmp.name) / "ti.db")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed_paper(self) -> None:
        now = int(time.time())
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.execute(
                """
                INSERT INTO market_events (
                  event_ts, detected_ts, venue, symbol, direction, phase,
                  trigger_window_seconds, return_pct, classification,
                  detector_version, detector_triggers_json, dedup_key, created_at
                ) VALUES (?, ?, 'bybit', 'SOL', ?, 'SHOCK_DETECTED',
                  60, -2.0, 'ASSET_SPECIFIC', 'v1', '["SHOCK_A"]', ?, ?)
                """,
                (now - 100, now - 100, SHOCK_DIRECTION_DOWN, f"dedup-ti-{now}", now - 100),
            )
            eid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            for i, net in enumerate((1.5, -0.5)):
                conn.execute(
                    """
                    INSERT INTO paper_strategy_runs (
                      event_id, strategy_name, strategy_version, reversal_variant,
                      exit_variant, eligibility, entry_ts, entry_price, exit_ts,
                      exit_price, gross_return, net_return, fee_bps, slippage_bps,
                      duration_seconds, created_at
                    ) VALUES (?, ?, 'v1', 'R1', 'EXIT_A', 1, ?, 100.0, ?, 101.0,
                              ?, ?, 10, 10, 60, ?)
                    """,
                    (
                        eid,
                        f"REVERSAL_R1_EXIT_A_{i}",
                        now - 200 + i,
                        now - 100 + i,
                        net + 0.4,
                        net,
                        now,
                    ),
                )
            conn.commit()

    def test_live_schema_includes_ti_v1(self) -> None:
        self.assertGreaterEqual(LIVE_SCHEMA_VERSION, 66)
        with market_events_connection() as conn:
            apply_migrations(conn)
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ti_%'"
                ).fetchall()
            }
        for name in (
            "ti_trades",
            "ti_market_snapshots",
            "ti_news",
            "ti_telegram",
            "ti_context",
            "ti_outcomes",
            "ti_ai_summaries",
            "ti_tags",
            "ti_notes",
        ):
            self.assertIn(name, tables)

    def test_manual_csv_paper_and_envelope(self) -> None:
        self._seed_paper()
        csv_path = Path(self.tmp.name) / "trades.csv"
        csv_path.write_text(
            "symbol,side,entry_ts,exit_ts,pnl_usd,pnl_pct,strategy\n"
            "BTC,LONG,1700000000,1700000600,12.5,1.2,manual_csv\n",
            encoding="utf-8",
        )
        with market_events_connection() as conn:
            apply_migrations(conn)
            paper = import_paper_trades(conn)
            self.assertEqual(paper["created"], 2)
            csv_stats = import_csv_trades(conn, csv_path)
            self.assertEqual(csv_stats["created"], 1)
            tid = import_manual_trade(
                conn,
                symbol="ETH",
                side="SHORT",
                note="test note",
                pnl_usd=-3.0,
                exit_ts=int(time.time()),
            )
            repo = TradeRepository(conn)
            repo.add_market_snapshot(
                MarketSnapshot(trade_id=tid, snapshot_ts=int(time.time()), price=3000.0),
            )
            repo.add_news(
                NewsItem(trade_id=tid, news_ts=int(time.time()), title="headline", summary="s"),
            )
            k = build_trade_knowledge(repo, tid)
            assert k is not None
            self.assertEqual(k.trade.source, "manual")
            self.assertIsNotNone(k.ai_summary)
            self.assertEqual(k.ai_summary.summary_text, "")
            self.assertTrue(any(t.tag == "manual" for t in k.tags))
            self.assertEqual(len(k.notes), 1)
            self.assertEqual(len(k.market_snapshots), 1)
            self.assertEqual(len(k.news), 1)
            self.assertIsNotNone(k.outcome)
            listing = run_trade_cli(conn, action="list", limit=20)
            self.assertIn("TRADE INTELLIGENCE — LIST", listing)
            report = run_trade_cli(conn, action="report", trade_id=tid)
            self.assertIn("placeholder", report)
            similar = run_trade_cli(conn, action="similar", trade_id=tid, limit=5)
            self.assertIn("SIMILAR", similar)
            conn.commit()

    def test_idempotent_schema(self) -> None:
        with market_events_connection() as conn:
            ensure_trade_intelligence_schema(conn)
            ensure_trade_intelligence_schema(conn)
            n = conn.execute("SELECT COUNT(*) AS n FROM ti_trades").fetchone()["n"]
            self.assertEqual(n, 0)


class TestTradeCliArgv(unittest.TestCase):
    def test_normalize_trade_argv(self) -> None:
        from bot.research.market_events.__main__ import _normalize_trade_argv

        self.assertEqual(_normalize_trade_argv(["trade", "import", "--source", "paper"]), ["trade-import", "--source", "paper"])
        self.assertEqual(_normalize_trade_argv(["trade", "list"]), ["trade-list"])
        self.assertEqual(_normalize_trade_argv(["trade", "report"]), ["trade-report"])
        self.assertEqual(_normalize_trade_argv(["trade", "similar", "--trade-id", "1"]), ["trade-similar", "--trade-id", "1"])
        self.assertEqual(_normalize_trade_argv(["performance"]), ["performance"])


if __name__ == "__main__":
    unittest.main()
