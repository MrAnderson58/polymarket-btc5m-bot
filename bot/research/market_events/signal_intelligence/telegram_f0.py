"""F.0 short Telegram message formats."""

from __future__ import annotations

import json
from typing import Any

from bot.research.market_events.market_event_alerts import PAPER_LABEL


def _opp_v2(conn: Any, event_id: int) -> tuple[float | None, dict[str, float]]:
    row = conn.execute(
        "SELECT score, score_breakdown_json FROM market_events_opportunity_scores_v2 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if not row:
        return None, {}
    return float(row["score"]), json.loads(row["score_breakdown_json"] or "{}")


def _f0_ai_line(conn: Any, event_id: int) -> str:
    row = conn.execute(
        "SELECT summary_en, bias FROM market_event_ai_analyses_f0 WHERE event_id = ? ORDER BY created_at DESC LIMIT 1",
        (event_id,),
    ).fetchone()
    if row and row["summary_en"]:
        return str(row["summary_en"]).split(".")[0][:40]
    pending = conn.execute(
        "SELECT confirmed_reversal FROM market_events_pending_shocks WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if pending and pending["confirmed_reversal"]:
        return f"R{pending['confirmed_reversal'][-1] if pending['confirmed_reversal'] else '2'}"
    return "Waiting R2"


def _funding_label(conn: Any, event_id: int) -> str:
    row = conn.execute(
        "SELECT funding FROM market_event_exchange_context WHERE event_id = ? LIMIT 1",
        (event_id,),
    ).fetchone()
    if not row or row["funding"] is None:
        return "—"
    f = abs(float(row["funding"]))
    if f >= 0.05:
        return "High"
    if f >= 0.02:
        return "Elevated"
    return "Normal"


def _history_pct(conn: Any, *, symbol: str, event_id: int, ret: float) -> str:
    rows = conn.execute(
        """
        SELECT p.confirmed_reversal FROM market_events e
        LEFT JOIN market_events_pending_shocks p ON p.event_id = e.id
        WHERE e.symbol = ? AND e.id != ? AND ABS(e.return_pct) >= ?
        ORDER BY e.event_ts DESC LIMIT 100
        """,
        (symbol, event_id, max(1.0, abs(ret) * 0.65)),
    ).fetchall()
    if not rows:
        return "—"
    revs = sum(1 for r in rows if r["confirmed_reversal"])
    return f"{int(100 * revs / len(rows))}%"


def format_shock_f0(conn: Any, event_id: int) -> str:
    row = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return "SHOCK event not found"
    score, breakdown = _opp_v2(conn, event_id)
    window = int(row["trigger_window_seconds"] or 60)
    win_label = f"{window // 60}m" if window >= 60 else f"{window}s"
    ret = float(row["return_pct"] or 0)
    lines = [
        "🚨 SHOCK",
        "",
        row["symbol"],
        f"{ret:+.1f}%",
        win_label,
        "",
    ]
    if score is not None:
        lines.append("Score")
        lines.append(f"{score:.0f}")
        lines.append("")
        for key in ("History", "Funding", "Telegram", "Liquidity", "Trend exhaustion"):
            if key in breakdown:
                lines.append(key)
                val = breakdown[key]
                if key == "History":
                    lines.append(_history_pct(conn, symbol=row["symbol"], event_id=event_id, ret=ret))
                elif key == "Funding":
                    lines.append(_funding_label(conn, event_id))
                else:
                    lines.append(f"+{val:.0f}")
        lines.append("")
    lines.extend([
        f"AI",
        _f0_ai_line(conn, event_id),
        "",
        PAPER_LABEL,
    ])
    return "\n".join(lines)


def format_entry_f0(
    conn: Any,
    *,
    event_id: int,
    symbol: str,
    reversal_variant: str,
    entry: float,
    stop: float,
    target: float,
) -> str:
    ai = _f0_ai_line(conn, event_id)
    return "\n".join([
        "✅ ENTRY",
        "",
        symbol,
        reversal_variant,
        "",
        "Entry",
        f"{entry:.4g}",
        "",
        "Stop",
        f"{stop:.4g}",
        "",
        "Target",
        f"{target:.4g}",
        "",
        "AI",
        ai,
        "",
        PAPER_LABEL,
    ])


def format_exit_f0(
    conn: Any,
    *,
    event_id: int,
    symbol: str,
    pnl_pct: float,
    holding_min: int,
    exit_variant: str,
    ai_correct: bool | None = None,
) -> str:
    ai_label = "AI Correct" if ai_correct else "AI Partial" if ai_correct is False else "AI —"
    return "\n".join([
        "🏁 RESULT",
        "",
        symbol,
        f"{pnl_pct:+.1f}%",
        "",
        "Holding",
        f"{holding_min}m",
        "",
        exit_variant,
        "",
        ai_label,
        "",
        PAPER_LABEL,
    ])


def format_score_block(breakdown: dict[str, float], total: float) -> str:
    lines = ["Score", f"{total:.0f}", ""]
    for k, v in breakdown.items():
        lines.extend([k, f"+{v:.0f}"])
    return "\n".join(lines)
