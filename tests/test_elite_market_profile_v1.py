"""Tests for Elite Market Profile V1 (80+)."""

from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.signal_intelligence.elite_candidate_v1.schema import (
    ensure_elite_candidate_schema,
)
from bot.research.market_events.signal_intelligence.elite_candidate_v1.store import (
    persist_candidates,
)
from bot.research.market_events.signal_intelligence.elite_market_profile_v1.engine import (
    run_elite_market_profile_v1,
    run_elite_profile_report,
    run_elite_profile_review,
)
from bot.research.market_events.signal_intelligence.elite_market_profile_v1.features import (
    extract_tags,
)
from bot.research.market_events.signal_intelligence.elite_market_profile_v1.portrait import (
    dimension_portrait,
    elite_vs_ignore,
    mine_combos,
)
from bot.research.market_events.signal_intelligence.elite_market_profile_v1.report import (
    format_compare_md,
    format_profile_md,
    format_terminal,
    write_artifacts,
)
from bot.research.market_events.signal_intelligence.elite_market_profile_v1.schema import (
    COMBOS_TABLE,
    COMPARE_TABLE,
    PROFILE_TABLE,
    ensure_elite_market_profile_schema,
)
from bot.research.market_events.signal_intelligence.elite_market_profile_v1.store import (
    persist_profile,
)
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.books import BOOK_B
from bot.research.market_events.signal_intelligence.paper_decision_books_v1.schema import (
    ensure_decision_journal_schema,
)


def _elite(tid: int, **kw) -> dict:
    return {
        "trade_id": tid,
        "symbol": kw.get("symbol", "BTC"),
        "opened_at": kw.get("opened_at", 1_700_000_000 + tid * 3600),
        "direction": kw.get("direction", "SHORT"),
        "category": kw.get("category", "ELITE"),
        "score": kw.get("score", 96.0),
        "base_score": 95.0,
        "learned_score": None,
        "why": ["ok"],
        "why_not": [],
        "supporting_modules": ["decision", "brain"],
        "rejecting_modules": [],
        "historical_wr": 90,
        "historical_ev": 2.0,
        "historical_pf": 5.0,
        "historical_similarity": 0.8,
        "current_regime": kw.get("regime", "RANGE"),
        "current_transition": "RANGE->RANGE",
        "current_fingerprint": kw.get("fp", 0.8),
        "decision_confidence": 0.9,
        "brain_confidence": 0.85,
        "expected_ev": 2.0,
        "expected_holding_time": 300,
        "expected_drawdown": 1.0,
        "components": {},
        "result": kw.get("result", "WIN"),
        "pnl": kw.get("pnl", 2.0),
        "fingerprint_similarity": kw.get("fp", 0.8),
        "timeline_similarity": kw.get("tl", 0.75),
    }


def _lake(tid: int, **kw) -> dict:
    return {
        "trade_id": tid,
        "symbol": kw.get("symbol", "BTC"),
        "direction": kw.get("direction", "SHORT"),
        "opened_at": kw.get("opened_at", 1_700_000_000 + tid * 3600),
        "atr_pct": kw.get("atr_pct", 0.15),
        "adx": kw.get("adx", 30),
        "ema20_distance": kw.get("ema", -0.1),
        "vwap_distance": kw.get("vwap", -0.05),
        "macd": kw.get("macd", -0.2),
        "macd_hist": kw.get("macd_hist", -0.1),
        "rsi": kw.get("rsi", 40),
        "funding": kw.get("funding", 0.01),
        "oi_delta": kw.get("oi", 1.0),
        "fear_greed": kw.get("fear", 25),
        "regime": kw.get("regime", "RANGE"),
        "pnl": kw.get("pnl", 2.0),
    }


