"""Tests for Counterfactual Engine."""

from __future__ import annotations

import unittest

from bot.ai_agent.counterfactual import build_counterfactual_matrix, classify_counterfactual


class _Row:
    def __init__(self, decision: str, pnl: float) -> None:
        self._data = {"decision": decision, "pnl": pnl}

    def __getitem__(self, key: str):
        return self._data[key]


class CounterfactualTestCase(unittest.TestCase):
    def test_classify(self) -> None:
        self.assertEqual(classify_counterfactual("ALLOW", 5), "true_allow")
        self.assertEqual(classify_counterfactual("ALLOW", -5), "false_allow")
        self.assertEqual(classify_counterfactual("SKIP", -5), "true_skip")
        self.assertEqual(classify_counterfactual("SKIP", 5), "false_skip")

    def test_matrix(self) -> None:
        rows = [
            _Row("ALLOW", 10),
            _Row("ALLOW", -5),
            _Row("SKIP", -3),
            _Row("SKIP", 8),
        ]
        m = build_counterfactual_matrix(rows)
        self.assertEqual(m["matrix"]["TP"], 1)
        self.assertEqual(m["matrix"]["FP"], 1)
        self.assertEqual(m["matrix"]["TN"], 1)
        self.assertEqual(m["matrix"]["FN"], 1)
