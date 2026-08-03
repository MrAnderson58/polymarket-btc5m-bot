"""S63 — Morning Trading Report (read-only).

One overnight summary: system, trading, market, patterns, AI coverage,
Telegram, health, and top action items. No strategy changes.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.research.market_events.signal_intelligence.drift_analyzer_s622 import (
    _enrich_from_snapshot_json,
    resolve_as_of,
)
from bot.research.market_events.signal_intelligence.feature_lab_s59 import load_lab_trades
from bot.research.market_events.signal_intelligence.pattern_discovery_s623 import (
    data_quality_stats,
    tag_trade,
)
from bot.research.market_events.signal_intelligence.trading_intelligence_report_s621 import (
    _enrich_decisions,
    _safe_float,
    _trade_ts,
    coin_ranking,
    default_report_dir,
    filter_by_period,
    hour_analysis,
    overall_performance,
    regime_analysis,
)

logger = logging.getLogger(__name__)

OVERNIGHT_SECONDS = 12 * 3600  # previous night ≈ last 12h

# Same analytics DB contract as rule-health / research-lake-health.
_REQUIRED_ANALYTICS_TABLES = (
    "market_events_paper_trades_s42",
    "market_events_research_lake_v1",
)


def morning_db_self_check(
    conn: Any,
    *,
    db_path: Path | str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Verify morning-report is on the research analytics SQLite (not sibling S60)."""
    path = str(Path(db_path).resolve()) if db_path else None
    missing: list[str] = []
    for table in _REQUIRED_ANALYTICS_TABLES:
        try:
            conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
        except Exception:
            missing.append(table)

    if missing:
        return {
            "ok": False,
            "status": "ERROR",
            "error": "wrong database",
            "path": path,
            "source": source,
            "missing_tables": missing,
            "rows": None,
        }

    rows = 0
    try:
        rows = int(
            conn.execute(
                "SELECT COUNT(*) FROM market_events_research_lake_v1"
            ).fetchone()[0]
        )
    except Exception:
        try:
            rows = int(
                conn.execute(
                    "SELECT COUNT(*) FROM market_events_paper_trades_s42 "
                    "WHERE status='CLOSED' AND pnl_pct IS NOT NULL"
                ).fetchone()[0]
            )
        except Exception:
            rows = 0

    return {
        "ok": True,
        "status": "OK",
        "error": None,
        "path": path,
        "source": source,
        "missing_tables": [],
        "rows": rows,
    }


def format_db_self_check(check: dict[str, Any]) -> str:
    lines = ["DB", str(check.get("status") or "ERROR")]
    if not check.get("ok"):
        lines.append(str(check.get("error") or "wrong database"))
    lines.extend(["path", str(check.get("path") or "—")])
    if check.get("ok"):
        lines.extend(["rows", str(check.get("rows") if check.get("rows") is not None else "—")])
    else:
        missing = check.get("missing_tables") or []
        if missing:
            lines.extend(["missing", ", ".join(str(t) for t in missing)])
    return "\n".join(lines)


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "bot" / "research" / "market_events" / "__main__.py").exists():
            return p
    return Path.cwd()


def morning_report_dir(root: Path | None = None) -> Path:
    return (root or _repo_root()) / "research" / "reports" / "morning"


def _fmt(v: Any, *, digits: int = 4) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f) >= 100:
        return f"{f:.2f}"
    return f"{f:.{digits}g}"


def _git_info(root: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"commit": None, "branch": None, "error": None}
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        out["commit"] = commit
        out["branch"] = branch
    except Exception as exc:
        out["error"] = str(exc)[:200]
    return out


def _file_size_mb(path: Path | None) -> float | None:
    if path is None or not path.exists():
        return None
    try:
        return round(path.stat().st_size / (1024 * 1024), 2)
    except Exception:
        return None


def _disk_usage(path: Path) -> dict[str, Any]:
    try:
        u = shutil.disk_usage(path)
        return {
            "total_gb": round(u.total / (1024 ** 3), 2),
            "used_gb": round(u.used / (1024 ** 3), 2),
            "free_gb": round(u.free / (1024 ** 3), 2),
            "used_pct": round(100.0 * u.used / u.total, 1) if u.total else None,
        }
    except Exception as exc:
        return {"error": str(exc)[:160]}


def _filter_since(rows: list[dict[str, Any]], *, now: int, seconds: int) -> list[dict[str, Any]]:
    cutoff = now - int(seconds)
    return [r for r in rows if _trade_ts(r) >= cutoff]


def _strategy_key(r: dict[str, Any]) -> str:
    return str(r.get("s40_signal_type") or r.get("strategy_label") or r.get("exit_reason") or "unknown")