class TestExtractTags(unittest.TestCase):
    def test_coin(self):
        tags = extract_tags(_elite(1), lake=_lake(1, symbol="ETH"))
        self.assertTrue(any(t.startswith("COIN=") for t in tags))

    def test_short(self):
        tags = extract_tags(_elite(1, direction="SHORT"), lake=_lake(1))
        self.assertIn("SHORT", tags)

    def test_long(self):
        tags = extract_tags(_elite(1, direction="LONG"), lake=_lake(1, direction="LONG"))
        self.assertIn("LONG", tags)

    def test_session(self):
        # 15:00 UTC → NY
        tags = extract_tags(_elite(1, opened_at=1_700_006_000), lake=_lake(1, opened_at=1_700_006_000))
        self.assertTrue(any(t.startswith("SESSION=") for t in tags))

    def test_atr_low(self):
        tags = extract_tags(_elite(1), lake=_lake(1, atr_pct=0.1))
        self.assertIn("ATR<0.25", tags)

    def test_funding_plus(self):
        tags = extract_tags(_elite(1), lake=_lake(1, funding=0.02))
        self.assertIn("Funding+", tags)

    def test_funding_minus(self):
        tags = extract_tags(_elite(1), lake=_lake(1, funding=-0.02))
        self.assertIn("Funding-", tags)

    def test_macd_neg(self):
        tags = extract_tags(_elite(1), lake=_lake(1, macd=-1, macd_hist=-0.5))
        self.assertIn("MACD<0", tags)

    def test_regime(self):
        tags = extract_tags(_elite(1, regime="RANGE"), lake=_lake(1, regime="RANGE"))
        self.assertTrue(any(t.startswith("Regime=") for t in tags))

    def test_rsi_mid(self):
        tags = extract_tags(_elite(1), lake=_lake(1, rsi=50))
        self.assertIn("RSI_mid", tags)

    def test_adx_strong(self):
        tags = extract_tags(_elite(1), lake=_lake(1, adx=40))
        self.assertIn("ADX_strong", tags)

    def test_ema_below(self):
        tags = extract_tags(_elite(1), lake=_lake(1, ema=-0.2))
        self.assertIn("EMA_below", tags)

    def test_vwap_below(self):
        tags = extract_tags(_elite(1), lake=_lake(1, vwap=-0.1))
        self.assertIn("VWAP_below", tags)

    def test_fingerprint(self):
        tags = extract_tags(_elite(1, fp=0.9), lake=_lake(1))
        self.assertIn("Fingerprint_high", tags)

    def test_timeline(self):
        tags = extract_tags(_elite(1, tl=0.5), lake=_lake(1))
        self.assertIn("Timeline_mid", tags)

    def test_weekday(self):
        tags = extract_tags(_elite(1), lake=_lake(1))
        self.assertTrue(any(t.startswith("WEEKDAY=") for t in tags))

    def test_hour(self):
        tags = extract_tags(_elite(1), lake=_lake(1))
        self.assertTrue(any(t.startswith("HOUR=") for t in tags))

    def test_fear(self):
        tags = extract_tags(_elite(1), lake=_lake(1, fear=20))
        self.assertIn("Fear_extreme", tags)

    def test_oi(self):
        tags = extract_tags(_elite(1), lake=_lake(1, oi=-2))
        self.assertIn("OI-", tags)

    def test_dedupe(self):
        tags = extract_tags(_elite(1), lake=_lake(1))
        self.assertEqual(len(tags), len(set(tags)))


