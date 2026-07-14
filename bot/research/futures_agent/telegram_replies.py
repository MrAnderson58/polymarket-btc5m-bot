"""Telegram reply formatting for Futures Agent inbound (Stage 1b)."""

from __future__ import annotations

from typing import Any

from bot.research.futures_agent.config import CANONICAL_ALIGNMENT_LABELS
from bot.research.futures_agent.context_report import _parse_meta, _resolve_alignment
from bot.research.futures_agent.pipeline import ProcessResult
from bot.research.futures_agent.snapshot import SnapshotResult


def format_telegram_rejected(proc: ProcessResult, *, raw_text: str | None = None) -> str:
    reason = proc.gate_reason or "did not pass gate"
    missing: list[str] = []
    if raw_text:
        try:
            from bot.research.market_events.signal_intelligence.telegram_inbound_g04 import (
                diagnose_signal_g04,
            )
            diag = diagnose_signal_g04(raw_text)
            missing = list(diag.missing)
            if diag.missing:
                reason = ", ".join(diag.missing)
        except Exception:
            pass
    lines = [
        "MESSAGE RECEIVED",
        f"Taxonomy: {proc.taxonomy or 'UNKNOWN'}",
        "Signal gate: rejected",
        f"Reason: {reason}",
    ]
    if missing:
        lines.append(f"Missing: {', '.join(missing)}")
    lines.extend([
        "",
        "Research mode. No order placed.",
    ])
    return "\n".join(lines)


def format_telegram_duplicate() -> str:
    return (
        "MESSAGE RECEIVED\n"
        "Status: duplicate (already processed)\n\n"
        "Research mode. No order placed."
    )


def format_telegram_unauthorized() -> str:
    return "Unauthorized chat. Message ignored."


def format_telegram_accepted(
    conn: Any,
    *,
    input_id: int,
    signal_id: int,
    snap: SnapshotResult | None,
) -> str:
    sig = conn.execute(
        """
        SELECT s.symbol, s.direction, s.entry_low, s.entry_high, s.stop_loss,
               i.status_detail
        FROM futures_agent_signals s
        JOIN futures_agent_inputs i ON i.id = s.input_id
        WHERE s.id = ?
        """,
        (signal_id,),
    ).fetchone()
    if not sig:
        return "SIGNAL ACCEPTED\nSignal data unavailable."

    tps = conn.execute(
        "SELECT target_price FROM futures_agent_targets WHERE signal_id = ? ORDER BY target_index",
        (signal_id,),
    ).fetchall()
    tp_str = " / ".join(f"{r['target_price']:.4g}" for r in tps) if tps else "—"

    pair = f"{sig['symbol']}USDT" if sig["symbol"] else "?"
    alt = conn.execute(
        """
        SELECT * FROM futures_agent_market_snapshots
        WHERE signal_id = ? AND symbol = ?
        ORDER BY id DESC LIMIT 1
        """,
        (signal_id, pair),
    ).fetchone()
    btc = conn.execute(
        "SELECT * FROM futures_agent_btc_context WHERE signal_id = ?",
        (signal_id,),
    ).fetchone()
    rs = conn.execute(
        "SELECT * FROM futures_agent_relative_strength WHERE signal_id = ?",
        (signal_id,),
    ).fetchone()

    alt_meta = _parse_meta(alt)
    align = _resolve_alignment(alt_meta, sig["status_detail"])

    lines = [
        "SIGNAL ACCEPTED",
        f"{sig['symbol'] or '?'} {sig['direction'] or ''}".strip(),
    ]
    if sig["entry_low"] is not None:
        hi = sig["entry_high"]
        if hi is not None and hi != sig["entry_low"]:
            lines.append(f"Entry: {sig['entry_low']:.4g}–{hi:.4g}")
        else:
            lines.append(f"Entry: {sig['entry_low']:.4g}")
    if sig["stop_loss"] is not None:
        lines.append(f"SL: {sig['stop_loss']:.4g}")
    lines.append(f"TP: {tp_str}")
    lines.append("")

    if alt:
        price = alt["futures_price"] or alt["spot_price"]
        lines.extend([
            "MARKET NOW",
            f"Price: {_fmt(price)}",
            f"Signal status: {alt_meta.get('signal_market_status', '—')}",
            "",
        ])
    elif snap and not snap.success:
        lines.extend([
            "MARKET NOW",
            f"Snapshot: failed ({snap.error or 'unknown'})",
            "Stage 1 signal preserved.",
            "",
        ])
    else:
        lines.extend(["MARKET NOW", "Snapshot pending", ""])

    if btc:
        lines.extend([
            "BTC CONTEXT",
            f"Regime: {btc['market_regime']}",
            f"Trend 15m / 1h / 4h: {_trend(btc['return_15m'])} / "
            f"{_trend(btc['return_1h'])} / {_trend(btc['return_4h'])}",
            "",
        ])

    if rs:
        lines.extend([
            "ALT vs BTC",
            f"Relative strength: {rs['relative_strength_label']}",
            f"Correlation: {_fmt(rs['correlation_to_btc'])}",
            f"Alignment: {align}",
            "",
        ])

    research = alt_meta.get("research_label") or (
        snap.research_label if snap and snap.research_label else "INSUFFICIENT_DATA"
    )
    lines.extend([
        "RESEARCH STATUS",
        f"Deterministic context: {research}",
        "LLM analysis: pending Stage 3",
        "",
        "Research mode. No order placed.",
    ])
    _assert_no_trading_recommendation(lines)
    return "\n".join(lines)


def _assert_no_trading_recommendation(lines: list[str]) -> None:
    joined = "\n".join(lines).lower()
    forbidden = ("buy now", "sell now", "place order", "open position", "take profit now")
    for phrase in forbidden:
        if phrase in joined:
            raise ValueError(f"reply must not contain trading recommendation: {phrase}")


def _fmt(val: Any) -> str:
    if val is None:
        return "—"
    if isinstance(val, float):
        return f"{val:.4g}"
    return str(val)


def _trend(return_pct: Any) -> str:
    if return_pct is None:
        return "—"
    r = float(return_pct)
    if r > 0.15:
        return "UP"
    if r < -0.15:
        return "DOWN"
    return "FLAT"