def _strategy_ranking(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[_strategy_key(r)].append(r)
    out = []
    for name, bucket in by.items():
        m = overall_performance(bucket)
        out.append({"strategy": name, **m})
    out.sort(key=lambda x: (
        -99.0 if x.get("pf_inf") else -(x.get("profit_factor") if x.get("profit_factor") is not None else -1.0),
        -(x.get("pnl") or 0),
    ))
    return out


def _load_json_if_fresh(path: Path, *, max_age_sec: int = 36 * 3600) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        age = time.time() - path.stat().st_mtime
        if age > max_age_sec:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _collect_system(root: Path, *, analytics_db_path: Path | str | None = None) -> dict[str, Any]:
    from bot.research.market_events.db_config import resolve_market_events_db_config
    from bot.research.market_events.research_sync_v1 import (
        resolve_research_analytics_sqlite_path,
    )

    git = _git_info(root)
    live = resolve_market_events_db_config()
    live_size = _file_size_mb(live.sqlite_path) if live.backend == "sqlite" else None

    analytics_path: Path | None = None
    analytics_source = None
    if analytics_db_path is not None:
        analytics_path = Path(analytics_db_path)
        analytics_source = "caller"
    else:
        try:
            analytics_path, analytics_source, _ = resolve_research_analytics_sqlite_path()
        except Exception as exc:
            analytics_source = f"err:{exc}"[:80]
    research_size = _file_size_mb(analytics_path) if analytics_path else None

    scanner_uptime = None
    collector_uptime = None
    markets_scanned = None
    try:
        from bot.research.market_events.process_manager import SERVICES, find_service_processes
        for key in ("shock-paper-core", "observe", "telegram"):
            try:
                svc = next(s for s in SERVICES if s.key == key)
                procs = find_service_processes(svc)
                if procs:
                    # approximate uptime from process start if available
                    info = {"running": True, "pid": procs[0].pid, "command": procs[0].command[:100]}
                    if key == "shock-paper-core":
                        scanner_uptime = info
                    if key == "observe":
                        collector_uptime = info
            except Exception:
                continue
    except Exception as exc:
        logger.debug("service probe failed: %s", exc)

    try:
        from bot.research.market_events.db import market_events_readonly_connection
        with market_events_readonly_connection() as conn:
            try:
                row = conn.execute(
                    "SELECT COUNT(DISTINCT symbol) AS n FROM market_snapshots_g3 "
                    "WHERE snapshot_ts >= ?",
                    (int(time.time()) - OVERNIGHT_SECONDS,),
                ).fetchone()
                markets_scanned = int(row["n"] or 0) if row else 0
            except Exception:
                try:
                    row = conn.execute(
                        "SELECT COUNT(*) AS n FROM market_snapshots_g3",
                    ).fetchone()
                    markets_scanned = int(row["n"] or 0) if row else None
                except Exception:
                    markets_scanned = None
    except Exception:
        pass

    return {
        "git_commit": git.get("commit"),
        "git_branch": git.get("branch"),
        "git_error": git.get("error"),
        "live_db_backend": live.backend,
        "live_db_size_mb": live_size,
        "research_db_backend": "sqlite" if analytics_path else None,
        "research_db_size_mb": research_size,
        "research_db_path": str(analytics_path) if analytics_path else None,
        "research_db_source": analytics_source,
        "research_separated": (
            str(analytics_path.resolve()) != str(live.sqlite_path.resolve())
            if analytics_path and live.sqlite_path
            else None
        ),
        "scanner": scanner_uptime or {"running": False},
        "collector": collector_uptime or {"running": False},
        "markets_scanned_overnight": markets_scanned,
    }


def _collect_trading(rows: list[dict[str, Any]], *, as_of: int) -> dict[str, Any]:
    overnight = _filter_since(rows, now=as_of, seconds=OVERNIGHT_SECONDS)
    h24 = filter_by_period(rows, "24h", now=as_of)
    window = overnight if overnight else h24
    window_label = "overnight_12h" if overnight else "24h_fallback"
    perf = overall_performance(window)
    strategies = _strategy_ranking(window)
    best = strategies[0] if strategies else None
    worst = strategies[-1] if strategies else None
    return {
        "window": window_label,
        "as_of": as_of,
        "trades_overnight": len(overnight),
        "trades_window": len(window),
        "performance": perf,
        "best_strategy": best,
        "worst_strategy": worst,
        "strategies": strategies[:15],
    }


def _collect_market(rows: list[dict[str, Any]], *, as_of: int) -> dict[str, Any]:
    overnight = _filter_since(rows, now=as_of, seconds=OVERNIGHT_SECONDS)
    h24 = filter_by_period(rows, "24h", now=as_of)
    window = overnight if overnight else h24
    coins = coin_ranking(window)
    hours = hour_analysis(window)
    regimes = regime_analysis(window)
    most_active = max(coins, key=lambda c: int(c.get("trades") or 0), default=None) if coins else None
    most_profitable = max(coins, key=lambda c: float(c.get("pnl") or 0), default=None) if coins else None
    worst_coin = min(coins, key=lambda c: float(c.get("pnl") or 0), default=None) if coins else None
    return {
        "coins_traded": [c.get("coin") for c in coins if int(c.get("trades") or 0) > 0],
        "coin_ranking": coins,
        "most_active_coin": most_active,
        "most_profitable_coin": most_profitable,
        "worst_coin": worst_coin,
        "hours_highest_pf": hours.get("top3") or [],
        "hours_lowest_pf": hours.get("worst3") or [],
        "regime_distribution": regimes,
    }


def _collect_patterns(root: Path, conn: Any) -> dict[str, Any]:
    intel = default_report_dir(root)
    cached = _load_json_if_fresh(intel / "patterns.json")
    drift_cached = _load_json_if_fresh(intel / "drift_report.json")
    source = "cache"
    patterns_report = cached
    if patterns_report is None:
        source = "computed"
        try:
            from bot.research.market_events.signal_intelligence.pattern_discovery_s623 import (
                run_pattern_discovery,
            )
            patterns_report = run_pattern_discovery(
                conn,
                periods=["lifetime", "24h"],
                last_trades=[100],
                report_dir=intel,
            )
        except Exception as exc:
            logger.warning("pattern discovery for morning-report failed: %s", exc)
            patterns_report = {"ok": False, "error": str(exc)[:200]}

    life = (patterns_report.get("universes") or {}).get("lifetime") or {}
    improvements = (life.get("top_best") or [])[:10]
    deteriorations = (
        (patterns_report.get("biggest_deterioration") or [])[:10]
        or (life.get("top_worst") or [])[:10]
    )
    if drift_cached:
        improvements = (drift_cached.get("top_improvement") or improvements)[:10]
        deteriorations = (drift_cached.get("top_deterioration") or deteriorations)[:10]

    enables = (patterns_report.get("candidate_enables") or [])[:10]
    disables = (patterns_report.get("candidate_disables") or [])[:10]
    unique = patterns_report.get("patterns_unique") or patterns_report.get("patterns") or []
    # "New" = unique patterns with stability high and listed in top best that aren't disables
    new_discovered = [
        {"label": p.get("label"), "stability": p.get("stability"), "metrics": p.get("metrics")}
        for p in (life.get("top_best") or [])[:10]
    ]

    return {
        "source": source,
        "top_improvements": improvements,
        "top_deteriorations": deteriorations,
        "new_discovered_patterns": new_discovered,
        "patterns_enabled_candidates": enables,
        "patterns_disabled_candidates": disables,
        "unique_patterns": len(unique) if isinstance(unique, list) else None,
        "consolidation": patterns_report.get("consolidation"),
        "data_quality": patterns_report.get("data_quality"),
    }


def _collect_ai_coverage(
    rows: list[dict[str, Any]],
    tagged: list[dict[str, Any]],
    patterns: dict[str, Any],
    drift: dict[str, Any] | None,
) -> dict[str, Any]:
    dq = patterns.get("data_quality")
    if not dq:
        dq = data_quality_stats(
            rows=rows,
            tagged=tagged,
            consolidation=patterns.get("consolidation") or {
                "duplicates_removed": 0,
                "unique_retained": 0,
                "raw_count": 0,
            },
        )
    drift_summary = None
    confidence = None
    if drift:
        chain = (drift.get("performance_drift") or drift.get("what_changed") or {})
        if isinstance(chain, dict) and "chain" in (drift.get("performance_drift") or {}):
            drift_summary = (drift.get("performance_drift") or {}).get("flags") or []
        elif isinstance(drift.get("what_changed"), list):
            drift_summary = drift.get("what_changed")
        # confidence proxy: share of high-stability unique patterns
        conf_scores = [
            float(c.get("confidence") or 0)
            for c in (patterns.get("patterns_enabled_candidates") or [])
        ]
        if conf_scores:
            confidence = round(sum(conf_scores) / len(conf_scores), 1)

    feature_coverage = {
        "unknown_regime_pct": dq.get("unknown_regime_pct"),
        "missing_ai_score_pct": dq.get("missing_ai_score_pct"),
        "missing_funding_pct": dq.get("missing_funding_pct"),
        "missing_rsi_pct": dq.get("missing_rsi_pct"),
        "rows_analysed": dq.get("rows_analysed"),
        "rows_skipped": dq.get("rows_skipped"),
    }
    return {
        "current_drift": drift_summary,
        "current_confidence": confidence,
        "feature_coverage": feature_coverage,
        "unknown_regime_pct": dq.get("unknown_regime_pct"),
        "missing_ai_score_pct": dq.get("missing_ai_score_pct"),
        "missing_funding_pct": dq.get("missing_funding_pct"),
        "missing_rsi_pct": dq.get("missing_rsi_pct"),
    }


def _collect_telegram(*, overnight_start: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": True,
        "collector_status": "unknown",
        "channels_connected": None,
        "messages_overnight": None,
        "signals_extracted": None,
        "ai_summaries_created": None,
        "last_successful_message_ts": None,
        "last_successful_summary_ts": None,
        "error": None,
        "traceback": None,
        "probable_reason": None,
    }
    try:
        from bot.research.futures_agent.telegram_intake_f52 import (
            build_telegram_status,
            is_poll_running,
        )
        st = build_telegram_status()
        result["collector_status"] = "running" if st.poll_running else "stopped"
        result["channels_connected"] = list(st.allowed_chats)
        result["poll_uptime"] = st.poll_uptime
        result["today_stats"] = dict(st.today_stats)
        result["bot_token_configured"] = bool(st.telegram_token and st.telegram_token != "MISSING")
        last = st.last_processed_message or {}
        result["last_successful_message_ts"] = last.get("received_at") or last.get("date")
        result["messages_overnight"] = int(st.today_stats.get("received") or 0)
        result["signals_extracted"] = int(st.today_stats.get("processed") or 0)
        if not st.poll_running:
            result["ok"] = False
            result["probable_reason"] = "Telegram poll process not running"
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)[:300]
        result["traceback"] = traceback.format_exc()[-2000:]
        result["probable_reason"] = "Failed to build telegram status (import/DB/config)"
        result["collector_status"] = "ERROR"

    # Market-events inbound overnight + AI summaries
    try:
        from bot.research.market_events.db import market_events_readonly_connection
        with market_events_readonly_connection() as conn:
            try:
                n = conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM market_events_inbound_trace_g04
                    WHERE created_at >= ?
                    """,
                    (overnight_start,),
                ).fetchone()
                result["me_inbound_overnight"] = int(n["n"] or 0)
            except Exception:
                result["me_inbound_overnight"] = None
            try:
                row = conn.execute(
                    "SELECT MAX(created_at) AS t FROM market_events_inbound_trace_g04 "
                    "WHERE error IS NULL OR error = ''",
                ).fetchone()
                if row and row["t"]:
                    result["last_successful_message_ts"] = result.get("last_successful_message_ts") or row["t"]
            except Exception:
                pass
            try:
                n = conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM market_events_signal_reports_f2
                    WHERE created_at >= ? AND ai_summary_v2_ru IS NOT NULL AND ai_summary_v2_ru != ''
                    """,
                    (overnight_start,),
                ).fetchone()
                result["ai_summaries_created"] = int(n["n"] or 0)
                row = conn.execute(
                    """
                    SELECT MAX(created_at) AS t FROM market_events_signal_reports_f2
                    WHERE ai_summary_v2_ru IS NOT NULL AND ai_summary_v2_ru != ''
                    """,
                ).fetchone()
                if row and row["t"]:
                    result["last_successful_summary_ts"] = row["t"]
            except Exception:
                # table/column may not exist
                result["ai_summaries_created"] = result.get("ai_summaries_created")
    except Exception as exc:
        if result.get("ok"):
            result["me_probe_error"] = str(exc)[:160]

    # Process-level status text snippet
    try:
        from bot.research.market_events.process_manager import telegram_status_report
        result["process_status_text"] = telegram_status_report()
        if "Running: no" in (result["process_status_text"] or "") and result.get("collector_status") != "ERROR":
            result["ok"] = False
            result["collector_status"] = result.get("collector_status") or "stopped"
            result["probable_reason"] = result.get("probable_reason") or "Telegram service not running"
    except Exception:
        pass

    return result


