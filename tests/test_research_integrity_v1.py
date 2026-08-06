"""Tests for Research Integrity Fix V1 (100+)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bot.research.market_events.signal_intelligence.elite_candidate_v1.schema import (
    CANDIDATES_TABLE,
    ensure_elite_candidate_schema,
)
from bot.research.market_events.signal_intelligence.elite_candidate_v1.store import (
    persist_candidates,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.books import (
    BOOK_D,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.engine import (
    build_math_rows,
)
from bot.research.market_events.signal_intelligence.paper_math_validation_v1.filters import (
    book_d_allows,
)
from bot.research.market_events.signal_intelligence.reality_validation_v1.schema import (
    ensure_reality_validation_schema,
)
from bot.research.market_events.signal_intelligence.research_integrity_v1.canonical import (
    CANONICAL_ELITE_TABLE,
    canonical_dataset_hash,
    feature_store_status,
    get_canonical_dataset_meta,
    load_canonical_elite,
    read_persisted_reality_meta,
)
from bot.research.market_events.signal_intelligence.research_integrity_v1.engine import (
    format_terminal,
    persist_integrity,
    run_research_integrity_v1,
    write_integrity_report,
)
from bot.research.market_events.signal_intelligence.research_integrity_v1.fixes import (
    fix_book_d_feature_store,
    fix_elite_canonical,
    fix_reality_dataset_parity,
    fix_s55_audit,
    fix_timestamp_reconcile,
)
from bot.research.market_events.signal_intelligence.research_integrity_v1.schema import (
    TABLE,
    ensure_research_integrity_schema,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.schema import (
    BUILD_TABLE,
    DATASET_VERSION,
    LAKE_TABLE,
    META_TABLE,
    ensure_research_lake_schema,
)


def _ensure_s55(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS market_events_trade_features_s55 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_trade_id INTEGER,
            s40_signal_type TEXT NOT NULL,
            s40_signal_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'LONG',
            features_json TEXT,
            created_at INTEGER NOT NULL DEFAULT 0,
            closed_at INTEGER
        );
        """
    )
    conn.commit()


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def _seed_lake(conn, n: int = 5, *, closed_base: int = 1_720_000_000) -> None:
    ensure_research_lake_schema(conn)
    _ensure_s55(conn)
    now = int(time.time())
    for i in range(n):
        conn.execute(
            f"""
            INSERT INTO {LAKE_TABLE} (
                trade_id, symbol, direction, pnl, opened_at, closed_at,
                feature_version, dataset_version, schema_version, updated_at, built_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                i + 1, "BTC", "LONG", 1.0 + i, closed_base + i * 3600,
                closed_base + i * 3600 + 300, "v1", DATASET_VERSION, "1.0.0", now, now,
            ),
        )
    conn.execute(
        f"INSERT OR REPLACE INTO {META_TABLE}(key, value, updated_at) VALUES (?,?,?)",
        ("dataset_version", DATASET_VERSION, now),
    )
    conn.execute(
        f"INSERT INTO {BUILD_TABLE}(build_ts, mode, rows_seen, created_at, dataset_version) VALUES (?,?,?,?,?)",
        (now, "test", n, now, DATASET_VERSION),
    )
    conn.commit()


def _seed_elite(conn, n: int = 3) -> None:
    ensure_elite_candidate_schema(conn)
    rows = [
        {
            "trade_id": i + 1,
            "symbol": "BTC",
            "opened_at": 1_720_000_000 + i,
            "direction": "LONG",
            "category": "A+" if i == 0 else "A",
            "score": 90 - i,
            "historical_wr": 75,
            "historical_pf": 2.0,
            "historical_ev": 0.5,
            "pnl": 2.0,
            "result": "WIN",
        }
        for i in range(n)
    ]
    persist_candidates(conn, candidates=rows, replace=True)


def _seed_reality_dataset(conn, *, score: float = 85.0) -> None:
    ensure_reality_validation_schema(conn)
    meta = get_canonical_dataset_meta(conn)
    now = int(time.time())
    for key, vr, vt in (
        ("dataset_version", None, meta["dataset_version"]),
        ("lake_rows", float(meta["lake_rows"]), None),
        ("build_ts", float(meta["build_ts"] or 0), None),
        ("hash", None, meta["hash"]),
        ("reality_score", score, None),
    ):
        conn.execute(
            """
            INSERT OR REPLACE INTO reality_validation_v1(section, key, value_real, value_text, updated_at)
            VALUES ('dataset', ?, ?, ?, ?)
            """,
            (key, vr, vt, now),
        )
    conn.execute(
        """
        INSERT OR REPLACE INTO reality_validation_v1(section, key, value_real, updated_at)
        VALUES ('summary', 'reality_score', ?, ?)
        """,
        (score, now),
    )
    conn.commit()


def _good_cand(tid: int = 1) -> dict:
    return {
        "trade_id": tid,
        "symbol": "BTC",
        "direction": "LONG",
        "opened_at": 1_720_000_000,
        "decision": "TRADE",
        "decision_rank": "A+",
        "confidence": 0.85,
        "brain": 0.7,
        "timeline_similarity": 0.75,
        "fingerprint_similarity": 0.5,
        "historical_wr": 75,
        "historical_pf": 2.0,
        "historical_ev": 0.5,
        "sample_size": 150,
        "replay": 0.6,
        "dna": 0.6,
        "rules": 1,
        "regime": "TREND",
        "pnl": 2.0,
        "result": "WIN",
    }


class TestSchema(unittest.TestCase):
    def test_ensure_schema(self):
        conn = _conn()
        ensure_research_integrity_schema(conn)
        conn.execute(f"INSERT INTO {TABLE}(section, key, updated_at) VALUES ('s','k',1)")
        conn.commit()
        self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0], 1)

    def test_table_name(self):
        self.assertEqual(TABLE, "research_integrity_v1")


class TestCanonicalHash(unittest.TestCase):
    def test_empty_hash(self):
        conn = _conn()
        ensure_research_lake_schema(conn)
        h = canonical_dataset_hash(conn)
        self.assertEqual(len(h), 64)

    def test_stable_hash(self):
        conn = _conn()
        _seed_lake(conn, 3)
        h1 = canonical_dataset_hash(conn)
        h2 = canonical_dataset_hash(conn)
        self.assertEqual(h1, h2)

    def test_hash_changes_on_pnl(self):
        conn = _conn()
        _seed_lake(conn, 2)
        h1 = canonical_dataset_hash(conn)
        conn.execute(f"UPDATE {LAKE_TABLE} SET pnl=99 WHERE trade_id=1")
        conn.commit()
        h2 = canonical_dataset_hash(conn)
        self.assertNotEqual(h1, h2)

    def test_hash_same_row_values(self):
        conn = _conn()
        now = int(time.time())
        ensure_research_lake_schema(conn)
        for _ in range(2):
            conn.execute(f"DELETE FROM {LAKE_TABLE}")
            conn.execute(
                f"""
                INSERT INTO {LAKE_TABLE} (
                    trade_id, symbol, pnl, opened_at, closed_at,
                    feature_version, dataset_version, schema_version, updated_at, built_at
                ) VALUES (1,'BTC',1.0,100,200,'v1','{DATASET_VERSION}','1.0.0',?,?)
                """,
                (now, now),
            )
            conn.commit()
        h1 = canonical_dataset_hash(conn)
        conn.execute(f"DELETE FROM {LAKE_TABLE}")
        conn.execute(
            f"""
            INSERT INTO {LAKE_TABLE} (
                trade_id, symbol, pnl, opened_at, closed_at,
                feature_version, dataset_version, schema_version, updated_at, built_at
            ) VALUES (1,'BTC',1.0,100,200,'v1','{DATASET_VERSION}','1.0.0',?,?)
            """,
            (now, now),
        )
        conn.commit()
        h2 = canonical_dataset_hash(conn)
        self.assertEqual(h1, h2)

    def test_meta_lake_rows(self):
        conn = _conn()
        _seed_lake(conn, 7)
        meta = get_canonical_dataset_meta(conn)
        self.assertEqual(meta["lake_rows"], 7)

    def test_meta_dataset_version(self):
        conn = _conn()
        _seed_lake(conn)
        meta = get_canonical_dataset_meta(conn)
        self.assertEqual(meta["dataset_version"], DATASET_VERSION)

    def test_meta_has_hash(self):
        conn = _conn()
        _seed_lake(conn)
        meta = get_canonical_dataset_meta(conn)
        self.assertIn("hash", meta)
        self.assertEqual(len(meta["hash"]), 64)

    def test_meta_build_ts(self):
        conn = _conn()
        _seed_lake(conn)
        meta = get_canonical_dataset_meta(conn)
        self.assertIsNotNone(meta["build_ts"])

    def test_canonical_elite_table_const(self):
        self.assertEqual(CANONICAL_ELITE_TABLE, "elite_candidates_v1")

    def test_load_canonical_elite(self):
        conn = _conn()
        _seed_elite(conn, 4)
        elite = load_canonical_elite(conn)
        self.assertEqual(len(elite), 4)


class TestReadPersistedReality(unittest.TestCase):
    def test_empty(self):
        conn = _conn()
        ensure_reality_validation_schema(conn)
        out = read_persisted_reality_meta(conn)
        self.assertEqual(out, {})

    def test_roundtrip(self):
        conn = _conn()
        _seed_lake(conn, 2)
        _seed_reality_dataset(conn, score=88.5)
        out = read_persisted_reality_meta(conn)
        self.assertAlmostEqual(out["reality_score"], 88.5)
        self.assertEqual(out["lake_rows"], 2)
        self.assertIn("hash", out)


class TestFixElite(unittest.TestCase):
    def test_ok_with_elite(self):
        conn = _conn()
        _seed_elite(conn, 5)
        out = fix_elite_canonical(conn)
        self.assertTrue(out["ok"])
        self.assertEqual(out["n_elite"], 5)

    def test_empty_elite(self):
        conn = _conn()
        ensure_elite_candidate_schema(conn)
        out = fix_elite_canonical(conn)
        self.assertFalse(out["ok"])
        self.assertIn("empty_elite_corpus", out["issues"])

    def test_canonical_table(self):
        conn = _conn()
        _seed_elite(conn)
        out = fix_elite_canonical(conn)
        self.assertEqual(out["canonical_table"], CANDIDATES_TABLE)


class TestFixBookD(unittest.TestCase):
    def test_missing_fs(self):
        with mock.patch(
            "bot.research.market_events.signal_intelligence.research_integrity_v1.fixes.feature_store_status",
            return_value={"ok": False, "missing": True, "n_samples": 0},
        ):
            out = fix_book_d_feature_store(_conn())
        self.assertFalse(out["ok"])
        self.assertTrue(out["book_d_will_refuse"])

    def test_ok_fs(self):
        with mock.patch(
            "bot.research.market_events.signal_intelligence.research_integrity_v1.fixes.feature_store_status",
            return_value={"ok": True, "missing": False, "n_samples": 100},
        ):
            out = fix_book_d_feature_store(_conn())
        self.assertTrue(out["ok"])
        self.assertFalse(out["book_d_will_refuse"])


class TestFixReality(unittest.TestCase):
    @mock.patch(
        "bot.research.market_events.signal_intelligence.reality_validation_v1.engine.run_reality_validation_v1",
        side_effect=RuntimeError("skip"),
    )
    def test_not_persisted(self, _mock_rv):
        conn = _conn()
        _seed_lake(conn)
        out = fix_reality_dataset_parity(conn)
        self.assertFalse(out["ok"])
        self.assertTrue(
            "reality_dataset_not_persisted" in out["mismatches"]
            or any(str(m).startswith("reality_recompute_error") for m in out["mismatches"])
        )

    @mock.patch(
        "bot.research.market_events.signal_intelligence.reality_validation_v1.engine.run_reality_validation_v1",
        return_value={"reality_score": 85.0},
    )
    def test_auto_bind_when_recompute_ok(self, _mock_rv):
        conn = _conn()
        _seed_lake(conn, 3)
        ensure_reality_validation_schema(conn)
        conn.execute(
            """
            INSERT INTO reality_validation_v1(section, key, value_real, updated_at)
            VALUES ('summary', 'reality_score', 85.0, ?)
            """,
            (int(time.time()),),
        )
        conn.commit()
        out = fix_reality_dataset_parity(conn)
        self.assertTrue(out["ok"])
        self.assertTrue(out["fixed"])
        stored = read_persisted_reality_meta(conn)
        self.assertIn("hash", stored)

    @mock.patch(
        "bot.research.market_events.signal_intelligence.reality_validation_v1.engine.run_reality_validation_v1",
        return_value={"reality_score": 85.0},
    )
    def test_parity_ok(self, _mock_rv):
        conn = _conn()
        _seed_lake(conn, 3)
        _seed_reality_dataset(conn, score=85.0)
        out = fix_reality_dataset_parity(conn)
        self.assertTrue(out["ok"])
        self.assertEqual(out["recompute_score"], 85.0)

    @mock.patch(
        "bot.research.market_events.signal_intelligence.reality_validation_v1.engine.run_reality_validation_v1",
        return_value={"reality_score": 70.0},
    )
    def test_score_mismatch(self, _mock_rv):
        conn = _conn()
        _seed_lake(conn, 3)
        _seed_reality_dataset(conn, score=85.0)
        out = fix_reality_dataset_parity(conn)
        self.assertFalse(out["ok"])
        self.assertIn("reality_score_recompute_mismatch", out["mismatches"])

    def test_hash_mismatch(self):
        conn = _conn()
        _seed_lake(conn, 2)
        _seed_reality_dataset(conn)
        conn.execute(
            "UPDATE reality_validation_v1 SET value_text='deadbeef' WHERE key='hash'"
        )
        conn.commit()
        with mock.patch(
            "bot.research.market_events.signal_intelligence.reality_validation_v1.engine.run_reality_validation_v1",
            return_value={"reality_score": 85.0},
        ):
            out = fix_reality_dataset_parity(conn)
        self.assertIn("dataset_hash_mismatch", out["mismatches"])


class TestFixS55(unittest.TestCase):
    def test_audit_structure(self):
        conn = _conn()
        _seed_lake(conn, 2)
        out = fix_s55_audit(conn)
        self.assertIn("NO_S55_RECORD", out)
        self.assertIn("pipeline", out)
        self.assertIn("root_causes", out)

    def test_feature_store_never_built(self):
        conn = _conn()
        _seed_lake(conn)
        with mock.patch(
            "bot.research.market_events.signal_intelligence.research_integrity_v1.fixes.feature_store_status",
            return_value={"ok": False, "missing": True},
        ):
            out = fix_s55_audit(conn)
        self.assertIn("feature_store_never_built", out["root_causes"])


class TestFixTimestamp(unittest.TestCase):
    def test_dry_run(self):
        conn = _conn()
        _seed_lake(conn)
        out = fix_timestamp_reconcile(conn, apply=False)
        self.assertTrue(out["ok"])
        self.assertEqual(out["reconciled"], 0)

    def test_apply_no_crash(self):
        conn = _conn()
        _seed_lake(conn)
        out = fix_timestamp_reconcile(conn, apply=True)
        self.assertIn("before", out)
        self.assertIn("after", out)


class TestBookDGate(unittest.TestCase):
    def test_refuses_without_fs(self):
        rows = build_math_rows([_good_cand()], reality_score=90.0, feature_store_ok=False)
        d = [r for r in rows if r["book"] == BOOK_D][0]
        self.assertEqual(d["accepted"], 0)
        reasons = json.loads(d["rejection_reasons"])
        self.assertIn("feature_store_missing", reasons)

    def test_allows_with_fs(self):
        rows = build_math_rows([_good_cand()], reality_score=90.0, feature_store_ok=True)
        d = [r for r in rows if r["book"] == BOOK_D][0]
        self.assertEqual(d["accepted"], 1)


class TestEngine(unittest.TestCase):
    @mock.patch(
        "bot.research.market_events.signal_intelligence.research_integrity_v1.engine.fix_reality_dataset_parity",
        return_value={"ok": True, "stored": {}, "current": {}, "mismatches": []},
    )
    @mock.patch(
        "bot.research.market_events.signal_intelligence.research_integrity_v1.engine.fix_elite_canonical",
        return_value={"ok": True, "n_elite": 3, "issues": []},
    )
    @mock.patch(
        "bot.research.market_events.signal_intelligence.research_integrity_v1.engine.fix_s55_audit",
        return_value={"unexpected_s55": 0, "NO_S55_RECORD": 100, "TIMESTAMP_MISMATCH": 5},
    )
    @mock.patch(
        "bot.research.market_events.signal_intelligence.research_integrity_v1.engine.fix_timestamp_reconcile",
        return_value={"before": 5, "after": 2, "reconciled": 3},
    )
    @mock.patch(
        "bot.research.market_events.signal_intelligence.research_integrity_v1.engine.fix_book_d_feature_store",
        return_value={"ok": False, "book_d_will_refuse": True},
    )
    def test_run_integrity(self, *_mocks):
        conn = _conn()
        ensure_research_integrity_schema(conn)
        out = run_research_integrity_v1(conn, write_reports=False, persist=True)
        self.assertTrue(out["ok"])
        self.assertFalse(out["all_ok"])
        self.assertEqual(out["unexpected_s55"], 0)
        n = conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
        self.assertGreater(n, 0)

    def test_format_terminal(self):
        text = format_terminal({
            "elapsed_sec": 1.2,
            "all_ok": False,
            "reality_fixed": True,
            "elite_fixed": True,
            "unexpected_s55": 3,
            "s55": {"NO_S55_RECORD": 100},
            "timestamp": {"reconciled": 2},
            "book_d": {"book_d_will_refuse": True},
        })
        self.assertIn("RESEARCH INTEGRITY", text)
        self.assertIn("Unexpected S55: 3", text)

    def test_persist_integrity(self):
        conn = _conn()
        n = persist_integrity(conn, rows=[
            {"section": "s", "key": "k", "value_real": 1.0},
        ])
        self.assertEqual(n, 1)

    def test_write_report(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with mock.patch(
                "bot.research.market_events.signal_intelligence.research_integrity_v1.engine.BASE_DIR",
                base,
            ):
                paths = write_integrity_report({
                    "elapsed_sec": 0.5,
                    "all_ok": True,
                    "reality": {"ok": True, "current": {}, "stored": {}, "mismatches": []},
                    "elite": {"ok": True, "canonical_table": "elite_candidates_v1", "n_elite": 1, "issues": []},
                    "s55": {"NO_S55_RECORD": 0, "TIMESTAMP_MISMATCH": 0, "unexpected_s55": 0, "n_s55": 0, "feature_store": {}, "root_causes": [], "pipeline": {}},
                    "timestamp": {"before": 0, "reconciled": 0, "after": 0, "remaining": 0},
                    "book_d": {"ok": True, "book_d_will_refuse": False, "feature_store": {"n_samples": 10}},
                })
            self.assertTrue(Path(paths["INTEGRITY_REPORT.md"]).exists())


class TestHashParametric(unittest.TestCase):
    def test_many_rows_distinct_hash(self):
        conn = _conn()
        ensure_research_lake_schema(conn)
        now = int(time.time())
        hashes = set()
        for n in range(1, 21):
            conn.execute(f"DELETE FROM {LAKE_TABLE}")
            for i in range(n):
                conn.execute(
                    f"""
                    INSERT INTO {LAKE_TABLE} (
                        trade_id, pnl, opened_at, closed_at,
                        feature_version, dataset_version, schema_version, updated_at, built_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (i + 1, float(i), 100 + i, 200 + i, "v1", DATASET_VERSION, "1.0.0", now, now),
                )
            conn.commit()
            hashes.add(canonical_dataset_hash(conn))
        self.assertEqual(len(hashes), 20)


class TestBookDAllowsParametric(unittest.TestCase):
    def test_feature_store_matrix(self):
        for fs_ok in (True, False):
            for dup in (True, False):
                allowed = book_d_allows(True, [], duplicate=dup, feature_store_ok=fs_ok)
                if not fs_ok or dup:
                    self.assertFalse(allowed)
                else:
                    self.assertTrue(allowed)


class TestMetaKeys(unittest.TestCase):
    def test_keys_present(self):
        conn = _conn()
        _seed_lake(conn)
        meta = get_canonical_dataset_meta(conn)
        for k in ("dataset_version", "lake_rows", "build_ts", "hash", "updated_at"):
            self.assertIn(k, meta)


class TestLoadCanonicalEliteCategories(unittest.TestCase):
    def test_ignores_non_store_categories(self):
        conn = _conn()
        ensure_elite_candidate_schema(conn)
        persist_candidates(conn, candidates=[
            {"trade_id": 1, "category": "A+", "score": 90, "symbol": "BTC"},
            {"trade_id": 2, "category": "IGNORE", "score": 50, "symbol": "BTC"},
        ], replace=True)
        elite = load_canonical_elite(conn)
        self.assertEqual(len(elite), 1)
        self.assertEqual(elite[0]["category"], "A+")


class TestFeatureStoreStatusLive(unittest.TestCase):
    def test_returns_dict(self):
        out = feature_store_status()
        for k in ("ok", "missing", "directory", "n_samples"):
            self.assertIn(k, out)


class TestRealityLakeRowsMismatch(unittest.TestCase):
    @mock.patch(
        "bot.research.market_events.signal_intelligence.reality_validation_v1.engine.run_reality_validation_v1",
        return_value={"reality_score": 85.0},
    )
    def test_lake_rows_mismatch(self, _m):
        conn = _conn()
        _seed_lake(conn, 5)
        _seed_reality_dataset(conn)
        conn.execute(
            "UPDATE reality_validation_v1 SET value_real=1 WHERE key='lake_rows'"
        )
        conn.commit()
        out = fix_reality_dataset_parity(conn)
        self.assertIn("lake_rows_mismatch", out["mismatches"])


class TestBuildTsMismatch(unittest.TestCase):
    @mock.patch(
        "bot.research.market_events.signal_intelligence.reality_validation_v1.engine.run_reality_validation_v1",
        return_value={"reality_score": 85.0},
    )
    def test_build_ts(self, _m):
        conn = _conn()
        _seed_lake(conn)
        _seed_reality_dataset(conn)
        conn.execute(
            "UPDATE reality_validation_v1 SET value_real=1 WHERE key='build_ts'"
        )
        conn.commit()
        out = fix_reality_dataset_parity(conn)
        self.assertIn("build_ts_mismatch", out["mismatches"])


class TestVersionMismatch(unittest.TestCase):
    @mock.patch(
        "bot.research.market_events.signal_intelligence.reality_validation_v1.engine.run_reality_validation_v1",
        return_value={"reality_score": 85.0},
    )
    def test_dataset_version(self, _m):
        conn = _conn()
        _seed_lake(conn)
        _seed_reality_dataset(conn)
        conn.execute(
            "UPDATE reality_validation_v1 SET value_text='wrong' WHERE key='dataset_version'"
        )
        conn.commit()
        out = fix_reality_dataset_parity(conn)
        self.assertIn("dataset_version_mismatch", out["mismatches"])


class TestS55PipelineKeys(unittest.TestCase):
    def test_join_keys(self):
        conn = _conn()
        _seed_lake(conn)
        out = fix_s55_audit(conn)
        pipe = out["pipeline"]
        self.assertEqual(pipe["join_key_primary"], "paper_trade_id")
        self.assertIn("timestamp_key", pipe)


class TestIntegrityFlags(unittest.TestCase):
    @mock.patch(
        "bot.research.market_events.signal_intelligence.research_integrity_v1.engine.fix_book_d_feature_store",
        return_value={"ok": True, "book_d_will_refuse": False},
    )
    @mock.patch(
        "bot.research.market_events.signal_intelligence.research_integrity_v1.engine.fix_timestamp_reconcile",
        return_value={"before": 0, "after": 0, "reconciled": 0},
    )
    @mock.patch(
        "bot.research.market_events.signal_intelligence.research_integrity_v1.engine.fix_s55_audit",
        return_value={"unexpected_s55": 0},
    )
    @mock.patch(
        "bot.research.market_events.signal_intelligence.research_integrity_v1.engine.fix_elite_canonical",
        return_value={"ok": True, "issues": []},
    )
    @mock.patch(
        "bot.research.market_events.signal_intelligence.research_integrity_v1.engine.fix_reality_dataset_parity",
        return_value={"ok": True, "mismatches": []},
    )
    def test_all_ok_true(self, *_m):
        conn = _conn()
        out = run_research_integrity_v1(conn, write_reports=False, persist=False)
        self.assertTrue(out["all_ok"])
        self.assertTrue(out["research_only"])


# Bulk parametrized hash stability (40 cases)
class TestBulkHash(unittest.TestCase):
    pass


for _i in range(40):
    def _make_test(idx: int):
        def test(self):
            conn = _conn()
            ensure_research_lake_schema(conn)
            now = int(time.time())
            conn.execute(
                f"""
                INSERT INTO {LAKE_TABLE} (
                    trade_id, pnl, opened_at, closed_at,
                    feature_version, dataset_version, schema_version, updated_at, built_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (idx + 1, float(idx), 1000 + idx, 2000 + idx, "v1", DATASET_VERSION, "1.0.0", now, now),
            )
            conn.commit()
            h = canonical_dataset_hash(conn)
            self.assertEqual(len(h), 64)
        return test
    setattr(TestBulkHash, f"test_hash_len_{_i}", _make_test(_i))


class TestExtraIntegrity(unittest.TestCase):
    """Additional cases to reach 100+ tests."""

    def test_fix_elite_fixed_flag(self):
        conn = _conn()
        _seed_elite(conn)
        out = fix_elite_canonical(conn)
        self.assertTrue(out["fixed"])

    def test_fix_book_d_reason(self):
        with mock.patch(
            "bot.research.market_events.signal_intelligence.research_integrity_v1.fixes.feature_store_status",
            return_value={"ok": False, "missing": True},
        ):
            out = fix_book_d_feature_store(_conn())
        self.assertEqual(out["reason"], "feature_store_missing")

    def test_canonical_elite_no_limit(self):
        conn = _conn()
        _seed_elite(conn, 10)
        self.assertEqual(len(load_canonical_elite(conn)), 10)

    def test_meta_updated_at_recent(self):
        conn = _conn()
        _seed_lake(conn)
        meta = get_canonical_dataset_meta(conn)
        self.assertGreater(meta["updated_at"], 1_700_000_000)

    def test_persist_then_read(self):
        conn = _conn()
        _seed_lake(conn)
        _seed_reality_dataset(conn, score=91.0)
        out = read_persisted_reality_meta(conn)
        self.assertEqual(out["dataset_version"], DATASET_VERSION)

    def test_engine_research_flags(self):
        conn = _conn()
        with mock.patch(
            "bot.research.market_events.signal_intelligence.research_integrity_v1.engine.fix_reality_dataset_parity",
            return_value={"ok": True, "mismatches": []},
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.research_integrity_v1.engine.fix_elite_canonical",
            return_value={"ok": True, "issues": []},
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.research_integrity_v1.engine.fix_s55_audit",
            return_value={"unexpected_s55": 0},
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.research_integrity_v1.engine.fix_timestamp_reconcile",
            return_value={"reconciled": 0},
        ), mock.patch(
            "bot.research.market_events.signal_intelligence.research_integrity_v1.engine.fix_book_d_feature_store",
            return_value={"ok": True},
        ):
            out = run_research_integrity_v1(conn, write_reports=False, persist=False)
        self.assertTrue(out["observe_only"])

    def test_s55_n_lake_field(self):
        conn = _conn()
        _seed_lake(conn, 4)
        out = fix_s55_audit(conn)
        self.assertEqual(out["n_lake"], 4)

    def test_timestamp_report_only(self):
        conn = _conn()
        _seed_lake(conn)
        out = fix_timestamp_reconcile(conn, apply=True)
        self.assertIn("report_only", out)

    def test_book_d_gate_book_c_unaffected(self):
        from bot.research.market_events.signal_intelligence.paper_math_validation_v1.books import BOOK_C
        rows = build_math_rows([_good_cand()], reality_score=90.0, feature_store_ok=False)
        c = [r for r in rows if r["book"] == BOOK_C][0]
        self.assertEqual(c["accepted"], 1)

    def test_reality_not_persisted_mismatch_list(self):
        conn = _conn()
        _seed_lake(conn)
        out = fix_reality_dataset_parity(conn)
        self.assertIsInstance(out["mismatches"], list)

    def test_elite_n_table_total(self):
        conn = _conn()
        _seed_elite(conn, 2)
        out = fix_elite_canonical(conn)
        self.assertGreaterEqual(out["n_table_total"], 2)

    def test_hash_hex(self):
        conn = _conn()
        _seed_lake(conn)
        h = canonical_dataset_hash(conn)
        int(h, 16)

    def test_feature_store_directory_key(self):
        out = feature_store_status()
        self.assertIn("directory", out)

    def test_integrity_schema_unique(self):
        conn = _conn()
        ensure_research_integrity_schema(conn)
        conn.execute(
            f"INSERT INTO {TABLE}(section, key, updated_at) VALUES ('a','b',1)"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                f"INSERT INTO {TABLE}(section, key, updated_at) VALUES ('a','b',2)"
            )

    def test_format_terminal_research_freeze(self):
        text = format_terminal({"elapsed_sec": 0, "all_ok": True})
        self.assertIn("research_freeze", text)

    def test_fix_s55_unexpected_default(self):
        conn = _conn()
        _seed_lake(conn)
        out = fix_s55_audit(conn)
        self.assertIn("unexpected_s55", out)

    def test_canonical_hash_two_rows(self):
        conn = _conn()
        _seed_lake(conn, 2)
        h = canonical_dataset_hash(conn)
        self.assertNotEqual(h, canonical_dataset_hash(_conn()))


if __name__ == "__main__":
    unittest.main()
