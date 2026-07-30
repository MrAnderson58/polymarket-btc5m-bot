"""Feature Store & ML Ranking V1 regression tests (shadow mode only)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.signal_intelligence import feature_store as fs
from bot.research.market_events.signal_intelligence import training_dataset as td


def _sample(i: int, *, profitable: int = 1, symbol: str = "BTC") -> dict:
    return {
        "sample_id": f"t:{i}",
        "paper_trade_id": i,
        "feature_version": fs.FEATURE_VERSION,
        "created_at": 1_700_000_000 + i,
        "symbol": symbol,
        "direction": "LONG" if i % 2 == 0 else "SHORT",
        "pattern": "breakout" if profitable else "chop",
        "gate_decision": "ALLOWED",
        "news_category": "macro",
        "market_regime": "RANGE",
        "confidence": 0.55 + (i % 5) * 0.05,
        "ai_score": 0.6,
        "macro_score": 0.4,
        "news_score": 0.3,
        "volatility": 1.2,
        "atr": 0.8,
        "vwap_distance": 0.1 * (1 if profitable else -1),
        "ema20_distance": 0.2,
        "ema50_distance": 0.15,
        "ema200_distance": 0.05,
        "rsi": 55.0,
        "macd": 0.01,
        "funding": -0.001,
        "oi_delta": 0.02,
        "liquidation_metric": 0.0,
        "spread": 0.0001,
        "book_imbalance": 0.1,
        "time_to_expiry": 120.0,
        "btc_move": 0.3 if profitable else -0.2,
        "volume": 1000.0,
        "fear_greed": 50.0,
        "trend": 0.1,
        "hour": float(i % 24),
        "weekday": float(i % 7),
        "holding_time": 600.0,
        "mfe": 2.0,
        "mae": -1.0,
        "pnl": 5.0 if profitable else -3.0,
        "pnl_pct": 5.0 if profitable else -3.0,
        "result": "WIN" if profitable else "LOSS",
        "profitable": profitable,
        "shadow_mode": True,
        "ml_may_execute": False,
    }


class TestFeatureStoreMlV1(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        os.environ["ML_FEATURE_STORE_DIR"] = str(root / "store")
        os.environ["ML_DATASET_DIR"] = str(root / "datasets")
        os.environ["ML_MODEL_DIR"] = str(root / "models")
        os.environ["ML_REPORT_PATH"] = str(root / "ML_REPORT.md")
        os.environ["ML_BACKEND"] = "sklearn"

    def tearDown(self) -> None:
        for k in (
            "ML_FEATURE_STORE_DIR",
            "ML_DATASET_DIR",
            "ML_MODEL_DIR",
            "ML_REPORT_PATH",
            "ML_BACKEND",
        ):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def test_feature_versioning_and_extract(self) -> None:
        row = {
            "id": 1,
            "s40_signal_type": "t",
            "s40_signal_id": 1,
            "symbol": "eth",
            "direction": "long",
            "entry": 100.0,
            "pnl_usd": 2.0,
            "pnl_pct": 2.0,
            "result": "WIN",
            "decision_confidence": 0.7,
            "pattern_json": json.dumps({"name": "engulfing"}),
            "news_category": "macro",
            "gate_decision": "ALLOWED",
            "market_regime": "WEAK_BULL",
            "atr": 1.5,
            "rsi": 60,
            "features_json": json.dumps({"vwap": 101.0, "ema20": 100.5, "macd": 0.02}),
            "closed_at": 100,
        }
        sample = fs.extract_sample(row)
        self.assertEqual(sample["feature_version"], fs.FEATURE_VERSION)
        self.assertEqual(sample["symbol"], "ETH")
        self.assertEqual(sample["pattern"], "engulfing")
        self.assertEqual(sample["profitable"], 1)
        self.assertTrue(sample["shadow_mode"])
        self.assertFalse(sample["ml_may_execute"])
        self.assertIn("confidence", fs.FEATURE_SPEC)
        self.assertEqual(fs.FEATURE_SPEC["confidence"]["version"], fs.FEATURE_VERSION)

    def test_dataset_creation(self) -> None:
        samples = [_sample(i, profitable=i % 3 != 0) for i in range(40)]
        persisted = fs.persist_samples(samples)
        self.assertTrue(persisted["ok"])
        self.assertEqual(persisted["n_samples"], 40)
        exported = td.export_training_dataset(
            samples,
            symbols=["BTC"],
            start_ts=1_700_000_000,
            end_ts=1_700_000_100,
        )
        self.assertTrue(exported["ok"])
        self.assertGreater(exported["n_rows"], 0)
        self.assertTrue(Path(exported["csv_path"]).exists())
        # version filter
        wrong = td.export_training_dataset(samples, feature_version="v999")
        self.assertEqual(wrong["n_rows"], 0)

    def test_training_prediction_report(self) -> None:
        samples = []
        for i in range(60):
            # Make signal weakly informative
            prof = 1 if (i % 5 != 0) else 0
            s = _sample(i, profitable=prof)
            s["btc_move"] = 1.0 if prof else -1.0
            s["confidence"] = 0.8 if prof else 0.4
            samples.append(s)
        fs.persist_samples(samples)
        td.export_training_dataset(samples)
        result = td.train_shadow_ml(samples)
        self.assertTrue(result["ok"])
        self.assertTrue(result["shadow_mode"])
        self.assertFalse(result["ml_may_execute"])
        self.assertGreaterEqual(result["feature_count"], 10)
        self.assertIn("roc_auc", result["test_metrics"] or {})
        self.assertTrue(Path(result["model_path"]).exists())
        score = td.predict_ml_score(samples[0])
        self.assertIsNotNone(score)
        self.assertGreaterEqual(float(score), 0.0)
        self.assertLessEqual(float(score), 1.0)
        path = td.write_ml_report(result)
        text = path.read_text(encoding="utf-8")
        self.assertIn("ML_REPORT", text)
        self.assertIn("Feature importance", text)
        self.assertIn("class balance", text.lower())

    def test_ml_never_executes(self) -> None:
        self.assertTrue(td.SHADOW_MODE)
        # predict path must keep safety flags
        samples = [_sample(i, profitable=i % 2) for i in range(30)]
        fs.persist_samples(samples)
        result = td.train_shadow_ml(samples)
        self.assertFalse(result.get("ml_may_execute"))
        for row in result.get("recommended_future_features") or []:
            self.assertIsInstance(row, str)

    def test_cli_registered(self) -> None:
        from bot.research.market_events.__main__ import main

        samples = [_sample(i, profitable=i % 2) for i in range(40)]
        fs.persist_samples(samples)
        td.export_training_dataset(samples)

        def _fake_sync(conn, **kwargs):
            return {"ok": True, "n_samples": len(samples), "feature_version": fs.FEATURE_VERSION}

        with mock.patch(
            "bot.research.market_events.signal_intelligence.feature_store.sync_feature_store",
            side_effect=_fake_sync,
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.training_dataset.fs.sync_feature_store",
            side_effect=_fake_sync,
        ), mock.patch(
            "bot.research.market_events.db.market_events_connection",
        ) as mconn, mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            class _C:
                def __enter__(self):
                    return mock.MagicMock()

                def __exit__(self, *a):
                    return False

            mconn.return_value = _C()
            with mock.patch("bot.research.market_events.event_schema.apply_migrations"):
                rc1 = main(["build-dataset"])
                rc2 = main(["train-ml"])
                rc3 = main(["ml-report"])
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        self.assertEqual(rc3, 0)


if __name__ == "__main__":
    unittest.main()
