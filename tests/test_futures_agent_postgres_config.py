"""PostgreSQL config and adapter regressions for futures_agent Stage 1."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.research.futures_agent.db import (
    AgentDbError,
    _PgConnWrapper,
    agent_connection,
    insert_returning_id,
)
from bot.research.futures_agent.env_bootstrap import (
    reset_bootstrap_for_tests,
    resolve_agent_db_config,
)
from bot.research.futures_agent.ingestion import ingest_forwarded_signal
from bot.research.futures_agent.pipeline import process_input, process_pending
from bot.research.futures_agent.schema import apply_migrations
from bot.research.futures_agent.schema_validate import _check_fk_postgres, validate_stage1_schema

EXPLICIT_LONG = (
    "SUI LONG\n"
    "Entry: 2.14-2.18\n"
    "SL: 2.05\n"
    "TP1: 2.32\n"
    "TP2: 2.45\n"
)


def _fake_psycopg2_modules() -> tuple[MagicMock, MagicMock]:
    extras = MagicMock()
    extras.RealDictCursor = MagicMock()
    psycopg2 = MagicMock()
    psycopg2.extras = extras
    return psycopg2, extras


class FuturesAgentConfigTestCase(unittest.TestCase):
    def tearDown(self) -> None:
        reset_bootstrap_for_tests()
        for key in (
            "FUTURES_AGENT_DATABASE_URL",
            "FUTURES_AGENT_SQLITE_PATH",
        ):
            os.environ.pop(key, None)

    def test_project_dotenv_postgres_used_without_shell_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "FUTURES_AGENT_DATABASE_URL=postgresql:///trading_ai\n",
                encoding="utf-8",
            )
            with patch(
                "bot.research.futures_agent.env_bootstrap.project_root",
                return_value=root,
            ):
                reset_bootstrap_for_tests()
                os.environ.pop("FUTURES_AGENT_DATABASE_URL", None)
                cfg = resolve_agent_db_config()
        self.assertEqual(cfg.backend, "postgresql")
        self.assertEqual(cfg.database_name, "trading_ai")
        self.assertEqual(cfg.config_source, "project_dotenv")

    def test_shell_env_overrides_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "FUTURES_AGENT_DATABASE_URL=postgresql:///from_dotenv\n",
                encoding="utf-8",
            )
            with patch(
                "bot.research.futures_agent.env_bootstrap.project_root",
                return_value=root,
            ):
                reset_bootstrap_for_tests()
                os.environ["FUTURES_AGENT_DATABASE_URL"] = "postgresql:///from_shell"
                cfg = resolve_agent_db_config()
        self.assertEqual(cfg.database_name, "from_shell")
        self.assertEqual(cfg.config_source, "shell_env")

    def test_postgres_config_never_silently_falls_back_to_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reset_bootstrap_for_tests()
            os.environ["FUTURES_AGENT_DATABASE_URL"] = "postgresql:///trading_ai"
            psycopg2, extras = _fake_psycopg2_modules()
            psycopg2.connect.side_effect = OSError("connection refused")
            with patch.dict(
                sys.modules,
                {"psycopg2": psycopg2, "psycopg2.extras": extras},
            ):
                with self.assertRaises(AgentDbError):
                    with agent_connection():
                        pass
            sqlite_path = Path(tmp) / "should_not_exist.db"
            self.assertFalse(sqlite_path.exists())

    def test_sqlite_fallback_when_no_postgres_url(self) -> None:
        with patch(
            "bot.research.futures_agent.env_bootstrap.project_root",
        ) as mock_root:
            with tempfile.TemporaryDirectory() as tmp:
                mock_root.return_value = Path(tmp)
                reset_bootstrap_for_tests()
                os.environ.pop("FUTURES_AGENT_DATABASE_URL", None)
                os.environ.pop("FUTURES_AGENT_SQLITE_PATH", None)
                cfg = resolve_agent_db_config()
        self.assertEqual(cfg.backend, "sqlite")
        self.assertEqual(cfg.config_source, "fallback_sqlite")

    def test_explicit_sqlite_url_overrides_project_postgres_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "FUTURES_AGENT_DATABASE_URL=postgresql:///trading_ai\n",
                encoding="utf-8",
            )
            db_path = root / "isolated_agent.db"
            with patch(
                "bot.research.futures_agent.env_bootstrap.project_root",
                return_value=root,
            ):
                reset_bootstrap_for_tests()
                os.environ.pop("FUTURES_AGENT_DATABASE_URL", None)
                with agent_connection(f"sqlite:///{db_path}") as conn:
                    apply_migrations(conn)
                    n = conn.execute(
                        "SELECT COUNT(*) AS n FROM futures_agent_migrations"
                    ).fetchone()["n"]
            self.assertEqual(n, 2)
            self.assertTrue(db_path.exists())

    def test_url_none_uses_configured_postgresql(self) -> None:
        reset_bootstrap_for_tests()
        os.environ["FUTURES_AGENT_DATABASE_URL"] = "postgresql:///trading_ai"
        psycopg2, extras = _fake_psycopg2_modules()
        mock_pg = MagicMock()
        psycopg2.connect.return_value = mock_pg
        with patch.dict(sys.modules, {"psycopg2": psycopg2, "psycopg2.extras": extras}):
            with agent_connection() as conn:
                self.assertIsInstance(conn, _PgConnWrapper)
        psycopg2.connect.assert_called_once()


class FuturesAgentPostgresAdapterTestCase(unittest.TestCase):
    def tearDown(self) -> None:
        reset_bootstrap_for_tests()
        os.environ.pop("FUTURES_AGENT_DATABASE_URL", None)

    def test_execute_no_params_preserves_literal_percent(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        psycopg2, extras = _fake_psycopg2_modules()
        with patch.dict(sys.modules, {"psycopg2": psycopg2, "psycopg2.extras": extras}):
            wrapper = _PgConnWrapper(mock_conn)
            wrapper.execute(
                "SELECT 1 WHERE 'futures_agent_x' LIKE 'futures_agent_%'"
            )
        mock_cur.execute.assert_called_once_with(
            "SELECT 1 WHERE 'futures_agent_x' LIKE 'futures_agent_%'"
        )

    def test_execute_converts_placeholders_only_with_params(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        psycopg2, extras = _fake_psycopg2_modules()
        with patch.dict(sys.modules, {"psycopg2": psycopg2, "psycopg2.extras": extras}):
            wrapper = _PgConnWrapper(mock_conn)
            wrapper.execute("SELECT version FROM t WHERE version = ?", (1,))
        mock_cur.execute.assert_called_once_with(
            "SELECT version FROM t WHERE version = %s",
            (1,),
        )

    def test_fk_validation_query_with_mock_postgres_cursor(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            {
                "table_name": "futures_agent_signals",
                "column_name": "input_id",
                "foreign_table": "futures_agent_inputs",
            },
            {
                "table_name": "futures_agent_targets",
                "column_name": "signal_id",
                "foreign_table": "futures_agent_signals",
            },
        ]
        mock_conn.cursor.return_value = mock_cur
        psycopg2, extras = _fake_psycopg2_modules()
        with patch.dict(sys.modules, {"psycopg2": psycopg2, "psycopg2.extras": extras}):
            wrapper = _PgConnWrapper(mock_conn)
            errors: list[str] = []
            _check_fk_postgres(wrapper, errors)
        executed_sql = mock_cur.execute.call_args[0][0]
        self.assertEqual(len(mock_cur.execute.call_args[0]), 1)
        self.assertNotIn("LIKE", executed_sql)
        self.assertEqual(errors, [])

    def test_migration_rollback_on_validation_failure(self) -> None:
        mock_conn = MagicMock()
        mock_pg = MagicMock()
        psycopg2, extras = _fake_psycopg2_modules()
        psycopg2.connect.return_value = mock_pg
        with patch.dict(sys.modules, {"psycopg2": psycopg2, "psycopg2.extras": extras}):
            reset_bootstrap_for_tests()
            os.environ["FUTURES_AGENT_DATABASE_URL"] = "postgresql:///trading_ai"
            with patch(
                "bot.research.futures_agent.schema.validate_stage1_schema",
                side_effect=[{"valid": False, "errors": ["missing FK"]}],
            ):
                with self.assertRaises(RuntimeError):
                    with agent_connection() as conn:
                        apply_migrations(conn)
            mock_pg.rollback.assert_called()

    def test_execute_does_not_append_returning_id(self) -> None:
        mock_conn = MagicMock()
        psycopg2, extras = _fake_psycopg2_modules()
        with patch.dict(sys.modules, {"psycopg2": psycopg2, "psycopg2.extras": extras}):
            wrapper = _PgConnWrapper(mock_conn)
            wrapper.execute(
                "INSERT INTO futures_agent_migrations (version, description) VALUES (?, ?)",
                (1, "stage1"),
            )
        executed_sql = mock_conn.cursor.return_value.execute.call_args[0][0]
        self.assertNotIn("RETURNING", executed_sql.upper())

    def test_insert_returning_id_appends_returning_for_inputs(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {"id": 42}
        mock_conn.cursor.return_value = mock_cur
        psycopg2, extras = _fake_psycopg2_modules()
        with patch.dict(sys.modules, {"psycopg2": psycopg2, "psycopg2.extras": extras}):
            wrapper = _PgConnWrapper(mock_conn)
            row_id = wrapper.insert_returning_id(
                "INSERT INTO futures_agent_inputs (raw_text, received_at, input_type, processing_status) "
                "VALUES (?, ?, ?, ?)",
                ("x", 1, "cli", "received"),
            )
        executed_sql = mock_cur.execute.call_args[0][0]
        self.assertIn("RETURNING ID", executed_sql.upper())
        self.assertEqual(row_id, 42)

    def test_migration_insert_via_execute_no_returning(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            conn = sqlite3.connect(f.name)
            conn.row_factory = sqlite3.Row
            apply_migrations(conn)
            row = conn.execute(
                "SELECT version, description FROM futures_agent_migrations WHERE version = 1"
            ).fetchone()
            self.assertIsNotNone(row)
            conn.close()

    def test_input_insert_returns_id_sqlite(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            url = f"sqlite:///{f.name}"
            with agent_connection(url) as conn:
                apply_migrations(conn)
                ing = ingest_forwarded_signal(
                    conn, raw_text=EXPLICIT_LONG, telegram_message_id="pg-test-1",
                )
            self.assertGreater(ing.input_id, 0)

    def test_signal_insert_returns_id_sqlite(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            url = f"sqlite:///{f.name}"
            with agent_connection(url) as conn:
                apply_migrations(conn)
                ing = ingest_forwarded_signal(
                    conn, raw_text=EXPLICIT_LONG, telegram_message_id="sig-id-1",
                )
                proc = process_input(conn, ing.input_id)
            self.assertIsNotNone(proc.signal_id)
            self.assertGreater(proc.signal_id, 0)

    def test_migration_idempotent(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            url = f"sqlite:///{f.name}"
            with agent_connection(url) as conn:
                first = apply_migrations(conn)
                second = apply_migrations(conn)
                from bot.research.futures_agent.schema_validate import validate_stage1_schema
                validation = validate_stage1_schema(conn)
            self.assertTrue(first)
            self.assertEqual(second, [])
            self.assertTrue(validation["valid"])

    def test_ingest_persists_input_signal_and_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "agent.db"
            url = f"sqlite:///{db_path}"
            with agent_connection(url) as conn:
                apply_migrations(conn)
                ing = ingest_forwarded_signal(
                    conn, raw_text=EXPLICIT_LONG, telegram_message_id="full-1",
                )
                proc = process_input(conn, ing.input_id)
                tps = conn.execute(
                    "SELECT target_index, target_price FROM futures_agent_targets "
                    "WHERE signal_id = ? ORDER BY target_index",
                    (proc.signal_id,),
                ).fetchall()
                sig = conn.execute(
                    "SELECT input_id FROM futures_agent_signals WHERE id = ?",
                    (proc.signal_id,),
                ).fetchone()
            self.assertEqual(sig["input_id"], ing.input_id)
            self.assertEqual(len(tps), 2)

    def test_ingest_persists_to_configured_sqlite_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "agent.db"
            url = f"sqlite:///{db_path}"
            with agent_connection(url) as conn:
                apply_migrations(conn)
                ing = ingest_forwarded_signal(
                    conn, raw_text=EXPLICIT_LONG, telegram_message_id="persist-1",
                )
                process_input(conn, ing.input_id)
                n_inputs = conn.execute("SELECT COUNT(*) AS n FROM futures_agent_inputs").fetchone()["n"]
                n_signals = conn.execute("SELECT COUNT(*) AS n FROM futures_agent_signals").fetchone()["n"]
            self.assertEqual(n_inputs, 1)
            self.assertEqual(n_signals, 1)

    def test_process_pending_same_backend_as_ingest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "shared.db"
            url = f"sqlite:///{db_path}"
            with agent_connection(url) as conn:
                apply_migrations(conn)
                ingest_forwarded_signal(
                    conn, raw_text=EXPLICIT_LONG, telegram_message_id="pending-1",
                )
                results = process_pending(conn, limit=5)
                n = conn.execute(
                    "SELECT COUNT(*) AS n FROM futures_agent_inputs WHERE processing_status != 'received'"
                ).fetchone()["n"]
            self.assertEqual(len(results), 1)
            self.assertEqual(n, 1)

    def test_insert_returning_id_helper_sqlite(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            conn = sqlite3.connect(f.name)
            conn.execute(
                "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)"
            )
            row_id = insert_returning_id(conn, "INSERT INTO t (v) VALUES (?)", ("hello",))
            self.assertEqual(row_id, 1)
            conn.close()


MARKET_REVIEW = (
    "ATOM Technical Analysis / Review\n"
    "Outlook neutral. BTC correlates with market."
)


class FuturesAgentBooleanPersistenceTestCase(unittest.TestCase):
    def tearDown(self) -> None:
        reset_bootstrap_for_tests()

    def _sqlite_conn(self, db_path: Path):
        return agent_connection(f"sqlite:///{db_path}")

    def test_explicit_signal_writes_passes_gate_true_as_bool(self) -> None:
        captured: list[tuple] = []
        real_insert = insert_returning_id

        def track_insert(conn, sql, params=None):
            if "futures_agent_signals" in sql:
                captured.append(tuple(params or ()))
            return real_insert(conn, sql, params)

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "bool.db"
            with patch(
                "bot.research.futures_agent.pipeline.insert_returning_id",
                side_effect=track_insert,
            ):
                with self._sqlite_conn(db_path) as conn:
                    apply_migrations(conn)
                    ing = ingest_forwarded_signal(
                        conn, raw_text=EXPLICIT_LONG, telegram_message_id="bool-true-1",
                    )
                    proc = process_input(conn, ing.input_id)
            self.assertTrue(proc.passes_gate)
            self.assertEqual(len(captured), 1)
            self.assertIs(captured[0][12], True)
            self.assertIsInstance(captured[0][12], bool)

    def test_rejected_signal_writes_passes_gate_false_as_bool(self) -> None:
        captured: list[tuple] = []
        real_insert = insert_returning_id

        def track_insert(conn, sql, params=None):
            if "futures_agent_signals" in sql:
                captured.append(tuple(params or ()))
            return real_insert(conn, sql, params)

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "bool.db"
            with patch(
                "bot.research.futures_agent.pipeline.insert_returning_id",
                side_effect=track_insert,
            ):
                with self._sqlite_conn(db_path) as conn:
                    apply_migrations(conn)
                    ing = ingest_forwarded_signal(
                        conn, raw_text=MARKET_REVIEW, telegram_message_id="bool-false-1",
                    )
                    proc = process_input(conn, ing.input_id)
            self.assertFalse(proc.passes_gate)
            self.assertEqual(len(captured), 1)
            self.assertIs(captured[0][12], False)
            self.assertIsInstance(captured[0][12], bool)

    def test_full_ingest_process_signal_targets_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "full.db"
            with self._sqlite_conn(db_path) as conn:
                apply_migrations(conn)
                ing = ingest_forwarded_signal(
                    conn, raw_text=EXPLICIT_LONG, telegram_message_id="full-tx-1",
                )
                proc = process_input(conn, ing.input_id)
                sig = conn.execute(
                    "SELECT passes_gate, input_id FROM futures_agent_signals WHERE id = ?",
                    (proc.signal_id,),
                ).fetchone()
                tps = conn.execute(
                    "SELECT COUNT(*) AS n FROM futures_agent_targets WHERE signal_id = ?",
                    (proc.signal_id,),
                ).fetchone()["n"]
            self.assertEqual(sig["input_id"], ing.input_id)
            self.assertEqual(sig["passes_gate"], 1)
            self.assertEqual(tps, 2)

    def test_signal_insert_failure_rolls_back_input_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rollback.db"
            with patch(
                "bot.research.futures_agent.pipeline.insert_returning_id",
                side_effect=AgentDbError("simulated signal insert failure"),
            ):
                with self.assertRaises(AgentDbError):
                    with self._sqlite_conn(db_path) as conn:
                        apply_migrations(conn)
                        ing = ingest_forwarded_signal(
                            conn, raw_text=EXPLICIT_LONG, telegram_message_id="rb-1",
                        )
                        process_input(conn, ing.input_id)
            with self._sqlite_conn(db_path) as conn:
                n_inputs = conn.execute(
                    "SELECT COUNT(*) AS n FROM futures_agent_inputs"
                ).fetchone()["n"]
            self.assertEqual(n_inputs, 0)

    def test_postgres_adapter_receives_bool_passes_gate_param(self) -> None:
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = {"id": 1}
        mock_conn.cursor.return_value = mock_cur
        psycopg2, extras = _fake_psycopg2_modules()
        with patch.dict(sys.modules, {"psycopg2": psycopg2, "psycopg2.extras": extras}):
            wrapper = _PgConnWrapper(mock_conn)
            insert_returning_id(
                wrapper,
                """
                INSERT INTO futures_agent_signals (
                    input_id, parser_version, taxonomy, symbol, direction,
                    entry_low, entry_high, stop_loss, leverage, timeframe,
                    explicit_confidence, parse_status, passes_gate, gate_reason, parse_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, "v2", "EXPLICIT_SIGNAL", "SUI", "LONG", 2.14, 2.18, 2.05,
                 None, None, None, "SUCCESS", True, "ok", "{}"),
            )
        params = mock_cur.execute.call_args[0][1]
        self.assertIs(params[12], True)
        self.assertIsInstance(params[12], bool)