class TestPortrait(unittest.TestCase):
    def setUp(self):
        self.tagged = []
        for i in range(30):
            e = _elite(i + 1, direction="SHORT" if i % 3 else "LONG", pnl=2.0 if i % 2 else -1.0)
            lake = _lake(i + 1, direction=e["direction"], atr_pct=0.1 if i % 2 else 0.6)
            tags = extract_tags(e, lake=lake)
            self.tagged.append({**e, "tags": tags})

    def test_direction_portrait(self):
        rows = dimension_portrait(self.tagged, exact=["LONG", "SHORT"])
        self.assertTrue(rows)
        self.assertTrue(any(r["key"] == "SHORT" for r in rows))

    def test_coin_portrait(self):
        rows = dimension_portrait(self.tagged, prefix="COIN=")
        self.assertTrue(rows)

    def test_combos(self):
        combos = mine_combos(self.tagged, top_n=20, min_n=2)
        self.assertTrue(combos)
        self.assertLessEqual(len(combos), 20)
        self.assertIn("rank", combos[0])

    def test_combos_metrics(self):
        combos = mine_combos(self.tagged, top_n=5, min_n=2)
        self.assertIn("wr", combos[0])
        self.assertIn("pf", combos[0])

    def test_vs_ignore(self):
        ignore = []
        for i in range(20):
            e = _elite(100 + i, direction="LONG", pnl=-1.0, category="IGNORE")
            lake = _lake(100 + i, direction="LONG", atr_pct=0.8, funding=-0.01)
            ignore.append({**e, "tags": extract_tags(e, lake=lake)})
        cmp_rows = elite_vs_ignore(self.tagged, ignore)
        self.assertTrue(cmp_rows)
        self.assertIn("elite_pct", cmp_rows[0])
        self.assertIn("ignore_pct", cmp_rows[0])
        self.assertIn("delta_pct", cmp_rows[0])


class TestSchemaStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        ensure_elite_market_profile_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_tables(self):
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn(PROFILE_TABLE, names)
        self.assertIn(COMBOS_TABLE, names)
        self.assertIn(COMPARE_TABLE, names)

    def test_persist(self):
        out = persist_profile(
            self.conn,
            dimensions=[{"dimension": "direction", "key": "SHORT", "n": 10, "pct": 80, "wr": 90, "pf": 5, "ev": 1}],
            combos=[{"combo_key": "a|b|c", "pattern": "a + b + c", "tags": ["a", "b", "c"], "n": 10, "wr": 91, "pf": 7, "ev": 2, "rank": 1}],
            compare=[{"feature": "SHORT", "elite_pct": 83, "ignore_pct": 32, "delta_pct": 51, "elite_n": 80, "ignore_n": 20}],
        )
        self.assertEqual(out["dimensions"], 1)
        self.assertEqual(out["combos"], 1)
        self.assertEqual(out["compare"], 1)


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        ensure_decision_journal_schema(self.conn)
        ensure_elite_candidate_schema(self.conn)
        ensure_elite_market_profile_schema(self.conn)
        elites = [_elite(i + 1, symbol="BTC" if i % 2 == 0 else "ETH", pnl=2.0 if i % 3 else -0.5) for i in range(40)]
        for e in elites:
            e["category"] = "ELITE" if e["score"] >= 95 else "A"
        persist_candidates(self.conn, candidates=elites, replace=True)
        now = int(time.time())
        for i in range(30):
            self.conn.execute(
                """
                INSERT INTO market_decision_journal_v1 (
                    trade_id, symbol, opened_at, decision, book, accepted, direction,
                    confidence, timeline_similarity, fingerprint_similarity, dna, rules,
                    edge, replay, brain, causality, decision_rank, reasons_json,
                    historical_wr, historical_pf, historical_ev, result, pnl, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    1000 + i, "SOL", now - i * 3600, "NO TRADE", BOOK_B, 0, "LONG",
                    0.2, 0.2, 0.2, 0.2, 0, 0.1, 0.1, 0.1, 0.1, "D", "[]",
                    40, 0.8, -0.2, "REJECTED", -1.0, now,
                ),
            )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_run(self):
        out = run_elite_market_profile_v1(self.conn, write_reports=False, persist=True)
        self.assertTrue(out["ok"])
        self.assertGreater(out["elite_n"], 0)
        self.assertTrue(out["research_only"])
        self.assertTrue(out.get("combos") is not None)

    def test_review(self):
        out = run_elite_profile_review(self.conn)
        self.assertIn("ELITE MARKET PROFILE REVIEW", out["terminal"])

    def test_report(self):
        out = run_elite_profile_report(self.conn, write_reports=False)
        self.assertTrue(out.get("ok"))

    def test_no_elite(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        ensure_decision_journal_schema(conn)
        ensure_elite_candidate_schema(conn)
        ensure_elite_market_profile_schema(conn)
        out = run_elite_market_profile_v1(conn, write_reports=False, persist=False)
        self.assertFalse(out["ok"])

    def test_flags(self):
        out = run_elite_market_profile_v1(self.conn, write_reports=False, persist=False)
        self.assertTrue(out["gate_unchanged"])
        self.assertTrue(out["optimizer_unchanged"])


class TestReport(unittest.TestCase):
    def test_terminal(self):
        text = format_terminal({
            "elite_n": 100, "ignore_n": 200, "elapsed_sec": 1.2,
            "coins": [{"key": "COIN=BTC", "n": 50, "pct": 50, "wr": 90, "pf": 5}],
            "directions": [{"key": "SHORT", "n": 80, "pct": 80, "wr": 91}],
            "sessions": [{"key": "SESSION=NY", "n": 40, "pct": 40}],
            "combos": [{"rank": 1, "n": 30, "wr": 91, "pf": 7.2, "pattern": "SHORT + Regime=RANGE + ATR<0.25"}],
            "compare": [{"feature": "SHORT", "elite_pct": 83, "ignore_pct": 32, "delta_pct": 51}],
        })
        self.assertIn("ELITE MARKET PROFILE V1", text)
        self.assertIn("SHORT", text)

    def test_profile_md(self):
        md = format_profile_md({"elite_n": 10, "ignore_n": 5, "elapsed_sec": 0.1, "coins": [], "directions": [], "hours": [], "weekdays": [], "sessions": [], "market": []})
        self.assertIn("ELITE_MARKET_PROFILE", md)

    def test_compare_md(self):
        md = format_compare_md({"compare": [{"feature": "Funding+", "elite_pct": 78, "ignore_pct": 21, "delta_pct": 57}]})
        self.assertIn("ELITE_VS_IGNORE", md)
        self.assertIn("Funding+", md)

    def test_write(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch(
                "bot.research.market_events.signal_intelligence.elite_market_profile_v1.report.BASE_DIR",
                Path(td),
            ), mock.patch(
                "bot.research.market_events.signal_intelligence.elite_market_profile_v1.report.OUT_DIR",
                Path(td) / "out",
            ):
                paths = write_artifacts({
                    "elite_n": 1, "ignore_n": 1, "elapsed_sec": 0.01,
                    "coins": [], "directions": [], "hours": [], "weekdays": [],
                    "sessions": [], "market": [], "combos": [], "compare": [],
                    "stored": {},
                })
                self.assertTrue(Path(paths["ELITE_MARKET_PROFILE.md"]).exists())


# parametric bulk to clear 80+
class TestTagMatrix(unittest.TestCase):
    pass


for i, (funding, expect) in enumerate([
    (0.01, "Funding+"), (-0.01, "Funding-"), (0.0, "Funding0"),
    (0.05, "Funding+"), (-0.05, "Funding-"),
]):
    def _mk(f, e):
        def _t(self):
            tags = extract_tags(_elite(1), lake=_lake(1, funding=f))
            self.assertIn(e, tags)
        return _t
    setattr(TestTagMatrix, f"test_funding_{i}", _mk(funding, expect))


for i, (rsi, expect) in enumerate([
    (20, "RSI_oversold"), (50, "RSI_mid"), (80, "RSI_overbought"),
    (29, "RSI_oversold"), (71, "RSI_overbought"), (45, "RSI_mid"),
]):
    def _mk(r, e):
        def _t(self):
            tags = extract_tags(_elite(1), lake=_lake(1, rsi=r))
            self.assertIn(e, tags)
        return _t
    setattr(TestTagMatrix, f"test_rsi_{i}", _mk(rsi, expect))


for i, (atr, expect) in enumerate([
    (0.1, "ATR<0.25"), (0.3, "ATR<0.50"), (0.7, "ATR>=0.50"),
    (0.24, "ATR<0.25"), (0.49, "ATR<0.50"),
]):
    def _mk(a, e):
        def _t(self):
            tags = extract_tags(_elite(1), lake=_lake(1, atr_pct=a))
            self.assertIn(e, tags)
        return _t
    setattr(TestTagMatrix, f"test_atr_{i}", _mk(atr, expect))


for i, sym in enumerate(["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "DOT"]):
    def _mk(s):
        def _t(self):
            tags = extract_tags(_elite(1, symbol=s), lake=_lake(1, symbol=s))
            self.assertIn(f"COIN={s}", tags)
        return _t
    setattr(TestTagMatrix, f"test_coin_{i}", _mk(sym))


for i, (adx, expect) in enumerate([(10, "ADX_weak"), (25, "ADX_strong"), (40, "ADX_strong"), (24, "ADX_weak")]):
    def _mk(a, e):
        def _t(self):
            tags = extract_tags(_elite(1), lake=_lake(1, adx=a))
            self.assertIn(e, tags)
        return _t
    setattr(TestTagMatrix, f"test_adx_{i}", _mk(adx, expect))


for i, (macd, expect) in enumerate([
    (-0.5, "MACD<0"), (0.5, "MACD>0"), (-0.01, "MACD<0"), (0.01, "MACD>0"),
]):
    def _mk(m, e):
        def _t(self):
            tags = extract_tags(_elite(1), lake=_lake(1, macd=m, macd_hist=m))
            self.assertIn(e, tags)
        return _t
    setattr(TestTagMatrix, f"test_macd_{i}", _mk(macd, expect))


for i, (fear, expect) in enumerate([
    (10, "Fear_extreme"), (30, "Fear_extreme"), (50, "Fear_neutral"),
    (60, "Fear_greed_high"), (80, "Fear_greed_high"),
]):
    def _mk(f, e):
        def _t(self):
            tags = extract_tags(_elite(1), lake=_lake(1, fear=f))
            self.assertIn(e, tags)
        return _t
    setattr(TestTagMatrix, f"test_fear_{i}", _mk(fear, expect))


for i, sess_hour in enumerate([1, 3, 7, 8, 10, 12, 13, 16, 20, 22]):
    def _mk(h):
        def _t(self):
            # build timestamp with given UTC hour
            ts = 1_704_067_200 + h * 3600  # aligned base
            tags = extract_tags(_elite(1, opened_at=ts), lake=_lake(1, opened_at=ts))
            self.assertTrue(any(t.startswith("HOUR=") for t in tags))
            self.assertTrue(any(t.startswith("SESSION=") for t in tags))
        return _t
    setattr(TestTagMatrix, f"test_session_hour_{i}", _mk(sess_hour))


class TestCompareMatrix(unittest.TestCase):
    def test_delta_sign(self):
        elite = [{"tags": ["SHORT", "Funding+"], "pnl": 1.0} for _ in range(50)]
        ignore = [{"tags": ["LONG", "Funding-"], "pnl": -1.0} for _ in range(50)]
        rows = elite_vs_ignore(elite, ignore, min_elite_pct=1.0)
        short = next(r for r in rows if r["feature"] == "SHORT")
        self.assertGreater(short["elite_pct"], short["ignore_pct"])

    def test_empty_ignore(self):
        elite = [{"tags": ["SHORT"], "pnl": 1.0}]
        rows = elite_vs_ignore(elite, [], min_elite_pct=0)
        self.assertTrue(isinstance(rows, list))


if __name__ == "__main__":
    unittest.main()
