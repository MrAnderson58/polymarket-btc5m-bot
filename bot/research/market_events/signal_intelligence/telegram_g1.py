"""Phase G.1 — human-readable Telegram format."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.telegram_f3 import format_symbol_usdt


@dataclass(frozen=True)
class LiquidityTrendTelegramG1:
    rendered: str


def render_liquidity_trend_telegram_g1(
    *,
    symbol: str,
    signal_type: str,
    headline: str,
    window_label: str,
    streak_line: str,
    move_pct: float,
    funding_line: str | None,
    oi_line: str | None,
    volume_line: str | None,
    historical_rate: float | None,
    reversal_probability: float,
    plan_action: str = "Ждать R2",
) -> str:
    sym = format_symbol_usdt(symbol)
    lines = [
        f"🚨 {sym}",
        "",
        headline,
        "",
        window_label,
        streak_line,
        f"{move_pct:+.1f}%",
    ]
    if funding_line:
        lines.extend(["", "Funding", funding_line])
    if oi_line:
        lines.extend(["", "OI", oi_line])
    if volume_line:
        lines.extend(["", "Объём", volume_line])
    if historical_rate is not None:
        lines.extend([
            "",
            "Исторически",
            f"{historical_rate * 100:.0f}%",
            "после такого происходил откат.",
        ])
    lines.extend([
        "",
        "Вероятность отката",
        f"{reversal_probability * 100:.0f}%",
        "",
        "План",
        plan_action,
    ])
    return "\n".join(lines)


def build_telegram_from_g1(conn: Any, event_id: int) -> str | None:
    row = conn.execute(
        "SELECT * FROM market_events_liquidity_trend_g1 WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if not row:
        return None
    if row["telegram_rendered"]:
        return str(row["telegram_rendered"])

    import json
    symbol = str(row["symbol"])
    signal_type = str(row["signal_type"])
    rev_prob = float(row["reversal_probability"] or 0.5)
    hist = float(row["historical_reversal_rate"]) if row["historical_reversal_rate"] else None

    headline_map = {
        "CAPITULATION": "Похоже на капитуляцию",
        "LIQUIDITY_ACCUMULATION": "Накопление ликвидности",
        "SLOW_TREND_SHOCK": "Медленный тренд-шок",
    }
    headline = headline_map.get(signal_type, "Целенаправленное движение")

    window_label = "15m"
    streak_line = ""
    move_pct = 0.0
    funding_line = oi_line = volume_line = None

    try:
        patterns = json.loads(row["consecutive_pattern_json"] or "[]")
        if patterns:
            p = max(patterns, key=lambda x: x.get("max_streak", 0))
            wm = int(p.get("window_minutes", 15))
            window_label = f"{wm}m" if wm < 60 else f"{wm // 60}h"
            color = "красных" if p.get("max_streak_color") == "red" else "зелёных"
            streak_line = f"{p.get('max_streak', 0)} {color} свечей подряд"
    except (json.JSONDecodeError, TypeError):
        pass

    try:
        windows = json.loads(row["mtf_windows_json"] or "[]")
        if windows:
            w = max(windows, key=lambda x: abs(x.get("cumulative_return_pct", 0)))
            move_pct = float(w.get("cumulative_return_pct", 0))
            if not streak_line:
                window_label = f"{w.get('window_minutes', 15)}m"
    except (json.JSONDecodeError, TypeError):
        pass

    try:
        liq = json.loads(row["liquidity_accum_json"] or "null")
        if liq:
            funding_line = "↓" if liq.get("funding_negative") else "→"
            oi_line = "↑" if liq.get("oi_rising") else "→"
            vol = liq.get("volume_rising")
            volume_line = "растёт" if vol else None
    except (json.JSONDecodeError, TypeError):
        pass

    try:
        cap = json.loads(row["capitulation_json"] or "null")
        if cap:
            volume_line = f"{cap.get('volume_multiple', 0):.1f}×"
    except (json.JSONDecodeError, TypeError):
        pass

    return render_liquidity_trend_telegram_g1(
        symbol=symbol,
        signal_type=signal_type,
        headline=headline,
        window_label=window_label,
        streak_line=streak_line or "—",
        move_pct=move_pct,
        funding_line=funding_line,
        oi_line=oi_line,
        volume_line=volume_line,
        historical_rate=hist,
        reversal_probability=rev_prob,
        plan_action=str(row["plan_action"] or "Ждать R2"),
    )
