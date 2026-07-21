"""Platform doctor — one-shot ops health for the AI Trading stack."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from bot.research.market_events.config import BASE_DIR

# Prefer AI analyst report artifacts; fall back to repo reports/
try:
    from bot.research.ai_analyst.config import REPORTS_DIR as _AI_REPORTS_DIR
except Exception:  # pragma: no cover
    _AI_REPORTS_DIR = BASE_DIR / "reports"

REPORTS_DIR = _AI_REPORTS_DIR


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""
    critical: bool = True

    def mark(self) -> str:
        return "✓" if self.ok else "✗"


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(BASE_DIR),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        return out.strip() or "unknown"
    except Exception:
        return "unknown"


def _check_sqlite() -> Check:
    try:
        from bot.research.market_events.db import market_events_readonly_connection
        from bot.research.market_events.db_config import resolve_market_events_db_config

        cfg = resolve_market_events_db_config()
        path = cfg.sqlite_path
        if cfg.backend == "sqlite" and path is not None and not path.exists():
            return Check("SQLite", False, f"missing {path}", critical=True)
        with market_events_readonly_connection() as conn:
            conn.execute("SELECT 1")
        return Check("SQLite", True, critical=True)
    except Exception as exc:
        return Check("SQLite", False, str(exc)[:80], critical=True)


def _check_postgres() -> Check:
    try:
        from bot.research.market_events.db_config import resolve_market_events_db_config

        cfg = resolve_market_events_db_config()
        if cfg.backend == "postgresql":
            from bot.research.market_events.db import market_events_readonly_connection

            with market_events_readonly_connection() as conn:
                conn.execute("SELECT 1")
            return Check("PostgreSQL", True, "connected", critical=False)
        if cfg.postgres_url_configured:
            return Check("PostgreSQL", True, "URL present", critical=False)
        # Optional — not configured still counts as soft OK for doctor summary
        return Check("PostgreSQL", True, "not configured", critical=False)
    except Exception as exc:
        return Check("PostgreSQL", False, str(exc)[:80], critical=False)


def _http_ok(url: str, *, timeout: float = 4.0) -> tuple[bool, str]:
    try:
        import requests

        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "polymarket-doctor/1.0"},
        )
        if resp.ok:
            return True, f"HTTP {resp.status_code}"
        return False, f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, str(exc)[:60]


def _check_yahoo() -> Check:
    ok, detail = _http_ok(
        "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD?interval=1d&range=5d",
    )
    return Check("Yahoo", ok, detail, critical=False)


def _check_binance() -> Check:
    ok, detail = _http_ok("https://fapi.binance.com/fapi/v1/ping")
    return Check("Binance", ok, detail, critical=False)


def _check_farside() -> Check:
    ok, detail = _http_ok("https://farside.co.uk/btc/")
    return Check("Farside", ok, detail, critical=False)


def _source_ok(rows: list[dict[str, Any]], *needles: str) -> bool:
    blob = " ".join(
        f"{r.get('source') or ''} {r.get('name') or ''} {r.get('source_type') or ''}".lower()
        for r in rows
    )
    return any(n in blob for n in needles)


def _proc_running(key: str) -> bool:
    try:
        from bot.research.market_events.process_manager import (
            _service_by_key,
            find_service_processes,
        )

        return bool(find_service_processes(_service_by_key(key)))
    except Exception:
        return False


def _check_news() -> list[Check]:
    rows: list[dict[str, Any]] = []
    try:
        from bot.research.market_events.signal_intelligence.multi_source.health import (
            fetch_source_health,
        )

        rows = fetch_source_health()
    except Exception:
        rows = []

    rss_ok = _proc_running("news-intel") or _source_ok(rows, "rss", "news")
    tg_ok = _proc_running("telegram") or _source_ok(rows, "telegram")
    x_ok = _proc_running("multi-source") or _source_ok(rows, "twitter", "x ", "x_")
    return [
        Check("RSS", rss_ok, critical=False),
        Check("Telegram", tg_ok, critical=False),
        Check("X", x_ok, critical=False),
    ]


def _check_ai() -> list[Check]:
    anthropic = bool(
        (os.getenv("ANTHROPIC_API_KEY") or os.getenv("AI_ANALYST_API_KEY") or "").strip()
    )
    openai = bool(
        (
            os.getenv("OPENAI_API_KEY")
            or os.getenv("OPENROUTER_API_KEY")
            or os.getenv("ME_AI_API_KEY")
            or ""
        ).strip()
    )
    return [
        Check(
            "Claude",
            anthropic,
            "key set" if anthropic else "missing key",
            critical=False,
        ),
        Check(
            "OpenAI",
            openai,
            "key set" if openai else "missing key",
            critical=False,
        ),
    ]


def _check_telegram_bot(*, skip_network: bool = False) -> Check:
    try:
        from bot.research.futures_agent.telegram_config import get_telegram_bot_token
        from bot.research.futures_agent.telegram_intake_f52 import check_telegram_connected

        token = get_telegram_bot_token()
        if not token:
            return Check("Connected", False, "no TELEGRAM_BOT_TOKEN", critical=True)
        if skip_network:
            return Check("Connected", True, "token present (network skipped)", critical=True)
        ok = bool(check_telegram_connected(token))
        return Check("Connected", ok, "getMe" if ok else "getMe failed", critical=True)
    except Exception as exc:
        return Check("Connected", False, str(exc)[:80], critical=True)


def _check_paper_trading() -> tuple[Check, int]:
    open_n = 0
    try:
        running = any(
            _proc_running(key)
            for key in ("shock-paper-core", "shock-paper-tradfi", "g3-live", "learning")
        )
        from bot.research.market_events.db import market_events_readonly_connection

        with market_events_readonly_connection() as conn:
            for table in ("ai_paper_trades_s47", "market_events_paper_trades_s42"):
                try:
                    row = conn.execute(
                        f"SELECT COUNT(*) AS n FROM {table} WHERE status = 'OPEN'",
                    ).fetchone()
                    open_n += int((row["n"] if row else 0) or 0)
                except Exception:
                    continue
        return Check("Running", running, "process up" if running else "no paper process", critical=False), open_n
    except Exception as exc:
        return Check("Running", False, str(exc)[:80], critical=False), 0


def _signals_today() -> int:
    try:
        from bot.research.market_events.db import market_events_readonly_connection

        start = int(
            datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        )
        with market_events_readonly_connection() as conn:
            try:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM ai_signal_history_s48
                    WHERE created_at >= ?
                    """,
                    (start,),
                ).fetchone()
                return int((row["n"] if row else 0) or 0)
            except Exception:
                return 0
    except Exception:
        return 0


