"""Phase F.6 — trader performance learning from paper closes."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.config import (
    F6_ENABLED,
    F6_MIN_SIGNALS,
    F6_WR_HIGH,
    F6_WR_LOW,
)

RECENT_WINDOW = 30
_OUTCOME_DEDUP_KEY = "dedup_keys"


@dataclass(frozen=True)
class TraderPerformanceF6:
    channel_name: str
    signals_count: int
    wins: int
    losses: int
    win_rate: float
    avg_rr: float
    avg_pnl: float
    max_drawdown: float
    avg_tp_time_sec: float
    author_score: float
    last_30_wins: int
    last_30_losses: int
    sufficient: bool


def resolve_channel_for_event(conn: Any, event_id: int) -> str | None:
    row = conn.execute(
        """
        SELECT source FROM market_event_context
        WHERE event_id = ? AND context_type IN ('TELEGRAM_SIGNAL', 'TRADER_THESIS')
        ORDER BY relevance_score DESC, context_ts DESC LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if not row:
        return None
    return str(row["source"] or "").strip() or None


def _display_channel(channel: str) -> str:
    if channel.startswith("telegram_inbound:"):
        return f"Telegram {channel.split(':', 1)[-1]}"
    return channel


def author_confidence_bonus(win_rate: float, *, signals_count: int) -> float:
    """Map win rate to confidence adjustment: 38% -> -1.0, 82% -> +0.7."""
    if signals_count < F6_MIN_SIGNALS:
        return 0.0
    wr = max(0.0, min(1.0, win_rate))
    span = F6_WR_HIGH - F6_WR_LOW
    if span <= 0:
        return 0.0
    bonus = -1.0 + (wr - F6_WR_LOW) / span * 1.7
    return round(max(-1.0, min(0.7, bonus)), 2)


def apply_author_confidence(market_confidence: float, perf: TraderPerformanceF6 | None) -> float:
    """Final confidence = market model + author bonus (when stats sufficient)."""
    if not perf or not perf.sufficient:
        return round(market_confidence, 1)
    bonus = author_confidence_bonus(perf.win_rate, signals_count=perf.signals_count)
    return round(min(10.0, max(0.0, market_confidence + bonus)), 1)


def _estimate_rr(*, gross_return: float | None, mfe: float | None, mae: float | None) -> float:
    if gross_return is not None and mae is not None and mae < -1e-9:
        return abs(float(gross_return)) / abs(float(mae))
    if mfe is not None and mae is not None and mae < -1e-9:
        return abs(float(mfe)) / abs(float(mae))
    if gross_return is not None:
        return max(0.1, abs(float(gross_return)) / 2.0)
    return 1.0


def _load_row(conn: Any, channel_name: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM market_events_trader_performance_f6 WHERE channel_name = ?",
        (channel_name,),
    ).fetchone()
    return dict(row) if row else None


def _parse_recent(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _recompute(recent: list[dict[str, Any]], channel_name: str) -> TraderPerformanceF6:
    signals_count = len(recent)
    wins = sum(1 for o in recent if o.get("win"))
    losses = signals_count - wins
    win_rate = wins / signals_count if signals_count else 0.0

    pnls = [float(o["net_return"]) for o in recent if o.get("net_return") is not None]
    avg_pnl = sum(pnls) / len(pnls) if pnls else 0.0

    rrs = [float(o["rr"]) for o in recent if o.get("rr") is not None]
    avg_rr = sum(rrs) / len(rrs) if rrs else 0.0

    tp_times = [
        float(o["duration_seconds"])
        for o in recent
        if o.get("exit_reason") == "TP" and o.get("duration_seconds") is not None
    ]
    avg_tp_time = sum(tp_times) / len(tp_times) if tp_times else 0.0

    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for o in recent:
        cumulative += float(o.get("net_return") or 0.0)
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)

    last_30 = recent[-RECENT_WINDOW:]
    last_30_wins = sum(1 for o in last_30 if o.get("win"))
    last_30_losses = len(last_30) - last_30_wins

    sufficient = signals_count >= F6_MIN_SIGNALS
    score = author_confidence_bonus(win_rate, signals_count=signals_count) if sufficient else 0.0

    return TraderPerformanceF6(
        channel_name=channel_name,
        signals_count=signals_count,
        wins=wins,
        losses=losses,
        win_rate=round(win_rate, 4),
        avg_rr=round(avg_rr, 2),
        avg_pnl=round(avg_pnl, 3),
        max_drawdown=round(max_dd, 3),
        avg_tp_time_sec=round(avg_tp_time, 1),
        author_score=score,
        last_30_wins=last_30_wins,
        last_30_losses=last_30_losses,
        sufficient=sufficient,
    )


