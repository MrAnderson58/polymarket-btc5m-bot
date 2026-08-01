"""Comprehensive tests for Signal Evolution Engine V1 (50+ cases)."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.signal_intelligence.signal_evolution_v1.decay import (
    edge_decay_profile,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.drift import (
    detect_drift,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.engine import (
    run_signal_evolution_v1,
    update_signal,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.library import (
    LIBRARY_TABLE,
    ensure_evolution_schema,
    load_evolution,
    upsert_evolution,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.lifecycle import (
    classify_lifecycle,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.ranking import (
    promotion_recommendation,
    score_signal,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.regimes import (
    REGIME_KEYS,
    regime_adaptation,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.rolling import (
    ROLLING_WINDOWS,
    rolling_edge,
    window_metrics,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.survival import (
    survival_probability,
)
from bot.research.market_events.signal_intelligence.signal_evolution_v1.report import (
    format_decay_md,
    format_drift_md,
    format_evolution_report,
    write_artifacts,
)


def _occ(n: int = 80, *, decaying: bool = False, signal: str = "s40:test") -> list[dict]:
    rows = []
    now = 1_700_000_000.0
    for i in range(n):
        if decaying:
            # early wins, late losses
            pnl = 1.0 if i < n // 3 else -0.8
        else:
            pnl = 1.0 if i % 3 != 0 else -0.5
        rows.append({
            "signal": signal,
            "ts": now + i * 86400.0,
            "pnl": pnl,
            "regime": "BULL" if i % 2 == 0 else "RANGE",
            "direction": "LONG",
            "atr_pct": 2.0 if i % 5 == 0 else 0.8,
            "funding": -0.0002,
            "fear_greed": 40.0 + (i % 10),
            "news_score": 0.4 if i % 7 == 0 else 0.0,
            "rsi": 30.0 + i % 20,
            "oi_delta": 0.5,
        })
    return rows


class TestRolling(unittest.TestCase):
    def test_windows(self) -> None:
        self.assertEqual(ROLLING_WINDOWS, (50, 100, 250, 500, 1000))

    def test_window_metrics(self) -> None:
        m = window_metrics([1.0, -0.5, 1.0, 0.5])
        self.assertEqual(m["n"], 4)
        self.assertGreater(m["wr"], 0.5)
        self.assertIsNotNone(m["ev"])

    def test_window_empty(self) -> None:
        self.assertEqual(window_metrics([])["n"], 0)

    def test_rolling_edge_keys(self) -> None:
        r = rolling_edge([1.0] * 60 + [-0.5] * 20)
        for w in ROLLING_WINDOWS:
            self.assertIn(f"last_{w}", r)
        self.assertIn("all", r)

    def test_sharpe_present(self) -> None:
        m = window_metrics([0.1, -0.05, 0.2, 0.05, -0.02, 0.15] * 5)
        self.assertIsNotNone(m["sharpe"])
        self.assertIsNotNone(m["max_dd"])


class TestDecay(unittest.TestCase):
    def test_decay_profile_fields(self) -> None:
        occ = _occ(120, decaying=True)
        d = edge_decay_profile([o["ts"] for o in occ], [o["pnl"] for o in occ])
        for k in ("edge_today", "edge_30d_ago", "edge_90d_ago", "slope", "half_life_days"):
            self.assertIn(k, d)

    def test_decay_empty(self) -> None:
        d = edge_decay_profile([], [])
        self.assertIsNone(d["slope"])

    def test_decaying_negative_slope(self) -> None:
        occ = _occ(100, decaying=True)
        d = edge_decay_profile([o["ts"] for o in occ], [o["pnl"] for o in occ])
        self.assertTrue(d["slope"] is None or d["slope"] <= 0.05)


class TestSurvival(unittest.TestCase):
    def test_survival_small(self) -> None:
        s = survival_probability([1, 2, 3], [1.0, -1.0, 1.0], window=50)
        self.assertIn("survival_prob", s)
        self.assertEqual(s["method"], "prior_wr")

    def test_survival_large(self) -> None:
        occ = _occ(120)
        s = survival_probability([o["ts"] for o in occ], [o["pnl"] for o in occ], window=20)
        self.assertGreaterEqual(s["survival_prob"], 0.0)
        self.assertLessEqual(s["survival_prob"], 1.0)


class TestLifecycle(unittest.TestCase):
    def test_birth(self) -> None:
        st = classify_lifecycle(
            n=5, age_days=2, rolling={"last_50": {"ev": 0.1}},
            decay={}, survival={"survival_prob": 0.5},
        )
        self.assertEqual(st, "BIRTH")

    def test_dead(self) -> None:
        st = classify_lifecycle(
            n=50, age_days=100,
            rolling={"last_50": {"ev": -0.2}, "all": {"ev": -0.1}},
            decay={"edge_today": -0.2, "slope": -0.05},
            survival={"survival_prob": 0.1},
        )
        self.assertEqual(st, "DEAD")

    def test_peak(self) -> None:
        st = classify_lifecycle(
            n=80, age_days=90,
            rolling={"last_50": {"ev": 0.5, "wr": 0.65}, "all": {"ev": 0.4}},
            decay={"slope": 0.0, "edge_today": 0.4},
            survival={"survival_prob": 0.7},
        )
        self.assertEqual(st, "PEAK")

    def test_growth(self) -> None:
        st = classify_lifecycle(
            n=25, age_days=30,
            rolling={"last_50": {"ev": 0.2, "wr": 0.52}, "all": {"ev": 0.15}},
            decay={"slope": 0.03},
            survival={"survival_prob": 0.5},
        )
        self.assertEqual(st, "GROWTH")

    def test_decay(self) -> None:
        st = classify_lifecycle(
            n=60, age_days=120,
            rolling={"last_50": {"ev": 0.05, "wr": 0.5}, "all": {"ev": 0.3}},
            decay={"slope": -0.05, "half_life_days": 20, "edge_today": 0.05, "edge_30d_ago": 0.4},
            survival={"survival_prob": 0.4},
        )
        self.assertEqual(st, "DECAY")


class TestRegimes(unittest.TestCase):
    def test_regime_keys(self) -> None:
        self.assertIn("BULL", REGIME_KEYS)
        self.assertIn("NEWS", REGIME_KEYS)

    def test_regime_adaptation(self) -> None:
        r = regime_adaptation(_occ(40))
        self.assertTrue(any(r[k]["n"] > 0 for k in REGIME_KEYS))


class TestDrift(unittest.TestCase):
    def test_detect_drift_fields(self) -> None:
        d = detect_drift(_occ(60, decaying=True))
        for k in ("distribution_drift", "concept_drift", "target_drift", "feature_drift", "drift_score", "drift_level"):
            self.assertIn(k, d)

    def test_drift_level_values(self) -> None:
        d = detect_drift(_occ(80, decaying=True))
        self.assertIn(d["drift_level"], ("LOW", "MED", "HIGH"))

    def test_drift_small(self) -> None:
        d = detect_drift(_occ(8))
        self.assertIn("drift_score", d)


class TestRanking(unittest.TestCase):
    def test_score_bounds(self) -> None:
        s = score_signal(
            rolling={"last_100": {"ev": 0.5, "wr": 0.6, "pf": 1.5, "sharpe": 1.0}, "all": {"n": 100}},
            decay={"slope": 0.01, "half_life_days": 100},
            survival={"survival_prob": 0.7},
            drift={"drift_score": 0.1},
            status="PEAK",
        )
        self.assertGreaterEqual(s["score"], 0)
        self.assertLessEqual(s["score"], 100)
        self.assertGreater(s["confidence"], 0)

    def test_promotion_never_auto(self) -> None:
        ranked = [
            {"signal": "a", "status": "PEAK", "score": 80, "confidence": 0.7, "drift": 0.1},
            {"signal": "b", "status": "DEAD", "score": 10, "confidence": 0.2, "drift": 0.8},
        ]
        p = promotion_recommendation(ranked)
        self.assertFalse(p["auto_promotion"])
        self.assertTrue(p["promote_candidates"] or p["retire_candidates"])


class TestLibrary(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "e.db"))
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_table_name(self) -> None:
        self.assertEqual(LIBRARY_TABLE, "market_signal_evolution_v1")

    def test_upsert_load(self) -> None:
        n = upsert_evolution(self.conn, [{
            "signal": "s40:x", "score": 77, "age": 12, "half_life": 45,
            "drift": 0.2, "confidence": 0.6, "status": "PEAK",
            "rolling": {}, "regimes": {}, "decay": {}, "survival": {}, "drift_detail": {},
        }])
        self.assertEqual(n, 1)
        rows = load_evolution(self.conn)
        self.assertEqual(rows[0]["signal"], "s40:x")
        self.assertEqual(rows[0]["status"], "PEAK")

    def test_upsert_conflict(self) -> None:
        row = {
            "signal": "lake:BTC|LONG|RANGE", "score": 50, "age": 1, "half_life": None,
            "drift": 0.1, "confidence": 0.4, "status": "GROWTH",
        }
        upsert_evolution(self.conn, [row])
        row["score"] = 66
        upsert_evolution(self.conn, [row])
        self.assertEqual(load_evolution(self.conn)[0]["score"], 66)


class TestEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "r.db"))
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_update_signal_fast(self) -> None:
        rec = update_signal(_occ(100))
        self.assertLess(rec["update_ms"], 500.0)
        self.assertIn(rec["status"], ("BIRTH", "GROWTH", "PEAK", "DECAY", "DEAD"))
        self.assertIn("half_life", rec)

    def test_run_end_to_end(self) -> None:
        occ = _occ(60, signal="s40:a") + _occ(60, decaying=True, signal="s40:b")
        with mock.patch(
            "bot.research.market_events.signal_intelligence.signal_evolution_v1.engine._load_occurrences",
            return_value=(occ, {"source": "synth"}),
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.signal_evolution_v1.engine.write_artifacts",
            return_value={"report_md": "/tmp/e.md"},
        ):
            out = run_signal_evolution_v1(self.conn, write_reports=True, persist_library=True, min_occurrences=5)
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(out["n_signals"], 2)
        self.assertTrue(out["within_budget"])
        self.assertTrue(out["gate_unchanged"])
        self.assertIn("promotion", out)
        self.assertGreater(out["library_upserted"], 0)

    def test_research_flags(self) -> None:
        occ = _occ(30, signal="x")
        with mock.patch(
            "bot.research.market_events.signal_intelligence.signal_evolution_v1.engine._load_occurrences",
            return_value=(occ, {"source": "synth"}),
        ):
            out = run_signal_evolution_v1(self.conn, write_reports=False, persist_library=False, min_occurrences=5)
        for k in ("research_only", "gate_unchanged", "strategy_unchanged", "paper_unchanged", "execution_unchanged", "optimizer_unchanged"):
            self.assertTrue(out[k])

    def test_cli_registered(self) -> None:
        from bot.research.market_events import __main__ as m
        src = Path(m.__file__).read_text(encoding="utf-8")
        self.assertIn('"market-signal-evolution"', src)
        self.assertIn("run_signal_evolution_v1", src)

    def test_package_exports(self) -> None:
        from bot.research.market_events.signal_intelligence import signal_evolution_v1 as pkg
        self.assertTrue(hasattr(pkg, "run_signal_evolution_v1"))
        self.assertEqual(pkg.LIBRARY_TABLE, "market_signal_evolution_v1")


class TestReport(unittest.TestCase):
    def _result(self) -> dict:
        occ = _occ(40, signal="s40:strong") + _occ(40, decaying=True, signal="s40:weak")
        a = update_signal([o for o in occ if o["signal"] == "s40:strong"])
        b = update_signal([o for o in occ if o["signal"] == "s40:weak"])
        ranked = sorted([a, b], key=lambda r: -r["score"])
        return {
            "n_signals": 2, "n_occurrences": 80, "elapsed_sec": 0.2, "mean_update_ms": 1.0,
            "load_stats": {"source": "test"},
            "ranked": ranked, "strongest": ranked[:1], "weakest": ranked[-1:],
            "lifecycle_counts": {"GROWTH": 1, "DECAY": 1},
            "drift_stats": {"mean_drift": 0.2},
            "promotion": promotion_recommendation(ranked),
            "research_only": True, "gate_unchanged": True, "strategy_unchanged": True,
            "paper_unchanged": True, "execution_unchanged": True, "optimizer_unchanged": True,
        }

    def test_format_report(self) -> None:
        self.assertIn("SIGNAL_EVOLUTION_REPORT", format_evolution_report(self._result()))

    def test_format_decay(self) -> None:
        self.assertIn("SIGNAL_DECAY", format_decay_md(self._result()))

    def test_format_drift(self) -> None:
        self.assertIn("FEATURE_DRIFT", format_drift_md(self._result()))

    def test_write_artifacts(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch(
            "bot.research.market_events.signal_intelligence.signal_evolution_v1.report.OUT_DIR",
            Path(tmp.name) / "out",
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.signal_evolution_v1.report.REPORT_MD",
            Path(tmp.name) / "SIGNAL_EVOLUTION_REPORT.md",
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.signal_evolution_v1.report.DECAY_MD",
            Path(tmp.name) / "SIGNAL_DECAY.md",
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.signal_evolution_v1.report.DRIFT_MD",
            Path(tmp.name) / "FEATURE_DRIFT.md",
        ):
            paths = write_artifacts(self._result())
        self.assertTrue(Path(paths["report_md"]).exists())
        self.assertTrue(Path(paths["decay_md"]).exists())
        self.assertTrue(Path(paths["drift_md"]).exists())


class TestScale(unittest.TestCase):
    def test_many_signals_budget(self) -> None:
        occ = []
        for i in range(20):
            occ.extend(_occ(40, signal=f"s40:sig{i}"))
        conn = sqlite3.connect(":memory:")
        t0 = time.time()
        with mock.patch(
            "bot.research.market_events.signal_intelligence.signal_evolution_v1.engine._load_occurrences",
            return_value=(occ, {"source": "synth"}),
        ):
            out = run_signal_evolution_v1(conn, write_reports=False, persist_library=False, min_occurrences=5)
        elapsed = time.time() - t0
        conn.close()
        self.assertEqual(out["n_signals"], 20)
        self.assertLess(out["mean_update_ms"], 500.0)
        self.assertLess(elapsed, 10.0)


class TestExtra(unittest.TestCase):
    def test_pf_none_all_wins(self) -> None:
        m = window_metrics([1.0, 2.0, 0.5])
        self.assertIsNone(m["pf"])

    def test_max_dd_negative(self) -> None:
        m = window_metrics([1.0, -2.0, 0.5])
        self.assertLessEqual(m["max_dd"], 0)

    def test_update_empty_pnls(self) -> None:
        rec = update_signal([{"signal": "x", "ts": 1, "pnl": None}])
        self.assertEqual(rec["status"], "BIRTH")

    def test_library_empty(self) -> None:
        conn = sqlite3.connect(":memory:")
        ensure_evolution_schema(conn)
        self.assertEqual(load_evolution(conn), [])
        conn.close()

    def test_promotion_hold(self) -> None:
        p = promotion_recommendation([
            {"signal": "a", "status": "BIRTH", "score": 40, "confidence": 0.3, "drift": 0.2}
        ])
        self.assertFalse(p["auto_promotion"])

    def test_half_life_in_update(self) -> None:
        rec = update_signal(_occ(100, decaying=True))
        # may be None if fit fails, but field must exist
        self.assertIn("half_life", rec)

    def test_drift_feature_keys(self) -> None:
        d = detect_drift(_occ(50))
        self.assertIsInstance(d["feature_drift"], dict)

    def test_regime_high_vol(self) -> None:
        r = regime_adaptation([{"pnl": 1.0, "regime": "BULL", "atr_pct": 3.0}])
        self.assertGreaterEqual(r["HIGH_VOL"]["n"], 1)

    def test_score_dead_penalty(self) -> None:
        s1 = score_signal(
            rolling={"last_100": {"ev": 0.1, "wr": 0.55}, "all": {"n": 50}},
            decay={}, survival={"survival_prob": 0.5}, drift={"drift_score": 0.2}, status="PEAK",
        )
        s2 = score_signal(
            rolling={"last_100": {"ev": 0.1, "wr": 0.55}, "all": {"n": 50}},
            decay={}, survival={"survival_prob": 0.5}, drift={"drift_score": 0.2}, status="DEAD",
        )
        self.assertGreater(s1["score"], s2["score"])

    def test_strongest_weakest_order(self) -> None:
        occ = _occ(50, signal="good") + _occ(50, decaying=True, signal="bad")
        conn = sqlite3.connect(":memory:")
        with mock.patch(
            "bot.research.market_events.signal_intelligence.signal_evolution_v1.engine._load_occurrences",
            return_value=(occ, {"source": "synth"}),
        ):
            out = run_signal_evolution_v1(conn, write_reports=False, persist_library=False)
        conn.close()
        self.assertGreaterEqual(out["strongest"][0]["score"], out["weakest"][0]["score"])

    def test_lifecycle_counts_present(self) -> None:
        occ = _occ(40, signal="a") + _occ(40, decaying=True, signal="b")
        conn = sqlite3.connect(":memory:")
        with mock.patch(
            "bot.research.market_events.signal_intelligence.signal_evolution_v1.engine._load_occurrences",
            return_value=(occ, {"source": "synth"}),
        ):
            out = run_signal_evolution_v1(conn, write_reports=False, persist_library=False)
        conn.close()
        self.assertTrue(out["lifecycle_counts"])

    def test_drift_stats_keys(self) -> None:
        occ = _occ(40, signal="a")
        conn = sqlite3.connect(":memory:")
        with mock.patch(
            "bot.research.market_events.signal_intelligence.signal_evolution_v1.engine._load_occurrences",
            return_value=(occ, {"source": "synth"}),
        ):
            out = run_signal_evolution_v1(conn, write_reports=False, persist_library=False)
        conn.close()
        for k in ("mean_drift", "high_drift", "med_drift", "low_drift"):
            self.assertIn(k, out["drift_stats"])

    def test_half_life_estimates_list(self) -> None:
        occ = _occ(100, decaying=True, signal="decay")
        conn = sqlite3.connect(":memory:")
        with mock.patch(
            "bot.research.market_events.signal_intelligence.signal_evolution_v1.engine._load_occurrences",
            return_value=(occ, {"source": "synth"}),
        ):
            out = run_signal_evolution_v1(conn, write_reports=False, persist_library=False)
        conn.close()
        self.assertIsInstance(out["half_life_estimates"], list)

    def test_min_occurrences_filter(self) -> None:
        occ = _occ(3, signal="tiny") + _occ(40, signal="ok")
        conn = sqlite3.connect(":memory:")
        with mock.patch(
            "bot.research.market_events.signal_intelligence.signal_evolution_v1.engine._load_occurrences",
            return_value=(occ, {"source": "synth"}),
        ):
            out = run_signal_evolution_v1(conn, write_reports=False, persist_library=False, min_occurrences=5)
        conn.close()
        self.assertEqual(out["n_signals"], 1)

    def test_macro_regime_tag(self) -> None:
        r = regime_adaptation([{"pnl": 1.0, "regime": "RANGE", "fear_greed": 10.0}])
        self.assertGreaterEqual(r["MACRO"]["n"], 1)

    def test_news_regime_tag(self) -> None:
        r = regime_adaptation([{"pnl": 1.0, "regime": "RANGE", "news_score": 0.5}])
        self.assertGreaterEqual(r["NEWS"]["n"], 1)


if __name__ == "__main__":
    unittest.main()
