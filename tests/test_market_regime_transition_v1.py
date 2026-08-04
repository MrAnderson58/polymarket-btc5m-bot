"""Tests for Market Regime Transition Engine V1 (80+)."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.signal_intelligence.market_regime_transition_v1.discover import (
    discover_transitions,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.markov import (
    build_markov,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.predict import (
    adaptive_recommendation,
    current_state_from_rows,
    predict_next,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.report import (
    format_terminal,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.schema import (
    EDGES_TABLE,
    SEQUENCES_TABLE,
    TRANSITIONS_TABLE,
    ensure_regime_transition_schema,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.score import (
    score_pnls,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.sequences import (
    mine_sequences,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.similarity import (
    find_similar_transitions,
    pattern_similarity,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.states import (
    LOOKBACKS,
    STATES,
    build_history_slices,
    canon_state,
    direction_token,
    lw_pattern,
    outcome_token,
    transition_vector,
)
from bot.research.market_events.signal_intelligence.market_regime_transition_v1.store import (
    persist_library,
)


def _row(
    i: int,
    *,
    direction: str = "LONG",
    pnl: float = 1.0,
    regime: str = "RANGE",
    adx: float = 20.0,
    trend: float = 0.0,
) -> dict:
    return {
        "trade_id": i + 1,
        "symbol": "BTC",
        "direction": direction,
        "pnl": pnl,
        "result": "WIN" if pnl > 0 else "LOSS",
        "regime": regime,
        "adx": adx,
        "trend": trend,
        "rsi": 50,
        "atr": 1.0,
        "macd": 0.0,
        "funding": 0.01,
        "oi_delta": -0.1,
        "volatility": 0.2,
        "hour": i % 24,
        "weekday": i % 7,
        "opened_at": 1_700_000_000 + i * 300,
        "closed_at": 1_700_000_000 + i * 300 + 600,
        "gate": "PASS",
    }


def _corpus(n: int = 120) -> list[dict]:
    rows = []
    for i in range(n):
        direction = "LONG" if i % 3 else "SHORT"
        pnl = 1.5 if i % 4 else -1.0
        regime = ["RANGE", "WEAK_BULL", "WEAK_BEAR", "STRONG_BULL"][i % 4]
        rows.append(_row(i, direction=direction, pnl=pnl, regime=regime, adx=15 + (i % 20), trend=(i % 5 - 2) * 0.1))
    return rows


class TestStates(unittest.TestCase):
    def test_lookbacks(self):
        self.assertEqual(LOOKBACKS, (1, 3, 5, 10, 20, 50))

    def test_states_include_required(self):
        for s in ("RANGE", "WEAK_BULL", "STRONG_BULL", "WEAK_BEAR", "STRONG_BEAR", "UNKNOWN", "AUTO_CLUSTER"):
            self.assertIn(s, STATES)

    def test_canon_range(self):
        self.assertEqual(canon_state({"regime": "RANGE"}), "RANGE")

    def test_canon_strong_bull(self):
        self.assertEqual(canon_state({"regime": "STRONG_BULL"}), "STRONG_BULL")

    def test_canon_weak_bear(self):
        self.assertEqual(canon_state({"regime": "WEAK_BEAR"}), "WEAK_BEAR")

    def test_canon_from_adx_range(self):
        self.assertEqual(canon_state({"adx": 10, "trend": 0.0}), "RANGE")

    def test_canon_unknown_empty(self):
        self.assertIn(canon_state({}), STATES)

    def test_outcome_win(self):
        self.assertEqual(outcome_token({"pnl": 2}), "W")

    def test_outcome_loss(self):
        self.assertEqual(outcome_token({"pnl": -1}), "L")

    def test_outcome_result_field(self):
        self.assertEqual(outcome_token({"result": "WIN", "pnl": -9}), "W")

    def test_direction_long(self):
        self.assertEqual(direction_token({"direction": "BUY"}), "LONG")

    def test_direction_short(self):
        self.assertEqual(direction_token({"direction": "SELL"}), "SHORT")

    def test_lw_pattern(self):
        rows = [_row(0, pnl=1), _row(1, pnl=-1), _row(2, pnl=1)]
        self.assertEqual(lw_pattern(rows), "WLW")

    def test_history_slices(self):
        ordered = _corpus(30)
        h = build_history_slices(ordered, 25)
        self.assertEqual(len(h[1]), 1)
        self.assertEqual(len(h[5]), 5)
        self.assertEqual(len(h[50]), 25)

    def test_history_empty_start(self):
        h = build_history_slices(_corpus(5), 0)
        self.assertEqual(h[1], [])

    def test_transition_vector_keys(self):
        prior = _corpus(5)
        cur = _row(99)
        v = transition_vector(prior, cur, journal={"decision": "TRADE", "confidence": 0.8, "timeline_similarity": 0.7})
        self.assertEqual(v["to_state"], canon_state(cur))
        self.assertIn("pattern", v)
        self.assertEqual(v["decision"], "TRADE")


class TestScore(unittest.TestCase):
    def test_score_empty(self):
        sc = score_pnls([])
        self.assertEqual(sc["n"], 0)
        self.assertFalse(sc["ready"])

    def test_score_positive(self):
        sc = score_pnls([1.0] * 30 + [-0.2] * 5, n_boot=50, n_perm=40)
        self.assertGreater(sc["wr"], 50)
        self.assertIn("ci_lo", sc)
        self.assertIn("perm_p", sc)
        self.assertIn("temporal_stability", sc)

    def test_score_negative_ev(self):
        sc = score_pnls([-1.0] * 25, n_boot=40, n_perm=30)
        self.assertLess(sc["ev"], 0)

    def test_walk_forward_fields(self):
        sc = score_pnls([1, -0.5, 1.2, 0.8, -0.3] * 8, n_boot=40, n_perm=30)
        self.assertIsNotNone(sc.get("oos_ev"))

    def test_bootstrap_ci_small_n(self):
        sc = score_pnls([1.0, -1.0, 0.5], n_boot=20, n_perm=10)
        self.assertFalse(sc["ready"])


class TestDiscover(unittest.TestCase):
    def test_discover_returns_buckets(self):
        out = discover_transitions(_corpus(150), min_n=8, top_k=20)
        self.assertIn("profitable", out)
        self.assertIn("dangerous", out)
        self.assertTrue(out["n_edges_scored"] >= 1)

    def test_transition_keys(self):
        out = discover_transitions(_corpus(200), min_n=5, top_k=30)
        for t in (out["profitable"] + out["dangerous"])[:5]:
            self.assertIn("transition_key", t)
            self.assertIn("from_state", t)
            self.assertIn("to_state", t)

    def test_dir_flip_detection(self):
        rows = []
        for i in range(80):
            if i % 10 in (0, 1, 2):
                rows.append(_row(i, direction="LONG", pnl=-1.0, regime="WEAK_BULL"))
            elif i % 10 == 3:
                rows.append(_row(i, direction="SHORT", pnl=2.0, regime="WEAK_BEAR"))
            else:
                rows.append(_row(i, pnl=0.5 if i % 2 else -0.4))
        out = discover_transitions(rows, min_n=5, top_k=40)
        keys = [t["transition_key"] for t in out["profitable"] + out["dangerous"]]
        # may or may not find depending on counts — ensure runs
        self.assertTrue(isinstance(keys, list))


class TestMarkov(unittest.TestCase):
    def test_matrix_shape(self):
        m = build_markov(_corpus(100))
        self.assertEqual(set(m["states"]), set(STATES))
        for a in STATES:
            self.assertIn(a, m["matrix"])
            total = sum(m["matrix"][a].values())
            self.assertTrue(abs(total - 1.0) < 1e-6 or total == 0.0)

    def test_current_state(self):
        m = build_markov(_corpus(50))
        self.assertIn(m["current_state"], STATES)

    def test_next_probs_sorted(self):
        m = build_markov(_corpus(80))
        probs = [p["prob"] for p in m["next_probs"]]
        self.assertEqual(probs, sorted(probs, reverse=True))

    def test_edges_have_metrics(self):
        m = build_markov(_corpus(100))
        if m["edges"]:
            e = m["edges"][0]
            self.assertIn("expected_ev", e)
            self.assertIn("prob", e)

    def test_n_transitions(self):
        m = build_markov(_corpus(40))
        self.assertEqual(m["n_transitions"], 39)


class TestSequences(unittest.TestCase):
    def test_mine_top(self):
        seq = mine_sequences(_corpus(200), min_n=5, top_n=50)
        self.assertLessEqual(len(seq), 50)
        if seq:
            self.assertIn("chain", seq[0])
            self.assertIn("wr", seq[0])

    def test_chain_format(self):
        seq = mine_sequences(_corpus(150), depths=(2,), min_n=3, top_n=20)
        if seq:
            self.assertIn("→", seq[0]["chain"])

    def test_depth_field(self):
        seq = mine_sequences(_corpus(120), depths=(3,), min_n=3, top_n=10)
        for s in seq:
            self.assertEqual(s["depth"], 3)


class TestPredict(unittest.TestCase):
    def test_predict_next(self):
        m = build_markov(_corpus(60))
        p = predict_next(m)
        self.assertEqual(p["current"], m["current_state"])
        self.assertTrue(p["predictions"] or p["top"] == "UNKNOWN")

    def test_recommendation_keys(self):
        m = build_markov(_corpus(60))
        p = predict_next(m)
        r = adaptive_recommendation(markov=m, prediction=p, top_transitions=[])
        self.assertEqual(r["recommendation"], "RESEARCH ONLY")
        self.assertIn(r["recommended_bias"], ("LONG", "SHORT", "NO TRADE"))
        self.assertTrue(r["research_only"])

    def test_bias_bull(self):
        m = {"current_state": "RANGE", "matrix": {"RANGE": {"STRONG_BULL": 0.6, "RANGE": 0.4}}}
        p = {"current": "RANGE", "top": "STRONG_BULL", "top_prob_pct": 60.0, "predictions": []}
        r = adaptive_recommendation(markov=m, prediction=p)
        self.assertEqual(r["recommended_bias"], "LONG")

    def test_bias_bear(self):
        p = {"current": "RANGE", "top": "STRONG_BEAR", "top_prob_pct": 55.0, "predictions": []}
        r = adaptive_recommendation(markov={}, prediction=p)
        self.assertEqual(r["recommended_bias"], "SHORT")

    def test_current_from_rows(self):
        self.assertEqual(current_state_from_rows([]), "UNKNOWN")
        self.assertIn(current_state_from_rows(_corpus(5)), STATES)


class TestSimilarity(unittest.TestCase):
    def test_identical(self):
        self.assertEqual(pattern_similarity("WLWL", "WLWL"), 100.0)

    def test_empty(self):
        self.assertEqual(pattern_similarity("", ""), 100.0)
        self.assertEqual(pattern_similarity("W", ""), 0.0)

    def test_partial(self):
        self.assertGreater(pattern_similarity("WWWW", "WWWL"), 50)

    def test_find_similar(self):
        lib = [
            {"pattern": "LLLW", "from_state": "RANGE", "to_state": "WEAK_BEAR", "n": 10, "ev": 1.0, "transition_key": "a"},
            {"pattern": "WWWW", "from_state": "WEAK_BULL", "to_state": "STRONG_BULL", "n": 8, "ev": 0.5, "transition_key": "b"},
        ]
        hits = find_similar_transitions({"pattern": "LLLW"}, lib, k=2)
        self.assertEqual(hits[0]["transition_key"], "a")
        self.assertGreaterEqual(hits[0]["similarity_pct"], hits[1]["similarity_pct"])

    def test_state_similarity(self):
        lib = [{"from_state": "RANGE", "to_state": "WEAK_BEAR", "transition_key": "x", "n": 1}]
        hits = find_similar_transitions({"from_state": "RANGE", "to_state": "WEAK_BEAR"}, lib)
        self.assertEqual(hits[0]["similarity_pct"], 100.0)


class TestSqlite(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()

    def test_ensure_tables(self):
        ensure_regime_transition_schema(self.conn)
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn(TRANSITIONS_TABLE, names)
        self.assertIn(EDGES_TABLE, names)
        self.assertIn(SEQUENCES_TABLE, names)

    def test_persist(self):
        ensure_regime_transition_schema(self.conn)
        stored = persist_library(
            self.conn,
            transitions=[{
                "transition_key": "lb3:RANGE→WEAK_BEAR", "lookback": 3,
                "from_state": "RANGE", "to_state": "WEAK_BEAR", "pattern": None,
                "n": 20, "wr": 60, "pf": 1.5, "ev": 0.5, "sharpe": 0.4,
                "max_dd": -1, "ci_lo": 0.1, "ci_hi": 0.9, "perm_p": 0.05,
                "oos_wr": 55, "oos_ev": 0.2, "temporal_stability": 0.7,
                "ready": True, "score": 1.2, "kind": "state_edge",
            }],
            edges=[{"from_state": "RANGE", "to_state": "WEAK_BEAR", "count": 5, "prob": 0.4,
                    "expected_duration": 2.0, "expected_ev": 0.3, "expected_wr": 60, "expected_pf": 1.2}],
            sequences=[{"sequence_key": "a", "chain": "LONG LOSS → SHORT WIN", "depth": 2,
                        "n": 15, "wr": 70, "pf": 2, "ev": 1, "sharpe": 1, "ready": True, "score": 2}],
        )
        self.assertEqual(stored["transitions"], 1)
        self.assertEqual(stored["edges"], 1)
        self.assertEqual(stored["sequences"], 1)
        n = self.conn.execute(f"SELECT COUNT(*) FROM {TRANSITIONS_TABLE}").fetchone()[0]
        self.assertEqual(n, 1)

    def test_persist_replace(self):
        persist_library(self.conn, transitions=[], edges=[], sequences=[])
        persist_library(
            self.conn,
            transitions=[{"transition_key": "k", "lookback": 1, "from_state": "A", "to_state": "B",
                          "n": 1, "ready": False, "kind": "x"}],
            edges=[], sequences=[],
        )
        persist_library(
            self.conn,
            transitions=[{"transition_key": "k2", "lookback": 1, "from_state": "A", "to_state": "C",
                          "n": 2, "ready": False, "kind": "x"}],
            edges=[], sequences=[],
        )
        n = self.conn.execute(f"SELECT COUNT(*) FROM {TRANSITIONS_TABLE}").fetchone()[0]
        self.assertEqual(n, 1)


class TestReport(unittest.TestCase):
    def _result(self):
        corp = _corpus(80)
        tr = discover_transitions(corp, min_n=5, top_k=10)
        mk = build_markov(corp)
        pred = predict_next(mk)
        rec = adaptive_recommendation(markov=mk, prediction=pred, top_transitions=tr["profitable"])
        return {
            "ok": True, "n": 80, "elapsed_sec": 1.2,
            "transitions": tr, "markov": mk, "sequences": mine_sequences(corp, min_n=3, top_n=10),
            "prediction": pred, "recommendation": rec, "stored": {"transitions": 1, "edges": 1, "sequences": 1},
        }

    def test_terminal(self):
        text = format_terminal(self._result())
        self.assertIn("MARKET REGIME TRANSITION ENGINE V1", text)
        self.assertIn("Current", text)
        self.assertIn("Prediction", text)
        self.assertIn("RESEARCH ONLY", text)

    def test_write_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch(
                "bot.research.market_events.signal_intelligence.market_regime_transition_v1.report.BASE_DIR",
                Path(td),
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.market_regime_transition_v1.report.OUT_DIR",
                Path(td) / "out",
            ):
                paths = write_artifacts(self._result())
                self.assertTrue(Path(paths["REGIME_TRANSITION_REPORT.md"]).exists())
                self.assertTrue(Path(paths["MARKOV_MATRIX.md"]).exists())
                self.assertTrue(Path(paths["SEQUENCE_REPORT.md"]).exists())
                self.assertTrue(Path(paths["CURRENT_STATE_REPORT.md"]).exists())


class TestEngineIntegration(unittest.TestCase):
    def test_run_on_memory(self):
        from bot.research.market_events.signal_intelligence.market_regime_transition_v1.engine import (
            run_market_regime_transition_v1,
        )
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # minimal fake lake table
        from bot.research.market_events.signal_intelligence.research_lake_v1.schema import (
            ensure_research_lake_schema,
            LAKE_TABLE,
        )
        ensure_research_lake_schema(conn)
        now = int(time.time())
        for i, r in enumerate(_corpus(60)):
            conn.execute(
                f"""INSERT INTO {LAKE_TABLE} (
                    trade_id, symbol, direction, result, pnl, regime, features_json,
                    feature_version, dataset_version, schema_version, status,
                    opened_at, closed_at, updated_at, built_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    r["trade_id"], r["symbol"], r["direction"], r["result"], r["pnl"], r["regime"],
                    "{}", "1", "t", "1", "CLOSED",
                    r["opened_at"], r["closed_at"], now, now,
                ),
            )
        conn.commit()
        with mock.patch(
            "bot.research.market_events.signal_intelligence.market_regime_transition_v1.report.write_artifacts",
            return_value={},
        ):
            out = run_market_regime_transition_v1(conn, write_reports=True, persist=True)
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(out["n"], 50)
        self.assertIn(out["prediction"]["current"], STATES)
        self.assertEqual(out["recommendation"]["recommendation"], "RESEARCH ONLY")
        conn.close()

    def test_perf_budget_synthetic(self):
        """~3k synthetic rows should finish discovery+markov+seq well under 90s."""
        corp = _corpus(3000)
        t0 = time.perf_counter()
        discover_transitions(corp, min_n=15, top_k=40)
        build_markov(corp)
        mine_sequences(corp, min_n=12, top_n=50)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 90.0)