def _persist(conn: Any, perf: TraderPerformanceF6, recent: list[dict[str, Any]]) -> None:
    now = int(time.time())
    insert_returning_id(
        conn,
        """
        INSERT INTO market_events_trader_performance_f6 (
          channel_name, signals_count, wins, losses, win_rate, avg_rr, avg_pnl,
          max_drawdown, avg_tp_time_sec, author_score, last_30_wins, last_30_losses,
          recent_outcomes_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_name) DO UPDATE SET
          signals_count = excluded.signals_count,
          wins = excluded.wins,
          losses = excluded.losses,
          win_rate = excluded.win_rate,
          avg_rr = excluded.avg_rr,
          avg_pnl = excluded.avg_pnl,
          max_drawdown = excluded.max_drawdown,
          avg_tp_time_sec = excluded.avg_tp_time_sec,
          author_score = excluded.author_score,
          last_30_wins = excluded.last_30_wins,
          last_30_losses = excluded.last_30_losses,
          recent_outcomes_json = excluded.recent_outcomes_json,
          updated_at = excluded.updated_at
        """,
        (
            perf.channel_name,
            perf.signals_count,
            perf.wins,
            perf.losses,
            perf.win_rate,
            perf.avg_rr,
            perf.avg_pnl,
            perf.max_drawdown,
            perf.avg_tp_time_sec,
            perf.author_score,
            perf.last_30_wins,
            perf.last_30_losses,
            json.dumps(recent[-RECENT_WINDOW * 3:]),
            now,
        ),
    )


def record_paper_close_f6(
    conn: Any,
    *,
    event_id: int,
    net_return: float,
    exit_reason: str | None,
    duration_seconds: int | None,
    mfe: float | None,
    mae: float | None,
    gross_return: float | None,
    reversal_variant: str,
    exit_variant: str,
) -> TraderPerformanceF6 | None:
    """Update channel performance after a paper run closes."""
    if not F6_ENABLED:
        return None

    channel = resolve_channel_for_event(conn, event_id)
    if not channel:
        return None

    dedup_key = f"{event_id}:{reversal_variant}:{exit_variant}"
    row = _load_row(conn, channel)
    recent = _parse_recent(row["recent_outcomes_json"] if row else None)
    if any(o.get("dedup_key") == dedup_key for o in recent):
        return load_trader_performance(conn, channel)

    outcome = {
        "dedup_key": dedup_key,
        "event_id": event_id,
        "net_return": round(float(net_return), 4),
        "win": float(net_return) > 0,
        "exit_reason": exit_reason,
        "duration_seconds": duration_seconds,
        "rr": round(_estimate_rr(gross_return=gross_return, mfe=mfe, mae=mae), 2),
        "closed_at": int(time.time()),
    }
    recent.append(outcome)
    perf = _recompute(recent, channel)
    _persist(conn, perf, recent)
    return perf


def load_trader_performance(conn: Any, channel_name: str) -> TraderPerformanceF6 | None:
    row = _load_row(conn, channel_name)
    if not row:
        return None
    sufficient = int(row["signals_count"]) >= F6_MIN_SIGNALS
    return TraderPerformanceF6(
        channel_name=str(row["channel_name"]),
        signals_count=int(row["signals_count"]),
        wins=int(row["wins"]),
        losses=int(row["losses"]),
        win_rate=float(row["win_rate"]),
        avg_rr=float(row["avg_rr"]),
        avg_pnl=float(row["avg_pnl"]),
        max_drawdown=float(row["max_drawdown"]),
        avg_tp_time_sec=float(row["avg_tp_time_sec"]),
        author_score=float(row["author_score"]),
        last_30_wins=int(row["last_30_wins"]),
        last_30_losses=int(row["last_30_losses"]),
        sufficient=sufficient,
    )


