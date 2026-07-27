"""Runtime health — unified project status, live watch, and self-test."""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR, MARKET_EVENTS_DATABASE_PATH

HEARTBEAT_STALE_SEC = 90
WRITE_ACTIVITY_SEC = 180
LOCK_LOG_LOOKBACK_SEC = 300
ERROR_LOG_LINES = 400

WATCH_CORE_SYMBOLS = ("BTC", "SOL", "ETH")


@dataclass
class RuntimeLine:
    label: str
    status: str  # OK | WARN | FAIL
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "OK"


@dataclass
class RuntimeHealth:
    lines: list[RuntimeLine] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    heartbeat_age_sec: int | None = None
    db_bytes: int = 0
    recent_errors: list[str] = field(default_factory=list)
    shadow_checked: int = 0
    shadow_accepted: int = 0
    near_miss_recent: int = 0
    events_recent: int = 0
    prices: dict[str, float | None] = field(default_factory=dict)
    uptime_sec: int | None = None
    last_fetch_ts: int | None = None
    sqlite_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return not self.reasons

    @property
    def overall(self) -> str:
        return "HEALTHY" if self.healthy else "WARNING"


def _logs_dir() -> Path:
    from bot.ops.process_utils import logs_dir

    return logs_dir()


def _proc_ok(key: str) -> tuple[bool, str]:
    try:
        from bot.research.market_events.process_manager import (
            _service_by_key,
            find_service_processes,
        )

        procs = find_service_processes(_service_by_key(key))
        if procs:
            return True, f"PID {procs[0].pid}"
        return False, "not running"
    except Exception as exc:
        return False, str(exc)[:40]


def _check_bybit(*, skip_network: bool) -> tuple[bool, str]:
    if skip_network:
        return True, "skipped"
    try:
        from bot.research.market_events.venue_bybit import BybitMarketClient

        client = BybitMarketClient(read_timeout=5.0, connect_timeout=3.0)
        ticker = client.fetch_ticker("BTCUSDT")
        if ticker and ticker.last_price > 0:
            return True, f"BTC {ticker.last_price:.2f}"
        return False, "no ticker"
    except Exception as exc:
        return False, str(exc)[:50]


def _check_polymarket(*, skip_network: bool) -> tuple[bool, str]:
    if skip_network:
        return True, "skipped"
    try:
        import requests

        url = "https://gamma-api.polymarket.com/markets"
        resp = requests.get(url, params={"limit": 1}, timeout=5.0)
        if resp.ok:
            return True, f"HTTP {resp.status_code}"
        return False, f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, str(exc)[:50]


def _heartbeat_age(conn: Any) -> tuple[int | None, str | None]:
    from bot.research.market_events.signal_intelligence.health_g3 import get_g3_ops_state

    raw = get_g3_ops_state(conn, "system_heartbeat_ts")
    if not raw:
        return None, None
    try:
        ts = int(raw)
    except ValueError:
        return None, None
    age = int(time.time()) - ts
    writer = get_g3_ops_state(conn, "system_heartbeat_writer")
    return age, writer


