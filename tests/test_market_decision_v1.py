"""Tests for Market Decision Engine V1."""

from __future__ import annotations

import unittest
from unittest import mock

from bot.research.market_events.signal_intelligence.market_decision_v1.decide import (
    decide_one,
    format_decision,
)
from bot.research.market_events.signal_intelligence.market_decision_v1.replay import (
    run_decision_replay,
)
from bot.research.market_events.signal_intelligence.market_decision_v1.report import (
    format_terminal,
)


def _ctx_minimal(n: int = 40) -> dict:
    trades = []
    for i in range(n):
        trades.append({
            "trade_id": i + 1,
            "symbol": "BTC",
            "direction": "LONG" if i % 2 == 0 else "SHORT",
            "opened_at": 1_700_000_000 + i * 300,
            "closed_at": 1_700_000_000 + i * 300 + 600,
            "pnl": 1.0 if i % 3 else -0.5,
            "rsi": 40,
            "funding": 0.0,
        })
    return {
        "ok": True,
        "trades": trades,
        "fp_index": {"ok": False},
        "fp_by_id": {},
        "fp_assignments": {},
        "tl_by_id": {},
        "tl_rows": [],
        "tl_chains": [],
        "enriched_by_id": {t["trade_id"]: t for t in trades},
        "dna_preds": [],
        "ready_preds": [],
        "block_preds": [],
        "edges": [],
        "replays": {},
        "causality": {},
        "optimizer_state": {},
        "experiments": [],
    }


class TestDecide(unittest.TestCase):
    def test_no_trade_when_modules_empty(self):
        ctx = _ctx_minimal()
        out = decide_one(ctx, ctx["trades"][0])
        self.assertEqual(out["decision"], "NO TRADE")
        self.assertTrue(out.get("why"))
        text = format_decision(out)
        self.assertIn("NO TRADE", text)
        self.assertIn("RESEARCH ONLY", text)

    def test_replay_shapes(self):
        ctx = _ctx_minimal(30)
        # Patch decide_one to approve every other matching direction trade
        calls = {"i": 0}

        def fake_decide(ctx, trade):
            calls["i"] += 1
            d = trade["direction"]
            keep = calls["i"] % 4 == 0
            return {
                "trade_id": trade["trade_id"],
                "decision": "TRADE" if keep else "NO TRADE",
                "direction": d if keep else None,
                "confidence": 0.9 if keep else 0.2,
                "why": ["ok"] if keep else ["no"],
            }

        with mock.patch(
            "bot.research.market_events.signal_intelligence.market_decision_v1.replay.decide_one",
            side_effect=fake_decide,
        ):
            rep = run_decision_replay(ctx)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["n_production"], 30)
        self.assertGreater(rep["skipped"], 0)
        self.assertIn("pf", rep["production"])
        self.assertIn("pf", rep["decision_engine"])
        term = format_terminal({**rep, "elapsed_sec": 1.0, "sample_decision": fake_decide(ctx, ctx["trades"][0])})
        self.assertIn("Production", term)
        self.assertIn("Decision Engine", term)
        self.assertIn("Skipped", term)


if __name__ == "__main__":
    unittest.main()
