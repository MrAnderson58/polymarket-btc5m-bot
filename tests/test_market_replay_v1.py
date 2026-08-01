"""Comprehensive tests for Market Replay Engine V1 (30+ cases)."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from bot.research.market_events.signal_intelligence.market_replay_v1.dataset import (
    nearest_by_ts,
    normalize_trade,
)
from bot.research.market_events.signal_intelligence.market_replay_v1.frames import (
    build_market_frames,
    future_path,
    recover_frame,
    timeline_index,
)
from bot.research.market_events.signal_intelligence.market_replay_v1.library import (
    LIBRARY_TABLE,
    ensure_replay_library_schema,
    load_replays,
    upsert_replays,
)
from bot.research.market_events.signal_intelligence.market_replay_v1.liquidity import (
    event_timeline,
    liquidity_evolution,
)
from bot.research.market_events.signal_intelligence.market_replay_v1.patterns import (
    discover_replay_patterns,
    missing_data_report,
)
from bot.research.market_events.signal_intelligence.market_replay_v1.similarity import (
    build_matrix,
    cosine_distances,
    dtw_distance,
    euclidean_distances,
    price_path_from_frames,
    sequence_similarity,
    similarity_for_trade,
    top_k_similar,
    trade_feature_vector,
)
from bot.research.market_events.signal_intelligence.market_replay_v1.timeline import (
    REPLAY_OFFSETS_MIN,
    SNAPSHOT_FIELDS,
    offset_label,
)
from bot.research.market_events.signal_intelligence.market_replay_v1.report import (
    format_library_md,
    format_replay_report,
    format_similarity_md,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.market_replay_v1.engine import (
    run_market_replay_v1,
)


def _synth_trade(i: int = 0, *, edge: bool = False) -> dict:
    base = 1_700_000_000 + i * 3600
    rsi = 28.0 if edge else 55.0
    funding = -0.0002 if edge else 0.0001
    return normalize_trade({
        "trade_id": i + 1,
        "id": i + 1,
        "symbol": "BTC" if i % 2 == 0 else "ETH",
        "direction": "LONG" if edge or i % 2 == 0 else "SHORT",
        "entry": 100.0 + i * 0.1,
        "exit": 101.0 + i * 0.1,
        "pnl": 1.5 if edge else -0.4,
        "opened_at": base,
        "closed_at": base + 900,
        "rsi": rsi,
        "atr": 1.2,
        "atr_pct": 1.1,
        "funding": funding,
        "funding_delta": -0.00005,
        "oi_delta": 1.2 if edge else -0.3,
        "open_interest": 1e6,
        "fear_greed": 35.0 if edge else 60.0,
        "btc_dominance": 52.0,
        "ema20_distance": -1.0,
        "ema50_distance": -0.5,
        "ema200_distance": 0.2,
        "vwap_distance": -0.3,
        "macd": -0.1,
        "adx": 22.0,
        "stoch_k": 20.0,
        "trend": -0.4 if edge else 0.3,
        "volume": 1000.0 + i,
        "news_score": 0.2,
        "ai_score": 0.4,
        "pattern": "sweep",
        "gate": "PASS" if edge else "REJECT",
        "regime": "RISK_OFF" if edge else "RISK_ON",
        "features": {"rsi": rsi},
        "macro": {"funding": funding, "fear_greed": 35.0 if edge else 60.0},
        "news": {"news_score": 0.2, "ai_score": 0.4},
        "patterns": {"pattern": "sweep", "edge_cluster": "MR"},
        "alpha_labels": {"discovery": {"cluster": "A" if edge else "B"}},
        "optimizer_state": {"opt": 1},
        "confidence": 0.7,
    })


def _candles(entry_ts: int, n: int = 40) -> list[dict]:
    out = []
    for i in range(n):
        ts = entry_ts - (n - i) * 300
        px = 100.0 + i * 0.05
        out.append({
            "open_ts": ts,
            "open": px,
            "high": px + 0.2,
            "low": px - 0.2,
            "close": px + 0.05,
            "volume": 10.0 + i,
        })
    return out


def _snaps(entry_ts: int, n: int = 30) -> list[dict]:
    out = []
    for i in range(n):
        ts = entry_ts - (n - i) * 600
        out.append({
            "snapshot_ts": ts,
            "funding": -0.0001 + i * 1e-6,
            "oi": 1e6 + i * 1000,
            "volume": 50 + i,
            "atr": 1.0 + i * 0.01,
            "dominance": 50 + i * 0.01,
            "fear_greed": 40 + i * 0.5,
            "liquidations": i,
        })
    return out


class TestTimeline(unittest.TestCase):
    def test_offsets_include_entry_and_wings(self) -> None:
        self.assertIn(0, REPLAY_OFFSETS_MIN)
        self.assertEqual(REPLAY_OFFSETS_MIN[0], -60)
        self.assertEqual(REPLAY_OFFSETS_MIN[-1], 60)
        self.assertEqual(len(REPLAY_OFFSETS_MIN), 15)

    def test_offset_labels(self) -> None:
        self.assertEqual(offset_label(0), "ENTRY")
        self.assertEqual(offset_label(-5), "T-5m")
        self.assertEqual(offset_label(10), "+10m")

    def test_snapshot_fields_cover_brief(self) -> None:
        for f in ("price", "volume", "oi", "funding", "rsi", "regime", "gate_decision"):
            self.assertIn(f, SNAPSHOT_FIELDS)


class TestDataset(unittest.TestCase):
    def test_normalize_trade(self) -> None:
        t = _synth_trade(3, edge=True)
        self.assertEqual(t["trade_id"], 4)
        self.assertEqual(t["gate_decision"], "PASS")
        self.assertEqual(t["alpha_cluster"], "A")
        self.assertGreater(t["entry_ts"], 0)

    def test_nearest_by_ts(self) -> None:
        items = [{"open_ts": 10}, {"open_ts": 20}, {"open_ts": 30}]
        self.assertEqual(nearest_by_ts(items, 25)["open_ts"], 20)
        self.assertEqual(nearest_by_ts(items, 5)["open_ts"], 10)
        self.assertIsNone(nearest_by_ts([], 1))


class TestFrames(unittest.TestCase):
    def test_recover_and_movie(self) -> None:
        t = _synth_trade(0, edge=True)
        candles = _candles(t["entry_ts"])
        snaps = _snaps(t["entry_ts"])
        frames = build_market_frames(t, candles=candles, snapshots=snaps)
        self.assertEqual(len(frames), 15)
        labels = [f["label"] for f in frames]
        self.assertIn("ENTRY", labels)
        self.assertIn("T-60m", labels)
        self.assertIn("+60m", labels)
        entry = next(f for f in frames if f["offset_min"] == 0)
        self.assertIsNotNone(entry.get("price"))
        self.assertIn("coverage", entry)
        fp = future_path(frames)
        self.assertTrue(all(x["offset_min"] >= 0 for x in fp))
        idx = timeline_index(frames)
        self.assertIn("ENTRY", idx)

    def test_recover_without_market_data(self) -> None:
        t = _synth_trade(1)
        f = recover_frame(t, offset_min=0, candle=None, snap=None)
        self.assertEqual(f["label"], "ENTRY")
        self.assertTrue(f["source"]["lake_features"])


class TestLiquidityEvents(unittest.TestCase):
    def test_liquidity_evolution(self) -> None:
        t = _synth_trade(0, edge=True)
        frames = build_market_frames(t, candles=_candles(t["entry_ts"]), snapshots=_snaps(t["entry_ts"]))
        liq = liquidity_evolution(frames)
        self.assertTrue(liq["ok"])
        self.assertIn("volume_expansion", liq)
        self.assertIn("oi_expansion", liq)

    def test_event_timeline_types(self) -> None:
        t = _synth_trade(0, edge=True)
        frames = build_market_frames(t, candles=_candles(t["entry_ts"]), snapshots=_snaps(t["entry_ts"]))
        # force a funding flip
        frames[0]["funding"] = -0.001
        frames[5]["funding"] = 0.001
        ev = event_timeline(t, frames)
        types = {e["type"] for e in ev}
        self.assertIn("entry", types)
        self.assertIn("news", types)
        self.assertIn("macro", types)
        self.assertIn("btc_event", types)


class TestSimilarity(unittest.TestCase):
    def test_feature_vector_and_matrix(self) -> None:
        trades = [_synth_trade(i, edge=(i % 3 == 0)) for i in range(20)]
        v = trade_feature_vector(trades[0])
        self.assertGreater(len(v), 10)
        X, raw = build_matrix(trades)
        self.assertEqual(X.shape[0], 20)

    def test_euclidean_and_cosine(self) -> None:
        trades = [_synth_trade(i, edge=(i < 5)) for i in range(30)]
        X, _ = build_matrix(trades)
        d = euclidean_distances(X, 0)
        self.assertEqual(len(d), 30)
        self.assertAlmostEqual(float(d[0]), 0.0, places=5)
        c = cosine_distances(X, 0)
        self.assertEqual(len(c), 30)

    def test_dtw_and_sequence(self) -> None:
        a = np.asarray([1.0, 2.0, 3.0, 2.5])
        b = np.asarray([1.1, 2.1, 2.9, 2.4])
        self.assertLess(dtw_distance(a, b), dtw_distance(a, a + 10))
        self.assertGreater(sequence_similarity(a, b), 0.5)

    def test_top_k_and_multimetric(self) -> None:
        trades = [_synth_trade(i, edge=(i % 2 == 0)) for i in range(40)]
        paths = [price_path_from_frames(build_market_frames(t, candles=[], snapshots=[])) for t in trades]
        top = top_k_similar(trades, query_idx=0, k=10, metric="euclidean")
        self.assertEqual(len(top), 10)
        multi = similarity_for_trade(trades, 0, k=10, frame_paths=paths)
        for m in ("euclidean", "cosine", "dtw", "sequence"):
            self.assertIn(m, multi)
            self.assertGreater(len(multi[m]), 0)


class TestLibrary(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "r.db"))
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_schema_upsert_load(self) -> None:
        ensure_replay_library_schema(self.conn)
        self.assertEqual(LIBRARY_TABLE, "market_trade_replays_v1")
        n = upsert_replays(self.conn, [{
            "trade_id": 7,
            "timeline": {"ENTRY": {"ts": 1}},
            "market_frames": [{"label": "ENTRY"}],
            "events": [],
            "features": {"rsi": 30},
            "future_path": [],
            "regime": "RANGE",
            "similar_ids": [1, 2, 3],
            "quality": 0.8,
            "liquidity": {},
            "missing_report": {},
        }])
        self.assertEqual(n, 1)
        rows = load_replays(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trade_id"], 7)


class TestPatterns(unittest.TestCase):
    def test_discover_and_missing(self) -> None:
        replays = []
        for i in range(20):
            t = _synth_trade(i, edge=(i % 2 == 0))
            frames = build_market_frames(t, candles=[], snapshots=[])
            replays.append({
                "trade_id": t["trade_id"],
                "regime": t["regime"],
                "features": {"regime": t["regime"], "direction": t["direction"],
                             "gate_decision": t["gate_decision"], "alpha_cluster": t["alpha_cluster"]},
                "liquidity": liquidity_evolution(frames),
                "quality": 0.6,
                "missing_report": {"fields": {"vwap": 1}, "entry_missing": ["vwap"]},
            })
        pats = discover_replay_patterns(replays)
        self.assertGreater(len(pats), 0)
        miss = missing_data_report(replays)
        self.assertEqual(miss["n_replays"], 20)
        self.assertIn("mean_quality", miss)


class TestReport(unittest.TestCase):
    def test_formatters_and_write(self) -> None:
        result = {
            "n_rows": 10, "n_replays": 10, "replay_coverage_pct": 80,
            "mean_quality": 0.7, "elapsed_sec": 1.0,
            "load_stats": {"source": "research_lake_v1", "n_candles": 0, "n_snapshots_g3": 0},
            "missing_data_report": {"entry_field_missing_share": {"vwap": 0.5}},
            "patterns": [{"count": 3, "pattern": "X", "example_trade_ids": [1]}],
            "examples": [{"trade_id": 1, "metrics": {"euclidean": []},
                          "replay_excerpt": {"quality": 0.7, "timeline_labels": ["ENTRY"],
                                            "entry_frame": {"price": 1}, "liquidity": {}, "events": []}}],
            "library_upserted": 10, "library_rows": [], "top20_replays": [],
            "similarity_accuracy": {"metric": "x", "hit_rate": 0.5, "n_pairs": 10},
            "similar_k": 100,
            "gate_unchanged": True, "paper_unchanged": True,
            "optimizer_unchanged": True, "execution_unchanged": True, "no_n_plus_1_sql": True,
        }
        self.assertIn("REPLAY_REPORT", format_replay_report(result))
        self.assertIn("REPLAY_LIBRARY", format_library_md(result))
        self.assertIn("SIMILARITY_REPORT", format_similarity_md(result))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch(
            "bot.research.market_events.signal_intelligence.market_replay_v1.report.OUT_DIR",
            Path(tmp.name) / "out",
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_replay_v1.report.REPORT_MD",
            Path(tmp.name) / "REPLAY_REPORT.md",
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_replay_v1.report.LIBRARY_MD",
            Path(tmp.name) / "REPLAY_LIBRARY.md",
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_replay_v1.report.SIMILARITY_MD",
            Path(tmp.name) / "SIMILARITY_REPORT.md",
        ):
            paths = write_artifacts(result, replays=[])
        self.assertTrue(Path(paths["report_md"]).exists())


class TestEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "e.db"))
        self.conn.row_factory = sqlite3.Row

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_run_end_to_end(self) -> None:
        trades = [_synth_trade(i, edge=(i % 3 == 0)) for i in range(60)]
        with mock.patch(
            "bot.research.market_events.signal_intelligence.market_replay_v1.engine.load_replay_trades",
            return_value=(trades, {"source": "synth"}),
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_replay_v1.engine.preload_candles_by_symbol",
            return_value={t["symbol"]: _candles(t["entry_ts"]) for t in trades},
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_replay_v1.engine.preload_snapshots_g3",
            return_value=_snaps(trades[0]["entry_ts"]),
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_replay_v1.engine.write_artifacts",
            return_value={"report_md": "/tmp/x.md"},
        ):
            out = run_market_replay_v1(self.conn, write_reports=True, persist_library=True)
        self.assertTrue(out["ok"])
        self.assertEqual(out["n_replays"], 60)
        self.assertTrue(out["gate_unchanged"])
        self.assertTrue(out["no_n_plus_1_sql"])
        self.assertGreater(out["library_upserted"], 0)
        self.assertIn("hit_rate", out["similarity_accuracy"] or {})

    def test_cli_registered(self) -> None:
        from bot.research.market_events import __main__ as m
        src = Path(m.__file__).read_text(encoding="utf-8")
        self.assertIn('"market-replay"', src)
        self.assertIn("run_market_replay_v1", src)

    def test_research_flags(self) -> None:
        trades = [_synth_trade(i) for i in range(15)]
        with mock.patch(
            "bot.research.market_events.signal_intelligence.market_replay_v1.engine.load_replay_trades",
            return_value=(trades, {"source": "synth"}),
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_replay_v1.engine.preload_candles_by_symbol",
            return_value={},
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_replay_v1.engine.preload_snapshots_g3",
            return_value=[],
        ):
            out = run_market_replay_v1(self.conn, write_reports=False, persist_library=False)
        for k in ("gate_unchanged", "paper_unchanged", "optimizer_unchanged",
                  "strategy_unchanged", "execution_unchanged", "no_n_plus_1_sql"):
            self.assertTrue(out[k])


class TestScale(unittest.TestCase):
    def test_3k_under_budget(self) -> None:
        trades = [_synth_trade(i, edge=(i % 4 == 0)) for i in range(3000)]
        conn = sqlite3.connect(":memory:")
        t0 = time.time()
        with mock.patch(
            "bot.research.market_events.signal_intelligence.market_replay_v1.engine.load_replay_trades",
            return_value=(trades, {"source": "synth"}),
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_replay_v1.engine.preload_candles_by_symbol",
            return_value={},
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.market_replay_v1.engine.preload_snapshots_g3",
            return_value=[],
        ):
            out = run_market_replay_v1(
                conn, write_reports=False, persist_library=False, similar_k=50
            )
        elapsed = time.time() - t0
        conn.close()
        self.assertEqual(out["n_replays"], 3000)
        self.assertLess(elapsed, 90.0, f"3k replay took {elapsed:.1f}s")


class TestExtraCoverage(unittest.TestCase):
    def test_empty_liquidity(self) -> None:
        self.assertFalse(liquidity_evolution([])["ok"])

    def test_future_path_empty(self) -> None:
        self.assertEqual(future_path([]), [])

    def test_dtw_empty(self) -> None:
        self.assertEqual(dtw_distance(np.asarray([]), np.asarray([1.0])), float("inf"))

    def test_sequence_short(self) -> None:
        self.assertEqual(sequence_similarity(np.asarray([1.0]), np.asarray([1.0])), 0.0)

    def test_top_k_empty(self) -> None:
        self.assertEqual(top_k_similar([], query_idx=0, k=10), [])

    def test_build_matrix_empty(self) -> None:
        X, raw = build_matrix([])
        self.assertEqual(X.size, 0)

    def test_price_path_nan_fill(self) -> None:
        path = price_path_from_frames([
            {"price": None}, {"price": 100.0}, {"price": 101.0},
        ])
        self.assertEqual(len(path), 3)
        self.assertTrue(np.isfinite(path).all())

    def test_upsert_skips_zero_id(self) -> None:
        conn = sqlite3.connect(":memory:")
        n = upsert_replays(conn, [{"trade_id": 0, "quality": 1}])
        self.assertEqual(n, 0)
        conn.close()

    def test_patterns_empty(self) -> None:
        self.assertEqual(discover_replay_patterns([]), [])

    def test_missing_report_empty(self) -> None:
        m = missing_data_report([])
        self.assertEqual(m["n_replays"], 0)

    def test_cosine_self(self) -> None:
        trades = [_synth_trade(i) for i in range(5)]
        X, _ = build_matrix(trades)
        d = cosine_distances(X, 0)
        self.assertAlmostEqual(float(d[0]), 0.0, places=5)

    def test_frame_far_candle_rejected(self) -> None:
        t = _synth_trade(0)
        far = [{"open_ts": t["entry_ts"] - 100000, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
        frames = build_market_frames(t, candles=far, snapshots=[])
        entry = next(f for f in frames if f["offset_min"] == 0)
        self.assertFalse(entry["source"]["candle"])


if __name__ == "__main__":
    unittest.main()