def _sqlite_lock_recent() -> tuple[bool, str, dict[str, Any]]:
    """SQLite lock health + breakdown for doctor.

    Returns (ok, short_detail, summary_dict).
    ok=False when real lock incidents > 0 OR writes/paper lost > 0 in the window.
    """
    summary: dict[str, Any] = {
        "raw_lock_events": 0,
        "retry_attempts": 0,
        "real_lock_incidents": 0,
        "writes_lost": 0,
        "paper_trades_lost": 0,
        "mfe_mae_deferred": 0,
        "lookback_sec": float(LOCK_LOG_LOOKBACK_SEC),
    }
    try:
        from bot.research.market_events.sqlite_manager_g05 import summarize_lock_diagnostics

        summary = summarize_lock_diagnostics(lookback_sec=float(LOCK_LOG_LOOKBACK_SEC))
        incidents = int(summary.get("real_lock_incidents") or 0)
        lost = int(summary.get("writes_lost") or 0) + int(summary.get("paper_trades_lost") or 0)
        if incidents > 0 or lost > 0:
            detail = (
                f"{incidents} incident(s), "
                f"{int(summary.get('raw_lock_events') or 0)} raw / "
                f"{int(summary.get('retry_attempts') or 0)} retries in {LOCK_LOG_LOOKBACK_SEC}s"
            )
            return False, detail, summary
        return True, "0 real incidents", summary
    except Exception:
        pass

    # Fallback: legacy stamped log lines
    now = int(time.time())
    cutoff = now - LOCK_LOG_LOOKBACK_SEC
    hits: list[str] = []
    log_dir = _logs_dir()
    for name in ("me-shock-paper-core.log", "me-shock-paper-tradfi.log"):
        path = log_dir / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for line in text.splitlines()[-ERROR_LOG_LINES:]:
            if "database is locked" not in line and "database table is locked" not in line:
                continue
            if len(line) < 19 or line[4] != "-" or line[10] != "T":
                continue
            stamp = line[:19]
            try:
                import datetime as _dt
                ts = int(_dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S").timestamp())
            except Exception:
                continue
            if ts < cutoff:
                continue
            hits.append(line.strip()[:120])
    if hits:
        summary["raw_lock_events"] = len(hits)
        summary["real_lock_incidents"] = len(hits)
        return False, f"{len(hits)} recent lock line(s) in logs", summary
    return True, "none in tail", summary


def _count_since(conn: Any, table: str, ts_col: str, since: int) -> int:
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE {ts_col} >= ?",
            (since,),
        ).fetchone()
        return int(row["n"] if row else 0)
    except Exception:
        return 0


def _recent_errors() -> list[str]:
    out: list[str] = []
    log_dir = _logs_dir()
    for name in ("me-shock-paper-core.log", "me-shock-paper-tradfi.log"):
        path = log_dir / name
        if not path.is_file():
            continue
        try:
            lines = path.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for line in reversed(lines[-ERROR_LOG_LINES:]):
            low = line.lower()
            if "cycle error" in low or "database is locked" in low:
                out.append(line.strip()[:140])
            elif "error" in low and "[shock-paper]" in line:
                out.append(line.strip()[:140])
            if len(out) >= 5:
                break
        if len(out) >= 5:
            break
    return out[:5]


