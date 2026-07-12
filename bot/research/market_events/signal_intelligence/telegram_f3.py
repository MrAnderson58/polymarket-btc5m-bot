"""Phase F.3 — high-quality Telegram signal v3 (10–15 sec decision layout)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.research.market_events.signal_intelligence.confidence_f1 import (
    ENTRY_READY,
    ENTRY_SCALE_IN_25,
    ENTRY_WAIT_R2,
    ENTRY_WAIT_R3,
)
from bot.research.market_events.signal_intelligence.market_structure_f2 import LABEL_SWEEP
from bot.research.market_events.signal_intelligence.signal_report_f2 import load_signal_report_f2

SEP = "━━━━━━━━━━━━━━"

CONF_HIGH = 7.0
CONF_MED = 4.0


@dataclass(frozen=True)
class SignalV3Context:
    symbol: str
    shock_return_pct: float
    window_minutes: int
    confidence_score: float
    reversal_probability: float
    rvol: float
    atr_percentile: float
    funding_regime: str
    oi_regime: str
    oi_change_pct: float | None
    structure_labels: list[str]
    correlation_peers: list[dict[str, Any]]
    historical_count: int
    historical_reversal_rate: float
    avg_reversal_minutes: int | None
    entry_recommendation: str
    ai_summary_ru: str


def confidence_emoji(score: float) -> str:
    if score >= CONF_HIGH:
        return "🟢"
    if score >= CONF_MED:
        return "🟡"
    return "🔴"


def format_symbol_usdt(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith("USDT"):
        return s
    return f"{s}USDT"


def reversal_pct(probability: float) -> int:
    p = probability
    if p <= 1.0:
        p *= 100.0
    return int(round(p))


def _peer_status(peer: str, peers: list[dict[str, Any]]) -> str:
    for p in peers:
        if p.get("peer") != peer:
            continue
        ret = p.get("return_pct")
        if ret is None:
            return "нет данных"
        if abs(float(ret)) < 0.5:
            return "стабильный"
        return f"{float(ret):+.1f}%"
    return "стабильный"


def _funding_label(regime: str) -> str:
    if regime == "accelerating":
        return "растёт"
    if regime == "flattening":
        return "снижается"
    return "нейтральный"


def _oi_label(change_pct: float | None, regime: str) -> str:
    if change_pct is not None:
        sign = "+" if change_pct >= 0 else ""
        return f"{sign}{change_pct:.0f}%"
    if regime == "rising":
        return "+↑"
    if regime == "falling":
        return "−↓"
    if regime == "divergence":
        return "дивергенция"
    return "нейтральный"


def _structure_headline(labels: list[str]) -> str | None:
    for label in labels:
        if label in (LABEL_SWEEP, "Break of Structure", "Change of Character", "Trend continuation"):
            return label
    for label in labels:
        if label not in ("range", "insufficient_data"):
            return label
    return None


def _factor_checklist(ctx: SignalV3Context) -> list[str]:
    lines = ["✔ Volume"]
    if any(p.get("peer") == "BTC" for p in ctx.correlation_peers) or ctx.symbol not in ("BTC",):
        lines.append("✔ BTC")
    lines.extend(["✔ Funding", "✔ OI", "✔ ATR", "✔ Structure"])
    return lines


def build_action_plan(entry: str, confidence: float) -> dict[str, str | None]:
    if entry == ENTRY_READY:
        return {"now": "Можно начинать набор", "after_r2": None, "after_r3": None}
    if entry == ENTRY_WAIT_R3 and confidence < CONF_MED:
        return {"now": "Лучше пропустить", "after_r2": None, "after_r3": None}
    if entry == ENTRY_WAIT_R3:
        return {"now": "Ждать R3", "after_r2": None, "after_r3": "50%"}
    if entry == ENTRY_SCALE_IN_25:
        return {"now": "Ждать R2", "after_r2": "25%", "after_r3": "50%"}
    return {"now": "Ждать R2", "after_r2": "25%", "after_r3": "50%"}


def _ai_one_liner(summary: str) -> str:
    for line in summary.split("\n"):
        line = line.strip()
        if line:
            return line
    return "Анализ недоступен."


def render_signal_v3(ctx: SignalV3Context) -> str:
    sym = format_symbol_usdt(ctx.symbol)
    emoji = confidence_emoji(ctx.confidence_score)
    rev = reversal_pct(ctx.reversal_probability)
    hist_pct = int(round(ctx.historical_reversal_rate * 100))
    plan = build_action_plan(ctx.entry_recommendation, ctx.confidence_score)
    structure = _structure_headline(ctx.structure_labels)

    happening: list[str] = [
        f"✔ BTC {_peer_status('BTC', ctx.correlation_peers)}",
        f"✔ ETH {_peer_status('ETH', ctx.correlation_peers)}",
        f"✔ Объем ×{ctx.rvol:.1f}",
        f"✔ ATR {int(ctx.atr_percentile)} percentile",
        f"✔ Funding {_funding_label(ctx.funding_regime)}",
        f"✔ OI {_oi_label(ctx.oi_change_pct, ctx.oi_regime)}",
    ]
    if structure:
        happening.append(f"✔ {structure}")

    history_lines = [
        f"{ctx.historical_count} похожих случаев",
        "",
        f"{hist_pct}% дали откат",
    ]
    if ctx.avg_reversal_minutes is not None:
        history_lines.extend(["", "Среднее время", f"{ctx.avg_reversal_minutes} минут"])

    plan_lines = ["Сейчас:", plan["now"]]
    if plan["after_r2"]:
        plan_lines.extend(["", "После R2:", plan["after_r2"]])
    if plan["after_r3"]:
        plan_lines.extend(["", "После R3:", plan["after_r3"]])

    lines = [
        f"🚨 {sym}",
        "",
        f"{ctx.shock_return_pct:+.1f}% за {ctx.window_minutes} минут",
        "",
        SEP,
        "",
        "Уверенность",
        f"{emoji} {ctx.confidence_score:.1f} / 10",
        "",
        "Вероятность отката",
        f"{rev}%",
        "",
        SEP,
        "",
        "Что происходит",
        "",
        *happening,
        "",
        *_factor_checklist(ctx),
        "",
        SEP,
        "",
        "История",
        "",
        *history_lines,
        "",
        SEP,
        "",
        "План",
        "",
        *plan_lines,
        "",
        SEP,
        "",
        "ИИ",
        "",
        _ai_one_liner(ctx.ai_summary_ru),
        "",
        SEP,
        "",
        "PAPER ONLY",
    ]
    return "\n".join(lines)


def _avg_reversal_minutes(conn: Any, event_ids: list[int]) -> int | None:
    if not event_ids:
        return None
    placeholders = ",".join("?" * len(event_ids))
    rows = conn.execute(
        f"""
        SELECT duration_seconds FROM paper_strategy_runs
        WHERE event_id IN ({placeholders}) AND duration_seconds IS NOT NULL AND duration_seconds > 0
        """,
        event_ids,
    ).fetchall()
    if not rows:
        return None
    secs = [int(r["duration_seconds"]) for r in rows]
    return max(1, int(round(sum(secs) / len(secs) / 60)))


def _oi_change_pct(conn: Any, event_id: int) -> float | None:
    row = conn.execute(
        """
        SELECT open_interest, oi_delta FROM market_events_funding_oi_history_f2
        WHERE event_id = ? AND timeframe = '5m' LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if not row or row["open_interest"] is None or row["oi_delta"] is None:
        return None
    oi = float(row["open_interest"])
    if oi <= 0:
        return None
    return float(row["oi_delta"]) / oi * 100.0


