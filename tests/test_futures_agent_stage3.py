"""Tests for Futures Agent Stage 3 research pipeline (Phase B)."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.research.futures_agent.env_bootstrap import reset_bootstrap_for_tests
from bot.research.futures_agent.db import agent_connection
from bot.research.futures_agent.research_classify_audit import (
    is_structural_explicit_candidate,
    render_explicit_recall_audit,
    run_explicit_recall_audit,
)
from bot.research.futures_agent.signal_format_audit import (
    SignalFormatTier,
    parse_signal_format_audit,
    render_signal_format_audit,
    run_signal_format_audit,
)
from bot.research.futures_agent.research_ingest import ingest_research_posts, run_thesis_extract
from bot.research.futures_agent.research_scoring import (
    SourceScoreInputs,
    bayesian_shrinkage_rate,
    compose_source_score,
    wilson_lower_bound,
)
from bot.research.futures_agent.research_taxonomy import (
    ResearchContentType,
    classify_research_content,
)
from bot.research.futures_agent.research_utils import content_hash, extract_symbols
from bot.research.futures_agent.schema import STAGE3_VERSION, apply_migrations
from bot.research.futures_agent.thesis_extract import extract_theses_from_post


WHALE_BORROW = (
    "A whale wallet 0xabc123 borrowed 5,000 ETH from Aave to sell on Binance."
)
THIRD_PARTY_LONG = (
    "Smart money trader opened a 20x leveraged ETH long position worth $12M."
)
AUTHOR_SIGNAL = (
    "ORDI LONG\nEntry: 45.2-45.8\nSL: 43.5\nTP1: 48.0\nTP2: 50.5"
)
ORDI_THESIS = (
    "ORDI is weaker than BTC. After support loss I expect continuation lower. "
    "Interested after retest."
)
NEWS_ETF = "Breaking: SEC approves spot Bitcoin ETF filing from major asset manager."
QUESTION_LONG_SHORT = "Is there any good training to learn how to long/short?"
WALLET_ACTIVITY = (
    "This wallet is fresh wallet. Receive 3.33 eth from another wallet with $2m. "
    "And then buy $cartel and send all to bitboy"
)
PROMO_WHALE = (
    "The whale bought another 953 BTC on Binance. Join our VIP signals t.me/vip for more trades."
)
HACKERS_TEXT = "i think russian hackers are top dogs"

# Production audit false positives (precision-first EXPLICIT_SIGNAL / position mgmt)
LOOKONCHAIN_SELL_FRAGMENT = "Sell 3-5$"
LOOKONCHAIN_TARGET_FRAGMENT = "0.75 target for short"
FALSE_TRADER_THESIS_CLOSE_LONG = (
    "I close the long few minutes after a plus 6%"
)
FALSE_WHALE_FLOW_FOLLOW_TRASH = (
    "follow my trash account guys https://debank.com/profile/0xabc123abc123abc123abc123abc123abc123abc1"
)

# Phase C.1 production taxonomy quality gate examples
THX_SUPPORT = "Thx for your support"
SUPPORT_DEFI = "We all should just support DeFi projects and not rely on centralized exchanges"
RETAIL_BUY_TOP = "Is retail buy the top nt dwf"
WAITING_CLOSE_ANT = (
    "just waiting to close my long for ANT.. I hope it will break usd 5"
)
FIDELITY_ETF_UPDATE = "Fidelity files updated S-1 application for spot Ethereum ETF"
BTC_PRICE_UPDATES = "$104,000 Bitcoin ... price updates"
SIGNALYP_ENJ = "#ENJ SHORT\nEntry: 1.2\nSL: 1.1\nTP1: 1.3"

MAVIA_LONG = """mavia long 20x
 вход по рынку
 выделяю 1.000$
 цели 2.9429 2.9794 3.0520
 стоп 2.8406"""

UNI_DEFERRED_STOP_1 = """Захожу в сетап UNI/USDT — LONG
 Рыночный вход: 14.362
 Тейк-профит: 14.850
 Стоп: пока не ставлю"""

UNI_DEFERRED_STOP_2 = """UNI LONG x25
 Вход: 14.366
 Тейки: 14.506 14.657 15.759
 Стоп: пока не ставлю"""

NEAR_TP_HIT_SUPPORT = """По вечернему сигналу NEAR... пробили все тейки.
Друзья спасибо за поддержку, работаем дальше"""

RU_SOCIAL_SUPPORT = "Ваш актив это огромная поддержка для меня"
RU_TECH_SUPPORT = "Цена у поддержки 1.25, жду отскок"


def _create_source_db(path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE telegram_messages (
            id INTEGER PRIMARY KEY,
            channel_name TEXT NOT NULL,
            message_text TEXT NOT NULL,
            message_date INTEGER NOT NULL,
            telegram_message_id TEXT NOT NULL
        )
        """,
    )
    conn.executemany(
        """
        INSERT INTO telegram_messages
        (channel_name, message_text, message_date, telegram_message_id)
        VALUES (?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()


class FuturesAgentStage3TestCase(unittest.TestCase):
    def setUp(self) -> None:
        reset_bootstrap_for_tests()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.agent_db = Path(self._tmpdir.name) / "agent.db"
        self.source_db = Path(self._tmpdir.name) / "source.db"
        self.agent_url = f"sqlite:///{self.agent_db}"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        reset_bootstrap_for_tests()

    def _agent_conn(self):
        return agent_connection(self.agent_url)

    def test_stage3_migration_creates_tables(self) -> None:
        with self._agent_conn() as conn:
            applied = apply_migrations(conn)
            self.assertTrue(any("v3" in item for item in applied))
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                ).fetchall()
            }
        for name in (
            "futures_agent_trader_posts",
            "futures_agent_trader_theses",
            "futures_agent_trader_levels",
            "futures_agent_thesis_outcomes",
            "futures_agent_source_scores",
        ):
            self.assertIn(name, tables)

    def test_whale_borrow_not_explicit_signal(self) -> None:
        cls = classify_research_content(WHALE_BORROW)
        self.assertIn(
            cls.content_type,
            (ResearchContentType.WHALE_FLOW, ResearchContentType.ONCHAIN_EVENT),
        )
        self.assertNotEqual(cls.content_type, ResearchContentType.EXPLICIT_SIGNAL)

    def test_third_party_long_not_explicit_signal(self) -> None:
        cls = classify_research_content(THIRD_PARTY_LONG)
        self.assertIn(
            cls.content_type,
            (ResearchContentType.WHALE_FLOW, ResearchContentType.ONCHAIN_EVENT),
        )

    def test_author_signal_is_explicit(self) -> None:
        cls = classify_research_content(AUTHOR_SIGNAL)
        self.assertEqual(cls.content_type, ResearchContentType.EXPLICIT_SIGNAL)

    def test_ordi_thesis_without_levels(self) -> None:
        cls = classify_research_content(ORDI_THESIS)
        self.assertEqual(cls.content_type, ResearchContentType.TRADER_THESIS)
        theses = extract_theses_from_post(
            ORDI_THESIS,
            ResearchContentType.TRADER_THESIS.value,
            symbols=["ORDI"],
        )
        self.assertEqual(len(theses), 1)
        thesis = theses[0]
        self.assertFalse(thesis.unresolved)
        self.assertEqual(thesis.symbol, "ORDI")
        self.assertEqual(thesis.direction, "SHORT")
        self.assertTrue(thesis.condition_text)
        self.assertEqual(len(thesis.levels), 0)

    def test_news_event_neutral_no_levels_required(self) -> None:
        cls = classify_research_content(NEWS_ETF)
        self.assertEqual(cls.content_type, ResearchContentType.NEWS_EVENT)
        theses = extract_theses_from_post(
            NEWS_ETF,
            ResearchContentType.NEWS_EVENT.value,
            symbols=["BTC"],
        )
        self.assertEqual(len(theses), 1)
        self.assertEqual(theses[0].direction, "NEUTRAL")

    def test_trader_thesis_excludes_questions_and_training(self) -> None:
        cls = classify_research_content(QUESTION_LONG_SHORT)
        self.assertEqual(cls.content_type, ResearchContentType.OTHER)

    def test_wallet_activity_is_whale_or_onchain_not_thesis(self) -> None:
        cls = classify_research_content(WALLET_ACTIVITY)
        # Precision-first: acceptable to drop to OTHER, but never TRADER_THESIS.
        self.assertNotEqual(cls.content_type, ResearchContentType.TRADER_THESIS)

    def test_promo_whale_prioritizes_whale_over_promo(self) -> None:
        cls = classify_research_content(PROMO_WHALE)
        self.assertIn(
            cls.content_type,
            (ResearchContentType.WHALE_FLOW, ResearchContentType.ONCHAIN_EVENT),
        )
        self.assertNotEqual(cls.content_type, ResearchContentType.PROMO)

    def test_hackers_text_not_news_event(self) -> None:
        cls = classify_research_content(HACKERS_TEXT)
        self.assertNotEqual(cls.content_type, ResearchContentType.NEWS_EVENT)

    def test_sell_fragment_is_not_explicit_signal(self) -> None:
        cls = classify_research_content(LOOKONCHAIN_SELL_FRAGMENT)
        self.assertNotEqual(cls.content_type, ResearchContentType.EXPLICIT_SIGNAL)

    def test_target_fragment_is_not_explicit_signal(self) -> None:
        cls = classify_research_content(LOOKONCHAIN_TARGET_FRAGMENT)
        self.assertNotEqual(cls.content_type, ResearchContentType.EXPLICIT_SIGNAL)

    def test_close_long_plus_pct_not_trader_thesis(self) -> None:
        cls = classify_research_content(FALSE_TRADER_THESIS_CLOSE_LONG)
        self.assertNotEqual(cls.content_type, ResearchContentType.TRADER_THESIS)
        self.assertIn(
            cls.content_type,
            (ResearchContentType.TRADE_UPDATE, ResearchContentType.RESULT_UPDATE),
        )

    def test_profile_follow_not_whale_flow(self) -> None:
        cls = classify_research_content(FALSE_WHALE_FLOW_FOLLOW_TRASH)
        self.assertNotEqual(cls.content_type, ResearchContentType.WHALE_FLOW)

    def test_bare_support_not_technical_levels(self) -> None:
        for text in (THX_SUPPORT, SUPPORT_DEFI):
            cls = classify_research_content(text)
            self.assertNotEqual(cls.content_type, ResearchContentType.TECHNICAL_LEVELS)

    def test_retail_buy_top_not_trader_thesis(self) -> None:
        cls = classify_research_content(RETAIL_BUY_TOP)
        self.assertNotEqual(cls.content_type, ResearchContentType.TRADER_THESIS)

    def test_waiting_close_ant_is_trade_update(self) -> None:
        cls = classify_research_content(WAITING_CLOSE_ANT)
        self.assertEqual(cls.content_type, ResearchContentType.TRADE_UPDATE)

    def test_etf_filing_not_trade_update(self) -> None:
        cls = classify_research_content(FIDELITY_ETF_UPDATE)
        self.assertNotEqual(cls.content_type, ResearchContentType.TRADE_UPDATE)

    def test_price_feed_not_trade_update(self) -> None:
        cls = classify_research_content(BTC_PRICE_UPDATES)
        self.assertNotEqual(cls.content_type, ResearchContentType.TRADE_UPDATE)

    def test_signalyp_enj_is_structural_and_explicit(self) -> None:
        self.assertTrue(is_structural_explicit_candidate(SIGNALYP_ENJ))
        cls = classify_research_content(SIGNALYP_ENJ)
        self.assertEqual(cls.content_type, ResearchContentType.EXPLICIT_SIGNAL)

    def test_mavia_long_is_explicit_signal(self) -> None:
        parsed = parse_signal_format_audit(MAVIA_LONG)
        self.assertEqual(parsed.tier, SignalFormatTier.FULL_SIGNAL)
        cls = classify_research_content(MAVIA_LONG)
        self.assertEqual(cls.content_type, ResearchContentType.EXPLICIT_SIGNAL)

    def test_uni_deferred_stop_signals(self) -> None:
        for text in (UNI_DEFERRED_STOP_1, UNI_DEFERRED_STOP_2):
            parsed = parse_signal_format_audit(text)
            self.assertEqual(parsed.tier, SignalFormatTier.DEFERRED_STOP_SIGNAL)
            self.assertTrue(parsed.has_deferred_stop)
            cls = classify_research_content(text)
            self.assertEqual(cls.content_type, ResearchContentType.EXPLICIT_SIGNAL)

    def test_near_tp_hit_not_technical_levels(self) -> None:
        cls = classify_research_content(NEAR_TP_HIT_SUPPORT)
        self.assertEqual(cls.content_type, ResearchContentType.RESULT_UPDATE)

    def test_russian_social_support_not_technical_levels(self) -> None:
        cls = classify_research_content(RU_SOCIAL_SUPPORT)
        self.assertNotEqual(cls.content_type, ResearchContentType.TECHNICAL_LEVELS)

    def test_russian_technical_support_is_technical_levels(self) -> None:
        cls = classify_research_content(RU_TECH_SUPPORT)
        self.assertEqual(cls.content_type, ResearchContentType.TECHNICAL_LEVELS)

    def test_signal_format_audit_mock_source(self) -> None:
        rows = [
            ("signalyp", MAVIA_LONG, 1_700_000_000, "m1"),
            ("signalyp", UNI_DEFERRED_STOP_1, 1_700_000_100, "m2"),
            ("signalyp", NEAR_TP_HIT_SUPPORT, 1_700_000_200, "m3"),
        ]
        _create_source_db(self.source_db, rows)

        def _reader():
            conn = sqlite3.connect(self.source_db)
            conn.row_factory = sqlite3.Row
            from bot.research.futures.source_reader import SqliteSourceReader
            return SqliteSourceReader(conn, path=str(self.source_db))

        with patch(
            "bot.research.futures_agent.signal_format_audit.open_stage3_source_reader",
            side_effect=_reader,
        ):
            report = run_signal_format_audit(channel="signalyp", limit=10)

        self.assertEqual(report.scanned, 3)
        self.assertGreaterEqual(report.full_signal, 1)
        self.assertGreaterEqual(report.deferred_stop_signal, 1)
        rendered = render_signal_format_audit(report)
        self.assertIn("FULL_SIGNAL", rendered)
        self.assertIn("DEFERRED_STOP_SIGNAL", rendered)

    def test_explicit_recall_audit_mock_source(self) -> None:
        rows = [
            ("signalyp", SIGNALYP_ENJ, 1_700_000_000, "s1"),
            ("signalyp", LOOKONCHAIN_SELL_FRAGMENT, 1_700_000_001, "s2"),
        ]
        _create_source_db(self.source_db, rows)

        def _reader():
            conn = sqlite3.connect(self.source_db)
            conn.row_factory = sqlite3.Row
            from bot.research.futures.source_reader import SqliteSourceReader
            return SqliteSourceReader(conn, path=str(self.source_db))

        with patch(
            "bot.research.futures_agent.research_classify_audit.open_stage3_source_reader",
            side_effect=_reader,
        ):
            report = run_explicit_recall_audit(channel="signalyp", limit=10)

        self.assertEqual(report.scanned, 2)
        self.assertEqual(report.structural_candidates, 1)
        self.assertEqual(report.classified_explicit, 1)
        self.assertEqual(report.true_positives, 1)
        rendered = render_explicit_recall_audit(report)
        self.assertIn("FULL_SIGNAL", rendered)
        self.assertIn("true positives", rendered)

    def test_content_hash_dedup_normalization(self) -> None:
        a = content_hash("BTC  LONG\nEntry 1.0")
        b = content_hash("btc long entry 1.0")
        self.assertEqual(a, b)

    def test_symbol_extraction(self) -> None:
        syms = extract_symbols("$ORDI looks weak vs BTCUSDT")
        self.assertIn("ORDI", syms)
        self.assertIn("BTC", syms)

    def test_ingest_idempotent(self) -> None:
        _create_source_db(
            self.source_db,
            [
                ("lookonchain", WHALE_BORROW, 1_700_000_000, "m1"),
                ("signalyp", AUTHOR_SIGNAL, 1_700_000_100, "m2"),
                ("signalyp", AUTHOR_SIGNAL, 1_700_000_200, "m3"),
            ],
        )

        def _reader():
            conn = sqlite3.connect(self.source_db)
            conn.row_factory = sqlite3.Row
            from bot.research.futures.source_reader import SqliteSourceReader
            return SqliteSourceReader(conn, path=str(self.source_db))

        with patch(
            "bot.research.futures_agent.research_ingest.open_stage3_source_reader",
            side_effect=_reader,
        ):
            with self._agent_conn() as conn:
                apply_migrations(conn)
                stats1 = ingest_research_posts(conn, limit=10)
                stats2 = ingest_research_posts(conn, limit=10)
                count = conn.execute(
                    "SELECT COUNT(*) AS n FROM futures_agent_trader_posts",
                ).fetchone()["n"]

        self.assertEqual(stats1.inserted, 2)
        self.assertEqual(stats2.inserted, 0)
        self.assertGreater(stats2.skipped_duplicate + stats2.skipped_hash_duplicate, 0)
        self.assertEqual(count, 2)

    def test_thesis_extract_persists_levels(self) -> None:
        _create_source_db(
            self.source_db,
            [("signalyp", AUTHOR_SIGNAL, 1_700_000_000, "sig1")],
        )

        def _reader():
            conn = sqlite3.connect(self.source_db)
            conn.row_factory = sqlite3.Row
            from bot.research.futures.source_reader import SqliteSourceReader
            return SqliteSourceReader(conn, path=str(self.source_db))

        with patch(
            "bot.research.futures_agent.research_ingest.open_stage3_source_reader",
            side_effect=_reader,
        ):
            with self._agent_conn() as conn:
                apply_migrations(conn)
                ingest_research_posts(conn, limit=10)
                stats = run_thesis_extract(conn)
                levels = conn.execute(
                    "SELECT level_type, price FROM futures_agent_trader_levels ORDER BY level_type",
                ).fetchall()

        self.assertEqual(stats["theses_inserted"], 1)
        self.assertGreaterEqual(stats["levels_inserted"], 2)
        level_types = {r["level_type"] for r in levels}
        self.assertIn("STOP", level_types)
        self.assertTrue({"ENTRY_LOW", "ENTRY_HIGH"} & level_types)

    def test_max_per_source_cap(self) -> None:
        rows = [
            ("lookonchain", f"Whale bought BTC msg {i}", 1_700_000_000 + i, f"w{i}")
            for i in range(5)
        ]
        rows.append(("signalyp", ORDI_THESIS, 1_700_000_500, "s1"))
        _create_source_db(self.source_db, rows)

        def _reader():
            conn = sqlite3.connect(self.source_db)
            conn.row_factory = sqlite3.Row
            from bot.research.futures.source_reader import SqliteSourceReader
            return SqliteSourceReader(conn, path=str(self.source_db))

        with patch(
            "bot.research.futures_agent.research_ingest.open_stage3_source_reader",
            side_effect=_reader,
        ):
            with self._agent_conn() as conn:
                apply_migrations(conn)
                stats = ingest_research_posts(conn, max_per_source=2)

        self.assertEqual(stats.inserted, 3)
        self.assertEqual(stats.skipped_source_cap, 3)
        self.assertEqual(stats.per_channel.get("lookonchain"), 2)
        self.assertEqual(stats.per_channel.get("signalyp"), 1)

    def test_wilson_and_bayesian_helpers(self) -> None:
        wlb = wilson_lower_bound(7, 10)
        self.assertIsNotNone(wlb)
        self.assertGreater(wlb, 0.39)
        shrunk = bayesian_shrinkage_rate(7, 10)
        self.assertIsNotNone(shrunk)
        composed = compose_source_score(
            SourceScoreInputs(wins=7, total=10, avg_mfe=2.0, avg_mae=1.0),
        )
        self.assertIsNotNone(composed["wilson_lower_bound"])
        self.assertEqual(composed["expectancy_proxy"], 1.0)

    def test_migration_version_constant(self) -> None:
        self.assertEqual(STAGE3_VERSION, 3)

    def test_stage3_source_required_message(self) -> None:
        from bot.research.futures_agent.source_requirements import (
            Stage3SourceRequiredError,
            open_stage3_source_reader,
            render_stage3_source_db_required,
        )

        os.environ.pop("FUTURES_SOURCE_DATABASE_URL", None)
        msg = render_stage3_source_db_required()
        self.assertIn("Stage 3 requires production source DB", msg)
        self.assertIn("FUTURES_SOURCE_DATABASE_URL = missing", msg)
        with self.assertRaises(Stage3SourceRequiredError) as ctx:
            open_stage3_source_reader()
        self.assertIn("Mac Mini", str(ctx.exception))

    def test_thesis_outcome_evaluation_deterministic(self) -> None:
        # Build a sqlite source DB with market_prices and feed a single thesis.
        conn_src = sqlite3.connect(self.source_db)
        conn_src.execute(
            "CREATE TABLE market_prices (symbol TEXT, ts INTEGER, price REAL)",
        )
        # Price path: 100 -> 110 -> 90 within 1d
        conn_src.executemany(
            "INSERT INTO market_prices(symbol, ts, price) VALUES (?, ?, ?)",
            [
                ("BTC", 1_700_000_000, 100.0),
                ("BTC", 1_700_000_100, 110.0),
                ("BTC", 1_700_000_200, 90.0),
            ],
        )
        conn_src.commit()
        conn_src.close()

        # Insert a post+thesis into agent DB.
        with self._agent_conn() as conn:
            apply_migrations(conn)
            post_id = conn.execute(
                """
                INSERT INTO futures_agent_trader_posts (
                  source_message_id, channel_name, message_ts, raw_text,
                  content_hash, content_type, symbols_json, deterministic_confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("x1", "signalyp", 1_700_000_000, "BTC LONG", "h1", "TRADER_THESIS", "[]", 0.7),
            ).lastrowid
            thesis_id = conn.execute(
                """
                INSERT INTO futures_agent_trader_theses (
                  post_id, symbol, direction, thesis_text, horizon, condition_text, invalidation_text, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (post_id, "BTC", "LONG", "BTC thesis", "1d", None, None, 0.7),
            ).lastrowid

            def _reader():
                c = sqlite3.connect(self.source_db)
                c.row_factory = sqlite3.Row
                from bot.research.futures.source_reader import SqliteSourceReader
                return SqliteSourceReader(c, path=str(self.source_db))

            from unittest.mock import patch as _patch
            with _patch(
                "bot.research.futures_agent.thesis_outcomes.open_stage3_source_reader",
                side_effect=_reader,
            ):
                from bot.research.futures_agent.thesis_outcomes import evaluate_theses
                evaluate_theses(conn, horizons={"1d": 24 * 3600}, progress_every=0)

            out = conn.execute(
                """
                SELECT * FROM futures_agent_thesis_outcomes
                WHERE thesis_id = ? AND evaluation_horizon = '1d'
                """,
                (thesis_id,),
            ).fetchone()
        self.assertIsNotNone(out)
        # MFE should see +10%, MAE should see -10% for LONG from entry 100
        self.assertAlmostEqual(out["price_at_thesis"], 100.0)
        self.assertAlmostEqual(out["mfe_pct"], 0.10, places=6)
        self.assertAlmostEqual(out["mae_pct"], -0.10, places=6)


if __name__ == "__main__":
    unittest.main()
