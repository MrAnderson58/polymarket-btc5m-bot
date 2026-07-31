"""Feature Recovery V2 — indicator + validator tests (20+)."""

from __future__ import annotations

import math
import unittest

from bot.research.market_events.signal_intelligence.candles import CandleBar, compute_atr, compute_ema, compute_vwap
from bot.research.market_events.signal_intelligence.feature_recovery_v2.health import (
    validate_feature_vector,
)
from bot.research.market_events.signal_intelligence.feature_recovery_v2.indicators import (
    compute_adx,
    compute_bollinger,
    compute_macd,
    compute_rsi,
    compute_slope,
    compute_stochastic,
    compute_trend_score,
    indicators_from_bars,
)


def _synth_bars(n: int = 120, *, trend: float = 0.1, seed: float = 100.0) -> list[CandleBar]:
    bars: list[CandleBar] = []
    px = seed
    for i in range(n):
        px = px * (1.0 + trend / 1000.0) + math.sin(i / 5.0) * 0.2
        o = px
        h = px + 0.8
        l = px - 0.8
        c = px + 0.1
        bars.append(CandleBar(open_ts=1_700_000_000 + i * 300, open=o, high=h, low=l, close=c, volume=1000 + i))
    return bars


class TestFeatureRecoveryIndicators(unittest.TestCase):
    def test_rsi_bounds(self) -> None:
        closes = [float(i) for i in range(1, 50)]
        rsi = compute_rsi(closes)
        self.assertIsNotNone(rsi)
        assert rsi is not None
        self.assertGreaterEqual(rsi, 0)
        self.assertLessEqual(rsi, 100)

    def test_rsi_uptrend_high(self) -> None:
        closes = [100.0 + i for i in range(40)]
        rsi = compute_rsi(closes)
        self.assertIsNotNone(rsi)
        assert rsi is not None
        self.assertGreater(rsi, 70)

    def test_rsi_insufficient(self) -> None:
        self.assertIsNone(compute_rsi([1.0, 2.0, 3.0]))

    def test_ema_follows_price(self) -> None:
        vals = [10.0] * 20 + [20.0] * 20
        ema = compute_ema(vals, 10)
        self.assertGreater(ema, 10.0)

    def test_atr_positive(self) -> None:
        atr = compute_atr(_synth_bars(40))
        self.assertGreater(atr, 0)

    def test_vwap_between_high_low(self) -> None:
        bars = _synth_bars(30)
        v = compute_vwap(bars)
        self.assertGreater(v, min(b.low for b in bars))
        self.assertLess(v, max(b.high for b in bars))

    def test_macd_tuple(self) -> None:
        closes = [100 + math.sin(i / 3) * 2 + i * 0.05 for i in range(80)]
        m, s, h = compute_macd(closes)
        self.assertIsNotNone(m)
        self.assertIsNotNone(s)
        self.assertIsNotNone(h)

    def test_bollinger_order(self) -> None:
        closes = [100 + math.sin(i / 4) for i in range(40)]
        mid, up, lo, pct_b = compute_bollinger(closes)
        self.assertIsNotNone(mid)
        assert mid is not None and up is not None and lo is not None
        self.assertGreater(up, mid)
        self.assertLess(lo, mid)
        self.assertIsNotNone(pct_b)

    def test_adx_range(self) -> None:
        adx = compute_adx(_synth_bars(80, trend=0.5))
        self.assertIsNotNone(adx)
        assert adx is not None
        self.assertGreaterEqual(adx, 0)
        self.assertLessEqual(adx, 100)

    def test_stochastic_bounds(self) -> None:
        k, d = compute_stochastic(_synth_bars(40))
        self.assertIsNotNone(k)
        assert k is not None
        self.assertGreaterEqual(k, 0)
        self.assertLessEqual(k, 100)
        self.assertIsNotNone(d)

    def test_slope_uptrend_positive(self) -> None:
        closes = [100.0 + i for i in range(30)]
        sl = compute_slope(closes, 20)
        self.assertIsNotNone(sl)
        assert sl is not None
        self.assertGreater(sl, 0)

    def test_trend_score_signed(self) -> None:
        t = compute_trend_score(ema20=110, ema50=100, ema200=90, adx=30, slope=0.2)
        self.assertIsNotNone(t)
        assert t is not None
        self.assertGreater(t, 0)

    def test_indicators_from_bars_fills_core(self) -> None:
        out = indicators_from_bars(_synth_bars(220))
        for k in ("rsi", "ema20_distance", "ema50_distance", "vwap_distance", "atr", "macd", "adx", "stoch_k", "trend"):
            self.assertIsNotNone(out.get(k), msg=k)

    def test_ema200_distance_present(self) -> None:
        out = indicators_from_bars(_synth_bars(220))
        self.assertIsNotNone(out.get("ema200_distance"))

    def test_bb_pct_b(self) -> None:
        out = indicators_from_bars(_synth_bars(60))
        self.assertIsNotNone(out.get("bb_pct_b"))

    def test_volatility_is_atr_pct_not_atr(self) -> None:
        out = indicators_from_bars(_synth_bars(60))
        self.assertIsNotNone(out.get("atr"))
        self.assertIsNotNone(out.get("atr_pct"))
        self.assertNotEqual(out.get("atr"), out.get("atr_pct"))


class TestFeatureValidators(unittest.TestCase):
    def test_rsi_out_of_range(self) -> None:
        issues = validate_feature_vector({"rsi": 120.0})
        self.assertTrue(any(i["rule"] == "RSI_OUT_OF_RANGE" for i in issues))

    def test_negative_atr(self) -> None:
        issues = validate_feature_vector({"atr": -1.0})
        self.assertTrue(any(i["rule"] == "NEGATIVE_ATR" for i in issues))

    def test_stub_atr_50(self) -> None:
        issues = validate_feature_vector({"atr": 50.0})
        self.assertTrue(any(i["rule"] == "STUB_ATR_50" for i in issues))

    def test_duplicate_ai_confidence(self) -> None:
        issues = validate_feature_vector({"confidence": 0.7, "ai_score": 0.7})
        self.assertTrue(any(i["rule"] == "DUPLICATE_OF_CONFIDENCE" for i in issues))

    def test_nan_detected(self) -> None:
        issues = validate_feature_vector({"rsi": float("nan")})
        self.assertTrue(any(i["rule"] == "NaN" for i in issues))

    def test_future_timestamp(self) -> None:
        issues = validate_feature_vector({"_candle_last_ts": 9_999_999_999})
        self.assertTrue(any(i["rule"] == "FUTURE_TIMESTAMP" for i in issues))

    def test_broken_candle_source(self) -> None:
        issues = validate_feature_vector({"_candle_source": "BROKEN_SOURCE"})
        self.assertTrue(any(i["rule"] == "BROKEN_SOURCE" for i in issues))

    def test_good_vector_clean(self) -> None:
        out = indicators_from_bars(_synth_bars(220))
        out["funding"] = 0.0001
        out["confidence"] = 0.6
        out["ai_score"] = None
        out["_candle_last_ts"] = 1_700_000_000
        out["_candle_source"] = "market_events_historical_candles"
        issues = validate_feature_vector(out)
        # atr may still trip STUB if exactly 50 — synth shouldn't
        self.assertFalse(any(i["rule"] == "STUB_ATR_50" for i in issues))
        self.assertFalse(any(i["rule"] == "RSI_OUT_OF_RANGE" for i in issues))


if __name__ == "__main__":
    unittest.main()