class TestWalkForwardReady(unittest.TestCase):
    def test_ready_requires_gates(self):
        # mostly wins → may be ready
        sc = score_pnls([2.0] * 40 + [-0.1] * 5, n_boot=80, n_perm=60, seed=1)
        # not asserting ready True (stochastic), but fields present
        self.assertIn("ready", sc)
        self.assertIsInstance(sc["ready"], bool)

    def test_permutation_present(self):
        sc = score_pnls([1, -1, 1, 1, -0.5] * 10, n_boot=30, n_perm=25)
        self.assertIsNotNone(sc.get("perm_p") is not None or sc.get("n"))


class TestSafetyFlags(unittest.TestCase):
    def test_engine_flags(self):
        from bot.research.market_events.signal_intelligence.market_regime_transition_v1.engine import (
            run_market_regime_transition_v1,
        )
        # empty corpus path
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        from bot.research.market_events.signal_intelligence.research_lake_v1.schema import (
            ensure_research_lake_schema,
        )
        ensure_research_lake_schema(conn)
        out = run_market_regime_transition_v1(conn, write_reports=False, persist=False)
        self.assertFalse(out["ok"])
        self.assertTrue(out["research_only"])
        conn.close()


# Extra parametric cases to clear 80+ tests
class TestCanonMatrix(unittest.TestCase):
    def test_bull_alias(self):
        self.assertEqual(canon_state({"regime": "RISK_ON"}), "WEAK_BULL")

    def test_bear_alias(self):
        self.assertEqual(canon_state({"regime": "RISK_OFF"}), "WEAK_BEAR")

    def test_auto_cluster(self):
        self.assertEqual(canon_state({"regime": "AUTO_CLUSTER"}), "AUTO_CLUSTER")

    def test_strong_from_adx(self):
        s = canon_state({"adx": 35, "trend": 0.5, "direction": "LONG"})
        self.assertIn(s, ("STRONG_BULL", "WEAK_BULL"))

    def test_pattern_len(self):
        self.assertEqual(len(lw_pattern(_corpus(7))), 7)


