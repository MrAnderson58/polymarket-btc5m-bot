"""Tests for Edge Discovery Engine V1."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from bot.research.market_events.signal_intelligence.edge_discovery_v1.clustering import (
    cluster_edges,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v1.engine import (
    run_edge_discovery_v1,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v1.metrics import (
    edge_score,
    evaluate_mask,
    max_drawdown,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v1.mining import (
    generate_atoms,
    mine_edges,
)
from bot.research.market_events.signal_intelligence.edge_discovery_v1.report import (
    format_report,
    write_artifacts,
)


def _synth(n: int = 240, seed: int = 21) -> list[dict]:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        rsi = float(rng.uniform(15, 85))
        funding = float(rng.normal(-0.0002 if rsi < 35 else 0.0002, 0.00015))
        oi = float(rng.normal(1.2 if funding < 0 else -0.4, 0.8))
        atr_pct = float(rng.uniform(0.2, 2.5))
        ema = float(rng.normal(-1.0 if rsi < 35 else 0.4, 0.6))
        fear = float(rng.uniform(10, 90))
        trend = float(rng.normal(-0.4 if rsi < 35 else 0.3, 0.7))
        edge = rsi < 35 and funding < 0 and oi > 0 and ema < 0
        pnl = float(rng.normal(1.4 if edge else -0.35, 0.4))
        rows.append({
            "pnl": pnl,
            "pnl_pct": pnl,
            "rsi": rsi,
            "ema20_distance": ema,
            "atr_pct": atr_pct,
            "funding": funding,
            "oi_delta": oi,
            "fear_greed": fear,
            "trend": trend,
            "hour": float(i % 24),
            "weekday": str(i % 7),
            "direction": "LONG" if i % 2 == 0 else "SHORT",
            "gate_decision": "PASS" if edge or i % 3 else "REJECT",
            "symbol": "BTC" if i % 2 else "ETH",
            "closed_at": 1_700_000_000 + i * 3600,
        })
    return rows


class TestEdgeMetrics(unittest.TestCase):
    def test_max_dd_and_score(self) -> None:
        self.assertLess(max_drawdown([1, -2, 0.5, -1]) or 0, 0)
        s = edge_score({
            "pf": 1.8, "expectancy": 0.5, "winrate": 58, "n": 120,
            "stability": 0.7, "p_value": 0.02, "oos_ok": True, "wf_ok": True,
        })
        self.assertGreater(s, 20)
        self.assertLessEqual(s, 100)

    def test_evaluate_mask(self) -> None:
        rows = _synth(120)
        mask = [True] * 40 + [False] * 80
        m = evaluate_mask(rows, mask, [r["pnl"] for r in rows], n_boot=30, n_perm=20)
        self.assertEqual(m["n"], 40)
        self.assertIn("ci95", m)


class TestEdgeMining(unittest.TestCase):
    def test_atoms_and_mine(self) -> None:
        rows = _synth(220)
        atoms = generate_atoms(rows)
        self.assertGreater(len(atoms), 10)
        out = mine_edges(rows, min_n=12, research_min_n=100)
        self.assertEqual(out["n_rows"], 220)
        self.assertGreater(out["n_combos_tested"], 50)
        self.assertIn("baseline", out)
        # Should find some TEST edges on synthetic edge structure
        self.assertGreaterEqual(len(out["candidates"]), 1)
        clustered = cluster_edges(list(out["candidates"]))
        self.assertIn("clusters", clustered)

    def test_end_to_end_artifacts(self) -> None:
        rows = _synth(200)
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.edge_discovery_v1.engine.load_edge_dataset",
                return_value=(rows, {"closed_s42": 200, "s55": 200, "matched": 200}),
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.edge_discovery_v1.report.OUT_DIR",
                td_path / "out",
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.edge_discovery_v1.report.REPORT_MD",
                td_path / "EDGE_DISCOVERY_REPORT.md",
            ):
                out = run_edge_discovery_v1(conn=None, write_reports=True, min_n=12)
            self.assertTrue(out["ok"])
            self.assertTrue(out["gate_unchanged"])
            self.assertTrue(out["optimizer_unchanged"])
            self.assertTrue(out["paper_unchanged"])
            self.assertTrue(Path(out["paths"]["report_md"]).exists())
            self.assertTrue((td_path / "out" / "edge_rules.json").exists())
            self.assertTrue((td_path / "out" / "edge_scoreboard.json").exists())
            self.assertTrue((td_path / "out" / "edge_clusters.json").exists())
            self.assertTrue((td_path / "out" / "edge_heatmaps.md").exists())
            self.assertIn("EDGE_DISCOVERY_REPORT", out["report_markdown"])
            md = format_report(out)
            self.assertIn("TOP-100", md)

    def test_cli_registered(self) -> None:
        from bot.research.market_events import __main__ as m

        src = Path(m.__file__).read_text(encoding="utf-8")
        self.assertIn('"edge-discovery"', src)
        self.assertIn("run_edge_discovery_v1", src)


if __name__ == "__main__":
    unittest.main()
