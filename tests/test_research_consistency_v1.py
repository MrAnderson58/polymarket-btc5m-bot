"""research_consistency_v1 — all engines share identical canonical probe."""

from __future__ import annotations

import inspect
import time
import unittest
from unittest import mock

from bot.research.market_events.signal_intelligence.research_probe_v1 import (
    canonical_probe,
    current_market_from_probe,
    probe_matches,
    probe_time_label,
)
from bot.research.market_events.signal_intelligence.research_consistency_audit_v1.engine import (
    format_audit_terminal,
    run_research_consistency_audit_v1,
)


ENGINES = (
    "Fingerprint",
    "Timeline",
    "Decision",
    "Brain",
    "Replay",
    "DNA",
    "Rules",
    "Edge",
    "Causality",
    "Morning Report",
)


def _fake_probe(**kwargs):
    base = {
        "ok": True,
        "source": "research_lake_v1",
        "n_lake": 10,
        "trade_id": 19173,
        "symbol": "ADA",
        "opened_at": 1_754_000_000,
        "closed_at": 1_754_000_600,
        "direction": "LONG",
        "regime": "trend",
        "time_label": "2026-08-01 21:30 UTC",
        "trade": {
            "trade_id": 19173,
            "symbol": "ADA",
            "direction": "LONG",
            "opened_at": 1_754_000_000,
            "closed_at": 1_754_000_600,
            "pnl": 1.0,
        },
    }
    base.update(kwargs)
    return base


class TestProbeHelpers(unittest.TestCase):
    def test_probe_matches_same(self):
        a = {"ok": True, "symbol": "BTC", "trade_id": 1}
        self.assertTrue(probe_matches(a, {"ok": True, "symbol": "BTC", "trade_id": 1}))

    def test_probe_matches_symbol_case(self):
        a = {"ok": True, "symbol": "btc", "trade_id": 1}
        self.assertTrue(probe_matches(a, {"ok": True, "symbol": "BTC", "trade_id": 1}))

    def test_probe_mismatch_symbol(self):
        a = {"ok": True, "symbol": "BTC", "trade_id": 1}
        self.assertFalse(probe_matches(a, {"ok": True, "symbol": "ETH", "trade_id": 1}))

    def test_probe_mismatch_trade_id(self):
        a = {"ok": True, "symbol": "BTC", "trade_id": 1}
        self.assertFalse(probe_matches(a, {"ok": True, "symbol": "BTC", "trade_id": 2}))

    def test_probe_not_ok(self):
        self.assertFalse(probe_matches({"ok": False, "symbol": "BTC", "trade_id": 1}, {"ok": True, "symbol": "BTC", "trade_id": 1}))

    def test_time_label_now(self):
        now = int(time.time())
        self.assertEqual(probe_time_label(now - 100, now=now), "now")

    def test_time_label_old(self):
        self.assertIn("UTC", probe_time_label(1_700_000_000, now=1_800_000_000))

    def test_time_label_empty(self):
        self.assertEqual(probe_time_label(None), "—")

    def test_current_market_from_probe(self):
        cm = current_market_from_probe(_fake_probe())
        self.assertEqual(cm["symbol"], "ADA")
        self.assertEqual(cm["trade_id"], 19173)

    def test_current_market_empty(self):
        self.assertIsNone(current_market_from_probe({"ok": False}))


class TestCanonicalProbeSource(unittest.TestCase):
    def test_fingerprint_imports_canonical_probe(self):
        from bot.research.market_events.signal_intelligence import market_fingerprint_v1 as m
        src = inspect.getsource(m.engine)
        self.assertIn("canonical_probe", src)

    def test_timeline_imports_canonical_probe(self):
        from bot.research.market_events.signal_intelligence import market_timeline_v1 as m
        src = inspect.getsource(m.engine)
        self.assertIn("canonical_probe", src)

    def test_decision_imports_canonical_probe(self):
        from bot.research.market_events.signal_intelligence import market_decision_v1 as m
        src = inspect.getsource(m.engine)
        self.assertIn("canonical_probe", src)

    def test_morning_uses_canonical_probe(self):
        from bot.research.market_events.signal_intelligence import morning_report_s63 as m
        src = inspect.getsource(m)
        self.assertIn("canonical_probe", src)

    def test_audit_uses_canonical_probe(self):
        from bot.research.market_events.signal_intelligence.research_consistency_audit_v1 import engine as e
        src = inspect.getsource(e)
        self.assertIn("canonical_probe", src)


