"""Phase F.5 — formatted historical precedent lines for Telegram."""

from __future__ import annotations

import datetime as dt
from typing import Any


def _format_date(ts: int) -> str:
    return dt.datetime.utcfromtimestamp(ts).strftime("%d %B").replace(
        "January", "января"
    ).replace("February", "февраля").replace("March", "марта").replace(
        "April", "апреля"
    ).replace("May", "мая").replace("June", "июня").replace(
        "July", "июля"
    ).replace("August", "августа").replace("September", "сентября").replace(
        "October", "октября"
    ).replace("November", "ноября"
    ).replace("December", "декабря")


def _estimate_reversal_pct(conn: Any, hist_event_id: int, shock_ret: float) -> tuple[float, int]:
    """Best-effort reversal outcome from paper runs or pending shock."""
    run = conn.execute(
        """
        SELECT net_return, gross_return, duration_seconds
        FROM paper_strategy_runs
        WHERE event_id = ? AND exit_ts IS NOT NULL
        ORDER BY net_return DESC LIMIT 1
        """,
        (hist_event_id,),
    ).fetchone()
    if run:
        pnl = float(run["net_return"] or run["gross_return"] or 0)
        hours = max(1, int(run["duration_seconds"] or 3600) // 3600)
        return pnl, hours

    pending = conn.execute(
        "SELECT confirmed_reversal FROM market_events_pending_shocks WHERE event_id = ?",
        (hist_event_id,),
    ).fetchone()
    if pending and pending["confirmed_reversal"]:
        est = abs(shock_ret) * 0.45
        return est, 6
    return abs(shock_ret) * 0.25, 4


def build_historical_examples(
    conn: Any,
    *,
    matches: list[dict[str, Any]],
    limit: int = 3,
) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for m in matches[:limit]:
        eid = int(m.get("event_id") or 0)
        if not eid:
            continue
        row = conn.execute(
            "SELECT symbol, return_pct, event_ts FROM market_events WHERE id = ?",
            (eid,),
        ).fetchone()
        if not row:
            continue
        shock_ret = float(row["return_pct"] or 0)
        rev_pct, hours = _estimate_reversal_pct(conn, eid, shock_ret)
        examples.append({
            "date_label": _format_date(int(row["event_ts"])),
            "symbol": str(row["symbol"]).upper(),
            "shock_pct": round(shock_ret, 1),
            "reversal_pct": round(rev_pct, 1),
            "hours": hours,
        })
    return examples


def format_historical_block(examples: list[dict[str, Any]]) -> list[str]:
    if not examples:
        return ["Похожих случаев в корпусе пока мало"]
    lines = ["Похожие случаи", ""]
    for ex in examples:
        lines.extend([
            ex["date_label"],
            "↓",
            ex["symbol"],
            "",
            f"{ex['shock_pct']:+.1f}%",
            "↓",
            f"+{ex['reversal_pct']:.1f}%",
            f"через {ex['hours']} ч",
            "",
            "---",
            "",
        ])
    if lines and lines[-1] == "":
        lines.pop()
    if lines and lines[-1] == "---":
        lines.pop()
    if lines and lines[-1] == "":
        lines.pop()
    return lines