def _collect_health(root: Path, system: dict[str, Any]) -> dict[str, Any]:
    disk = _disk_usage(root)
    doctor = None
    try:
        from bot.research.market_events.doctor import collect_doctor
        doctor = collect_doctor(skip_network=True)
        # serialize Check objects
        def _chk(c: Any) -> dict[str, Any]:
            return {
                "name": getattr(c, "name", str(c)),
                "ok": bool(getattr(c, "ok", False)),
                "detail": getattr(c, "detail", None),
            }
        doctor = {
            "ok": doctor.get("ok"),
            "sha": doctor.get("sha"),
            "db": [_chk(c) for c in doctor.get("db") or []],
            "telegram": _chk(doctor["telegram"]) if doctor.get("telegram") else None,
            "paper": _chk(doctor["paper"]) if doctor.get("paper") else None,
            "open_trades": doctor.get("open_trades"),
            "signals_today": doctor.get("signals_today"),
            "soft_failures": doctor.get("soft_failures"),
            "s56": _chk(doctor["s56"]) if doctor.get("s56") else None,
            "s57": _chk(doctor["s57"]) if doctor.get("s57") else None,
        }
    except Exception as exc:
        doctor = {"ok": False, "error": str(exc)[:200]}

    exceptions: list[str] = []
    warnings: list[str] = []
    if doctor and not doctor.get("ok"):
        exceptions.append("doctor overall not OK")
    for name in doctor.get("soft_failures") or [] if isinstance(doctor, dict) else []:
        warnings.append(f"soft failure: {name}")

    # DB growth proxy: research + live sizes
    growth = {
        "live_db_size_mb": system.get("live_db_size_mb"),
        "research_db_size_mb": system.get("research_db_size_mb"),
    }

    # Slow queries: not instrumented globally — report unavailable rather than invent
    slow_queries = {"available": False, "note": "No global slow-query log wired"}

    # Log tail for recent ERROR/WARNING lines
    try:
        from bot.ops.process_utils import logs_dir
        log_dir = logs_dir()
        for name in ("me-telegram.log", "me-shock-paper-core.log", "me-observe.log"):
            p = log_dir / name
            if not p.exists():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")[-8000:]
                for line in text.splitlines()[-40:]:
                    low = line.lower()
                    if "traceback" in low or " error" in low or low.startswith("error"):
                        exceptions.append(f"{name}: {line[:160]}")
                    elif "warning" in low:
                        warnings.append(f"{name}: {line[:160]}")
            except Exception:
                continue
    except Exception:
        pass

    return {
        "database_growth": growth,
        "disk_usage": disk,
        "slow_queries": slow_queries,
        "exceptions": exceptions[:20],
        "warnings": warnings[:20],
        "doctor": doctor,
    }


