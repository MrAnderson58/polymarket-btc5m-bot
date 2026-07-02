"""Tests for Trading Brain v1."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.database import connect, init_db
from bot.report.memory import set_reports_root
from bot.trading_brain.context import build_decision_context, build_explainability_context
from bot.trading_brain.knowledge import discover_causal_knowledge, persist_knowledge
from bot.trading_brain.learning import load_trade_context, run_brain_learning
from bot.trading_brain.memory import load_memory, sync_memory_engine
from bot.trading_brain.report import build_brain_report
from bot.trading_brain.similarity import SimilarityEngineV2
from tests.ai_agent_helpers import seed_trades


class TradingBrainTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.db"
        self.reports_dir = Path(self._tmpdir.name) / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        set_reports_root(self.reports_dir)
        init_db(self.db_path)

    def tearDown(self) -> None:
        set_reports_root(None)
        self._tmpdir.cleanup()

    def test_memory_engine_syncs_trades(self) -> None:
        with connect(self.db_path) as conn:
            seed_trades(conn, 3)
            conn.commit()
            counts = sync_memory_engine(conn)
            conn.commit()
            rows = load_memory(conn, category="trade")
        self.assertEqual(counts["trade"], 3)
        self.assertEqual(len(rows), 3)

    def test_similarity_engine_v2(self) -> None:
        historical = [
            {
                "entry_price": 0.38,
                "btc_move_30s": 2.0,
                "btc_acceleration": 1.0,
                "spread": 0.01,
                "seconds_from_start": 40,
                "distance_to_strike": 5.0,
                "volatility": 0.02,
                "market_regime": "Range",
                "strategy_name": "NO_C",
                "btc_direction": "flat",
                "entry_bucket": "0.38",
                "holding_bucket": "short",
                "side": "NO",
                "outcome": "win",
                "pnl": 10.0,
                "mae": -2.0,
                "mfe": 12.0,
            }
            for _ in range(12)
        ]
        engine = SimilarityEngineV2(k=5)
        engine.fit(historical)
        signal = {**historical[0], "pnl": None, "outcome": "unknown"}
        stats = engine.query(signal, k=5)
        self.assertEqual(stats["engine"], "v2")
        self.assertGreaterEqual(stats["similar_count"], 1)
        self.assertGreater(stats["win_rate"], 0.9)

    def test_knowledge_discovery_and_context(self) -> None:
        rows = []
        for i in range(20):
            rows.append(
                {
                    "market_regime": "Range" if i % 2 == 0 else "Panic",
                    "entry_price": 0.38,
                    "btc_direction": "flat",
                    "spread": 0.01 if i % 2 == 0 else 0.03,
                    "btc_move_30s": 2.0,
                    "pnl": 8.0 if i % 2 == 0 else -6.0,
                }
            )
        links = discover_causal_knowledge(rows)
        self.assertTrue(links)

        similar = {
            "similar_count": 40,
            "win_rate": 0.6,
            "profit_factor": 1.8,
            "avg_pnl": 2.5,
            "avg_mae": -3.0,
            "avg_mfe": 5.0,
            "top_regime": "Range",
            "engine": "v2",
        }
        knowledge = [
            {
                "feature": "market_regime",
                "condition": "regime=Range",
                "effect": 3.2,
                "direction": "positive",
                "confidence": 75.0,
            }
        ]
        features = {
            "strategy_name": "NO_C",
            "side": "NO",
            "entry_price": 0.38,
            "market_regime": "Range",
            "btc_move_30s": 2.0,
            "btc_acceleration": 1.0,
            "btc_direction": "flat",
            "spread": 0.01,
            "seconds_from_start": 40,
            "distance_to_strike": 5.0,
            "volatility": 0.02,
            "entry_bucket": "0.38",
            "holding_bucket": "short",
        }
        ctx = build_decision_context(
            trade_id=1,
            features=features,
            similar=similar,
            knowledge=knowledge,
            memory_refs={"trade_memory_count": 10},
        )
        self.assertTrue(ctx["observe_only"])
        self.assertNotIn("decision", ctx)

        expl = build_explainability_context(features, similar, knowledge)
        self.assertIn("supportive_reasons", expl)
        self.assertIn("hint_allow_if", expl)

        with connect(self.db_path) as conn:
            n = persist_knowledge(conn, links)
            conn.commit()
        self.assertGreater(n, 0)

    def test_run_brain_learning(self) -> None:
        with connect(self.db_path) as conn:
            seed_trades(conn, 5)
            conn.commit()
            summary = run_brain_learning(conn)
            conn.commit()
            first_trade_id = conn.execute(
                "SELECT MIN(trade_id) AS tid FROM brain_trade_context"
            ).fetchone()["tid"]
            ctx = load_trade_context(conn, int(first_trade_id))
            report = build_brain_report(conn)

        self.assertEqual(summary["trades_processed"], 5)
        self.assertEqual(summary["contexts_built"], 5)
        self.assertIn("knowledge_links", summary)
        self.assertIsNotNone(ctx)
        self.assertIn("decision_context", ctx)
        self.assertIn("explainability", ctx)
        self.assertEqual(report["contexts_stored"], 5)
        self.assertEqual(report["version"], "1.0")

    def test_brain_wired_into_ai_learning(self) -> None:
        state_path = Path(self._tmpdir.name) / "ai_agent_state.json"
        with mock.patch("bot.ai_agent.learning._state_path", return_value=state_path):
            with mock.patch(
                "bot.ai_agent.journal.journal_root",
                return_value=Path(self._tmpdir.name) / "ai_journal",
            ):
                from bot.ai_agent.learning import run_daily_learning

                with connect(self.db_path) as conn:
                    seed_trades(conn, 3)
                    conn.commit()
                    summary = run_daily_learning(conn)
                    row = conn.execute(
                        "SELECT COUNT(*) AS n FROM brain_trade_context"
                    ).fetchone()
        self.assertIn("brain", summary)
        self.assertEqual(summary["brain"]["trades_processed"], 3)
        self.assertEqual(row["n"], 3)


if __name__ == "__main__":
    unittest.main()
