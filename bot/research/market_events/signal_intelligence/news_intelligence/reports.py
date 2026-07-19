"""S41 — markdown market briefs for Claude (no X posts)."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.db import market_events_readonly_connection

logger = logging.getLogger(__name__)

REPORTS_DIR = BASE_DIR / "reports"

_PERIOD_FILES = {
    "morning": "morning.md",
    "afternoon": "afternoon.md",
    "evening": "evening.md",
}


def period_name_for_hour(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    return "evening"


def _latest_brief(conn: Any) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM market_daily_briefs
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    for key in (
        "top_bullish_json",
        "top_bearish_json",
        "macro_events_json",
        "fed_json",
        "etf_json",
        "whales_json",
        "polymarket_json",
    ):
        try:
            d[key.replace("_json", "")] = json.loads(d.get(key) or "[]")
        except Exception:
            d[key.replace("_json", "")] = []
    return d


def _latest_summaries(conn: Any, *, limit: int = 12) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT symbol, summary, bullish_score, bearish_score, importance, headline_count
        FROM market_news_summary
        ORDER BY period_end DESC, importance DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _bullets(items: list[Any], *, empty: str = "—") -> str:
    if not items:
        return empty
    lines: list[str] = []
    for it in items:
        if isinstance(it, dict):
            sym = it.get("symbol") or ""
            summary = str(it.get("summary") or "").split("\n")[0]
            lines.append(f"- **{sym}**: {summary}" if sym else f"- {summary}")
        else:
            lines.append(f"- {it}")
    return "\n".join(lines)


def render_market_brief_md(
    *,
    brief: dict[str, Any] | None,
    summaries: list[dict[str, Any]],
    period: str,
    generated_at: int,
) -> str:
    dt = datetime.fromtimestamp(generated_at)
    top_news = [
        f"- **{s['symbol']}**: {str(s.get('summary') or '').split(chr(10))[0]}"
        for s in summaries[:8]
    ] or ["- No recent headlines"]

    if brief:
        narrative = brief.get("global_narrative") or "—"
        bullish = _bullets(brief.get("top_bullish") or [])
        bearish = _bullets(brief.get("top_bearish") or [])
        macro = _bullets(brief.get("macro_events") or [])
        fed = _bullets(brief.get("fed") or [])
        etf = _bullets(brief.get("etf") or [])
        whales = _bullets(brief.get("whales") or [])
        poly = _bullets(brief.get("polymarket") or [])
        risk = brief.get("risk_level") or "UNKNOWN"
        conclusion = (
            f"Risk level **{risk}**. "
            f"Watchlist focus on bullish leaders and elevated macro/ETF headlines. "
            f"This brief is structured input for Claude — not trading advice."
        )
    else:
        narrative = "No global brief yet — run news-intel aggregation first."
        bullish = bearish = macro = fed = etf = whales = poly = "—"
        risk = "UNKNOWN"
        conclusion = "Insufficient data for AI conclusion."

    return "\n".join([
        "# Market Brief",
        "",
        f"_Period: {period} · Generated: {dt.isoformat(timespec='seconds')}_",
        "",
        "## Top narratives",
        narrative,
        "",
        "## Top news",
        *top_news,
        "",
        "## Bullish assets",
        bullish,
        "",
        "## Bearish assets",
        bearish,
        "",
        "## Macro",
        macro,
        "",
        "### Fed",
        fed,
        "",
        "### ETF",
        etf,
        "",
        "## Whales",
        whales,
        "",
        "## Polymarket",
        poly,
        "",
        "## Risk level",
        risk,
        "",
        "## AI conclusion",
        conclusion,
        "",
    ])


def write_period_reports_s41(
    *,
    reports_dir: Path | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Write morning.md / afternoon.md / evening.md (current period + refresh others)."""
    now_ts = int(now if now is not None else time.time())
    hour = datetime.fromtimestamp(now_ts).hour
    current = period_name_for_hour(hour)
    out_dir = reports_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    with market_events_readonly_connection() as conn:
        brief = _latest_brief(conn)
        summaries = _latest_summaries(conn)

    written: list[str] = []
    for period, filename in _PERIOD_FILES.items():
        md = render_market_brief_md(
            brief=brief,
            summaries=summaries,
            period=period,
            generated_at=now_ts,
        )
        path = out_dir / filename
        path.write_text(md, encoding="utf-8")
        written.append(str(path))
        logger.info("s41 report written %s (focus=%s)", path.name, current)

    return {"period": current, "files": written, "reports_dir": str(out_dir)}
