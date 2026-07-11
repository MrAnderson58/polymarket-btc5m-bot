"""Phase F.2 Task I — premium Russian Telegram layout."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.market_event_alerts import PAPER_LABEL
from bot.research.market_events.signal_intelligence.correlation_f2 import VERDICT_BROKEN, VERDICT_CONFIRMED
from bot.research.market_events.signal_intelligence.signal_report_f2 import load_signal_report_f2

SEP = "━━━━━━━━━━━━"


def _funding_arrow(regime: str) -> str:
    if regime == "accelerating":
        return "↑"
    if regime == "flattening":
        return "↓"
    return "→"


def _oi_arrow(regime: str) -> str:
    if regime in ("rising", "divergence"):
        return "↑"
    if regime == "falling":
        return "↓"
    return "→"


def _peer_label(peers: list[dict[str, Any]], verdict: str) -> str:
    if verdict == VERDICT_BROKEN:
        return "корреляция нарушена"
    if verdict == VERDICT_CONFIRMED:
        return "подтверждается рынком"
    for p in peers:
        if p.get("peer") == "BTC" and p.get("return_pct") is not None:
            r = float(p["return_pct"])
            if abs(r) < 0.5:
                return "BTC нейтрален"
            return f"BTC {r:+.1f}%"
    return "рынок нейтрален"


def _window_label(seconds: int) -> str:
    if seconds >= 60:
        return f"{seconds // 60} минут"
    return f"{seconds} сек"


def _price_levels(conn: Any, event_id: int, target_pct: float, stop_pct: float) -> tuple[str, str]:
    row = conn.execute("SELECT direction FROM market_events WHERE id = ?", (event_id,)).fetchone()
    snap = conn.execute(
        """
        SELECT price FROM market_event_snapshots
        WHERE event_id = ? ORDER BY snapshot_ts DESC LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    price = float(snap["price"]) if snap and snap["price"] else None
    if not price:
        bars_row = conn.execute(
            """
            SELECT close FROM market_events_historical_candles c
            JOIN market_events e ON e.symbol = c.symbol
            WHERE e.id = ? AND c.venue = 'binance_futures' AND c.timeframe = '5m'
            ORDER BY c.open_ts DESC LIMIT 1
            """,
            (event_id,),
        ).fetchone()
        price = float(bars_row["close"]) if bars_row else None
    if not price:
        return f"{target_pct:+.1f}%", f"−{stop_pct:.1f}%"

    tp = target_pct / 100.0
    sp = stop_pct / 100.0
    direction = row["direction"] if row else "DOWN"
    if direction == "UP":
        target = price * (1 - tp)
        stop = price * (1 + sp)
    else:
        target = price * (1 + tp)
        stop = price * (1 - sp)
    return f"{target:.2f}", f"{stop:.2f}"


def render_premium_report(conn: Any, *, event_id: int, data: dict[str, Any]) -> str:
    sym = data["symbol"]
    ret = float(data["shock_return_pct"])
    window = _window_label(int(data.get("window_seconds") or 900))
    conf = float(data["confidence_score"])
    rev = int(float(data["reversal_probability"]) * 100)
    consensus = data["exchange_consensus"]
    vol = data["volume_label"]
    atr_p = int(float(data["atr_percentile"]))
    fund = data.get("funding_regime", "unknown")
    oi = data.get("oi_regime", "unknown")
    peers = data.get("correlation_peers") or []
    verdict = data.get("correlation_verdict", "")
    hist_n = int(data.get("historical_count") or 0)
    hist_rev = int(data.get("historical_reversal_count") or 0)
    plan = str(data.get("entry_recommendation", "WAIT_R2")).replace("_", " ")
    target_pct = float(data.get("expected_target_pct") or 0)
    stop_pct = float(data.get("expected_stop_pct") or 0)
    ai = data.get("ai_summary_v2_ru") or ""

    target, stop = _price_levels(conn, event_id, target_pct, stop_pct)

    lines = [
        f"🚨 {sym}",
        "",
        f"{ret:+.1f}% за {window}",
        "",
        SEP,
        "",
        "Уверенность",
        "",
        f"{conf:.1f} / 10",
        "",
        "Вероятность отката",
        "",
        f"{rev}%",
        "",
        SEP,
        "",
        "Рынок",
        "",
        f"✔ Consensus: {consensus}",
        f"✔ Volume: {vol}",
        f"✔ ATR: {atr_p} percentile",
        f"✔ Funding {_funding_arrow(fund)}",
        f"✔ OI {_oi_arrow(oi)}",
        f"✔ {_peer_label(peers, verdict)}",
        "",
        SEP,
        "",
        "История",
        "",
        f"{hist_n} похожих события" if hist_n != 1 else "1 похожее событие",
        f"{hist_rev} закончились откатом",
        "",
        SEP,
        "",
        "План",
        "",
        plan,
        "",
        "Target",
        "",
        target,
        "",
        "Stop",
        "",
        stop,
        "",
        SEP,
        "",
        "AI",
        "",
        ai.split("\n")[0] if ai else "—",
        "",
        "PAPER ONLY",
    ]
    return "\n".join(lines)


def format_shock_f2(conn: Any, event_id: int) -> str:
    report = load_signal_report_f2(conn, event_id)
    if report and report.telegram_rendered:
        return report.telegram_rendered
    row = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    if not row:
        return "SHOCK: событие не найдено"
    from bot.research.market_events.signal_intelligence.telegram_f1 import format_shock_f1
    return format_shock_f1(conn, event_id)


def format_entry_f2(
    conn: Any,
    *,
    event_id: int,
    symbol: str,
    reversal_variant: str,
    entry: float,
    stop: float,
    target: float,
) -> str:
    report = load_signal_report_f2(conn, event_id)
    conf = f"{report.confidence_score:.1f}/10" if report else "—"
    plan = report.entry_recommendation.replace("_", " ") if report else reversal_variant
    lines = [
        f"✅ ENTRY · {symbol}",
        reversal_variant,
        "",
        SEP,
        "",
        f"Entry {entry:.4g}",
        f"Stop {stop:.4g}",
        f"Target {target:.4g}",
        "",
        f"Уверенность {conf}",
        f"План {plan}",
        "",
        "PAPER ONLY",
    ]
    return "\n".join(lines)


def format_result_f2(
    conn: Any,
    *,
    event_id: int,
    symbol: str,
    pnl_pct: float,
    holding_min: int,
    exit_variant: str,
    ai_agreed: bool | None = None,
    hist_matched: bool | None = None,
) -> str:
    report = load_signal_report_f2(conn, event_id)
    conf = f"{report.confidence_score:.1f}/10" if report else "—"
    ai_label = "AI ✓" if ai_agreed else "AI ✗" if ai_agreed is False else "AI —"
    hist_label = "История ✓" if hist_matched else "История ✗" if hist_matched is False else "История —"
    lines = [
        f"🏁 RESULT · {symbol}",
        f"{pnl_pct:+.1f}%",
        "",
        SEP,
        "",
        f"Holding {holding_min}m · {exit_variant}",
        f"Уверенность {conf}",
        ai_label,
        hist_label,
        "",
        "PAPER ONLY",
    ]
    return "\n".join(lines)