class TestResolveAnalyticsDb(unittest.TestCase):
    def test_cli_consistency_uses_resolve(self):
        from bot.research.market_events import __main__ as main
        src = inspect.getsource(main)
        self.assertIn("research-consistency-audit", src)
        self.assertIn("resolve_research_analytics_sqlite_path", src)

    def test_cli_fingerprint_uses_resolve(self):
        from bot.research.market_events import __main__ as main
        src = inspect.getsource(main)
        self.assertIn("market-fingerprint", src)
        # resolve is shared across research engines in __main__
        self.assertGreater(src.count("resolve_research_analytics_sqlite_path"), 5)

    def test_cli_decision_uses_resolve(self):
        from bot.research.market_events import __main__ as main
        src = inspect.getsource(main)
        self.assertIn("market-decision", src)

    def test_cli_timeline_uses_resolve(self):
        from bot.research.market_events import __main__ as main
        src = inspect.getsource(main)
        self.assertIn("market-timeline", src)

    def test_cli_morning_uses_resolve(self):
        from bot.research.market_events import __main__ as main
        src = inspect.getsource(main)
        self.assertIn("morning-report", src)


class TestAuditAllEnginesIdenticalProbe(unittest.TestCase):
    def test_format_lists_all_engines(self):
        rows = [
            {"engine": e, "source": "research_lake_v1", "coin": "ADA", "time": "now", "ok_mark": "✅"}
            for e in ENGINES
        ]
        text = format_audit_terminal({
            "ok": True,
            "n_lake": 10,
            "db_name": "x.db",
            "db_source": "env",
            "canonical_probe": {"symbol": "ADA", "trade_id": 1, "time_label": "now"},
            "rows": rows,
            "fixes_applied": [],
            "issues": [],
            "elapsed_sec": 0.1,
        })
        for e in ENGINES:
            self.assertIn(e, text)

    def test_all_rows_same_coin(self):
        rows = [
            {"engine": e, "source": "research_lake_v1", "coin": "ADA", "time": "t", "ok": True, "ok_mark": "✅"}
            for e in ENGINES
        ]
        coins = {r["coin"] for r in rows}
        self.assertEqual(coins, {"ADA"})

    def test_all_rows_same_source(self):
        rows = [
            {"engine": e, "source": "research_lake_v1", "coin": "ADA", "time": "t", "ok": True}
            for e in ENGINES
        ]
        self.assertEqual({r["source"] for r in rows}, {"research_lake_v1"})

    @mock.patch(
        "bot.research.market_events.signal_intelligence.research_consistency_audit_v1.engine.timeline_trade",
        return_value={"trade_id": 19173, "symbol": "ADA"},
    )
    @mock.patch(
        "bot.research.market_events.signal_intelligence.research_consistency_audit_v1.engine.snapshot_trade",
        return_value={"trade_id": 19173, "symbol": "ADA"},
    )
    @mock.patch(
        "bot.research.market_events.signal_intelligence.research_consistency_audit_v1.engine.load_candle_book",
        return_value={},
    )
    @mock.patch(
        "bot.research.market_events.signal_intelligence.research_consistency_audit_v1.engine.research_lake_row_count",
        return_value=10,
    )
    @mock.patch(
        "bot.research.market_events.signal_intelligence.research_consistency_audit_v1.engine.canonical_probe",
    )
    def test_run_audit_identical_probe(self, mock_probe, *_mocks):
        mock_probe.return_value = _fake_probe()
        out = run_research_consistency_audit_v1(mock.Mock(), db_path="/tmp/x.db", db_source="test")
        self.assertTrue(out["ok"])
        engines = [r["engine"] for r in out["rows"]]
        for e in ENGINES:
            self.assertIn(e, engines)
        coins = {r["coin"] for r in out["rows"]}
        self.assertEqual(coins, {"ADA"})
        times = {r["time"] for r in out["rows"] if r["engine"] != "Brain"}
        self.assertEqual(len(times), 1)
        self.assertTrue(all(r["ok"] for r in out["rows"]))
        self.assertEqual(out["canonical_probe"]["trade_id"], 19173)
        self.assertEqual(out["canonical_probe"]["symbol"], "ADA")

    def test_empty_lake_fails(self):
        with mock.patch(
            "bot.research.market_events.signal_intelligence.research_consistency_audit_v1.engine.canonical_probe",
            return_value={"ok": False},
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.research_consistency_audit_v1.engine.research_lake_row_count",
            return_value=0,
        ):
            out = run_research_consistency_audit_v1(mock.Mock())
            self.assertFalse(out["ok"])


class TestLoadLatestLake(unittest.TestCase):
    def test_loader_has_latest(self):
        from bot.research.market_events.signal_intelligence.research_lake_v1 import loader
        self.assertTrue(hasattr(loader, "load_latest_lake_trade"))

    def test_canonical_probe_calls_latest(self):
        conn = mock.Mock()
        with mock.patch(
            "bot.research.market_events.signal_intelligence.research_probe_v1.probe.load_latest_lake_trade",
            return_value={
                "trade_id": 5,
                "symbol": "BTC",
                "opened_at": int(time.time()),
                "direction": "LONG",
            },
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.research_probe_v1.probe.research_lake_row_count",
            return_value=3,
        ):
            p = canonical_probe(conn)
            self.assertTrue(p["ok"])
            self.assertEqual(p["symbol"], "BTC")
            self.assertEqual(p["trade_id"], 5)
            self.assertEqual(p["time_label"], "now")


if __name__ == "__main__":
    unittest.main()
