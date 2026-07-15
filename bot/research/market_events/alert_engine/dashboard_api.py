"""Phase E.5 — read-only JSON dashboard API (stdlib HTTP)."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from bot.research.market_events.alert_engine.ai_comparison import comparison_report
from bot.research.market_events.alert_engine.daily_digest import build_daily_digest
from bot.research.market_events.alert_engine.timeline import build_event_timeline
from bot.research.market_events.alert_engine.weekly_report import build_weekly_report
from bot.research.market_events.db import market_events_connection
from bot.research.market_events.event_schema import apply_migrations


def _json_response(handler: BaseHTTPRequestHandler, data: Any, status: int = 200) -> None:
    body = json.dumps(data, indent=2, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _query_int(qs: dict, key: str, default: int) -> int:
    vals = qs.get(key)
    if not vals:
        return default
    try:
        return int(vals[0])
    except ValueError:
        return default


class DashboardHandler(BaseHTTPRequestHandler):
    """Read-only market events dashboard."""

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        try:
            with market_events_connection() as conn:
                apply_migrations(conn)
                if path == "/events":
                    limit = _query_int(qs, "limit", 50)
                    rows = conn.execute(
                        """
                        SELECT id, event_ts, symbol, direction, return_pct, phase,
                               classification, asset_class
                        FROM market_events ORDER BY event_ts DESC LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                    _json_response(self, {"mode": "PRODUCTION ONLINE", "events": [dict(r) for r in rows]})
                elif path == "/paper":
                    limit = _query_int(qs, "limit", 50)
                    rows = conn.execute(
                        """
                        SELECT r.*, e.symbol FROM paper_strategy_runs r
                        JOIN market_events e ON e.id = r.event_id
                        ORDER BY COALESCE(r.entry_ts, 0) DESC LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                    _json_response(self, {"mode": "PAPER ONLINE", "runs": [dict(r) for r in rows]})
                elif path == "/alerts":
                    limit = _query_int(qs, "limit", 50)
                    rows = conn.execute(
                        """
                        SELECT * FROM market_event_alert_log
                        ORDER BY created_at DESC LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                    _json_response(self, {"alerts": [dict(r) for r in rows]})
                elif path == "/daily":
                    msg, day_key = build_daily_digest(conn)
                    _json_response(self, {"period": day_key, "report": msg})
                elif path == "/weekly":
                    msg, week_key = build_weekly_report(conn)
                    _json_response(self, {"period": week_key, "report": msg})
                elif path == "/stats":
                    from bot.research.market_events.signal_intelligence.heartbeat_diagnostics_g352 import (
                        read_heartbeat_diagnostics,
                    )
                    from bot.research.market_events.signal_intelligence.priority_engine_f5 import (
                        dashboard_skipped_count,
                    )
                    from bot.research.market_events.signal_intelligence.telegram_dedupe_f41 import (
                        duplicate_prevented_count,
                    )
                    day_start = int(datetime.now(timezone.utc).replace(
                        hour=0, minute=0, second=0, microsecond=0,
                    ).timestamp())
                    stats = {
                        "mode": "SHADOW + PAPER + REPLAY",
                        "events_total": conn.execute("SELECT COUNT(*) FROM market_events").fetchone()[0],
                        "events_today": conn.execute(
                            "SELECT COUNT(*) FROM market_events WHERE event_ts >= ?", (day_start,),
                        ).fetchone()[0],
                        "paper_open": conn.execute(
                            "SELECT COUNT(*) FROM paper_strategy_runs WHERE exit_ts IS NULL AND entry_ts IS NOT NULL",
                        ).fetchone()[0],
                        "ai_pending": conn.execute(
                            "SELECT COUNT(*) FROM market_event_analysis_jobs WHERE status='pending'",
                        ).fetchone()[0],
                        "opportunity_scores": conn.execute(
                            "SELECT COUNT(*) FROM market_events_opportunity_scores",
                        ).fetchone()[0],
                        "ai_comparisons": conn.execute(
                            "SELECT COUNT(*) FROM market_events_ai_comparisons",
                        ).fetchone()[0],
                        "duplicate_telegram_prevented": duplicate_prevented_count(conn),
                        "duplicate_telegram_prevented_today": duplicate_prevented_count(
                            conn, since_ts=day_start,
                        ),
                        "f5_signals_total": conn.execute(
                            "SELECT COUNT(*) FROM market_events_signal_reports_f5",
                        ).fetchone()[0],
                        "f5_telegram_eligible": conn.execute(
                            "SELECT COUNT(*) FROM market_events_signal_reports_f5 WHERE telegram_eligible = 1",
                        ).fetchone()[0],
                        "f5_dashboard_only": dashboard_skipped_count(conn),
                        "f5_dashboard_only_today": dashboard_skipped_count(conn, since_ts=day_start),
                        "heartbeat": read_heartbeat_diagnostics(conn),
                    }
                    _json_response(self, stats)
                elif path.startswith("/timeline/"):
                    eid = int(path.split("/")[-1])
                    timeline = build_event_timeline(conn, event_id=eid)
                    trace: list[dict] = []
                    try:
                        from bot.research.market_events.signal_intelligence.signal_trace_f51 import (
                            fetch_trace_rows,
                        )
                        trace = fetch_trace_rows(conn, event_id=eid)
                    except Exception:
                        pass
                    _json_response(self, {"event_id": eid, "timeline": timeline, "signal_trace": trace})
                elif path == "/exhaustion":
                    limit = _query_int(qs, "limit", 50)
                    rows = conn.execute(
                        "SELECT * FROM market_events_exhaustion ORDER BY event_ts DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                    _json_response(self, {"exhaustion": [dict(r) for r in rows]})
                elif path == "/opportunity":
                    limit = _query_int(qs, "limit", 50)
                    rows = conn.execute(
                        """
                        SELECT o.*, e.symbol FROM market_events_opportunity_scores_v2 o
                        JOIN market_events e ON e.id = o.event_id
                        ORDER BY o.score DESC LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                    _json_response(self, {"opportunity_v2": [dict(r) for r in rows]})
                elif path == "/multitimeframe":
                    det = qs.get("detector", [None])[0]
                    limit = _query_int(qs, "limit", 50)
                    if det:
                        rows = conn.execute(
                            "SELECT * FROM market_events_multitimeframe WHERE detector_id = ? ORDER BY event_ts DESC LIMIT ?",
                            (det, limit),
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            "SELECT * FROM market_events_multitimeframe ORDER BY event_ts DESC LIMIT ?",
                            (limit,),
                        ).fetchall()
                    _json_response(self, {"multitimeframe": [dict(r) for r in rows]})
                elif path == "/exchanges":
                    rows = conn.execute(
                        "SELECT * FROM market_event_exchange_symbols ORDER BY updated_at DESC LIMIT 100",
                    ).fetchall()
                    _json_response(self, {"exchanges": [dict(r) for r in rows]})
                elif path == "/signals":
                    limit = _query_int(qs, "limit", 50)
                    events = conn.execute(
                        """
                        SELECT e.id, e.symbol, e.return_pct, e.event_ts,
                               o.score AS opp_score, a.confidence AS ai_conf
                        FROM market_events e
                        LEFT JOIN market_events_opportunity_scores_v2 o ON o.event_id = e.id
                        LEFT JOIN market_event_ai_analyses_f0 a ON a.event_id = e.id
                        ORDER BY e.event_ts DESC LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                    _json_response(self, {"signals": [dict(r) for r in events]})
                elif path == "/signals-f5":
                    limit = _query_int(qs, "limit", 50)
                    rows = conn.execute(
                        """
                        SELECT f.*, e.symbol, e.return_pct, e.event_ts
                        FROM market_events_signal_reports_f5 f
                        JOIN market_events e ON e.id = f.event_id
                        ORDER BY f.dynamic_confidence DESC, f.created_at DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                    _json_response(self, {"signals_f5": [dict(r) for r in rows]})
                elif path == "/market-score":
                    limit = _query_int(qs, "limit", 50)
                    rows = conn.execute(
                        """
                        SELECT i.event_id, i.market_score, i.final_confidence,
                               i.success_probability, i.component_scores_json,
                               i.created_at, e.symbol
                        FROM market_events_market_intelligence_f7 i
                        JOIN market_events e ON e.id = i.event_id
                        ORDER BY i.market_score DESC, i.created_at DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                    _json_response(self, {"market_score": [dict(r) for r in rows]})
                elif path == "/liquidations":
                    limit = _query_int(qs, "limit", 50)
                    rows = conn.execute(
                        """
                        SELECT i.event_id, i.liquidation_regime,
                               i.liquidation_continuation_prob, i.liquidation_reversal_prob,
                               i.liquidation_intel_json, i.created_at, e.symbol
                        FROM market_events_market_intelligence_f7 i
                        JOIN market_events e ON e.id = i.event_id
                        ORDER BY i.created_at DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                    _json_response(self, {"liquidations": [dict(r) for r in rows]})
                elif path == "/dominance":
                    limit = _query_int(qs, "limit", 50)
                    rows = conn.execute(
                        """
                        SELECT i.event_id, i.dominance_regime, i.dominance_json,
                               i.created_at, e.symbol
                        FROM market_events_market_intelligence_f7 i
                        JOIN market_events e ON e.id = i.event_id
                        ORDER BY i.created_at DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                    _json_response(self, {"dominance": [dict(r) for r in rows]})
                elif path == "/whales":
                    limit = _query_int(qs, "limit", 50)
                    rows = conn.execute(
                        """
                        SELECT i.event_id, i.whale_score, i.whale_intel_json,
                               i.created_at, e.symbol
                        FROM market_events_market_intelligence_f7 i
                        JOIN market_events e ON e.id = i.event_id
                        ORDER BY i.whale_score DESC, i.created_at DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                    _json_response(self, {"whales": [dict(r) for r in rows]})
                elif path == "/signal-outcomes":
                    from bot.research.market_events.signal_intelligence.yesterday_report_f72 import (
                        outcome_dashboard_stats,
                    )
                    stats = outcome_dashboard_stats(conn)
                    active = conn.execute(
                        """
                        SELECT o.*, e.return_pct FROM market_events_signal_outcomes_f72 o
                        JOIN market_events e ON e.id = o.event_id
                        WHERE o.status != 'CLOSED'
                        ORDER BY o.entry_time DESC LIMIT 50
                        """,
                    ).fetchall()
                    start = int(__import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc,
                    ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
                    closed = conn.execute(
                        """
                        SELECT o.*, e.return_pct FROM market_events_signal_outcomes_f72 o
                        JOIN market_events e ON e.id = o.event_id
                        WHERE o.status = 'CLOSED' AND o.exit_time >= ?
                        ORDER BY o.exit_time DESC LIMIT 50
                        """,
                        (start,),
                    ).fetchall()
                    _json_response(self, {
                        "active_signals": stats["active_signals"],
                        "closed_today": stats["closed_today"],
                        "win_rate": stats["win_rate"],
                        "avg_rr": stats["avg_rr"],
                        "avg_hold_time_sec": stats["avg_hold_seconds"],
                        "total_paper_pnl_today": stats["total_paper_pnl_today"],
                        "active": [dict(r) for r in active],
                        "closed_today_list": [dict(r) for r in closed],
                    })
                elif path == "/near-miss":
                    from bot.research.market_events.signal_intelligence.reports_f73 import (
                        diagnostics_dashboard_stats,
                    )
                    stats = diagnostics_dashboard_stats(conn)
                    _json_response(self, stats)
                elif path == "/liquidity-trend":
                    limit = _query_int(qs, "limit", 50)
                    rows = conn.execute(
                        """
                        SELECT g.*, e.return_pct FROM market_events_liquidity_trend_g1 g
                        JOIN market_events e ON e.id = g.event_id
                        ORDER BY g.event_ts DESC LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                    stats_row = conn.execute(
                        "SELECT COUNT(*) AS n FROM market_events_liquidity_trend_g1",
                    ).fetchone()
                    _json_response(self, {
                        "total": int(stats_row["n"] if stats_row else 0),
                        "signals": [dict(r) for r in rows],
                    })
                elif path == "/ai-research":
                    limit = _query_int(qs, "limit", 50)
                    rows = conn.execute(
                        """
                        SELECT r.*, e.return_pct FROM market_events_ai_research_g2 r
                        JOIN market_events e ON e.id = r.event_id
                        ORDER BY r.created_at DESC LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                    _json_response(self, {"research": [dict(r) for r in rows]})
                elif path == "/candidates":
                    from bot.research.market_events.signal_intelligence.candidate_g31 import (
                        candidate_stats_dict,
                        fetch_top_candidates_g31,
                    )
                    limit = _query_int(qs, "limit", 100)
                    hours = _query_int(qs, "hours", 24)
                    rows = fetch_top_candidates_g31(conn, limit=limit)
                    stats = candidate_stats_dict(conn, hours=hours)
                    latest_ts = rows[0]["candidate_ts"] if rows else None
                    _json_response(self, {
                        "tab": "Candidates",
                        "latest_cycle_ts": latest_ts,
                        "stats": stats,
                        "candidates": [dict(r) for r in rows],
                    })
                elif path == "/replay":
                    from bot.research.market_events.signal_intelligence.replay_g32 import (
                        replay_dashboard_rows,
                    )
                    limit = _query_int(qs, "limit", 50)
                    _json_response(self, {
                        "tab": "Replay",
                        "rows": replay_dashboard_rows(conn, limit=limit),
                    })
                elif path == "/trend-coverage":
                    from bot.research.market_events.signal_intelligence.trend_coverage_g33 import (
                        trend_coverage_dashboard,
                    )
                    limit = _query_int(qs, "limit", 50)
                    _json_response(self, trend_coverage_dashboard(conn, limit=limit))
                elif path == "/score-diagnostics":
                    from bot.research.market_events.signal_intelligence.score_breakdown_g34 import (
                        score_diagnostics_dashboard,
                    )
                    hours = _query_int(qs, "hours", 24)
                    _json_response(self, score_diagnostics_dashboard(conn, hours=hours))
                elif path == "/validation":
                    from bot.research.market_events.signal_intelligence.auto_validation_g4 import (
                        validation_dashboard_g4,
                    )
                    days = _query_int(qs, "days", 30)
                    _json_response(self, validation_dashboard_g4(conn, days=days))
                elif path == "/validation/feature-importance":
                    from bot.research.market_events.signal_intelligence.auto_validation_g4 import (
                        compute_feature_importance_g4,
                    )
                    _json_response(self, {
                        "tab": "Feature Importance",
                        "factors": compute_feature_importance_g4(conn),
                    })
                elif path == "/validation/false-rejects":
                    from bot.research.market_events.signal_intelligence.auto_validation_g4 import (
                        format_false_rejects_report_g4,
                    )
                    _json_response(self, {
                        "tab": "False Rejects",
                        "report": format_false_rejects_report_g4(conn),
                    })
                elif path == "/validation/false-accepts":
                    from bot.research.market_events.signal_intelligence.auto_validation_g4 import (
                        format_false_accepts_report_g4,
                    )
                    _json_response(self, {
                        "tab": "False Accepts",
                        "report": format_false_accepts_report_g4(conn),
                    })
                elif path == "/validation/optimizer":
                    from bot.research.market_events.signal_intelligence.threshold_optimizer_g42 import (
                        run_threshold_optimizer_g42,
                    )
                    days = _query_int(qs, "days", 30)
                    scenarios = run_threshold_optimizer_g42(conn, days=days)
                    _json_response(self, {
                        "tab": "Optimizer",
                        "scenarios": [s.__dict__ for s in scenarios],
                    })
                elif path == "/quant-research":
                    from bot.research.market_events.signal_intelligence.quant_research_g50 import (
                        quant_research_dashboard_g50,
                    )
                    _json_response(self, quant_research_dashboard_g50(conn))
                elif path == "/market-memory":
                    from bot.research.market_events.signal_intelligence.market_memory_g36 import (
                        market_memory_dashboard_g36,
                    )
                    _json_response(self, market_memory_dashboard_g36(conn))
                elif path == "/signal-discovery":
                    from bot.research.market_events.signal_intelligence.signal_discovery_g37 import (
                        signal_discovery_dashboard_g37,
                    )
                    hours = _query_int(qs, "hours", 24)
                    _json_response(self, signal_discovery_dashboard_g37(conn, hours=hours))
                elif path == "/experimental-signals":
                    from bot.research.market_events.signal_intelligence.experimental_g39 import (
                        experimental_dashboard_g39,
                    )
                    limit = _query_int(qs, "limit", 50)
                    _json_response(self, experimental_dashboard_g39(conn, limit=limit))
                elif path == "/shadow-signals":
                    from bot.research.market_events.signal_intelligence.shadow_g40 import (
                        shadow_dashboard_g40,
                    )
                    status = qs.get("status", [None])[0]
                    symbol = qs.get("symbol", [None])[0]
                    grade = qs.get("grade", [None])[0]
                    data = shadow_dashboard_g40(conn, status=status, symbol=symbol)
                    if grade:
                        data["signals"] = [s for s in data["signals"] if s.get("grade") == grade]
                    _json_response(self, data)
                elif path == "/signal-inbox":
                    from bot.research.market_events.signal_intelligence.signal_inbox_s23 import (
                        signal_inbox_dashboard_s23,
                    )
                    parsed = qs.get("parsed", [None])[0]
                    decision = qs.get("decision", [None])[0]
                    limit = _query_int(qs, "limit", 100)
                    _json_response(self, signal_inbox_dashboard_s23(
                        conn, parsed=parsed, decision=decision, limit=limit,
                    ))
                elif path == "/pattern-explorer":
                    from bot.research.market_events.signal_intelligence.pattern_evidence_s32 import (
                        pattern_explorer_dashboard_s32,
                    )
                    symbol = qs.get("symbol", [None])[0]
                    direction = qs.get("direction", [None])[0]
                    limit = _query_int(qs, "limit", 20)
                    _json_response(self, pattern_explorer_dashboard_s32(
                        conn, symbol=symbol, direction=direction, limit=limit,
                    ))
                else:
                    _json_response(self, {
                        "endpoints": [
                            "/events", "/paper", "/alerts", "/daily", "/weekly", "/stats",
                            "/timeline/{id}", "/exhaustion", "/opportunity", "/multitimeframe",
                            "/exchanges", "/signals", "/signals-f5",
                            "/market-score", "/liquidations", "/dominance", "/whales",
                            "/signal-outcomes", "/near-miss", "/liquidity-trend", "/ai-research",
                            "/candidates", "/replay", "/trend-coverage", "/score-diagnostics",
                            "/validation", "/validation/feature-importance",
                            "/validation/false-rejects", "/validation/false-accepts",
                            "/validation/optimizer",                             "/quant-research", "/market-memory",
                            "/signal-discovery", "/experimental-signals", "/shadow-signals",
                            "/signal-inbox", "/pattern-explorer",
                        ],
                    })
        except Exception as exc:
            _json_response(self, {"error": str(exc)}, status=500)


def run_dashboard_api(*, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"Dashboard API listening on http://{host}:{port}")
    server.serve_forever()
