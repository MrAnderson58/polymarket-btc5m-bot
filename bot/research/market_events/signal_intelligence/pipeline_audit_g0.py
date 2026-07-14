"""Phase G.0 — End-to-End Pipeline Audit & Repair (research diagnostics only)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditStageG0:
    name: str
    status: str
    detail: str


def _since(hours: int = 24) -> int:
    return int(time.time()) - hours * 3600


def _count(conn: Any, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] if row and row[0] is not None else 0)


def _latest_snapshot_age_sec(conn: Any) -> int | None:
    row = conn.execute(
        "SELECT MAX(snapshot_ts) AS ts FROM market_snapshots_g3",
    ).fetchone()
    if not row or not row["ts"]:
        return None
    return int(time.time()) - int(row["ts"])


def audit_recorder_g0(conn: Any) -> AuditStageG0:
    total = _count(conn, "SELECT COUNT(*) FROM market_snapshots_g3")
    recent = _count(
        conn,
        "SELECT COUNT(*) FROM market_snapshots_g3 WHERE snapshot_ts >= ?",
        (_since(1),),
    )
    age = _latest_snapshot_age_sec(conn)
    if total == 0:
        return AuditStageG0("Recorder", "FAIL", "no snapshots in DB — run g3-run")
    if age is not None and age > 300:
        return AuditStageG0(
            "Recorder", "FAIL",
            f"stale snapshot ({age}s ago); {total} total, {recent} last hour",
        )
    return AuditStageG0("Recorder", "PASS", f"{total} snapshots ({recent} last hour)")


def audit_snapshot_g0(conn: Any) -> AuditStageG0:
    row = conn.execute(
        """
        SELECT id, btc_price, funding, open_interest, fear_greed, collector_latency_ms
        FROM market_snapshots_g3 ORDER BY snapshot_ts DESC LIMIT 1
        """,
    ).fetchone()
    if not row:
        return AuditStageG0("Snapshot", "FAIL", "no snapshot row")
    missing = []
    if row["btc_price"] is None:
        missing.append("btc_price")
    if row["funding"] is None:
        missing.append("funding")
    if row["open_interest"] is None:
        missing.append("open_interest")
    if missing:
        return AuditStageG0("Snapshot", "FAIL", f"missing fields: {', '.join(missing)}")
    return AuditStageG0(
        "Snapshot", "PASS",
        f"id={row['id']} latency={row['collector_latency_ms']}ms",
    )


def audit_history_g0(conn: Any) -> AuditStageG0:
    total = _count(conn, "SELECT COUNT(*) FROM market_events_historical_candles")
    recent = _count(
        conn,
        """
        SELECT COUNT(*) FROM market_events_historical_candles
        WHERE open_ts >= ?
        """,
        (_since(1),),
    )
    if total == 0:
        return AuditStageG0(
            "History", "FAIL",
            "no candles — run history-backfill or g3-run (G3.8 backfill)",
        )
    return AuditStageG0("History", "PASS", f"{total} candles ({recent} last hour)")


def audit_trend_g0(conn: Any) -> AuditStageG0:
    from bot.research.market_events.signal_intelligence.candidate_g31 import load_g31_universe_symbols

    universe = load_g31_universe_symbols(conn)
    since = _since(1)
    trend_syms = conn.execute(
        """
        SELECT DISTINCT symbol FROM market_trend_windows_g3
        WHERE created_at >= ?
        """,
        (since,),
    ).fetchall()
    have = {str(r["symbol"]).upper() for r in trend_syms}
    missing = [s for s in universe if s.upper() not in have]
    trend_count = _count(
        conn,
        "SELECT COUNT(*) FROM market_trend_windows_g3 WHERE created_at >= ?",
        (since,),
    )
    if trend_count == 0:
        return AuditStageG0(
            "Trend", "FAIL",
            f"No trends in last hour for {len(universe)} symbols — check candle history",
        )
    if missing:
        return AuditStageG0(
            "Trend", "FAIL",
            f"No trend for {len(missing)} symbols ({', '.join(missing[:5])}{'…' if len(missing) > 5 else ''})",
        )
    return AuditStageG0("Trend", "PASS", f"{trend_count} windows across {len(have)} symbols")


def audit_candidate_g0(conn: Any) -> AuditStageG0:
    since = _since(1)
    total = _count(
        conn,
        "SELECT COUNT(*) FROM market_candidate_g31 WHERE created_at >= ?",
        (since,),
    )
    latest_ts = conn.execute(
        "SELECT MAX(candidate_ts) AS ts FROM market_candidate_g31",
    ).fetchone()
    if total == 0:
        return AuditStageG0("Candidate", "FAIL", "no candidates in last hour — run g3-run")
    return AuditStageG0(
        "Candidate", "PASS",
        f"{total} rows (latest cycle ts={latest_ts['ts'] if latest_ts else '—'})",
    )


def audit_experimental_g0(conn: Any) -> AuditStageG0:
    from bot.research.market_events.signal_intelligence.config import G39_EXPERIMENTAL_MODE

    if not G39_EXPERIMENTAL_MODE:
        recent = _count(
            conn,
            "SELECT COUNT(*) FROM market_experimental_signals_g39 WHERE created_at >= ?",
            (_since(24),),
        )
        return AuditStageG0(
            "Experimental", "FAIL",
            f"ME_G3_EXPERIMENTAL_MODE=false ({recent} signals in 24h)",
        )
    since = _since(1)
    n = _count(
        conn,
        "SELECT COUNT(*) FROM market_experimental_signals_g39 WHERE created_at >= ?",
        (since,),
    )
    eligible = _count(
        conn,
        """
        SELECT COUNT(*) FROM market_candidate_g31
        WHERE created_at >= ? AND confidence IS NOT NULL
        """,
        (since,),
    )
    if n == 0 and eligible > 0:
        return AuditStageG0(
            "Experimental", "FAIL",
            f"mode on but 0 signals ({eligible} candidates last hour)",
        )
    return AuditStageG0("Experimental", "PASS", str(n))


def audit_shadow_g0(conn: Any) -> AuditStageG0:
    from bot.research.market_events.signal_intelligence.config import G40_SHADOW_ENABLED

    if not G40_SHADOW_ENABLED:
        return AuditStageG0("Shadow", "FAIL", "ME_SHADOW_ENABLED=false")

    since = _since(24)
    signals = _count(
        conn,
        "SELECT COUNT(*) FROM market_shadow_signals WHERE created_at >= ?",
        (since,),
    )
    traces = _count(
        conn,
        "SELECT COUNT(*) FROM market_shadow_pipeline_trace WHERE created_at >= ?",
        (since,),
    )
    persist_created = _count(
        conn,
        """
        SELECT COUNT(*) FROM market_shadow_pipeline_trace
        WHERE outcome = 'CREATED' AND created_at >= ?
        """,
        (since,),
    )

    if traces == 0:
        return AuditStageG0(
            "Shadow", "FAIL",
            "Persist never called — no pipeline traces (run g3-run with ME_SHADOW_ENABLED=true)",
        )

    if persist_created == 0 and signals == 0:
        last = conn.execute(
            """
            SELECT symbol, outcome, trace_json FROM market_shadow_pipeline_trace
            ORDER BY created_at DESC LIMIT 1
            """,
        ).fetchone()
        reason = "all candidates skipped"
        if last and last["trace_json"]:
            try:
                steps = json.loads(last["trace_json"])
                persist = next((s for s in steps if s.get("step") == "Persist"), None)
                if persist and persist.get("reason"):
                    reason = persist["reason"]
            except (json.JSONDecodeError, TypeError):
                pass
        return AuditStageG0("Shadow", "FAIL", f"Persist never succeeded — {reason}")

    return AuditStageG0("Shadow", "PASS", f"{signals} signals, {persist_created} persist OK (24h)")


def audit_telegram_g0(conn: Any) -> AuditStageG0:
    from bot.research.market_events.alert_config import alerts_enabled

    since = _since(1)
    sent = _count(
        conn,
        "SELECT COUNT(*) FROM market_event_alert_log WHERE sent = 1 AND created_at >= ?",
        (since,),
    )
    pending = _count(
        conn,
        "SELECT COUNT(*) FROM market_event_alert_log WHERE sent = 0",
    )
    delivery_sent = _count(
        conn,
        """
        SELECT COUNT(*) FROM market_event_telegram_delivery_log
        WHERE status = 'sent' AND created_at >= ?
        """,
        (since,),
    )
    if not alerts_enabled():
        return AuditStageG0("Telegram", "FAIL", "ME_TELEGRAM_ALERTS_ENABLED=false")
    if sent == 0 and delivery_sent == 0:
        return AuditStageG0(
            "Telegram", "FAIL",
            f"No messages sent last hour (pending log={pending})",
        )
    return AuditStageG0(
        "Telegram", "PASS",
        f"{sent} alerts sent, {delivery_sent} deliveries (pending={pending})",
    )


def audit_followup_g0(conn: Any) -> AuditStageG0:
    open_live = _count(
        conn,
        "SELECT COUNT(*) FROM market_live_signals_g3 WHERE status IN ('ACTIVE', 'TP1_HIT')",
    )
    open_shadow = _count(
        conn,
        "SELECT COUNT(*) FROM market_shadow_signals WHERE status = 'OPEN'",
    )
    open_exp = _count(
        conn,
        "SELECT COUNT(*) FROM market_experimental_signals_g39 WHERE status IN ('ACTIVE', 'TP1_HIT')",
    )
    horizon_rows = _count(
        conn,
        "SELECT COUNT(*) FROM market_shadow_horizons WHERE checked_at >= ?",
        (_since(24),),
    )
    total_open = open_live + open_shadow + open_exp
    return AuditStageG0(
        "Followup", "PASS",
        f"open live={open_live} shadow={open_shadow} exp={open_exp} "
        f"shadow_horizon_checks_24h={horizon_rows}",
    )


def run_pipeline_audit_g0(conn: Any) -> list[AuditStageG0]:
    return [
        audit_recorder_g0(conn),
        audit_snapshot_g0(conn),
        audit_history_g0(conn),
        audit_trend_g0(conn),
        audit_candidate_g0(conn),
        audit_experimental_g0(conn),
        audit_shadow_g0(conn),
        audit_telegram_g0(conn),
        audit_followup_g0(conn),
    ]


def format_pipeline_audit_g0(conn: Any) -> str:
    stages = run_pipeline_audit_g0(conn)
    lines: list[str] = []
    for s in stages:
        lines.extend([s.name, s.status, s.detail, ""])
    return "\n".join(lines).rstrip()


def format_recorder_debug_g0(conn: Any) -> str:
    from bot.research.market_events.signal_intelligence.health_g3 import get_g3_ops_state

    since_min = int(time.time()) - 3600
    snaps_hour = _count(
        conn,
        "SELECT COUNT(*) FROM market_snapshots_g3 WHERE snapshot_ts >= ?",
        (since_min,),
    )
    candles_hour = _count(
        conn,
        """
        SELECT COUNT(*) FROM market_events_historical_candles
        WHERE open_ts >= ?
        """,
        (since_min,),
    )
    memory_hour = _count(
        conn,
        "SELECT COUNT(*) FROM market_market_memory WHERE ts >= ?",
        (since_min,),
    )

    snap = conn.execute(
        """
        SELECT funding, open_interest, fear_greed, collector_latency_ms, recorder_status
        FROM market_snapshots_g3 ORDER BY snapshot_ts DESC LIMIT 1
        """,
    ).fetchone()

    health_raw = get_g3_ops_state(conn, "recorder_health")
    health: dict[str, Any] = {}
    if health_raw:
        try:
            health = json.loads(health_raw)
        except (json.JSONDecodeError, TypeError):
            health = {}

    try:
        from bot.research.market_events.signal_intelligence.market_data_source_g01 import (
            get_active_provider_display,
            probe_active_provider_quick_g03,
        )
        api_status = probe_active_provider_quick_g03(conn)
    except Exception as exc:
        api_status = f"FAIL: {exc}"

    lines = [
        "Recorder Debug",
        "",
        "Current provider",
        get_active_provider_display(conn),
        "",
        "API",
        api_status,
        "",
        "Snapshots/min",
        f"{snaps_hour / 60:.2f} ({snaps_hour} last hour)" + (" OK" if snaps_hour > 0 else " FAIL"),
        "",
        "Candles/min",
        f"{candles_hour / 60:.2f} ({candles_hour} last hour)" + (" OK" if candles_hour > 0 else " FAIL"),
        "",
        "Market memory/hour",
        str(memory_hour),
        "",
        "Funding",
        f"OK ({snap['funding']})" if snap and snap["funding"] is not None else "FAIL",
        "",
        "OI",
        f"OK ({snap['open_interest']})" if snap and snap["open_interest"] is not None else "FAIL",
        "",
        "FearGreed",
        str(snap["fear_greed"] if snap else "—"),
        "",
        "Latency",
        f"{snap['collector_latency_ms'] if snap and snap['collector_latency_ms'] else health.get('latency_ms', '—')} ms",
        "",
        "Status",
        str(snap["recorder_status"] if snap else health.get("status", "—")),
        "",
        "Errors",
        str(health.get("error") or "none"),
    ]
    return "\n".join(lines)


def format_telegram_debug_g0(conn: Any) -> str:
    from bot.research.futures_agent.telegram_config import get_telegram_bot_token
    from bot.research.market_events.alert_config import alerts_enabled, resolve_alert_chat_id
    from bot.research.market_events.telegram_ops.config_report import fetch_bot_info

    resolution = resolve_alert_chat_id()
    token = get_telegram_bot_token()
    bot_info = fetch_bot_info() if token else None

    queue = _count(conn, "SELECT COUNT(*) FROM market_event_alert_log WHERE sent = 0")
    pending_delivery = _count(
        conn,
        """
        SELECT COUNT(*) FROM market_event_telegram_delivery_log
        WHERE status = 'failed'
          AND (http_code IN (429,500,502,503,504) OR error LIKE 'timeout:%')
        """,
    )

    last_send = conn.execute(
        """
        SELECT created_at, alert_type, latency_ms FROM market_event_telegram_delivery_log
        WHERE status = 'sent' ORDER BY created_at DESC LIMIT 1
        """,
    ).fetchone()
    last_err = conn.execute(
        """
        SELECT created_at, error, alert_type FROM market_event_telegram_delivery_log
        WHERE status = 'failed' ORDER BY created_at DESC LIMIT 1
        """,
    ).fetchone()

    lines = [
        "Telegram Debug",
        "",
        "Queue",
        str(queue),
        "",
        "Pending (retryable)",
        str(pending_delivery),
        "",
        "Last send",
        (
            f"{last_send['alert_type']} {int(time.time()) - int(last_send['created_at'])}s ago"
            if last_send else "never"
        ),
        "",
        "Last error",
        (f"{last_err['alert_type']}: {last_err['error']}" if last_err else "none"),
        "",
        "Token OK",
        "YES" if token else "NO",
        "",
        "Chat OK",
        "YES" if resolution.chat_id else "NO",
        "",
        "Alerts enabled",
        "YES" if alerts_enabled() else "NO",
        "",
        "Bot connected",
        f"YES @{bot_info.get('username')}" if bot_info else "NO",
    ]
    return "\n".join(lines)


@dataclass
class EmitTestResultG0:
    ok: bool
    message: str
    signal_id: int | None = None
    telegram_sent: bool = False


def emit_test_signal_g0(conn: Any) -> EmitTestResultG0:
    """Artificial candidate → shadow persist → telegram → followup (no threshold gates)."""
    from bot.research.market_events.signal_intelligence.candidate_g31 import CandidateG31
    from bot.research.market_events.signal_intelligence.shadow_g40 import (
        SHADOW_HEADER,
        _claude_summary_at_signal,
        persist_shadow_signal_g40,
        send_shadow_telegram_g40,
    )
    from bot.research.market_events.signal_intelligence.trend_windows_g3 import TrendWindowG3

    logger.info("SHADOW START emit-test-signal")

    now = int(time.time())
    snap = conn.execute(
        "SELECT id FROM market_snapshots_g3 ORDER BY snapshot_ts DESC LIMIT 1",
    ).fetchone()
    if snap:
        snap_id = int(snap["id"])
    else:
        conn.execute(
            """
            INSERT INTO market_snapshots_g3 (
              snapshot_uuid, snapshot_ts, btc_price, created_at
            ) VALUES (?, ?, 65000.0, ?)
            """,
            (f"emit-test-{now}", now, now),
        )
        conn.commit()
        snap_id = int(conn.execute("SELECT id FROM market_snapshots_g3 ORDER BY id DESC LIMIT 1").fetchone()["id"])

    trend = TrendWindowG3(
        symbol="BTC", window_minutes=15, pattern_type="emit_test",
        consecutive_candles=6, trend_score=40.0, direction="DOWN", details={},
    )
    candidate = CandidateG31(
        symbol="BTC",
        trend_score=40.0,
        market_score=31.0,
        liquidity_score=42.0,
        confidence=5.8,
        rr=1.7,
        btc_alignment="Neutral",
        funding_score=50.0,
        oi_score=50.0,
        volume_score=18.0,
        atr_score=50.0,
        fear_greed=50.0,
        candidate_state="emit_test",
        rejection_reason="emit-test bypass",
        direction="SHORT",
        trend_coverage_pct=100.0,
        trend_windows_json="[]",
        trend=trend,
    )

    logger.info("SHADOW THRESHOLDS BYPASS emit-test-signal")

    try:
        sig = persist_shadow_signal_g40(
            conn, snapshot_id=snap_id, candidate=candidate, event_id=None,
        )
    except Exception as exc:
        logger.error("SHADOW FAILED reason=persist exception: %s", exc)
        return EmitTestResultG0(False, f"FAILED\n\npersist exception: {exc}")

    if not sig:
        logger.error("SHADOW FAILED reason=persist returned None")
        return EmitTestResultG0(False, "FAILED\n\npersist returned None")

    conn.execute(
        """
        UPDATE market_shadow_signals
        SET production_rejection_json = ?
        WHERE id = ?
        """,
        (json.dumps({"emit_test": True, "lane": "emit-test-signal"}, ensure_ascii=False), sig.signal_id),
    )

    logger.info("SHADOW DB INSERT id=%s symbol=BTC", sig.signal_id)

    claude = _claude_summary_at_signal(conn, symbol="BTC", candidate=candidate)
    if SHADOW_HEADER not in (sig.telegram_rendered or ""):
        logger.warning("SHADOW telegram render missing header")

    tg_ok = send_shadow_telegram_g40(conn, sig)
    logger.info("SHADOW TELEGRAM sent=%s id=%s", tg_ok, sig.signal_id)

    row = conn.execute(
        "SELECT status FROM market_shadow_signals WHERE id = ?",
        (sig.signal_id,),
    ).fetchone()
    followup_ok = row is not None and str(row["status"]) == "OPEN"
    logger.info("SHADOW FOLLOWUP scheduled=%s signal_id=%s", followup_ok, sig.signal_id)

    conn.commit()

    if not followup_ok:
        return EmitTestResultG0(
            False,
            f"FAILED\n\nDB insert ok id={sig.signal_id} but followup status not OPEN",
            signal_id=sig.signal_id,
            telegram_sent=tg_ok,
        )

    status = "PASS" if tg_ok else "PARTIAL"
    msg = "\n".join([
        status,
        "",
        f"signal_id={sig.signal_id}",
        f"telegram_sent={'YES' if tg_ok else 'NO'}",
        f"followup=OPEN",
        f"claude={'yes' if claude else 'no'}",
    ])
    return EmitTestResultG0(tg_ok, msg, signal_id=sig.signal_id, telegram_sent=tg_ok)


def format_emit_test_signal_g0(conn: Any) -> str:
    result = emit_test_signal_g0(conn)
    return result.message


def count_emit_test_shadow_signals_g0(conn: Any) -> int:
    return _count(
        conn,
        """
        SELECT COUNT(*) FROM market_shadow_signals
        WHERE production_rejection_json LIKE '%emit_test%'
           OR production_rejection_json LIKE '%emit-test-signal%'
        """,
    )
