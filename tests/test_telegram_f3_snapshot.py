"""Snapshot tests for Phase F.3 Telegram signal v3 layout."""

from __future__ import annotations

import unittest

from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.market_event_alerts import format_shock_alert
from bot.research.market_events.signal_intelligence.signal_report_f2 import run_signal_report_f2
from bot.research.market_events.signal_intelligence.telegram_f3 import (
    SignalV3Context,
    build_action_plan,
    confidence_emoji,
    format_shock_f3,
    render_signal_v3,
    reversal_pct,
)
from tests.f0_test_utils import conn_ctx, make_db, seed_candles, seed_event

FIXTURE_CTX = SignalV3Context(
    symbol="SOL",
    shock_return_pct=-4.3,
    window_minutes=14,
    confidence_score=9.1,
    reversal_probability=0.74,
    rvol=5.2,
    atr_percentile=98.0,
    funding_regime="stable",
    oi_regime="rising",
    oi_change_pct=8.0,
    structure_labels=["Liquidity Sweep"],
    correlation_peers=[
        {"peer": "BTC", "return_pct": 0.1},
        {"peer": "ETH", "return_pct": -0.2},
    ],
    historical_count=37,
    historical_reversal_rate=0.74,
    avg_reversal_minutes=13,
    entry_recommendation="WAIT_R2",
    ai_summary_ru=(
        "Причина движения похожа на техническое снятие ликвидности.\n"
        "Фундаментальных новостей нет."
    ),
)

EXPECTED_SNAPSHOT = """\
🚨 SOLUSDT

-4.3% за 14 минут

━━━━━━━━━━━━━━

Уверенность
🟢 9.1 / 10

Вероятность отката
74%

━━━━━━━━━━━━━━

Что происходит

✔ BTC стабильный
✔ ETH стабильный
✔ Объем ×5.2
✔ ATR 98 percentile
✔ Funding нейтральный
✔ OI +8%
✔ Liquidity Sweep

✔ Volume
✔ BTC
✔ Funding
✔ OI
✔ ATR
✔ Structure

━━━━━━━━━━━━━━

История

37 похожих случаев

74% дали откат

Среднее время
13 минут

━━━━━━━━━━━━━━

План

Сейчас:
Ждать R2

После R2:
25%

После R3:
50%

━━━━━━━━━━━━━━

ИИ

Причина движения похожа на техническое снятие ликвидности.

━━━━━━━━━━━━━━

PAPER ONLY"""


class TelegramF3SnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.db = make_db()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_full_snapshot_exact(self) -> None:
        text = render_signal_v3(FIXTURE_CTX)
        self.assertEqual(text, EXPECTED_SNAPSHOT)

    def test_block_order(self) -> None:
        text = render_signal_v3(FIXTURE_CTX)
        anchors = [
            "🚨 SOLUSDT",
            "-4.3% за 14 минут",
            "Уверенность",
            "Вероятность отката",
            "Что происходит",
            "История",
            "План",
            "ИИ",
            "PAPER ONLY",
        ]
        positions = [text.index(a) for a in anchors]
        self.assertEqual(positions, sorted(positions))

    def test_separators_count(self) -> None:
        text = render_signal_v3(FIXTURE_CTX)
        self.assertEqual(text.count("━━━━━━━━━━━━━━"), 6)

    def test_confidence_emoji_levels(self) -> None:
        self.assertEqual(confidence_emoji(9.0), "🟢")
        self.assertEqual(confidence_emoji(5.5), "🟡")
        self.assertEqual(confidence_emoji(3.0), "🔴")

    def test_reversal_as_percent_not_decimal(self) -> None:
        self.assertEqual(reversal_pct(0.74), 74)
        self.assertEqual(reversal_pct(74.0), 74)
        text = render_signal_v3(FIXTURE_CTX)
        self.assertIn("74%", text)
        self.assertNotRegex(text, r"0\.74")

    def test_action_plan_wait_r2(self) -> None:
        plan = build_action_plan("WAIT_R2", 8.0)
        self.assertEqual(plan["now"], "Ждать R2")
        self.assertEqual(plan["after_r2"], "25%")
        self.assertEqual(plan["after_r3"], "50%")

    def test_action_plan_ready(self) -> None:
        plan = build_action_plan("READY_TO_ENTER", 8.0)
        self.assertEqual(plan["now"], "Можно начинать набор")

    def test_action_plan_skip(self) -> None:
        plan = build_action_plan("WAIT_R3", 3.5)
        self.assertEqual(plan["now"], "Лучше пропустить")

    def test_factor_checklist_present(self) -> None:
        text = render_signal_v3(FIXTURE_CTX)
        for factor in ("Volume", "BTC", "Funding", "OI", "ATR", "Structure"):
            self.assertIn(f"✔ {factor}", text)

    def test_history_aggregate_stats(self) -> None:
        text = render_signal_v3(FIXTURE_CTX)
        self.assertIn("37 похожих случаев", text)
        self.assertIn("74% дали откат", text)
        self.assertIn("13 минут", text)

    def test_integration_format_shock_alert(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            seed_candles(conn, symbol="SOL", n=60, shock=True)
            eid = seed_event(conn, symbol="SOL", ret=-4.3)
            conn.execute(
                "UPDATE market_events SET trigger_window_seconds = 840 WHERE id = ?",
                (eid,),
            )
            run_signal_report_f2(conn, eid)
            text = format_shock_alert(conn, eid)
            self.assertIn("SOLUSDT", text)
            self.assertIn("PAPER ONLY", text)

    def test_format_shock_f3_fallback(self) -> None:
        with conn_ctx(self.db) as conn:
            apply_migrations(conn)
            text = format_shock_f3(conn, 99999)
            self.assertIn("не найдено", text.lower())


if __name__ == "__main__":
    unittest.main()