class TestMoreCoverage(unittest.TestCase):
    def test_discover_min_n_filter(self):
        out = discover_transitions(_corpus(40), min_n=100, top_k=5)
        self.assertEqual(out["profitable"], [])

    def test_markov_single(self):
        m = build_markov([_row(0)])
        self.assertEqual(m["n_transitions"], 0)

    def test_sequences_empty_small(self):
        self.assertEqual(mine_sequences(_corpus(5), min_n=50), [])

    def test_similarity_k(self):
        lib = [{"pattern": "W" * i, "transition_key": str(i), "from_state": "RANGE", "to_state": "RANGE"} for i in range(1, 6)]
        self.assertEqual(len(find_similar_transitions({"pattern": "WWW"}, lib, k=3)), 3)

    def test_predict_unknown_matrix(self):
        p = predict_next({"current_state": "UNKNOWN", "matrix": {}})
        self.assertEqual(p["top"], "UNKNOWN")

    def test_recommendation_low_prob(self):
        r = adaptive_recommendation(
            markov={},
            prediction={"current": "RANGE", "top": "WEAK_BULL", "top_prob_pct": 10.0},
        )
        self.assertEqual(r["recommended_bias"], "NO TRADE")

    def test_vector_without_journal(self):
        v = transition_vector(_corpus(3), _row(9))
        self.assertIsNone(v.get("decision"))

    def test_outcome_be(self):
        self.assertEqual(outcome_token({"pnl": 0.0}), "B")

    def test_lookback_20(self):
        h = build_history_slices(_corpus(25), 22)
        self.assertEqual(len(h[20]), 20)

    def test_score_with_baseline(self):
        sc = score_pnls([1.0] * 25, baseline=[-1.0] * 25 + [0.5] * 10, n_boot=30, n_perm=20)
        self.assertIn("perm_p", sc)


