"""Market Mathematics Research V1 — metrics, studies, reports (20+)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from bot.research.market_events.signal_intelligence.market_math_v1.engine import (
    format_market_math_report,
    run_market_math_research,
    write_market_math_artifacts,
)
from bot.research.market_events.signal_intelligence.market_math_v1.metrics import (
    effective_pf,
    rich_metrics,
    significance_ok,
)
from bot.research.market_events.signal_intelligence.market_math_v1.studies import (
    study_explainable_tree,
    study_feature_buckets,
    study_feature_pairs,
    study_feature_triples,
    study_gate_contribution,
    study_harmful_regimes,
    study_symbol_rankings,
    study_top_rules,
)


def _synth(n: int = 200, seed: int = 11) -> list[dict]:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        rsi = float(rng.uniform(15, 85))
        funding = float(rng.normal(-0.0001 if rsi < 40 else 0.0002, 0.0002))
        oi = float(rng.normal(1.0 if funding < 0 else -0.5, 1.0))
        atr_pct = float(rng.uniform(0.1, 2.0))
        edge = rsi < 40 and funding < 0 and oi > 0
        pnl = float(rng.normal(1.5 if edge else -0.4, 0.35))
        rows.append({
            "pnl": pnl,
            "pnl_pct": pnl,
            "rsi": rsi,
            "atr": atr_pct * 100,
            "atr_pct": atr_pct,
            "ema20_distance": float(rng.normal(-1 if edge else 0.5, 0.5)),
            "ema50_distance": float(rng.normal(-1, 0.5)),
            "ema200_distance": float(rng.normal(-1, 0.7)),
            "vwap_distance": float(rng.normal(-0.5 if edge else 0.2, 0.4)),
            "funding": funding,
            "funding_delta": funding * 0.1,
            "oi": 1e6,
            "oi_delta": oi,
            "fear_greed": float(rng.uniform(10, 90)),
            "adx": float(rng.uniform(10, 50)),
            "macd": float(rng.normal(0, 1)),
            "macd_hist": float(rng.normal(-0.2 if edge else 0.1, 0.5)),
            "trend": float(rng.normal(-0.5 if edge else 0.2, 0.8)),
            "stoch_k": float(rng.uniform(5, 95)),
            "stoch_d": float(rng.uniform(5, 95)),
            "direction": "LONG" if i % 2 == 0 else "SHORT",
            "symbol": "BTC" if i % 3 else "ETH",
            "mfe_pct": float(rng.uniform(0, 3)),
            "mae_pct": float(rng.uniform(-3, 0)),
            "closed_at": 1_700_000_000 + i * 300,
        })
    return rows


class TestMetrics(unittest.TestCase):
    def test_rich_metrics_basic(self) -> None:
        m = rich_metrics([1.0, 1.0, -0.5, 0.5], n_boot=40, seed=1)
        self.assertEqual(m["n"], 4)
        self.assertGreater(m["winrate"], 50)
        self.assertIsNotNone(m["expectancy"])

    def test_pf_and_kelly(self) -> None:
        m = rich_metrics([2.0, 2.0, -1.0], n_boot=30, seed=2)
        self.assertEqual(m["profit_factor"], 4.0)
        self.assertIsNotNone(m["kelly"])

    def test_effective_pf_inf(self) -> None:
        m = rich_metrics([1.0, 2.0], n_boot=10, seed=0)
        self.assertTrue(m.get("pf_inf") or m.get("profit_factor") == "inf")
        self.assertGreater(effective_pf(m), 1)

    def test_significance_helper(self) -> None:
        m = {"n": 30, "p_value": 0.05, "expectancy": 0.5, "ci95": (0.1, 0.9)}
        self.assertTrue(significance_ok(m, min_n=20, alpha=0.1))


class TestStudies(unittest.TestCase):
    def test_feature_buckets(self) -> None:
        out = study_feature_buckets(_synth(180), min_n=5, n_boot=30, n_perm=20)
        self.assertIn("rsi", out["features"])
        self.assertGreater(len(out["features"]["rsi"]), 0)
        self.assertIn("n", out["baseline"])

    def test_pairs(self) -> None:
        out = study_feature_pairs(_synth(180), min_n=8, n_boot=20, n_perm=15, max_results=100)
        self.assertGreater(out["n_tested"], 0)

    def test_triples(self) -> None:
        out = study_feature_triples(_synth(200), min_n=6, n_boot=15, n_perm=10, max_keep=50)
        self.assertIn("triples", out)

    def test_tree(self) -> None:
        out = study_explainable_tree(_synth(220), min_n=12, max_depth=3)
        self.assertIn("tree", out)
        self.assertIn("leaves", out)

    def test_gate(self) -> None:
        out = study_gate_contribution(_synth(160), min_n=8)
        self.assertTrue(out["filters"])
        statuses = {f["status"] for f in out["filters"]}
        self.assertTrue(statuses & {"USEFUL", "USELESS", "HARMFUL"})

    def test_symbols(self) -> None:
        out = study_symbol_rankings(_synth(120), min_n=5)
        self.assertTrue(any(s["symbol"] in ("BTC", "ETH") for s in out))
        self.assertTrue(all("grade" in s for s in out))

    def test_top_and_harmful(self) -> None:
        rows = _synth(200)
        feat = study_feature_buckets(rows, min_n=5, n_boot=20, n_perm=15)
        pairs = study_feature_pairs(rows, min_n=8, n_boot=15, n_perm=10, max_results=80)
        trips = study_feature_triples(rows, min_n=6, n_boot=10, n_perm=8, max_keep=40)
        tree = study_explainable_tree(rows, min_n=10, max_depth=2)
        top = study_top_rules(pairs, trips, feat, tree, top_n=20)
        self.assertLessEqual(len(top), 20)
        harm = study_harmful_regimes(feat, pairs, min_n=5)
        self.assertIn("regimes", harm)


class TestEngine(unittest.TestCase):
    def test_end_to_end(self) -> None:
        rows = _synth(220)
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.market_math_v1.engine.count_corpus",
                return_value={"closed_s42": 220, "s55": 220, "matched": 220},
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.market_math_v1.engine.load_market_math_dataset",
                return_value=rows,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.market_math_v1.engine.OUT_DIR",
                td_path / "out",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.market_math_v1.engine.REPORT_MD",
                td_path / "MARKET_MATHEMATICS_REPORT.md",
            ):
                out = run_market_math_research(conn=None, write_reports=True)
            self.assertTrue(out["ok"])
            self.assertEqual(out["n_trades"], 220)
            self.assertEqual(out["matched_rows"], 220)
            self.assertTrue(out["gate_strategy_paper_optimizer_unchanged"])
            self.assertTrue(Path(out["paths"]["report_md"]).exists())
            self.assertTrue((td_path / "out" / "top_rules.json").exists())
            self.assertTrue((td_path / "out" / "feature_statistics.json").exists())
            self.assertIn("MARKET_MATHEMATICS_REPORT", out["report_markdown"])

    def test_format_report(self) -> None:
        md = format_market_math_report({
            "n_trades": 10,
            "baseline": {"expectancy": 0.1, "profit_factor": 1.2, "winrate": 55, "sharpe": 0.2},
            "top_rules": [{"rule": "rsi<35", "n": 5, "winrate": 60, "expectancy": 0.5,
                           "profit_factor": 1.5, "ci95": (0.1, 0.9), "confidence": 0.4}],
            "gate_contribution": {"filters": [{"filter": "atr", "status": "USEFUL",
                                               "delta_ev": 0.1, "delta_pf": 0.2, "good_rule": "atr<=1"}]},
            "symbol_rankings": [{"symbol": "BTC", "grade": "GOOD", "n": 10, "expectancy": 0.2,
                                 "profit_factor": 1.3, "winrate": 55}],
            "market_regimes": {"regimes": []},
            "recommendations": {"can_remove": [], "can_strengthen": [], "can_weaken": [], "need_check": []},
            "market_map": {"most_stable_features": ["rsi"], "most_useless_features": [], "significant_triples": 0},
            "elapsed_sec": 1.0,
        })
        self.assertIn("TOP rules", md)

    def test_write_artifacts_keys(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            result = {
                "n_trades": 5,
                "baseline": {"n": 5},
                "feature_statistics": {"baseline": {"n": 5}, "features": {}},
                "pair_statistics": {"pairs": []},
                "triple_statistics": {"triples": []},
                "gate_contribution": {"filters": []},
                "market_regimes": {"regimes": []},
                "top_rules": [],
                "symbol_rankings": [],
                "recommendations": {},
                "decision_tree": {},
                "market_map": {},
                "elapsed_sec": 0.1,
            }
            with mock.patch(
                "bot.research.market_events.signal_intelligence.market_math_v1.engine.OUT_DIR",
                td_path,
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.market_math_v1.engine.REPORT_MD",
                td_path / "MARKET_MATHEMATICS_REPORT.md",
            ):
                paths = write_market_math_artifacts(result)
            for key in (
                "feature_statistics.json", "pair_statistics.json", "triple_statistics.json",
                "gate_contribution.json", "market_regimes.json", "top_rules.json",
                "symbol_rankings.json", "recommendations.json",
            ):
                self.assertIn(key, paths)
                self.assertTrue(Path(paths[key]).exists())


class TestLoaderNoLimit50(unittest.TestCase):
    def test_join_loads_all_without_default_limit(self) -> None:
        import sqlite3

        from bot.research.market_events.signal_intelligence.market_math_v1.dataset import (
            JOIN_SQL,
            count_corpus,
            load_all_closed_trade_rows,
        )
        from bot.research.market_events.signal_intelligence.market_math_v1.debug import (
            format_market_math_debug,
        )

        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(
            """
            CREATE TABLE market_events_paper_trades_s42 (
              id INTEGER PRIMARY KEY,
              status TEXT,
              pnl_pct REAL,
              pnl_usd REAL,
              closed_at INTEGER,
              updated_at INTEGER,
              symbol TEXT,
              direction TEXT,
              entry REAL,
              mfe_pct REAL,
              mae_pct REAL
            );
            CREATE TABLE market_events_trade_features_s55 (
              id INTEGER PRIMARY KEY,
              paper_trade_id INTEGER,
              gate_decision TEXT,
              market_regime TEXT,
              ai_score REAL, macro_score REAL, news_score REAL,
              volatility REAL, atr REAL, rsi REAL, funding REAL, oi_delta REAL,
              spread REAL, volume REAL, fear_greed REAL, trend REAL,
              hour REAL, weekday REAL, features_json TEXT,
              mfe_pct REAL, mae_pct REAL, duration_sec INTEGER,
              pnl_usd REAL, pnl_pct REAL
            );
            CREATE VIEW research_dataset AS SELECT 1 AS x WHERE 0;
            """
        )
        n = 73  # deliberately not 50
        for i in range(1, n + 1):
            con.execute(
                "INSERT INTO market_events_paper_trades_s42 "
                "(id, status, pnl_pct, pnl_usd, closed_at, symbol, direction, entry) "
                "VALUES (?, 'CLOSED', ?, ?, ?, 'BTC', 'LONG', 100)",
                (i, 0.1 * (1 if i % 2 else -1), 1.0, 1_700_000_000 + i),
            )
            con.execute(
                "INSERT INTO market_events_trade_features_s55 "
                "(paper_trade_id, atr, rsi, funding, features_json, pnl_pct) "
                "VALUES (?, 1.2, 35, -0.0001, '{}', ?)",
                (i, 0.1),
            )
        # orphan s55 + unmatched closed without features (INNER JOIN drops it)
        con.execute(
            "INSERT INTO market_events_paper_trades_s42 "
            "(id, status, pnl_pct, closed_at, symbol, direction) "
            "VALUES (9999, 'CLOSED', 1.0, 1, 'ETH', 'SHORT')"
        )
        con.execute(
            "INSERT INTO market_events_trade_features_s55 (paper_trade_id, atr) VALUES (NULL, 9.9)"
        )
        con.commit()

        stats = count_corpus(con)
        self.assertEqual(stats["closed_s42"], n + 1)
        self.assertEqual(stats["s55"], n + 1)
        self.assertEqual(stats["matched"], n)
        self.assertIn("INNER JOIN", JOIN_SQL)
        self.assertNotIn("LIMIT", JOIN_SQL)

        rows = load_all_closed_trade_rows(con, print_stats=False)
        self.assertEqual(len(rows), n)

        text = format_market_math_debug(con, db_path=":memory:")
        self.assertIn("Loaded CLOSED trades:", text)
        self.assertIn(str(n + 1), text)
        self.assertIn("Matched rows:", text)
        self.assertIn("market_events_paper_trades_s42", text)
        self.assertIn("feature_store", text)
        self.assertIn("training_dataset", text)


if __name__ == "__main__":
    unittest.main()
