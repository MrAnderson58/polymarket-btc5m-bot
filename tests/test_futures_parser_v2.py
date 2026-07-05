"""Tests for deterministic_v2 futures parser, taxonomy, and signal gate."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from bot.database import connect, init_db
from bot.research.futures.config import PARSER_VERSION, PARSER_VERSION_V2
from bot.research.futures.parse_pipeline import parse_and_store_messages
from bot.research.futures.parser_v2 import extract_symbol_v2, parse_signal_v2
from bot.research.futures.schema import PARSE_AUDIT_TABLE, SIGNALS_TABLE, ensure_tables
from bot.research.futures.source_reader import SqliteSourceReader
from bot.research.futures.taxonomy import MessageType, classify_message


EXPLICIT_LONG = (
    "BTC LONG\n"
    "Entry: 95000-95200\n"
    "SL: 94000\n"
    "TP1: 96000\n"
    "TP2: 97000\n"
    "Leverage 10x"
)

EXPLICIT_SHORT = (
    "ETH SHORT\n"
    "Entry: 3500\n"
    "SL: 3600\n"
    "TP: 3400"
)

MARKET_REVIEW_TICKER = (
    "ATOM Technical Analysis / Review\n"
    "Price action on ATOMUSDT shows consolidation.\n"
    "Outlook: neutral to bullish over the next week.\n"
    "Not financial advice."
)

ALTCOIN_REVIEW_BTC_BODY = (
    "ICP Market Review\n"
    "ICP/USDT broke support at 12.50.\n"
    "Similar to BTC movement last week, momentum is weak.\n"
    "ICP correlates with BTC in risk-off environments.\n"
    "LONG-term holders may wait for reversal."
)

PROMO_URL_TICKER = (
    "Join our VIP channel: t.me/signalyp_vip\n"
    "Learn to trade LINK like a pro — subscribe for 20% discount!"
)

TP_UPDATE = (
    "BTC TP1 hit! 🎯\n"
    "Take profit reached at 96000.\n"
    "+2.1% on the position."
)

SL_UPDATE = (
    "ETH stop triggered\n"
    "SL hit at 3600. Position closed at loss."
)

POSITION_CLOSE = (
    "Closed SOL LONG manually\n"
    "Fixed profit before news event. Position closed."
)

MALFORMED = (
    "LONG maybe something\n"
    "numbers everywhere 123 456 but no symbol header"
)


class TaxonomyTestCase(unittest.TestCase):
    def test_explicit_long_signal(self) -> None:
        tax = classify_message(EXPLICIT_LONG)
        self.assertEqual(tax.message_type, MessageType.EXPLICIT_SIGNAL)

    def test_market_review_not_signal(self) -> None:
        tax = classify_message(MARKET_REVIEW_TICKER)
        self.assertEqual(tax.message_type, MessageType.MARKET_REVIEW)

    def test_promo_not_signal(self) -> None:
        tax = classify_message(PROMO_URL_TICKER)
        self.assertEqual(tax.message_type, MessageType.PROMO)

    def test_tp_hit(self) -> None:
        tax = classify_message(TP_UPDATE)
        self.assertEqual(tax.message_type, MessageType.TP_HIT)

    def test_sl_hit(self) -> None:
        tax = classify_message(SL_UPDATE)
        self.assertEqual(tax.message_type, MessageType.SL_HIT)

    def test_position_close(self) -> None:
        tax = classify_message(POSITION_CLOSE)
        self.assertEqual(tax.message_type, MessageType.POSITION_CLOSE)


class ParserV2TestCase(unittest.TestCase):
    def test_explicit_long_passes_gate(self) -> None:
        r = parse_signal_v2(EXPLICIT_LONG)
        self.assertTrue(r.passes_gate)
        self.assertEqual(r.parsed.symbol, "BTC")
        self.assertEqual(r.parsed.side, "LONG")
        self.assertEqual(r.parsed.entry_min, 95000.0)
        self.assertEqual(r.parsed.stop_loss, 94000.0)
        self.assertEqual(r.parsed.take_profits, [96000.0, 97000.0])

    def test_explicit_short_passes_gate(self) -> None:
        r = parse_signal_v2(EXPLICIT_SHORT)
        self.assertTrue(r.passes_gate)
        self.assertEqual(r.parsed.symbol, "ETH")
        self.assertEqual(r.parsed.side, "SHORT")

    def test_market_review_blocked(self) -> None:
        r = parse_signal_v2(MARKET_REVIEW_TICKER)
        self.assertFalse(r.passes_gate)
        self.assertEqual(r.message_type, MessageType.MARKET_REVIEW)
        sym, _ = extract_symbol_v2(MARKET_REVIEW_TICKER)
        if sym:
            self.assertNotEqual(sym, "BTC")

    def test_altcoin_review_not_btc(self) -> None:
        sym, reason = extract_symbol_v2(ALTCOIN_REVIEW_BTC_BODY)
        self.assertEqual(sym, "ICP")
        self.assertNotEqual(sym, "BTC")
        r = parse_signal_v2(ALTCOIN_REVIEW_BTC_BODY)
        self.assertFalse(r.passes_gate)

    def test_promo_blocked(self) -> None:
        r = parse_signal_v2(PROMO_URL_TICKER)
        self.assertFalse(r.passes_gate)
        self.assertEqual(r.message_type, MessageType.PROMO)

    def test_tp_update_not_stored_as_signal(self) -> None:
        r = parse_signal_v2(TP_UPDATE)
        self.assertFalse(r.passes_gate)
        self.assertEqual(r.message_type, MessageType.TP_HIT)

    def test_sl_update_not_stored_as_signal(self) -> None:
        r = parse_signal_v2(SL_UPDATE)
        self.assertFalse(r.passes_gate)

    def test_position_close_not_stored_as_signal(self) -> None:
        r = parse_signal_v2(POSITION_CLOSE)
        self.assertFalse(r.passes_gate)

    def test_malformed_fails_gate(self) -> None:
        r = parse_signal_v2(MALFORMED)
        self.assertFalse(r.passes_gate)


class ParserV2PipelineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.source_db = Path(self._tmpdir.name) / "source.db"
        self.research_db = Path(self._tmpdir.name) / "research.db"
        conn = sqlite3.connect(self.source_db)
        conn.executescript("""
            CREATE TABLE telegram_messages (
                id INTEGER PRIMARY KEY,
                source TEXT,
                text TEXT,
                timestamp INTEGER,
                message_id TEXT
            );
        """)
        rows = [
            ("signalyp", EXPLICIT_LONG, 1700000000, "s1"),
            ("signalyp", MARKET_REVIEW_TICKER, 1700000100, "s2"),
            ("signalyp", ALTCOIN_REVIEW_BTC_BODY, 1700000200, "s3"),
            ("signalyp", PROMO_URL_TICKER, 1700000300, "s4"),
        ]
        conn.executemany(
            "INSERT INTO telegram_messages (source, text, timestamp, message_id) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        conn.close()
        init_db(self.research_db)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_v2_only_stores_gated_signals(self) -> None:
        reader = SqliteSourceReader(sqlite3.connect(self.source_db))
        with connect(self.research_db) as research_conn:
            ensure_tables(research_conn)
            stats = parse_and_store_messages(
                research_conn, reader,
                source_filter="signalyp",
                parser_version=PARSER_VERSION_V2,
            )
            research_conn.commit()
            v2_count = research_conn.execute(
                f"SELECT COUNT(*) FROM {SIGNALS_TABLE} WHERE parser_version = ?",
                (PARSER_VERSION_V2,),
            ).fetchone()[0]
            v1_count = research_conn.execute(
                f"SELECT COUNT(*) FROM {SIGNALS_TABLE} WHERE parser_version = ?",
                (PARSER_VERSION,),
            ).fetchone()[0]
        reader.close()
        self.assertEqual(stats["inserted"], 1)
        self.assertEqual(v2_count, 1)
        self.assertEqual(v1_count, 0)
        self.assertGreaterEqual(stats["skipped_gate"], 2)

    def test_v1_and_v2_coexist(self) -> None:
        reader = SqliteSourceReader(sqlite3.connect(self.source_db))
        with connect(self.research_db) as research_conn:
            ensure_tables(research_conn)
            parse_and_store_messages(
                research_conn, reader,
                source_filter="signalyp",
                parser_version=PARSER_VERSION,
            )
            parse_and_store_messages(
                research_conn, reader,
                source_filter="signalyp",
                parser_version=PARSER_VERSION_V2,
            )
            research_conn.commit()
            total = research_conn.execute(f"SELECT COUNT(*) FROM {SIGNALS_TABLE}").fetchone()[0]
            audit = research_conn.execute(f"SELECT COUNT(*) FROM {PARSE_AUDIT_TABLE}").fetchone()[0]
        reader.close()
        self.assertGreaterEqual(total, 1)
        self.assertGreaterEqual(audit, 4)


class ParserQualityAuditTestCase(unittest.TestCase):
    def test_audit_on_synthetic_corpus(self) -> None:
        from bot.research.futures.parser_quality_audit import run_parser_audit

        conn = sqlite3.connect(":memory:")
        conn.executescript("""
            CREATE TABLE telegram_messages (
                id INTEGER PRIMARY KEY,
                source TEXT,
                text TEXT,
                timestamp INTEGER,
                message_id TEXT
            );
        """)
        corpus = [
            EXPLICIT_LONG, EXPLICIT_SHORT, MARKET_REVIEW_TICKER,
            ALTCOIN_REVIEW_BTC_BODY, PROMO_URL_TICKER, TP_UPDATE,
            SL_UPDATE, POSITION_CLOSE, MALFORMED,
        ]
        for i, text in enumerate(corpus):
            conn.execute(
                "INSERT INTO telegram_messages VALUES (?, 'signalyp', ?, ?, ?)",
                (i + 1, text, 1700000000 + i * 100, f"m{i}"),
            )
        conn.commit()
        reader = SqliteSourceReader(conn)
        report = run_parser_audit(source=reader, source_filter="signalyp", sample_size=9)
        self.assertEqual(report["validation_sample_size"], 9)
        self.assertEqual(report["v2"]["review_leaks"], 0)
        self.assertGreater(report["v2"]["explicit_precision"], 0.5)


if __name__ == "__main__":
    unittest.main()
