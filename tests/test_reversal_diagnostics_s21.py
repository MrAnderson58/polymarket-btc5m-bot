"""Phase S2.1 — Reversal Diagnostics tests (read-only report)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bot.research.market_events.db import market_events_connection
from bot.research.market_events.db_config import configure_unit_test_db_isolation
from bot.research.market_events.event_schema import apply_migrations
from bot.research.market_events.signal_intelligence.candles import CandleBar
from bot.research.market_events.signal_intelligence.reversal_diagnostics_s21 import (
    REJECTION_NO_REVERSAL,
    RULE_REVERSAL,
    RULE_VOLUME,
    ablate_rules,
    analyze_confirmation_factors,
    format_reversal_diagnostics_s21,
)


def _bars(*, n: int = 50, up: bool = True, start: float = 100.0) -> list[CandleBar]:
    out: list[CandleBar] = []
    px = start
    ts = 1_700_000_000
    for i in range(n):
        nxt = px * (1.003 if up else 0.997)
        out.append(
            CandleBar(
                open_ts=ts + i * 300,
                open=px,
                high=max(px, nxt) * 1.001,
                low=min(px, nxt) * 0.999,
                close=nxt,
                volume=1000 + i * 5,
            )
        )
        px = nxt
    return out


def _insert_candidate(
    conn,
    *,
    symbol: str,
    reason: str | None,
    state: str = "rejected",
    conf: float = 8.0,
    ms: float = 70.0,
    liq: float = 75.0,
    rr: float = 3.0,
    vol: float = 55.0,
    funding: float = 60.0,
    btc: str = "Aligned",
    direction: str = "LONG",
    win: bool | None = True,
) -> int:
    ts = 1_700_100_000
    cur = conn.execute(
        """
        INSERT INTO market_candidate_g31 (
          snapshot_id, candidate_ts, symbol, trend_score, market_score, liquidity_score,
          confidence, rr, btc_alignment, funding_score, oi_score, volume_score, atr_score,
          fear_greed, candidate_state, rejection_reason, direction, created_at
        ) VALUES (NULL, ?, ?, 60, ?, ?, ?, ?, ?, ?, 50, ?, 50, 50, ?, ?, ?, ?)
        """,
        (ts, symbol, ms, liq, conf, rr, btc, funding, vol, state, reason, direction, ts),
    )
    cid = int(cur.lastrowid)
    if win is not None:
        conn.execute(
            """
            INSERT INTO market_candidate_outcomes_g32 (
              candidate_id, symbol, direction, created_at, price_entry,
              max_profit_pct, max_drawdown_pct, would_hit_tp, would_hit_sl,
              best_rr, replay_status, updated_at
            ) VALUES (?, ?, ?, ?, 100, ?, 0.5, ?, 0, 2.5, 'COMPLETE', ?)
            """,
            (cid, symbol, direction, ts, 1.5 if win else -1.0, 1 if win else 0, ts),
        )
    return cid


class TestConfirmationFactorsS21(unittest.TestCase):
    def test_long_uptrend_soft_passes(self) -> None:
        factors = analyze_confirmation_factors(
            _bars(up=True), direction="LONG", volume_score=55.0,
        )
        by_name = {f.name: f for f in factors}
        self.assertTrue(by_name["Volume"].ok)
        self.assertTrue(by_name["Trend slope"].ok)

    def test_weak_volume_flagged(self) -> None:
        factors = analyze_confirmation_factors(
            _bars(up=True), direction="LONG", volume_score=20.0,
        )
        vol = next(f for f in factors if f.name == "Volume")
        self.assertFalse(vol.ok)


class TestAblationS21(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "me.db")
        configure_unit_test_db_isolation(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_ablate_reversal_unlocks_and_measures_wr(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            # Would become signals if reversal gate ignored
            _insert_candidate(
                conn, symbol="BTC", reason=REJECTION_NO_REVERSAL, win=True,
            )
            _insert_candidate(
                conn, symbol="ETH", reason=REJECTION_NO_REVERSAL, win=True,
            )
            _insert_candidate(
                conn, symbol="SOL", reason=REJECTION_NO_REVERSAL, win=False,
            )
            # Blocked by volume — should not count in reversal-only ablation
            _insert_candidate(
                conn, symbol="DOGE", reason="Volume weak (22)", vol=22.0, win=True,
            )
            conn.commit()
            rows = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT c.*, o.would_hit_tp, o.max_profit_pct
                    FROM market_candidate_g31 c
                    LEFT JOIN market_candidate_outcomes_g32 o ON o.candidate_id = c.id
                    """
                ).fetchall()
            ]

        ab = ablate_rules(rows, skip_rules=frozenset({RULE_REVERSAL}))
        self.assertEqual(ab.extra_signals, 3)
        self.assertAlmostEqual(ab.wr_pct or 0, 66.7, delta=0.1)

        ab_vol = ablate_rules(rows, skip_rules=frozenset({RULE_VOLUME}))
        self.assertEqual(ab_vol.extra_signals, 1)

    def test_report_format(self) -> None:
        with market_events_connection() as conn:
            apply_migrations(conn)
            _insert_candidate(conn, symbol="BTC", reason=REJECTION_NO_REVERSAL, win=True)
            _insert_candidate(conn, symbol="ETH", reason=REJECTION_NO_REVERSAL, win=False)
            conn.commit()
            text = format_reversal_diagnostics_s21(conn, limit=500)

        self.assertIn("Rejected", text)
        self.assertIn("No reversal confirmation", text)
        self.assertIn("BTC", text)
        self.assertIn("что не хватило", text)
        self.assertIn("Filter usefulness", text)
        self.assertIn("Если убрать только правило", text)
        self.assertIn("исторический WR", text)


if __name__ == "__main__":
    unittest.main()