def _action_items(
    *,
    telegram: dict[str, Any],
    ai: dict[str, Any],
    market: dict[str, Any],
    trading: dict[str, Any],
    health: dict[str, Any],
    system: dict[str, Any],
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []

    def add(priority: str, title: str, detail: str) -> None:
        items.append({"priority": priority, "title": title, "detail": detail})

    if not telegram.get("ok") or telegram.get("collector_status") in ("ERROR", "stopped"):
        add(
            "P0",
            "Fix Telegram collector",
            telegram.get("probable_reason")
            or telegram.get("error")
            or f"status={telegram.get('collector_status')}",
        )

    ur = _safe_float(ai.get("unknown_regime_pct"))
    if ur is not None and ur >= 50:
        add("P1", "Too many Unknown regimes", f"unknown_regime_pct={ur}%")

    mf = _safe_float(ai.get("missing_funding_pct"))
    if mf is not None and mf >= 50:
        add("P1", "Funding missing", f"missing_funding_pct={mf}%")

    ma = _safe_float(ai.get("missing_ai_score_pct"))
    if ma is not None and ma >= 50:
        add("P1", "AI coverage low", f"missing_ai_score_pct={ma}%")

    coins = market.get("coins_traded") or []
    if coins and "ETH" not in coins and len(coins) <= 2:
        add("P2", "Need more ETH trades", f"coins_traded={coins}")

    perf = trading.get("performance") or {}
    if int(perf.get("trades") or 0) == 0:
        add("P1", "No overnight trades", "trading window empty — check scanner/collector")

    if not (system.get("scanner") or {}).get("running"):
        add("P0", "Scanner not running", "shock-paper-core process not detected")

    disk = health.get("disk_usage") or {}
    if _safe_float(disk.get("used_pct")) is not None and float(disk["used_pct"]) >= 90:
        add("P0", "Disk almost full", f"used_pct={disk.get('used_pct')}%")

    if health.get("exceptions"):
        add("P1", "Review overnight exceptions", f"{len(health['exceptions'])} exception line(s) in logs/doctor")

    # Deduplicate by title, keep first 5
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for it in items:
        if it["title"] in seen:
            continue
        seen.add(it["title"])
        out.append(it)
        if len(out) >= 5:
            break
    if not out:
        out.append({
            "priority": "P3",
            "title": "No critical actions",
            "detail": "Morning checks did not flag P0/P1 issues",
        })
    return out


def run_morning_report(
    conn: Any,
    *,
    now: int | None = None,
    report_dir: Path | None = None,
    root: Path | None = None,
    db_path: Path | str | None = None,
    db_source: str | None = None,
) -> dict[str, Any]:
    t0 = time.time()
    root = root or _repo_root()
    wall_now = int(now if now is not None else time.time())
    overnight_start = wall_now - OVERNIGHT_SECONDS

    db_check = morning_db_self_check(conn, db_path=db_path, source=db_source)
    if not db_check.get("ok"):
        return {
            "ok": False,
            "stage": "S63",
            "generated_at": wall_now,
            "generated_at_iso": datetime.fromtimestamp(wall_now, tz=timezone.utc).isoformat(),
            "db": db_check,
            "error": "wrong database",
            "elapsed_sec": round(time.time() - t0, 3),
        }

    rows = load_lab_trades(conn)
    _enrich_decisions(conn, rows)
    _enrich_from_snapshot_json(rows)
    tagged = [t for t in (tag_trade(r) for r in rows) if t is not None]
    as_of_info = resolve_as_of(rows, wall_now=wall_now)
    # Prefer wall clock for "overnight"; fall back to research as_of for empty windows
    as_of = wall_now
    overnight_wall = _filter_since(rows, now=wall_now, seconds=OVERNIGHT_SECONDS)
    if not overnight_wall and as_of_info.get("as_of_mode") == "latest_trade":
        as_of = int(as_of_info["as_of"])

    system = _collect_system(root, analytics_db_path=db_path or db_check.get("path"))
    system["trades_collected_overnight"] = len(
        _filter_since(rows, now=as_of, seconds=OVERNIGHT_SECONDS),
    )

    trading = _collect_trading(rows, as_of=as_of)
    market = _collect_market(rows, as_of=as_of)

    drift_path = default_report_dir(root) / "drift_report.json"
    drift_cached = _load_json_if_fresh(drift_path)
    patterns = _collect_patterns(root, conn)
    ai = _collect_ai_coverage(rows, tagged, patterns, drift_cached)
    telegram = _collect_telegram(overnight_start=overnight_start)
    health = _collect_health(root, system)
    actions = _action_items(
        telegram=telegram,
        ai=ai,
        market=market,
        trading=trading,
        health=health,
        system=system,
    )

    lake_info: dict[str, Any] = {"n_lake": 0, "lag": None, "status": "n/a"}
    s55_info: dict[str, Any] = {"missing": None, "missing_pct": None}
    brain_info: dict[str, Any] = {"status": "n/a", "notes": None}
    best_rules: list[dict[str, Any]] = []
    worst_rules: list[dict[str, Any]] = []
    try:
        from bot.research.market_events.signal_intelligence.research_lake_v1 import (
            diagnose_missing_s55_joins,
            lake_lag,
            research_lake_health_v1,
        )

        lag = lake_lag(conn)
        lh = research_lake_health_v1(conn)
        diag = diagnose_missing_s55_joins(conn)
        lake_info = {
            "n_lake": lag.get("n_lake"),
            "lag": lag.get("lag"),
            "status": lh.get("status"),
            "n_s42_closed": lag.get("n_s42_closed"),
        }
        s55_info = {
            "missing": diag.get("n_missing"),
            "missing_pct": diag.get("missing_pct"),
            "expected": diag.get("missing_expected"),
            "unexpected": diag.get("missing_unexpected"),
            "status": "OK" if int(diag.get("missing_unexpected") or 0) == 0 else "FAIL",
            "verdict": diag.get("verdict"),
            "reasons": diag.get("reasons"),
        }
    except Exception as exc:
        lake_info["status"] = f"err:{exc}"[:80]

    try:
        from bot.research.market_events.signal_intelligence.trading_rules_v1 import (
            run_trading_rules_v1,
        )

        rules = run_trading_rules_v1(conn, write_reports=False)
        ready = rules.get("ready_for_paper") or []
        hard = rules.get("hard_block") or []
        best_rules = [
            {
                "rule": " + ".join(r.get("conditions") or []),
                "pf": r.get("pf"),
                "ev": r.get("ev"),
                "n": r.get("n"),
                "status": r.get("stability_status"),
            }
            for r in ready[:5]
        ]
        worst_rules = [
            {
                "rule": " + ".join(r.get("conditions") or []),
                "pf": r.get("pf"),
                "ev": r.get("ev"),
                "n": r.get("n"),
            }
            for r in hard[:5]
        ]
    except Exception as exc:
        logger.debug("morning-report rules failed: %s", exc)

    fingerprint: dict[str, Any] = {}
    try:
        from bot.research.market_events.signal_intelligence.market_fingerprint_v1 import (
            run_market_fingerprint_v1,
        )

        fp = run_market_fingerprint_v1(conn, write_reports=False, limit=5000)
        sim = fp.get("similarity") or {}
        cur = fp.get("current_market") or {}
        fingerprint = {
            "symbol": cur.get("symbol"),
            "direction": cur.get("direction"),
            "regime": cur.get("regime"),
            "similarity_pct": sim.get("similarity_pct"),
            "closest_fingerprint": sim.get("closest_fingerprint") or cur.get("fingerprint"),
            "historical_wr": sim.get("historical_wr"),
            "historical_pf": sim.get("historical_pf"),
            "historical_ev": sim.get("historical_ev"),
            "recommendation": sim.get("recommendation") or "RESEARCH ONLY",
            "n_snapshots": fp.get("n_snapshots"),
            "elapsed_sec": fp.get("elapsed_sec"),
        }
    except Exception as exc:
        logger.debug("morning-report fingerprint failed: %s", exc)
        fingerprint = {"recommendation": "RESEARCH ONLY", "error": str(exc)[:120]}

    try:
        # Lightweight brain probe — no mutation
        brain_info = {
            "status": "observe",
            "notes": f"actions={len(actions)} overnight={system.get('trades_collected_overnight')}",
        }
    except Exception:
        pass

    alerts = list(actions)
    if lake_info.get("lag"):
        alerts.insert(0, {"priority": "HIGH", "title": f"lake lag={lake_info.get('lag')}"})
    # Only alert on unexpected S55 gaps — expected materialization misses are normal.
    if int(s55_info.get("unexpected") or 0) > 0:
        alerts.insert(
            0,
            {
                "priority": "HIGH",
                "title": (
                    f"missing_s55_unexpected={s55_info.get('unexpected')} "
                    f"(expected={s55_info.get('expected')})"
                ),
            },
        )

    report = {
        "ok": True,
        "stage": "S63",
        "generated_at": wall_now,
        "generated_at_iso": datetime.fromtimestamp(wall_now, tz=timezone.utc).isoformat(),
        "as_of": as_of,
        "as_of_mode": as_of_info.get("as_of_mode"),
        "overnight_seconds": OVERNIGHT_SECONDS,
        "n_trades_loaded": len(rows),
        "db": db_check,
        "system": system,
        "trading": trading,
        "market": market,
        "patterns": patterns,
        "ai": ai,
        "telegram": telegram,
        "system_health": health,
        "top_action_items": actions,
        "lake": lake_info,
        "s55": s55_info,
        "fingerprint": fingerprint,
        "brain": brain_info,
        "best_rules": best_rules,
        "worst_rules": worst_rules,
        "alerts": alerts,
        "elapsed_sec": round(time.time() - t0, 3),
    }

    out_dir = Path(report_dir) if report_dir else morning_report_dir(root)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "latest.md"
    json_path = out_dir / "latest.json"
    md_path.write_text(format_morning_markdown(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["export_paths"] = {
        "markdown": str(md_path.resolve()),
        "json": str(json_path.resolve()),
    }
    return report


def format_morning_markdown(report: dict[str, Any]) -> str:
    s = report.get("system") or {}
    t = report.get("trading") or {}
    m = report.get("market") or {}
    p = report.get("patterns") or {}
    a = report.get("ai") or {}
    tg = report.get("telegram") or {}
    h = report.get("system_health") or {}
    perf = t.get("performance") or {}

    lines = [
        "# Morning Trading Report (S63)",
        "",
        f"_generated={report.get('generated_at_iso')} elapsed={report.get('elapsed_sec')}s "
        f"as_of_mode={report.get('as_of_mode')}_",
        "",
        "## SYSTEM",
        "",
        f"- Git commit: `{s.get('git_commit') or '—'}`",
        f"- Branch: `{s.get('git_branch') or '—'}`",
        f"- Live DB size: {_fmt(s.get('live_db_size_mb'))} MB ({s.get('live_db_backend')})",
        f"- Research DB size: {_fmt(s.get('research_db_size_mb'))} MB ({s.get('research_db_backend')})",
        f"- Trades collected overnight: **{s.get('trades_collected_overnight')}**",
        f"- Markets scanned overnight: **{s.get('markets_scanned_overnight')}**",
        f"- Scanner: {'UP' if (s.get('scanner') or {}).get('running') else 'DOWN'}",
        f"- Collector: {'UP' if (s.get('collector') or {}).get('running') else 'DOWN'}",
        "",
        "## TRADING",
        "",
        f"_window={t.get('window')}_",
        "",
        f"- Trades: **{perf.get('trades')}**",
        f"- PnL: **{_fmt(perf.get('pnl'))}**",
        f"- Profit Factor: **{_fmt(perf.get('profit_factor'))}**",
        f"- Win Rate: **{_fmt(perf.get('winrate'))}%**",
        f"- Expectancy: **{_fmt(perf.get('expectancy'))}**",
        f"- Sharpe: **{_fmt(perf.get('sharpe'))}**",
        f"- Max DD: **{_fmt(perf.get('max_drawdown'))}**",
        f"- Best strategy: **{(t.get('best_strategy') or {}).get('strategy', '—')}** "
        f"(PF={_fmt((t.get('best_strategy') or {}).get('profit_factor'))}, "
        f"PnL={_fmt((t.get('best_strategy') or {}).get('pnl'))})",
        f"- Worst strategy: **{(t.get('worst_strategy') or {}).get('strategy', '—')}** "
        f"(PF={_fmt((t.get('worst_strategy') or {}).get('profit_factor'))}, "
        f"PnL={_fmt((t.get('worst_strategy') or {}).get('pnl'))})",
        "",
        "## MARKET",
        "",
        f"- Coins traded: {', '.join(m.get('coins_traded') or []) or '—'}",
        f"- Most active: **{(m.get('most_active_coin') or {}).get('coin', '—')}** "
        f"(n={(m.get('most_active_coin') or {}).get('trades')})",
        f"- Most profitable: **{(m.get('most_profitable_coin') or {}).get('coin', '—')}** "
        f"(PnL={_fmt((m.get('most_profitable_coin') or {}).get('pnl'))})",
        f"- Worst coin: **{(m.get('worst_coin') or {}).get('coin', '—')}** "
        f"(PnL={_fmt((m.get('worst_coin') or {}).get('pnl'))})",
        "- Hours highest PF: "
        + (", ".join(
            f"H{h.get('hour'):02d}(PF={_fmt(h.get('pf'))})"
            for h in (m.get("hours_highest_pf") or [])
        ) or "—"),
        "- Hours lowest PF: "
        + (", ".join(
            f"H{h.get('hour'):02d}(PF={_fmt(h.get('pf'))})"
            for h in (m.get("hours_lowest_pf") or [])
        ) or "—"),
        "- Regime distribution:",
    ]
    for reg in m.get("regime_distribution") or []:
        lines.append(
            f"  - {reg.get('regime')}: n={reg.get('trades')} "
            f"PF={_fmt(reg.get('pf'))} PnL={_fmt(reg.get('pnl'))}"
        )

    lines.extend(["", "## PATTERNS", "", f"_source={p.get('source')}_", "", "### Top 10 improvements", ""])
    for i, c in enumerate(p.get("top_improvements") or [], 1):
        label = c.get("label") or c.get("reason") or (
            f"{c.get('dimension')}={c.get('category')}" if c.get("dimension") else None
        ) or "—"
        met = c.get("metrics") or {}
        vs = c.get("vs_lifetime_24h") or {}
        pf = met.get("profit_factor") if met else None
        exp = met.get("expectancy") if met else None
        if pf is None and vs:
            pf = vs.get("delta_profit_factor")
            exp = vs.get("delta_expectancy")
            lines.append(
                f"{i}. {label} — ΔPF={_fmt(pf)} ΔE={_fmt(exp)}"
            )
        else:
            lines.append(
                f"{i}. {label} — PF={_fmt(pf or c.get('pf'))} "
                f"E={_fmt(exp if exp is not None else c.get('expectancy'))}"
            )
    if not (p.get("top_improvements") or []):
        lines.append("_None._")

    lines.extend(["", "### Top 10 deteriorations", ""])
    for i, c in enumerate(p.get("top_deteriorations") or [], 1):
        label = c.get("label") or c.get("reason") or (
            f"{c.get('dimension')}={c.get('category')}" if c.get("dimension") else None
        ) or "—"
        lines.append(f"{i}. {label}")
    if not (p.get("top_deteriorations") or []):
        lines.append("_None._")

    lines.extend(["", "### New discovered patterns", ""])
    for i, c in enumerate(p.get("new_discovered_patterns") or [], 1):
        lines.append(f"{i}. {c.get('label')} (stability={_fmt(c.get('stability'))})")
    if not (p.get("new_discovered_patterns") or []):
        lines.append("_None._")

    lines.extend(["", "### Patterns disabled (candidates)", ""])
    for i, c in enumerate(p.get("patterns_disabled_candidates") or [], 1):
        lines.append(
            f"{i}. Disable **{c.get('label')}** — confidence={c.get('confidence_pct')}"
        )
    if not (p.get("patterns_disabled_candidates") or []):
        lines.append("_None._")

    lines.extend(["", "### Patterns enabled (candidates)", ""])
    for i, c in enumerate(p.get("patterns_enabled_candidates") or [], 1):
        lines.append(
            f"{i}. Enable **{c.get('label')}** — confidence={c.get('confidence_pct')}"
        )
    if not (p.get("patterns_enabled_candidates") or []):
        lines.append("_None._")

    lines.extend([
        "",
        "## AI",
        "",
        f"- Current drift: {a.get('current_drift') or '—'}",
        f"- Current confidence: {_fmt(a.get('current_confidence'))}",
        f"- Unknown regime %: **{_fmt(a.get('unknown_regime_pct'))}**",
        f"- Missing AI score %: **{_fmt(a.get('missing_ai_score_pct'))}**",
        f"- Missing funding %: **{_fmt(a.get('missing_funding_pct'))}**",
        f"- Missing RSI %: **{_fmt(a.get('missing_rsi_pct'))}**",
        "",
        "## TELEGRAM",
        "",
        f"- Collector status: **{tg.get('collector_status')}**",
        f"- Channels connected: {tg.get('channels_connected') or '—'}",
        f"- Messages collected overnight/today: **{tg.get('messages_overnight')}**",
        f"- Signals extracted: **{tg.get('signals_extracted')}**",
        f"- AI summaries created: **{tg.get('ai_summaries_created')}**",
        f"- Last successful message: {tg.get('last_successful_message_ts') or '—'}",
        f"- Last successful summary: {tg.get('last_successful_summary_ts') or '—'}",
    ])
    if not tg.get("ok"):
        lines.extend([
            "",
            "### ERROR",
            "",
            f"- Error: {tg.get('error') or '—'}",
            f"- Probable reason: **{tg.get('probable_reason') or '—'}**",
            "",
            "```",
            (tg.get("traceback") or "—")[-1500:],
            "```",
        ])

    disk = h.get("disk_usage") or {}
    growth = h.get("database_growth") or {}
    lines.extend([
        "",
        "## SYSTEM HEALTH",
        "",
        f"- Database growth: live={_fmt(growth.get('live_db_size_mb'))} MB, "
        f"research={_fmt(growth.get('research_db_size_mb'))} MB",
        f"- Disk usage: {_fmt(disk.get('used_pct'))}% "
        f"(free={_fmt(disk.get('free_gb'))} GB / total={_fmt(disk.get('total_gb'))} GB)",
        f"- Slow queries: {(h.get('slow_queries') or {}).get('note', '—')}",
        "- Exceptions:",
    ])
    for e in h.get("exceptions") or []:
        lines.append(f"  - {e}")
    if not (h.get("exceptions") or []):
        lines.append("  - _none_")
    lines.append("- Warnings:")
    for w in h.get("warnings") or []:
        lines.append(f"  - {w}")
    if not (h.get("warnings") or []):
        lines.append("  - _none_")

    lines.extend(["", "## TOP 5 ACTION ITEMS", ""])
    for i, it in enumerate(report.get("top_action_items") or [], 1):
        lines.append(f"{i}. **[{it.get('priority')}] {it.get('title')}** — {it.get('detail')}")

    lines.extend(["", "---", "_Read only. No strategy changes._", ""])
    return "\n".join(lines)


def format_morning_summary(report: dict[str, Any]) -> str:
    """One-screen plain text (no Markdown / no JSON)."""
    db = report.get("db") or {}
    lines = [
        "MORNING REPORT",
        "",
        format_db_self_check(db),
        "",
    ]
    if not report.get("ok") or not db.get("ok"):
        return "\n".join(lines).rstrip() + "\n"

    t = (report.get("trading") or {}).get("performance") or {}
    lake = report.get("lake") or {}
    s55 = report.get("s55") or {}
    fp = report.get("fingerprint") or {}
    brain = report.get("brain") or {}
    best = (report.get("trading") or {}).get("best_strategy") or {}
    worst = (report.get("trading") or {}).get("worst_strategy") or {}
    alerts = report.get("alerts") or report.get("top_action_items") or []

    lines.extend([
        "Trades",
        f"  n={t.get('trades')} window={(report.get('trading') or {}).get('window')}",
        "",
        "PF",
        f"  {t.get('profit_factor')}",
        "",
        "EV",
        f"  {t.get('expectancy')}",
        "",
        "Best Rules",
    ])
    for i, r in enumerate((report.get("best_rules") or [])[:5], 1):
        cond = r.get("rule") or r.get("strategy") or "—"
        lines.append(f"  {i}. {cond} PF={r.get('pf') or r.get('profit_factor')} EV={r.get('ev')}")
    if not (report.get("best_rules") or []):
        name = best.get("strategy") or "—"
        lines.append(f"  1. {name} PF={best.get('profit_factor')} PnL={best.get('pnl')}")

    lines.extend(["", "Worst Rules"])
    for i, r in enumerate((report.get("worst_rules") or [])[:5], 1):
        cond = r.get("rule") or r.get("strategy") or "—"
        lines.append(f"  {i}. {cond} PF={r.get('pf') or r.get('profit_factor')} EV={r.get('ev')}")
    if not (report.get("worst_rules") or []):
        name = worst.get("strategy") or "—"
        lines.append(f"  1. {name} PF={worst.get('profit_factor')} PnL={worst.get('pnl')}")

    lines.extend([
        "",
        "Lake",
        f"  n={lake.get('n_lake')} lag={lake.get('lag')} status={lake.get('status')}",
        "",
        "S55 joins",
        "Expected",
        f"  {s55.get('expected')}",
        "Unexpected",
        f"  {s55.get('unexpected')}",
        "Status",
        f"  {s55.get('status') or 'n/a'}",
        "",
        "Current Market",
        f"  {fp.get('symbol') or '—'} {fp.get('direction') or ''} regime={fp.get('regime') or '—'}",
        "",
        "Similarity",
        f"  {fp.get('similarity_pct')}%",
        "",
        "Closest Fingerprint",
        f"  {fp.get('closest_fingerprint') or 'n/a'}",
        "",
        "Historical WR",
        f"  {fp.get('historical_wr')}%",
        "",
        "Historical PF",
        f"  {fp.get('historical_pf')}",
        "",
        "Historical EV",
        f"  {fp.get('historical_ev')}",
        "",
        "Recommendation",
        f"  {fp.get('recommendation') or 'RESEARCH ONLY'}",
        "",
        "Brain",
        f"  {brain.get('status') or 'n/a'} notes={brain.get('notes') or '—'}",
        "",
        "Alerts",
    ])
    if not alerts:
        lines.append("  (none)")
    else:
        for a in alerts[:6]:
            if isinstance(a, dict):
                lines.append(f"  - [{a.get('priority') or 'INFO'}] {a.get('title') or a}")
            else:
                lines.append(f"  - {a}")
    return "\n".join(lines)


__all__ = [
    "format_db_self_check",
    "format_morning_markdown",
    "format_morning_summary",
    "morning_db_self_check",
    "morning_report_dir",
    "run_morning_report",
]