def _last_report_utc() -> str:
    try:
        if not REPORTS_DIR.is_dir():
            return "—"
        preferred = (
            "market_report.md",
            "market_context.json",
            "telegram_brief.md",
            "btc_brief.md",
        )
        candidates = []
        for name in preferred:
            p = REPORTS_DIR / name
            if p.is_file():
                candidates.append(p)
        if not candidates:
            candidates = list(REPORTS_DIR.glob("*.md")) + list(REPORTS_DIR.glob("*.json"))
            candidates = [p for p in candidates if p.is_file()]
        if not candidates:
            return "—"
        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        ts = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)
        return ts.strftime("%H:%M UTC")
    except Exception:
        return "—"


def collect_doctor(*, skip_network: bool = False) -> dict[str, Any]:
    """Run checks and return structured platform status."""
    sha = _git_sha()
    db_sqlite = _check_sqlite()
    db_pg = _check_postgres()

    if skip_network:
        market = [
            Check("Yahoo", True, "skipped", critical=False),
            Check("Binance", True, "skipped", critical=False),
            Check("Farside", True, "skipped", critical=False),
        ]
    else:
        market = [_check_yahoo(), _check_binance(), _check_farside()]

    news = _check_news()
    ai = _check_ai()
    tg_bot = _check_telegram_bot(skip_network=skip_network)
    paper, open_trades = _check_paper_trading()
    signals_today = _signals_today()
    last_report = _last_report_utc()

    critical = [db_sqlite, tg_bot]
    soft = [db_pg, *market, *news, *ai, paper]
    market_ok = sum(1 for c in market if c.ok) >= 1 or skip_network
    overall_ok = all(c.ok for c in critical) and market_ok

    return {
        "ok": overall_ok,
        "sha": sha,
        "db": [db_sqlite, db_pg],
        "market": market,
        "news": news,
        "ai": ai,
        "telegram": tg_bot,
        "paper": paper,
        "open_trades": open_trades,
        "signals_today": signals_today,
        "last_report": last_report,
        "soft_failures": [c.name for c in soft if not c.ok],
        "generated_at": int(time.time()),
    }


def format_doctor(data: dict[str, Any]) -> str:
    """Render human-readable doctor report matching the ops cheat-sheet layout."""
    lines = [
        "========================",
        "AI Trading Platform",
        "========================",
        "",
        "Git SHA:",
        str(data["sha"]),
        "",
        "DB",
    ]
    for c in data["db"]:
        lines.append(f"{c.mark()} {c.name}")
    lines.extend(["", "Market Data"])
    for c in data["market"]:
        lines.append(f"{c.mark()} {c.name}")
    lines.extend(["", "News"])
    for c in data["news"]:
        lines.append(f"{c.mark()} {c.name}")
    lines.extend(["", "AI"])
    for c in data["ai"]:
        lines.append(f"{c.mark()} {c.name}")
    tg = data["telegram"]
    paper = data["paper"]
    lines.extend(
        [
            "",
            "Telegram",
            f"{tg.mark()} Connected",
            "",
            "Paper Trading",
            f"{paper.mark()} Running",
            "",
            "Open Trades:",
            str(data["open_trades"]),
            "",
            "Signals today:",
            str(data["signals_today"]),
            "",
            "Last Report:",
            str(data["last_report"]),
            "",
            "Everything OK" if data["ok"] else "Issues detected — check ✗ items above",
        ]
    )
    return "\n".join(lines)


def run_doctor(*, skip_network: bool = False) -> str:
    """Build human-readable platform doctor report."""
    return format_doctor(collect_doctor(skip_network=skip_network))


def doctor_as_dict(*, skip_network: bool = False) -> dict[str, Any]:
    data = collect_doctor(skip_network=skip_network)
    return {
        "ok": data["ok"],
        "sha": data["sha"],
        "open_trades": data["open_trades"],
        "signals_today": data["signals_today"],
        "last_report": data["last_report"],
        "report": format_doctor(data),
        "generated_at": data["generated_at"],
    }
