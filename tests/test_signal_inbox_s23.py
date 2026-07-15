"""Phase S2.3 — Signal Inbox + Telegram ingestion stabilization tests."""

from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import patch

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.signal_inbox_s23 import (
    format_signal_received_reply_s23,
    list_signal_inbox_s23,
    map_decision_label_s23,
    parse_signal_inbox_s23,
    process_telegram_signal_inbox_s23,
    signal_inbox_dashboard_s23,
)
from bot.research.market_events.sqlite_manager_g05 import PURE_READONLY_COMMANDS


SAMPLE_SXT = """#SXT SHORT

Entry: 0.00907

TP:
0.008966
0.008607
0.008015

SL:
0.009673
"""

SAMPLE_BTC = """$BTC LONG
Entry 64500
TP 65100
SL 64000
"""


class TestParseInboxS23(unittest.TestCase):
    def test_sxt_short(self) -> None:
        p = parse_signal_inbox_s23(SAMPLE_SXT)
        self.assertTrue(p.parsed_ok)
        self.assertEqual(p.symbol, "SXT")
        self.assertEqual(p.direction, "SHORT")
        self.assertAlmostEqual(p.entry or 0, 0.00907, places=5)
        self.assertAlmostEqual(p.stop or 0, 0.009673, places=5)
        self.assertIsNotNone(p.tp1)

    def test_btc_dollar_long(self) -> None:
        p = parse_signal_inbox_s23(SAMPLE_BTC)
        self.assertTrue(p.parsed_ok)
        self.assertEqual(p.symbol, "BTC")
        self.assertEqual(p.direction, "LONG")

    def test_russian_short(self) -> None:
        p = parse_signal_inbox_s23("#ETH ШОРТ\nEntry: 3000\nSL: 3100\nTP: 2900")
        self.assertTrue(p.parsed_ok)
        self.assertEqual(p.symbol, "ETH")
        self.assertEqual(p.direction, "SHORT")

    def test_buy_sell_aliases(self) -> None:
        self.assertEqual(parse_signal_inbox_s23("BTC BUY Entry 1 SL 0.9 TP 1.1").direction, "LONG")
        self.assertEqual(parse_signal_inbox_s23("SOL SELL Entry 100 SL 110 TP 90").direction, "SHORT")

    def test_never_drop_random_text(self) -> None:
        p = parse_signal_inbox_s23("hello random noise")
        self.assertFalse(p.parsed_ok)
        self.assertIn("missing", p.parser_reason)