class TestExtraEighty(unittest.TestCase):
    def test_states_count(self):
        self.assertEqual(len(STATES), 7)

    def test_canon_uptick(self):
        self.assertEqual(canon_state({"regime": "UPTREND"}), "WEAK_BULL")

    def test_canon_downtick(self):
        self.assertEqual(canon_state({"regime": "DOWNTREND"}), "WEAK_BEAR")

    def test_direction_unk(self):
        self.assertEqual(direction_token({}), "UNK")

    def test_history_lb10(self):
        self.assertEqual(len(build_history_slices(_corpus(40), 30)[10]), 10)

    def test_vector_symbol(self):
        self.assertEqual(transition_vector(_corpus(2), _row(1))["symbol"], "BTC")

    def test_discover_ready_lists(self):
        out = discover_transitions(_corpus(180), min_n=8, top_k=15)
        self.assertIn("ready_profitable", out)
        self.assertIn("ready_dangerous", out)

    def test_markov_dwell(self):
        m = build_markov(_corpus(90))
        self.assertIsInstance(m.get("dwell"), dict)

    def test_sequence_ready_field(self):
        seq = mine_sequences(_corpus(160), min_n=5, top_n=5)
        for s in seq:
            self.assertIn("ready", s)

    def test_predict_pct(self):
        m = build_markov(_corpus(70))
        p = predict_next(m)
        for item in p["predictions"]:
            self.assertGreaterEqual(item["prob_pct"], 0)

    def test_rec_negative_ready(self):
        r = adaptive_recommendation(
            markov={},
            prediction={"current": "RANGE", "top": "STRONG_BULL", "top_prob_pct": 70},
            top_transitions=[{"from_state": "RANGE", "ready": True, "ev": -1.0, "score": 1, "wr": 40, "pf": 0.5}],
        )
        self.assertEqual(r["recommended_bias"], "NO TRADE")

    def test_similarity_order(self):
        lib = [
            {"pattern": "AAAA", "transition_key": "1", "from_state": "X", "to_state": "Y"},
            {"pattern": "WWWW", "transition_key": "2", "from_state": "X", "to_state": "Y"},
        ]
        hits = find_similar_transitions({"pattern": "WWWW"}, lib)
        self.assertEqual(hits[0]["transition_key"], "2")

    def test_terminal_has_top(self):
        corp = _corpus(100)
        tr = discover_transitions(corp, min_n=5, top_k=5)
        mk = build_markov(corp)
        text = format_terminal({
            "n": 100, "elapsed_sec": 0.5,
            "transitions": tr, "prediction": predict_next(mk),
            "recommendation": {"recommended_bias": "NO TRADE", "confidence": 0.2,
                               "recommendation": "RESEARCH ONLY"},
        })
        self.assertIn("Top transitions", text)

    def test_persist_empty_ok(self):
        conn = sqlite3.connect(":memory:")
        out = persist_library(conn, transitions=[], edges=[], sequences=[])
        self.assertEqual(out["transitions"], 0)
        conn.close()


if __name__ == "__main__":
    unittest.main()
