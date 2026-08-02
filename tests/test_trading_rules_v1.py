"""Tests for Trading Rules Extraction V1."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.signal_intelligence.trading_dna_v1.setups import DNARule
from bot.research.market_events.signal_intelligence.trading_rules_v1.engine import (
    run_trading_rules_v1,
)
from bot.research.market_events.signal_intelligence.trading_rules_v1.format_out import (
    format_terminal,
)
from bot.research.market_events.signal_intelligence.trading_rules_v1.minimize import (
    dedupe_setups,
    minimize_conditions,
)
from bot.research.market_events.signal_intelligence.trading_rules_v1.parse import (
    canonicalize,
    is_local_condition,
    rule_from_setup,
    split_conditions,
)
from bot.research.market_events.signal_intelligence.trading_rules_v1.validate import (
    universality,
    validate_rule,
)


def _rows(n: int = 600) -> list[dict]:
    rows = []
    syms = ["BTC", "ETH", "SOL", "XRP", "LINK", "OP"]
    for i in range(n):
        # Strong edge when ADX high + MACD+ + EMA bull + low ATR
        adx = 30.0 if i % 3 == 0 else 15.0
        macd = 0.5 if i % 3 == 0 else -0.4
        ema_bull = i % 3 == 0
        atr = 0.3 if i % 3 == 0 else 1.2
        good = adx > 22.5 and macd > 0 and ema_bull and atr < 0.6
        pnl = 1.2 if good else (-0.8 if i % 5 == 0 else 0.1)
        if i % 17 == 0:
            pnl = -2.0  # some losses even in good
        rows.append({
            "symbol": syms[i % len(syms)],
            "direction": "LONG" if i % 2 == 0 else "SHORT",
            "pnl": pnl,
            "rsi": 40 + i % 20,
            "atr_pct": atr,
            "adx": adx,
            "macd": macd,
            "ema20": 110 if ema_bull else 90,
            "ema50": 100,
            "hour": i % 24,
            "weekday": i % 7,
            "gate": "INSUFFICIENT_HISTORY" if i < 50 and not good else "PASS",
            "regime": "RANGE",
            "pattern": "g31",
            "confidence": 6.0 if good else 4.0,
            "funding_sign": "+",
            "oi_sign": "+",
            "candle_hit": True,
        })
    return rows


def _atomics() -> list[DNARule]:
    return [
        DNARule("adx", "ADX>22.5", ("adx",), lambda r: float(r.get("adx") or 0) > 22.5),
        DNARule("macd", "MACD+", ("macd",), lambda r: float(r.get("macd") or 0) > 0),
        DNARule("ema", "EMA20>EMA50", ("ema20", "ema50"),
                lambda r: float(r.get("ema20") or 0) > float(r.get("ema50") or 0)),
        DNARule("atr", "ATR%<0.6", ("atr_pct",), lambda r: float(r.get("atr_pct") or 99) < 0.6),
        DNARule("gate", "Gate=INSUFFICIENT_HISTORY", ("gate",),
                lambda r: str(r.get("gate")) == "INSUFFICIENT_HISTORY"),
        DNARule("coin", "Coin=BTC", ("symbol",), lambda r: str(r.get("symbol")) == "BTC"),
        DNARule("hour", "Hour=10", ("hour",), lambda r: r.get("hour") == 10),
    ]


class TestParse(unittest.TestCase):
    def test_split(self) -> None:
        self.assertEqual(split_conditions("A + B + C"), ["A", "B", "C"])

    def test_canon(self) -> None:
        self.assertEqual(canonicalize("MACD+"), "MACD>0")
        self.assertEqual(canonicalize("ATR%<0.6"), "ATR<0.6")

    def test_local(self) -> None:
        self.assertTrue(is_local_condition("Coin=ETH"))
        self.assertTrue(is_local_condition("Hour=19"))
        self.assertFalse(is_local_condition("ADX>22.5"))

    def test_rule_from_setup(self) -> None:
        r = rule_from_setup("EMA20>EMA50 + MACD+")
        self.assertIn("MACD>0", r["conditions"])


class TestValidate(unittest.TestCase):
    def test_universality_multi(self) -> None:
        u = universality(_rows(100))
        self.assertTrue(u["n_symbols"] >= 3)

    def test_universality_single_coin(self) -> None:
        rows = [{**r, "symbol": "BTC"} for r in _rows(40)]
        u = universality(rows)
        self.assertFalse(u["ok"])

    def test_validate_ready(self) -> None:
        rows = _rows(700)
        val = validate_rule(
            rows,
            ["ADX>22.5", "MACD>0", "EMA20>EMA50", "ATR<0.6"],
            atomics=_atomics(),
            raw_conditions=["ADX>22.5", "MACD+", "EMA20>EMA50", "ATR%<0.6"],
            min_n=100,
            kind="ready",
        )
        self.assertIsNotNone(val)
        assert val is not None
        self.assertGreaterEqual(val["n"], 100)
        self.assertIn("ci_lo", val)

    def test_validate_block(self) -> None:
        rows = _rows(200)
        val = validate_rule(
            rows,
            ["Gate=INSUFFICIENT_HISTORY"],
            atomics=_atomics(),
            raw_conditions=["Gate=INSUFFICIENT_HISTORY"],
            min_n=20,
            kind="block",
        )
        self.assertIsNotNone(val)
        assert val is not None
        self.assertTrue(val.get("block_ok") or float(val.get("ev") or 0) < 0)


class TestMinimize(unittest.TestCase):
    def test_dedupe(self) -> None:
        setups = [
            {"setup": "A + B", "pf": 2},
            {"setup": "B + A", "pf": 3},
            {"setup": "A + C", "pf": 2},
        ]
        d = dedupe_setups(setups)
        self.assertEqual(len(d), 2)

    def test_minimize(self) -> None:
        rows = _rows(700)
        out = minimize_conditions(
            rows,
            ["ADX>22.5", "MACD>0", "EMA20>EMA50", "ATR<0.6"],
            atomics=_atomics(),
            raw_conditions=["ADX>22.5", "MACD+", "EMA20>EMA50", "ATR%<0.6"],
            min_n=80,
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertGreaterEqual(len(out["conditions"]), 1)


class TestFormat(unittest.TestCase):
    def test_terminal_shape(self) -> None:
        text = format_terminal({
            "ready_for_paper": [{
                "conditions": ["EMA20>EMA50", "ADX>22.5", "MACD>0", "ATR<0.6"],
                "n": 2790, "wr": 55.0, "pf": 64.0, "ev": 0.95,
                "ci_lo": 0.7, "ci_hi": 1.2, "confidence": 0.53,
                "passes_min_n": True, "min_n": 500,
            }],
            "hard_block": [{
                "conditions": ["Gate=INSUFFICIENT_HISTORY"],
                "n": 45, "wr": 10.0, "pf": 0.37, "ev": -28.0,
                "ci_lo": -40.0, "ci_hi": -10.0,
            }],
        })
        self.assertIn("READY FOR PAPER", text)
        self.assertIn("HARD BLOCK", text)
        self.assertIn("EMA20>EMA50", text)
        self.assertIn("Gate=INSUFFICIENT_HISTORY", text)
        self.assertNotIn("{", text.split("\n")[0])


class TestEngine(unittest.TestCase):
    def test_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite3.connect(str(Path(td) / "e.db"))
            out = run_trading_rules_v1(conn, write_reports=False, limit=5)
            self.assertFalse(out["ok"])
            self.assertTrue(out["no_new_features"])
            conn.close()

    def test_mocked(self) -> None:
        rows = [
            {
                "trade_id": i + 1,
                "symbol": ["BTC", "ETH", "SOL"][i % 3],
                "direction": "LONG",
                "pnl": 1.0 if i % 4 else -0.5,
                "opened_at": 1_700_000_000 + i * 300,
                "confidence": 0.5,
                "features_json": "{}",
            }
            for i in range(120)
        ]
        with tempfile.TemporaryDirectory() as td:
            conn = sqlite3.connect(str(Path(td) / "m.db"))
            with mock.patch(
                "bot.research.market_events.signal_intelligence.trading_rules_v1.engine._load_trades",
                return_value=(rows, {"source": "mock"}),
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.trading_dna_v1.features.load_candle_book",
                return_value={},
            ):
                out = run_trading_rules_v1(conn, write_reports=False, min_n=30)
            self.assertTrue(out["ok"])
            self.assertIn("terminal", out)
            self.assertIn("READY FOR PAPER", out["terminal"])
            self.assertLessEqual(len(out["ready_for_paper"]), 10)
            self.assertLessEqual(len(out["hard_block"]), 10)
            conn.close()


class TestCli(unittest.TestCase):
    def test_help(self) -> None:
        import subprocess
        import sys

        r = subprocess.run(
            [sys.executable, "-m", "bot.research.market_events", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("trading-rules", r.stdout + r.stderr)
        self.assertIn("rule-health", r.stdout + r.stderr)


class TestPfAndStability(unittest.TestCase):
    def test_pf_formula(self) -> None:
        from bot.research.market_events.signal_intelligence.trading_rules_v1.pf_verify import (
            verify_profit_factor,
        )

        out = verify_profit_factor([1.0, 2.0, -1.0, -0.5, 0.0])
        self.assertTrue(out["ok"])
        self.assertEqual(out["gross_profit"], 3.0)
        self.assertEqual(out["gross_loss"], 1.5)
        self.assertEqual(out["pf"], 2.0)
        self.assertEqual(out["duplicates"], 0)

    def test_stability_paper_ready(self) -> None:
        from bot.research.market_events.signal_intelligence.trading_rules_v1.stability import (
            monthly_stability,
        )

        rows = []
        # 4 consecutive months of stable edge
        for mi, month_base in enumerate([1_700_000_000, 1_702_800_000, 1_705_400_000, 1_708_000_000]):
            for i in range(50):
                rows.append({
                    "pnl": 1.0 if i % 3 else -0.4,
                    "closed_at": month_base + i * 60,
                })
        stab = monthly_stability(rows, pred=lambda r: True, min_n=40, min_pf=1.1)
        self.assertEqual(stab["status"], "PAPER_READY")
        self.assertGreaterEqual(stab["stable_streak"], 3)

    def test_stability_research_only(self) -> None:
        from bot.research.market_events.signal_intelligence.trading_rules_v1.stability import (
            monthly_stability,
        )

        rows = [{"pnl": -1.0, "closed_at": 1_700_000_000 + i} for i in range(100)]
        stab = monthly_stability(rows, pred=lambda r: True, min_n=40, min_pf=1.1)
        self.assertEqual(stab["status"], "RESEARCH_ONLY")


if __name__ == "__main__":
    unittest.main()
