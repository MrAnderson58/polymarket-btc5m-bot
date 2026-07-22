"""S50 — Intelligence compression pipeline tests."""

from __future__ import annotations

import unittest

from bot.research.ai_analyst.intelligence_compression import (
    build_market_signal_events,
    cluster_events,
    compress_intelligence_for_llm,
    dedupe_events,
    format_top_events_text,
    normalize_event,
    rank_events,
)


def _ev(title: str, **kwargs) -> dict:
    base = {
        "title": title,
        "importance": 0.6,
        "confidence": 0.7,
        "freshness": 1.0,
        "source_count": 1,
        "last_seen": 1_700_000_000,
        "market_impact": "MEDIUM",
        "polarity": "Neutral",
    }
    base.update(kwargs)
    return base


class TestNormalizeDedupS50(unittest.TestCase):
    def test_normalize_requires_title(self) -> None:
        self.assertIsNone(normalize_event({}))
        row = normalize_event(_ev("BlackRock ETF +420M"))
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["title"], "BlackRock ETF +420M")
        self.assertIn("etf", row["tokens"] or set())

    def test_dedupe_near_duplicate_titles(self) -> None:
        a = normalize_event(_ev("BlackRock ETF inflows +420M"))
        b = normalize_event(_ev("BlackRock ETF inflows +420M today"))
        assert a and b
        out = dedupe_events([a, b])
        self.assertEqual(len(out), 1)


class TestClusterScoreS50(unittest.TestCase):
    def test_cluster_merges_similar_stories(self) -> None:
        a = normalize_event(_ev("Binance lists new token", narrative="listing"))
        b = normalize_event(_ev("Binance listing announced", narrative="listing"))
        assert a and b
        deduped = dedupe_events([a, b])
        clustered = cluster_events(deduped)
        self.assertEqual(len(clustered), 1)
        self.assertGreaterEqual(int(clustered[0].get("cluster_size") or 1), 1)

    def test_rank_prefers_high_impact(self) -> None:
        low = normalize_event(_ev("Minor headline", market_impact="LOW", importance=0.2))
        high = normalize_event(_ev("ETF +420M", market_impact="HIGH", importance=0.9))
        assert low and high
        ranked = rank_events([low, high])
        self.assertEqual(ranked[0]["title"], "ETF +420M")


class TestPipelineS50(unittest.TestCase):
    def test_compress_reduces_firehose_to_top_n(self) -> None:
        raw = [
            _ev(f"Headline {i}", importance=0.1 + i * 0.01)
            for i in range(40)
        ]
        raw.append(_ev("BlackRock ETF +420M", market_impact="HIGH", importance=0.95))
        top, meta = compress_intelligence_for_llm(raw, limit=5)
        self.assertLessEqual(len(top), 5)
        self.assertEqual(meta["stats"]["raw_count"], 41)
        self.assertGreaterEqual(meta["stats"]["deduped_count"], 1)
        titles = [t["title"] for t in top]
        self.assertTrue(any("ETF" in t for t in titles))

    def test_market_signals_injected(self) -> None:
        signals = build_market_signal_events(
            etf={"btc_etf": {"netflow_1d": 420}},
            funding={"extreme_funding": True, "current": 0.0012},
        )
        self.assertGreaterEqual(len(signals), 2)
        top, meta = compress_intelligence_for_llm([], market_signals=signals, limit=5)
        self.assertGreaterEqual(meta["stats"]["synthetic_added"], 2)
        self.assertTrue(any("ETF" in e["title"] for e in top))

    def test_top_events_text_format(self) -> None:
        top, _ = compress_intelligence_for_llm([
            _ev("BlackRock ETF +420M", market_impact="HIGH"),
            _ev("CPI tomorrow", market_impact="HIGH"),
            _ev("Whale 18k BTC", market_impact="HIGH"),
        ], limit=5)
        text = format_top_events_text(top)
        self.assertIn("Top Events", text)
        self.assertIn("BlackRock ETF +420M", text)
        self.assertIn("Impact HIGH", text)
        self.assertIn("1.", text)


class TestPromptIntegrationS50(unittest.TestCase):
    def test_prompt_builder_includes_compression_preface(self) -> None:
        from bot.research.ai_analyst.prompt_builder import build_messages

        ctx = {
            "intelligence": {
                "top_events": [{"rank": 1, "title": "ETF +420M", "impact": "HIGH"}],
                "top_events_text": "Top Events\n\n1.\nETF +420M\nImpact HIGH",
                "compression": {"stats": {"raw_count": 50, "top_count": 1}},
            },
        }
        _system, user = build_messages(prompt_name="json_summary", context=ctx)
        self.assertIn("Pre-compressed intelligence", user)
        self.assertIn("Top Events", user)
        self.assertIn("raw=50", user)


if __name__ == "__main__":
    unittest.main()