def _db_size_bytes() -> int:
    path = MARKET_EVENTS_DATABASE_PATH
    if not path.is_file():
        return 0
    total = path.stat().st_size
    for suffix in ("-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.is_file():
            total += p.stat().st_size
    return total


def _format_db_size(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f} GB"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    if n >= 1000:
        return f"{n / 1000:.1f} KB"
    return f"{n} B"


def _line(label: str, ok: bool, detail: str = "", *, warn_if_false: bool = True) -> RuntimeLine:
    if ok:
        return RuntimeLine(label, "OK", detail)
    status = "WARN" if warn_if_false else "FAIL"
    return RuntimeLine(label, status, detail)


def collect_runtime_health(*, skip_network: bool = False) -> RuntimeHealth:
    rh = RuntimeHealth()
    now = int(time.time())
    since = now - WRITE_ACTIVITY_SEC

    core_ok, core_d = _proc_ok("shock-paper-core")
    tradfi_ok, tradfi_d = _proc_ok("shock-paper-tradfi")
    dash_ok, dash_d = _proc_ok("dashboard")

    rh.lines.append(_line("Core", core_ok, core_d))
    if not core_ok:
        rh.reasons.append("Core not running")
    rh.lines.append(_line("TradFi", tradfi_ok, tradfi_d, warn_if_false=False))

    bybit_ok, bybit_d = _check_bybit(skip_network=skip_network)
    poly_ok, poly_d = _check_polymarket(skip_network=skip_network)
    rh.lines.append(_line("Bybit", bybit_ok, bybit_d))
    rh.lines.append(_line("Polymarket", poly_ok, poly_d))
    if not bybit_ok:
        rh.reasons.append("Bybit unreachable")
    if not poly_ok:
        rh.reasons.append("Polymarket unreachable")

    lock_ok, lock_d, lock_summary = _sqlite_lock_recent()
    rh.sqlite_summary = lock_summary
    rh.lines.append(
        _line("SQLite", lock_ok, "0 real incidents" if lock_ok else lock_d),
    )
    if not lock_ok:
        rh.reasons.append("database is locked (recent)")
    if int(lock_summary.get("writes_lost") or 0) or int(lock_summary.get("paper_trades_lost") or 0):
        rh.reasons.append("sqlite integrity loss recorded")

    rh.db_bytes = _db_size_bytes()

    try:
        from bot.research.market_events.db import market_events_readonly_connection

        with market_events_readonly_connection() as conn:
            conn.execute("SELECT 1")
            age, writer = _heartbeat_age(conn)
            rh.heartbeat_age_sec = age
            if age is None:
                hb_ok = False
                hb_detail = "no heartbeat ts"
            elif age <= HEARTBEAT_STALE_SEC:
                hb_ok = True
                hb_detail = f"{age} sec ago"
            else:
                hb_ok = False
                hb_detail = f"{age} sec ago (stale)"
            rh.lines.append(RuntimeLine("Heartbeat", "OK" if hb_ok else "WARN", hb_detail))
            if not hb_ok:
                rh.reasons.append("Heartbeat stale")

            rh.events_recent = _count_since(conn, "market_events", "created_at", since)
            events_ok = rh.events_recent > 0 or not core_ok
            if core_ok and rh.events_recent == 0:
                # Shock events rare; pipeline alive if near_miss or shadow writes
                nm = _count_since(conn, "market_events_near_miss_summaries", "created_at", since)
                sh = _count_since(conn, "market_events_shadow", "created_at", since)
                events_ok = nm > 0 or sh > 0
                events_detail = "pipeline writes" if events_ok else "no recent rows"
            else:
                events_detail = f"{rh.events_recent} in {WRITE_ACTIVITY_SEC}s"
            rh.lines.append(_line("Events", events_ok, events_detail))
            if core_ok and not events_ok:
                rh.reasons.append("Events not writing")

            rh.near_miss_recent = _count_since(
                conn, "market_events_near_miss_summaries", "created_at", since,
            )
            nm_ok = rh.near_miss_recent > 0 or not core_ok
            if core_ok and rh.near_miss_recent == 0:
                row_nm = conn.execute(
                    "SELECT MAX(created_at) AS m FROM market_events_near_miss_summaries",
                ).fetchone()
                last_nm = int(row_nm["m"] or 0) if row_nm and row_nm["m"] else 0
                if last_nm and (now - last_nm) <= 120:
                    nm_ok = True
                    rh.near_miss_recent = _count_since(
                        conn, "market_events_near_miss_summaries", "created_at", now - 120,
                    )
            rh.lines.append(
                _line("Near miss", nm_ok, f"{rh.near_miss_recent} in {WRITE_ACTIVITY_SEC}s"),
            )
            if core_ok and not nm_ok:
                rh.reasons.append("Near miss not writing")

            rh.shadow_checked = _count_since(conn, "market_events_shadow", "created_at", since)
            try:
                row = conn.execute(
                    """
                    SELECT SUM(accepted) AS a FROM market_events_shadow
                    WHERE created_at >= ?
                    """,
                    (since,),
                ).fetchone()
                rh.shadow_accepted = int(row["a"] or 0) if row else 0
            except Exception:
                rh.shadow_accepted = 0
            sh_ok = rh.shadow_checked > 0 or not core_ok
            rh.lines.append(
                _line(
                    "Shadow",
                    sh_ok,
                    f"checked={rh.shadow_checked} accepted={rh.shadow_accepted}",
                ),
            )
            if core_ok and rh.shadow_checked == 0:
                rh.reasons.append("Shadow not writing")

            if not dash_ok:
                rh.reasons.append("Dashboard not running")

    except Exception as exc:
        rh.lines.append(RuntimeLine("SQLite", "FAIL", str(exc)[:60]))
        rh.reasons.append("DB unreachable")

    rh.recent_errors = _recent_errors()
    return rh


def format_runtime_health(rh: RuntimeHealth) -> str:
    def _pad(label: str, text: str) -> str:
        dots = max(1, 16 - len(label))
        return f"{label}{'.' * dots} {text}"

    display_order = (
        "Core", "TradFi", "Bybit", "Polymarket", "SQLite",
        "Heartbeat", "Events", "Near miss", "Shadow",
    )
    by_label = {x.label: x for x in rh.lines}

    lines = ["SYSTEM STATUS", ""]
    for label in display_order:
        item = by_label.get(label)
        if not item:
            continue
        if label == "Heartbeat":
            if rh.heartbeat_age_sec is not None and item.ok:
                lines.append(_pad("Heartbeat", f"{rh.heartbeat_age_sec} sec ago"))
            elif item.ok:
                lines.append(_pad("Heartbeat", "OK"))
            else:
                lines.append(_pad("Heartbeat", item.detail or "stale"))
        elif label == "SQLite":
            if item.ok:
                lines.append(_pad("SQLite", "OK"))
            else:
                lines.append(_pad("SQLite", f"FAIL — {item.detail}" if item.detail else "FAIL"))
            try:
                from bot.research.market_events.sqlite_manager_g05 import (
                    format_lock_diagnostics_block,
                )

                summary = rh.sqlite_summary or None
                lines.extend(format_lock_diagnostics_block(summary))
            except Exception:
                pass
        elif item.ok:
            lines.append(_pad(label, "OK"))
        else:
            lines.append(_pad(label, f"WARN — {item.detail}" if item.detail else "WARN"))

    lines.append("")
    lines.append(f"DB size .......... {_format_db_size(rh.db_bytes)}")
    lines.append("")
    lines.append(f"Overall: {rh.overall}")
    if rh.reasons:
        lines.append("")
        lines.append("Reason:")
        for r in rh.reasons:
            lines.append(r)
    if rh.recent_errors:
        lines.append("")
        lines.append("Recent errors:")
        for e in rh.recent_errors:
            lines.append(f"  {e}")
    return "\n".join(lines)


def run_runtime_doctor(*, skip_network: bool = False) -> str:
    return format_runtime_health(collect_runtime_health(skip_network=skip_network))


def _fetch_prices(symbols: tuple[str, ...]) -> dict[str, float | None]:
    out: dict[str, float | None] = {s: None for s in symbols}
    try:
        from bot.research.market_events.venue_bybit import BybitMarketClient

        client = BybitMarketClient(read_timeout=4.0, connect_timeout=2.0)
        for sym in symbols:
            try:
                t = client.fetch_ticker(f"{sym}USDT")
                if t and t.last_price > 0:
                    out[sym] = float(t.last_price)
            except Exception:
                pass
    except Exception:
        pass
    return out


def _runner_uptime_sec() -> int | None:
    try:
        from bot.research.market_events.process_manager import (
            _service_by_key,
            find_service_processes,
        )

        procs = find_service_processes(_service_by_key("shock-paper-core"))
        if not procs:
            return None
        pid = procs[0].pid
        out = __import__("subprocess").check_output(
            ["ps", "-o", "etime=", "-p", str(pid)],
            text=True,
            timeout=3,
        ).strip()
        # etime formats: ss, mm:ss, hh:mm:ss, dd-hh:mm:ss
        sec = 0
        if "-" in out:
            day_part, rest = out.split("-", 1)
            sec += int(day_part) * 86400
            out = rest
        parts = out.split(":")
        parts = [int(p) for p in parts]
        if len(parts) == 1:
            sec += parts[0]
        elif len(parts) == 2:
            sec += parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            sec += parts[0] * 3600 + parts[1] * 60 + parts[2]
        return sec
    except Exception:
        return None


def collect_watch_snapshot(*, skip_network: bool = False) -> RuntimeHealth:
    """One frame for watch display."""
    rh = collect_runtime_health(skip_network=skip_network)
    if not skip_network:
        rh.prices = _fetch_prices(WATCH_CORE_SYMBOLS)
        rh.last_fetch_ts = int(time.time())
    rh.uptime_sec = _runner_uptime_sec()
    return rh


def format_watch_frame(rh: RuntimeHealth) -> str:
    from bot.research.market_events.collector_heartbeat import format_uptime

    lines = [
        f"market_events watch  {time.strftime('%H:%M:%S')}",
        "",
    ]
    for sym in WATCH_CORE_SYMBOLS:
        px = rh.prices.get(sym)
        lines.append(f"{sym:4}  {px:.4f}" if px is not None else f"{sym:4}  —")
    lines.extend([
        "",
        f"events checked ... {rh.shadow_checked} (shadow rows / {WRITE_ACTIVITY_SEC}s)",
        f"events accepted .. {rh.shadow_accepted}",
        f"near miss ........ {rh.near_miss_recent}",
        f"heartbeat ........ {rh.heartbeat_age_sec}s ago"
        if rh.heartbeat_age_sec is not None
        else "heartbeat ........ —",
        f"last fetch ....... {time.strftime('%H:%M:%S', time.localtime(rh.last_fetch_ts))}"
        if rh.last_fetch_ts
        else "last fetch ....... —",
        f"errors ........... {len(rh.recent_errors)} recent",
        f"uptime ........... {format_uptime(rh.uptime_sec) if rh.uptime_sec else '—'}",
        f"overall .......... {rh.overall}",
    ])
    if rh.recent_errors:
        lines.append("")
        lines.append(rh.recent_errors[0][:100])
    return "\n".join(lines)


def run_watch(*, interval_sec: float = 5.0, skip_network: bool = False) -> None:
    """Full-screen refresh loop (htop-style). Ctrl+C to exit."""
    try:
        while True:
            rh = collect_watch_snapshot(skip_network=skip_network)
            frame = format_watch_frame(rh)
            if sys.stdout.isatty():
                sys.stdout.write("\033[2J\033[H")
            print(frame, flush=True)
            time.sleep(interval_sec)
    except KeyboardInterrupt:
        print("\nwatch stopped.", flush=True)


def run_self_test(*, skip_network: bool = False) -> str:
    """End-to-end smoke: DB, schema, APIs, detector, shadow + heartbeat writes."""
    failures: list[str] = []

    def fail(msg: str) -> None:
        failures.append(msg)

    import tempfile
    from pathlib import Path

    from bot.research.market_events.db import market_events_connection
    from bot.research.market_events.db_config import configure_unit_test_db_isolation
    from bot.research.market_events.event_schema import apply_migrations

    tmp = tempfile.TemporaryDirectory()
    try:
        db_path = Path(tmp.name) / "selftest.db"
        configure_unit_test_db_isolation(db_path)
        with market_events_connection() as conn:
            apply_migrations(conn)
            conn.commit()
            for table in (
                "market_events_shadow",
                "market_events_g3_ops_state",
                "market_events_near_miss_summaries",
                "market_events",
            ):
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if not row:
                    fail(f"missing table {table}")

            from bot.research.market_events.adaptive_shock_shadow import (
                evaluate_detectors_for_profile,
                persist_shadow_evals,
            )
            from bot.research.market_events.config import ADAPTIVE_V1_THRESHOLDS, PROFILE_ADAPTIVE_V1
            from bot.research.market_events.price_feed import PriceTick, SymbolPriceState

            now = int(time.time())
            st = SymbolPriceState(symbol="BTC", pair="BTCUSDT")
            st.append(PriceTick(ts=now - 30, price=100.0, volume=1.0), max_age_sec=300)
            st.append(PriceTick(ts=now, price=100.35, volume=1.0), max_age_sec=300)
            evals = evaluate_detectors_for_profile(
                st,
                profile_name=PROFILE_ADAPTIVE_V1,
                thresholds=ADAPTIVE_V1_THRESHOLDS,
                now_ts=now,
            )
            if not evals:
                fail("detector returned no evals")
            else:
                n = persist_shadow_evals(conn, evals[:1])
                if n != 1:
                    fail("shadow persist failed")

            from bot.research.market_events.signal_intelligence.heartbeat_diagnostics_g352 import (
                write_system_heartbeat,
            )

            write_system_heartbeat(conn, writer="self-test", force=True)
            conn.commit()
            hb = conn.execute(
                "SELECT value FROM market_events_g3_ops_state WHERE key='system_heartbeat_writer'",
            ).fetchone()
            if not hb or hb["value"] != "self-test":
                fail("heartbeat write failed")
    except Exception as exc:
        fail(f"db pipeline: {exc}")
    finally:
        tmp.cleanup()

    if not skip_network:
        ok_b, _ = _check_bybit(skip_network=False)
        if not ok_b:
            fail("Bybit API")
        ok_p, _ = _check_polymarket(skip_network=False)
        if not ok_p:
            fail("Polymarket API")
    else:
        pass

    if failures:
        lines = ["FAIL", "", "reason:"]
        lines.extend(failures)
        return "\n".join(lines)
    return "PASS"