class FuturesAgentCliConfigTestCase(unittest.TestCase):
    def tearDown(self) -> None:
        reset_bootstrap_for_tests()
        os.environ.pop("FUTURES_AGENT_DATABASE_URL", None)

    def test_audit_reports_backend_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "reports").mkdir()
            (root / "bot" / "research" / "futures").mkdir(parents=True)
            (root / "bot" / "research" / "futures" / "parser_v2.py").touch()
            (root / "bot" / "research" / "futures" / "taxonomy.py").touch()
            (root / "bot" / "research" / "futures" / "source_reader.py").touch()
            (root / ".env").write_text(
                "FUTURES_AGENT_DATABASE_URL=postgresql:///trading_ai\n",
                encoding="utf-8",
            )
            db_path = root / "data" / "agent.db"
            with patch(
                "bot.research.futures_agent.env_bootstrap.project_root",
                return_value=root,
            ), patch(
                "bot.research.futures_agent.audit.project_root",
                return_value=root,
            ), patch(
                "bot.research.futures_agent.db.agent_connection",
            ) as mock_conn:
                mock_ctx = MagicMock()
                mock_ctx.__enter__.return_value = MagicMock()
                mock_ctx.__exit__.return_value = False
                mock_conn.return_value = mock_ctx
                reset_bootstrap_for_tests()
                os.environ.pop("FUTURES_AGENT_DATABASE_URL", None)
                from bot.research.futures_agent.audit import run_architecture_audit

                audit = run_architecture_audit()
        diag = audit["db_diagnostics"]
        self.assertEqual(diag["backend"], "postgresql")
        self.assertEqual(diag["database_name"], "trading_ai")
        self.assertEqual(diag["config_source"], "project_dotenv")
        self.assertFalse(db_path.exists())


@unittest.skipUnless(
    os.getenv("FUTURES_AGENT_PG_SMOKE_TEST"),
    "set FUTURES_AGENT_PG_SMOKE_TEST=1 for real PostgreSQL smoke test",
)
class FuturesAgentPostgresSmokeTest(unittest.TestCase):
    def tearDown(self) -> None:
        reset_bootstrap_for_tests()

    def test_real_postgresql_migrate_and_ingest(self) -> None:
        reset_bootstrap_for_tests()
        url = os.environ.get("FUTURES_AGENT_DATABASE_URL", "")
        if not url.startswith(("postgres://", "postgresql://")):
            self.skipTest("FUTURES_AGENT_DATABASE_URL must be PostgreSQL")
        with agent_connection() as conn:
            applied = apply_migrations(conn)
            validation = validate_stage1_schema(conn)
            if applied:
                applied_again = apply_migrations(conn)
                self.assertEqual(applied_again, [])
        self.assertTrue(validation["valid"])


if __name__ == "__main__":
    unittest.main()
