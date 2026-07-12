"""Phase F.5.2 — Telegram signal intake diagnostics."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.research.futures_agent.db import AgentDbError, agent_connection, insert_returning_id
from bot.research.futures_agent.env_bootstrap import AgentDbConfig, env_file_path, project_root, resolve_agent_db_config
from bot.research.futures_agent.schema import apply_migrations
from bot.research.futures_agent.telegram_config import get_allowed_chat_ids, get_telegram_bot_token

STATS_FILE = "data/futures_agent_telegram_poll_stats.json"
LOCK_FILE = "data/futures_agent_telegram_poll.lock"
STATS_INTERVAL_SEC = 30 * 60

IGNORE_CHAT_NOT_ALLOWED = "chat not allowed"
IGNORE_EMPTY_MESSAGE = "empty message"
IGNORE_PARSER_REJECTED = "parser rejected"
IGNORE_SNAPSHOT_UNAVAILABLE = "snapshot unavailable"
IGNORE_DUPLICATE = "duplicate message"
IGNORE_DATABASE_ERROR = "database error"
IGNORE_NON_MESSAGE_UPDATE = "non-message update"

ALL_IGNORE_REASONS = frozenset({
    IGNORE_CHAT_NOT_ALLOWED,
    IGNORE_EMPTY_MESSAGE,
    IGNORE_PARSER_REJECTED,
    IGNORE_SNAPSHOT_UNAVAILABLE,
    IGNORE_DUPLICATE,
    IGNORE_DATABASE_ERROR,
    IGNORE_NON_MESSAGE_UPDATE,
})


def _stats_path() -> Path:
    return project_root() / STATS_FILE


def _lock_path() -> Path:
    return project_root() / LOCK_FILE


def _day_start_ts() -> int:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


def _format_duration(seconds: float) -> str:
    sec = max(0, int(seconds))
    if sec < 60:
        return f"{sec} sec"
    if sec < 3600:
        return f"{sec // 60} min"
    hours = sec // 3600
    mins = (sec % 3600) // 60
    return f"{hours} hr {mins} min"


def _format_ago(ts: int | None) -> str:
    if not ts:
        return "never"
    delta = int(time.time()) - int(ts)
    if delta < 0:
        return "just now"
    return f"{_format_duration(delta)} ago"


def _redact_token(token: str) -> str:
    if not token:
        return "(not set)"
    if len(token) <= 10:
        return "***"
    return f"{token[:6]}...{token[-4:]}"


def is_poll_running() -> bool:
    path = _lock_path()
    if not path.is_file():
        return False
    try:
        import fcntl
        fd = os.open(str(path), os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except BlockingIOError:
            return True
        finally:
            os.close(fd)
    except Exception:
        return path.exists()


@dataclass
class PollSessionStats:
    started_at: int = field(default_factory=lambda: int(time.time()))
    last_message_at: int | None = None
    received: int = 0
    processed: int = 0
    ignored: int = 0
    parser_errors: int = 0
    snapshot_failures: int = 0
    replies_sent: int = 0
    replies_failed: int = 0
    processing_ms_total: int = 0
    processing_count: int = 0

    @property
    def avg_processing_ms(self) -> float:
        if self.processing_count <= 0:
            return 0.0
        return self.processing_ms_total / self.processing_count

    def record_received(self) -> None:
        self.received += 1

    def record_processed(self, *, processing_ms: int | None = None) -> None:
        self.processed += 1
        if processing_ms is not None:
            self.processing_ms_total += processing_ms
            self.processing_count += 1

    def record_ignored(self, *, parser_error: bool = False, snapshot_failure: bool = False) -> None:
        self.ignored += 1
        if parser_error:
            self.parser_errors += 1
        if snapshot_failure:
            self.snapshot_failures += 1

    def record_reply(self, *, sent: bool) -> None:
        if sent:
            self.replies_sent += 1
        else:
            self.replies_failed += 1

    def touch_message(self) -> None:
        self.last_message_at = int(time.time())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_poll_stats() -> PollSessionStats:
    path = _stats_path()
    if not path.is_file():
        return PollSessionStats()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PollSessionStats(**{k: data[k] for k in PollSessionStats.__dataclass_fields__ if k in data})
    except (json.JSONDecodeError, OSError, TypeError, KeyError):
        return PollSessionStats()


def save_poll_stats(stats: PollSessionStats) -> None:
    path = _stats_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stats.to_dict(), indent=2), encoding="utf-8")


def log_ignored(reason: str, *, logger: Any | None = None) -> None:
    msg = f"Ignored:\n{reason}"
    if logger is not None:
        logger.info(msg)
    else:
        print(msg)


def format_poll_startup(cfg: AgentDbConfig, *, telegram_connected: bool) -> str:
    chats = get_allowed_chat_ids()
    backend_label = "PostgreSQL" if cfg.is_postgres else cfg.backend.capitalize()
    lines = [
        f"Backend: {backend_label}",
    ]
    if cfg.database_name:
        lines.append(f"Database: {cfg.database_name}")
    elif cfg.sqlite_path:
        lines.append(f"Database: {cfg.sqlite_path}")
    lines.append(f"Telegram: {'connected' if telegram_connected else 'not connected'}")
    lines.append(f"Allowed chats: {', '.join(str(c) for c in sorted(chats)) or '(none)'}")
    lines.append("")
    lines.append("Listening...")
    return "\n".join(lines)


def format_periodic_stats(stats: PollSessionStats) -> str:
    lines = [
        "Telegram poll",
        "",
        f"received: {stats.received}",
        "",
        f"processed: {stats.processed}",
        "",
        f"ignored: {stats.ignored}",
        "",
        f"parser errors: {stats.parser_errors}",
        "",
        f"avg processing: {stats.avg_processing_ms:.0f} ms",
        "",
        "last message:",
        _format_ago(stats.last_message_at),
    ]
    return "\n".join(lines)


def query_db_today_stats(conn: Any) -> dict[str, int]:
    since = _day_start_ts()
    tg_like = "telegram:%"
    received = conn.execute(
        """
        SELECT COUNT(*) AS n FROM futures_agent_inputs
        WHERE source LIKE ? AND received_at >= ?
        """,
        (tg_like, since),
    ).fetchone()["n"]

    processed = conn.execute(
        """
        SELECT COUNT(*) AS n FROM futures_agent_inputs
        WHERE source LIKE ? AND received_at >= ? AND processing_status = 'complete'
        """,
        (tg_like, since),
    ).fetchone()["n"]

    parser_failures = conn.execute(
        """
        SELECT COUNT(*) AS n FROM futures_agent_inputs i
        JOIN futures_agent_signals s ON s.input_id = i.id
        WHERE i.source LIKE ? AND i.received_at >= ? AND s.parse_status = 'FAILED'
        """,
        (tg_like, since),
    ).fetchone()["n"]

    snapshot_failures = conn.execute(
        """
        SELECT COUNT(*) AS n FROM futures_agent_inputs i
        JOIN futures_agent_signals s ON s.input_id = i.id
        LEFT JOIN futures_agent_btc_context b ON b.signal_id = s.id
        WHERE i.source LIKE ? AND i.received_at >= ?
          AND s.passes_gate = 1 AND b.id IS NULL
          AND i.processing_status NOT IN ('pending_snapshot', 'complete')
        """,
        (tg_like, since),
    ).fetchone()["n"]

    ignored = max(0, int(received) - int(processed))
    return {
        "received": int(received),
        "processed": int(processed),
        "ignored": ignored,
        "parser_failures": int(parser_failures),
        "snapshot_failures": int(snapshot_failures),
    }


def check_telegram_connected(token: str | None = None) -> bool:
    token = token or get_telegram_bot_token()
    if not token:
        return False
    try:
        from bot.research.futures_agent.telegram_inbound import _api_call
        _api_call(token, "getMe", timeout=8)
        return True
    except Exception:
        return False


def _load_offset() -> int:
    from bot.research.futures_agent.telegram_inbound import _load_offset as load
    return load()


def _load_last_message() -> dict[str, Any] | None:
    from bot.research.futures_agent.telegram_inbound import _load_last_message as load
    return load()


@dataclass(frozen=True)
class TelegramStatusReport:
    backend: str
    database: str
    env_path: str
    telegram_token: str
    allowed_chats: tuple[int, ...]
    last_update_id: int
    last_processed_message: dict[str, Any] | None
    poll_running: bool
    poll_uptime: str
    session_stats: PollSessionStats
    today_stats: dict[str, int]
    replies_sent: int
    replies_failed: int


def build_telegram_status() -> TelegramStatusReport:
    cfg = resolve_agent_db_config()
    stats = load_poll_stats()
    token = get_telegram_bot_token()
    chats = tuple(sorted(get_allowed_chat_ids()))
    running = is_poll_running()

    if running and stats.started_at:
        uptime = _format_duration(time.time() - stats.started_at)
    elif stats.started_at:
        uptime = f"not running (last session {_format_ago(stats.started_at)})"
    else:
        uptime = "not running"

    db_label = cfg.database_name or cfg.sqlite_path or cfg.url
    today: dict[str, int] = {
        "received": 0, "processed": 0, "ignored": 0,
        "parser_failures": 0, "snapshot_failures": 0,
    }
    try:
        with agent_connection() as conn:
            apply_migrations(conn)
            today = query_db_today_stats(conn)
    except Exception:
        pass

    return TelegramStatusReport(
        backend=cfg.backend,
        database=str(db_label),
        env_path=str(env_file_path()),
        telegram_token=_redact_token(token),
        allowed_chats=chats,
        last_update_id=_load_offset(),
        last_processed_message=_load_last_message(),
        poll_running=running,
        poll_uptime=uptime,
        session_stats=stats,
        today_stats=today,
        replies_sent=stats.replies_sent,
        replies_failed=stats.replies_failed,
    )


def format_telegram_status(report: TelegramStatusReport) -> str:
    last_msg = report.last_processed_message
    last_msg_str = (
        f"chat={last_msg.get('chat_id')} msg={last_msg.get('message_id')} "
        f"({_format_ago(last_msg.get('ts'))})"
        if last_msg else "(none)"
    )
    t = report.today_stats
    s = report.session_stats
    lines = [
        "TELEGRAM INTAKE STATUS (F.5.2)",
        "",
        f"backend: {report.backend}",
        f"database: {report.database}",
        f"env path: {report.env_path}",
        f"telegram token: {report.telegram_token}",
        f"allowed chats: {', '.join(str(c) for c in report.allowed_chats) or '(none)'}",
        "",
        f"last update_id: {report.last_update_id}",
        f"last processed message: {last_msg_str}",
        f"poll running: {'yes' if report.poll_running else 'no'}",
        f"poll uptime: {report.poll_uptime}",
        "",
        "session (current/last poll file):",
        f"  received: {s.received}",
        f"  processed: {s.processed}",
        f"  ignored: {s.ignored}",
        f"  parser failures: {s.parser_errors}",
        f"  snapshot failures: {s.snapshot_failures}",
        f"  replies sent: {s.replies_sent}",
        f"  replies failed: {s.replies_failed}",
        "",
        "today (database):",
        f"  processed today: {t['processed']}",
        f"  ignored today: {t['ignored']}",
        f"  parser failures: {t['parser_failures']}",
        f"  snapshot failures: {t['snapshot_failures']}",
    ]
    return "\n".join(lines)


@dataclass
class SelfTestStep:
    name: str
    ok: bool
    detail: str
    latency_ms: int


def run_telegram_selftest() -> tuple[list[SelfTestStep], bool]:
    steps: list[SelfTestStep] = []
    token = get_telegram_bot_token()

    t0 = time.perf_counter()
    ok = bool(token)
    steps.append(SelfTestStep("token configured", ok, "present" if ok else "TELEGRAM_BOT_TOKEN missing", int((time.perf_counter() - t0) * 1000)))
    if not ok:
        return steps, False

    from bot.research.futures_agent.telegram_inbound import _api_call

    t0 = time.perf_counter()
    try:
        me = _api_call(token, "getMe", timeout=10)
        bot = me.get("result", {})
        detail = f"@{bot.get('username', '?')} id={bot.get('id')}"
        steps.append(SelfTestStep("getMe", True, detail, int((time.perf_counter() - t0) * 1000)))
    except Exception as exc:
        steps.append(SelfTestStep("getMe", False, str(exc), int((time.perf_counter() - t0) * 1000)))
        return steps, False

    t0 = time.perf_counter()
    try:
        data = _api_call(token, "getUpdates", {"timeout": 0, "limit": 1}, timeout=8)
        updates = data.get("result", [])
        steps.append(SelfTestStep("getUpdates", True, f"{len(updates)} pending", int((time.perf_counter() - t0) * 1000)))
    except Exception as exc:
        steps.append(SelfTestStep("getUpdates", False, str(exc), int((time.perf_counter() - t0) * 1000)))
        return steps, False

    t0 = time.perf_counter()
    try:
        with agent_connection() as conn:
            apply_migrations(conn)
            row = conn.execute("SELECT 1 AS ok").fetchone()
            ok_db = bool(row and row["ok"] == 1)
        detail = resolve_agent_db_config().backend
        steps.append(SelfTestStep("database connect", ok_db, detail, int((time.perf_counter() - t0) * 1000)))
    except AgentDbError as exc:
        steps.append(SelfTestStep("database connect", False, str(exc), int((time.perf_counter() - t0) * 1000)))
        return steps, False

    t0 = time.perf_counter()
    try:
        with agent_connection() as conn:
            apply_migrations(conn)
            conn.execute("SAVEPOINT f52_selftest")
            test_id = insert_returning_id(
                conn,
                """
                INSERT INTO futures_agent_inputs (
                  source, telegram_message_id, raw_text, received_at,
                  input_type, processing_status
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "selftest:f52",
                    f"selftest-{int(time.time())}",
                    "F.5.2 selftest row — rolled back",
                    int(time.time()),
                    "cli",
                    "received",
                ),
            )
            conn.execute("ROLLBACK TO SAVEPOINT f52_selftest")
            detail = f"insert id={test_id} rolled back"
            steps.append(SelfTestStep("test write + rollback", True, detail, int((time.perf_counter() - t0) * 1000)))
    except Exception as exc:
        steps.append(SelfTestStep("test write + rollback", False, str(exc), int((time.perf_counter() - t0) * 1000)))
        return steps, False

    return steps, True


def format_telegram_selftest(steps: list[SelfTestStep], *, all_ok: bool) -> str:
    lines = [
        "TELEGRAM SELFTEST (F.5.2)",
        "",
    ]
    for step in steps:
        status = "OK" if step.ok else "FAIL"
        lines.append(f"[{status}] {step.name} ({step.latency_ms} ms)")
        lines.append(f"       {step.detail}")
    lines.extend([
        "",
        f"result: {'PASS' if all_ok else 'FAIL'}",
        f"total: {sum(s.latency_ms for s in steps)} ms",
    ])
    return "\n".join(lines)