def load_trader_performance_for_event(conn: Any, event_id: int) -> TraderPerformanceF6 | None:
    channel = resolve_channel_for_event(conn, event_id)
    if not channel:
        return None
    return load_trader_performance(conn, channel)


def format_author_telegram_block(perf: TraderPerformanceF6 | None, *, channel: str | None = None) -> list[str]:
    sep = "──────────────"
    lines = ["", sep, "", "Автор сигнала", ""]
    if channel:
        lines.append(_display_channel(channel))
        lines.append("")
    if not perf or not perf.sufficient:
        lines.extend(["Автор новый", "", "Статистика недостаточна"])
        return lines

    lines.extend([
        f"Win Rate: {perf.win_rate * 100:.0f}%",
        "",
        f"Средний RR: {perf.avg_rr:.1f}",
        "",
        "Последние 30 сигналов:",
        f"{perf.last_30_wins} успешных",
        f"{perf.last_30_losses} неудачных",
    ])
    return lines


def trader_performance_report(conn: Any, *, channel: str | None = None) -> str:
    if channel:
        perf = load_trader_performance(conn, channel)
        if not perf:
            return f"TRADER PERFORMANCE — {channel}\n\n(no data)"
        rows = [perf]
    else:
        db_rows = conn.execute(
            "SELECT channel_name FROM market_events_trader_performance_f6 ORDER BY signals_count DESC",
        ).fetchall()
        rows = [load_trader_performance(conn, r["channel_name"]) for r in db_rows]
        rows = [r for r in rows if r]

    lines = ["TRADER PERFORMANCE REPORT (F.6)", ""]
    for perf in rows:
        lines.extend([
            f"Channel: {_display_channel(perf.channel_name)}",
            f"  signals: {perf.signals_count}  win rate: {perf.win_rate * 100:.1f}%",
            f"  avg RR: {perf.avg_rr:.2f}  avg PnL: {perf.avg_pnl:+.2f}%",
            f"  max drawdown: {perf.max_drawdown:.2f}%  avg TP time: {perf.avg_tp_time_sec:.0f}s",
            f"  author score: {perf.author_score:+.2f}",
            f"  last 30: {perf.last_30_wins}W / {perf.last_30_losses}L",
            "",
        ])
    return "\n".join(lines).strip()


def trader_ranking_report(conn: Any, *, limit: int = 20) -> str:
    db_rows = conn.execute(
        """
        SELECT * FROM market_events_trader_performance_f6
        WHERE signals_count >= ?
        ORDER BY author_score DESC, win_rate DESC
        LIMIT ?
        """,
        (F6_MIN_SIGNALS, limit),
    ).fetchall()
    worst = conn.execute(
        """
        SELECT * FROM market_events_trader_performance_f6
        WHERE signals_count >= ?
        ORDER BY author_score ASC, win_rate ASC
        LIMIT ?
        """,
        (F6_MIN_SIGNALS, min(limit, 10)),
    ).fetchall()

    lines = ["TRADER RANKING REPORT (F.6)", "", "Top authors:"]
    for i, row in enumerate(db_rows, 1):
        lines.append(
            f"  {i}. {_display_channel(row['channel_name'])} — "
            f"WR {float(row['win_rate']) * 100:.0f}%  "
            f"RR {float(row['avg_rr']):.1f}  "
            f"score {float(row['author_score']):+.2f}  "
            f"n={int(row['signals_count'])}"
        )
    lines.extend(["", "Weakest authors:"])
    for row in worst:
        lines.append(
            f"  • {_display_channel(row['channel_name'])} — "
            f"WR {float(row['win_rate']) * 100:.0f}%  "
            f"score {float(row['author_score']):+.2f}  "
            f"n={int(row['signals_count'])}"
        )
    if not db_rows:
        lines.append("  (insufficient data — need more closed paper trades)")
    return "\n".join(lines)