class TestInboxPersistDecisionS23(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "me.db")
        configure_unit_test_db_isolation(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_always_persist_and_ro_decision(self) -> None:
        fake = {
            "decision": {"decision": "SHORT", "probability": 63, "summary": "Trend bearish", "risks": ["Conflict"]},
            "market": {"direction": "SHORT", "reasons": ["Trend DOWN"]},
            "news": {"sentiment": "bullish", "reasons": ["ETF"]},
            "run_id": None,
        }
        with patch(
            "bot.research.market_events.signal_intelligence.signal_inbox_s23.run_inbox_decision_ro_s23",
            return_value=fake,
        ):
            reply, inbox_id = process_telegram_signal_inbox_s23(
                raw_text=SAMPLE_SXT,
                chat_id=123,
                telegram_user="tester",
            )
        self.assertIsNotNone(inbox_id)
        self.assertIn("SIGNAL RECEIVED", reply)
        self.assertIn("SXT", reply)
        self.assertIn("SHORT", reply)
        self.assertIn("SELL", reply)  # decision label
        self.assertIn("63%", reply)

        with market_events_connection() as conn:
            row = conn.execute(
                "SELECT * FROM market_signal_inbox_s23 WHERE id = ?", (inbox_id,),
            ).fetchone()
            runs = conn.execute("SELECT COUNT(*) AS n FROM market_decision_runs_s20").fetchone()["n"]
        self.assertEqual(row["parsed_ok"], 1)
        self.assertEqual(row["symbol"], "SXT")
        self.assertEqual(row["decision_label"], "SELL")
        self.assertIsNone(row["decision_run_id"])
        self.assertEqual(runs, 0)

    def test_unparsed_still_persisted(self) -> None:
        reply, inbox_id = process_telegram_signal_inbox_s23(
            raw_text="random blah blah",
            chat_id=1,
            telegram_user="x",
        )
        self.assertIn("Parsed", reply)
        with market_events_connection() as conn:
            row = conn.execute(
                "SELECT parsed_ok, raw_text, status FROM market_signal_inbox_s23 WHERE id = ?",
                (inbox_id,),
            ).fetchone()
        self.assertEqual(row["parsed_ok"], 0)
        self.assertIn("random", row["raw_text"])
        self.assertEqual(row["status"], "rejected")

    def test_dashboard_filters(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            process_telegram_signal_inbox_s23(raw_text=SAMPLE_BTC, chat_id=1)
            process_telegram_signal_inbox_s23(raw_text="noise", chat_id=1)
            data = signal_inbox_dashboard_s23(conn, parsed="rejected")
            self.assertGreaterEqual(data["counts"]["rejected_n"], 1)
            rejected = list_signal_inbox_s23(conn, parsed=False)
            self.assertTrue(all(not r["parsed_ok"] for r in rejected))


class TestRoCommandsS23(unittest.TestCase):
    def test_help_is_pure_ro(self) -> None:
        self.assertIn("/help", PURE_READONLY_COMMANDS)
        self.assertIn("/market", PURE_READONLY_COMMANDS)
        self.assertIn("/status", PURE_READONLY_COMMANDS)
        self.assertIn("/decision", PURE_READONLY_COMMANDS)

    def test_map_labels(self) -> None:
        self.assertEqual(map_decision_label_s23("LONG"), "BUY")
        self.assertEqual(map_decision_label_s23("SHORT"), "SELL")
        self.assertEqual(map_decision_label_s23("FLAT"), "WAIT")

    def test_reply_format(self) -> None:
        text = format_signal_received_reply_s23(
            symbol="BTC",
            direction="LONG",
            decision_label="WAIT",
            probability=63,
            reason_lines=["News bullish", "Trend bearish", "Conflict"],
            parsed_ok=True,
            parser_reason="ok",
        )
        self.assertIn("✅ SIGNAL RECEIVED", text)
        self.assertIn("WAIT", text)
        self.assertIn("Conflict", text)


class TestLockSmokeCommandsS23(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "me.db")
        configure_unit_test_db_isolation(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_100_ro_commands_zero_locked(self) -> None:
        from bot.research.market_events.signal_intelligence.telegram_command_router_g351 import (
            handle_market_events_command,
        )

        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()

        stop = threading.Event()
        writer_errors: list[str] = []

        def _writer() -> None:
            while not stop.is_set():
                try:
                    with market_events_connection() as conn:
                        conn.execute(
                            """
                            INSERT INTO market_events_command_trace_g351 (
                              message_id, command, stage, status, reason, latency_ms, created_at
                            ) VALUES (NULL, '/s23-w', 'PULSE', 'PASS', NULL, 1, strftime('%s','now'))
                            """
                        )
                        conn.commit()
                except Exception as exc:
                    writer_errors.append(str(exc))
                stop.wait(0.01)

        w = threading.Thread(target=_writer, daemon=True)
        w.start()
        cmds = (["/status", "/decision BTC", "/help", "/market"] * 25)
        locked = 0
        try:
            with ThreadPoolExecutor(max_workers=12) as pool:
                futs = [pool.submit(handle_market_events_command, c) for c in cmds]
                for fut in as_completed(futs):
                    r = fut.result()
                    if "database is locked" in (r.reply_text or "").lower():
                        locked += 1
        finally:
            stop.set()
            w.join(timeout=2)

        self.assertEqual(locked, 0, msg=f"locked={locked} writer_errors={writer_errors[:3]}")


if __name__ == "__main__":
    unittest.main()
