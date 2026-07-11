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
                    }
                    _json_response(self, stats)
                elif path.startswith("/timeline/"):
                    eid = int(path.split("/")[-1])
                    timeline = build_event_timeline(conn, event_id=eid)
                    _json_response(self, {"event_id": eid, "timeline": timeline})
                else:
                    _json_response(self, {
                        "endpoints": [
                            "/events", "/paper", "/alerts", "/daily", "/weekly", "/stats", "/timeline/{id}",
                        ],
                    })
        except Exception as exc:
            _json_response(self, {"error": str(exc)}, status=500)


def run_dashboard_api(*, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"Dashboard API listening on http://{host}:{port}")
    server.serve_forever()