def build_v3_context(conn: Any, event_id: int) -> SignalV3Context | None:
    row = conn.execute("SELECT * FROM market_events WHERE id = ?", (event_id,)).fetchone()
    report = load_signal_report_f2(conn, event_id)
    if not row or not report:
        return None

    window_min = max(1, int(row["trigger_window_seconds"] or 900) // 60)
    peers = report.correlation_snapshot.get("peers") or []
    hist_ids = [int(m["event_id"]) for m in report.historical_matches if m.get("event_id")]

    return SignalV3Context(
        symbol=str(row["symbol"]),
        shock_return_pct=float(row["return_pct"] or 0),
        window_minutes=window_min,
        confidence_score=report.confidence_score,
        reversal_probability=report.reversal_probability,
        rvol=max(report.rvol_20, report.rvol_100),
        atr_percentile=report.atr_percentile,
        funding_regime=report.funding_regime,
        oi_regime=report.oi_regime,
        oi_change_pct=_oi_change_pct(conn, event_id),
        structure_labels=report.market_structure_labels,
        correlation_peers=peers,
        historical_count=report.historical_count,
        historical_reversal_rate=report.historical_reversal_rate,
        avg_reversal_minutes=_avg_reversal_minutes(conn, hist_ids),
        entry_recommendation=report.entry_recommendation,
        ai_summary_ru=report.ai_summary_v2_ru,
    )


def format_shock_f3(conn: Any, event_id: int) -> str:
    ctx = build_v3_context(conn, event_id)
    if ctx:
        return render_signal_v3(ctx)
    from bot.research.market_events.signal_intelligence.telegram_f2 import format_shock_f2
    return format_shock_f2(conn, event_id)


def format_entry_f3(
    conn: Any,
    *,
    event_id: int,
    symbol: str,
    reversal_variant: str,
    entry: float,
    stop: float,
    target: float,
) -> str:
    ctx = build_v3_context(conn, event_id)
    conf = f"{ctx.confidence_score:.1f}/10" if ctx else "—"
    emoji = confidence_emoji(ctx.confidence_score) if ctx else ""
    plan = build_action_plan(ctx.entry_recommendation, ctx.confidence_score)["now"] if ctx else reversal_variant
    lines = [
        f"✅ ENTRY · {format_symbol_usdt(symbol)}",
        reversal_variant,
        "",
        SEP,
        "",
        f"{emoji} Уверенность {conf}".strip(),
        f"План: {plan}",
        "",
        f"Entry {entry:.4g}",
        f"Stop {stop:.4g}",
        f"Target {target:.4g}",
        "",
        "PAPER ONLY",
    ]
    return "\n".join(lines)


def format_result_f3(
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
    ctx = build_v3_context(conn, event_id)
    conf = f"{ctx.confidence_score:.1f}/10" if ctx else "—"
    emoji = confidence_emoji(ctx.confidence_score) if ctx else ""
    rev = reversal_pct(ctx.reversal_probability) if ctx else "—"
    ai_label = "ИИ ✓" if ai_agreed else "ИИ ✗" if ai_agreed is False else "ИИ —"
    hist_label = "История ✓" if hist_matched else "История ✗" if hist_matched is False else "История —"
    lines = [
        f"🏁 RESULT · {format_symbol_usdt(symbol)}",
        f"{pnl_pct:+.1f}%",
        "",
        SEP,
        "",
        f"{emoji} Уверенность {conf}".strip(),
        f"Ожидали откат {rev}%" if ctx else "",
        f"Holding {holding_min}m · {exit_variant}",
        ai_label,
        hist_label,
        "",
        "PAPER ONLY",
    ]
    return "\n".join(line for line in lines if line is not None)
