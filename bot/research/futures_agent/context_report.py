"""Human-readable context report for a gated signal."""

from __future__ import annotations

import json
from typing import Any

from bot.research.futures_agent.config import CANONICAL_ALIGNMENT_LABELS


def format_context_report(conn: Any, signal_id: int) -> str:
    sig = conn.execute(
        """
        SELECT s.symbol, s.direction, s.entry_low, s.entry_high, s.stop_loss,
               i.raw_text, i.processing_status, i.status_detail
        FROM futures_agent_signals s
        JOIN futures_agent_inputs i ON i.id = s.input_id
        WHERE s.id = ?
        """,
        (signal_id,),
    ).fetchone()
    if not sig:
        return f"Signal {signal_id} not found."

    pair = f"{sig['symbol']}USDT" if sig["symbol"] else "?"
    alt = conn.execute(
        """
        SELECT * FROM futures_agent_market_snapshots
        WHERE signal_id = ? AND symbol = ?
        ORDER BY id DESC LIMIT 1
        """,
        (signal_id, pair),
    ).fetchone()
    if not alt:
        alt = conn.execute(
            """
            SELECT * FROM futures_agent_market_snapshots
            WHERE signal_id = ?
            ORDER BY id LIMIT 1
            """,
            (signal_id,),
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

    lines = ["SIGNAL", f"{sig['symbol'] or '?'} {sig['direction'] or ''}".strip(), ""]
    if sig["entry_low"] is not None:
        hi = sig["entry_high"]
        if hi is not None and hi != sig["entry_low"]:
            lines.append(f"entry: {sig['entry_low']:.4g}–{hi:.4g}")
        else:
            lines.append(f"entry: {sig['entry_low']:.4g}")

    if alt:
        price = alt["futures_price"] or alt["spot_price"]
        lines.extend([
            "",
            "ALT MARKET",
            f"price: {_fmt(price)}",
            f"5m / 15m / 1h / 4h trend: {_trend(alt['return_5m'])} / "
            f"{_trend(alt['return_15m'])} / {_trend(alt['return_1h'])} / "
            f"{_trend(alt['return_4h'])}",
            f"volatility (realized %/1m bar): {_fmt(alt['realized_vol'])}",
            f"distance from local high/low: {_fmt(alt['distance_from_local_high'])}% / "
            f"{_fmt(alt['distance_from_local_low'])}%",
            f"data quality: {alt['data_quality']}",
        ])
        if alt_meta.get("signal_market_status"):
            lines.append(f"signal vs market: {alt_meta['signal_market_status']}")
            if alt_meta.get("entry_distance_pct") is not None:
                lines.append(f"entry distance: {_fmt(alt_meta['entry_distance_pct'])}%")
    else:
        lines.extend(["", "ALT MARKET", "no snapshot — run: snapshot --signal-id"])

    if btc:
        lines.extend([
            "",
            "BTC CONTEXT",
            f"price: {_fmt(btc['btc_price'])}",
            f"5m / 15m / 1h / 4h trend: {_trend(btc['return_5m'])} / "
            f"{_trend(btc['return_15m'])} / {_trend(btc['return_1h'])} / "
            f"{_trend(btc['return_4h'])}",
            f"regime: {btc['market_regime']} (vol={btc['volatility_regime']})",
        ])
    else:
        lines.extend(["", "BTC CONTEXT", "no snapshot"])

    if rs:
        lines.extend([
            "",
            "RELATIVE STRENGTH",
            f"{rs['symbol']} vs BTC",
            f"5m / 15m / 1h excess return: {_fmt(rs['excess_return_5m'])}% / "
            f"{_fmt(rs['excess_return_15m'])}% / {_fmt(rs['excess_return_1h'])}%",
            f"correlation: {_fmt(rs['correlation_to_btc'])} (n={alt_meta.get('correlation_sample_n', '—')})",
            f"beta: {_fmt(rs['beta_to_btc'])} (n={alt_meta.get('beta_sample_n', '—')})",
            f"alignment: {align}",
        ])
    else:
        lines.extend(["", "RELATIVE STRENGTH", "no snapshot"])

    lines.extend([
        "",
        "Preliminary research label only — not a trading recommendation.",
        "No order is placed. Research mode.",
    ])
    return "\n".join(lines)


def _parse_meta(row: Any) -> dict:
    if not row:
        return {}
    raw = row["raw_metadata_json"]
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def _resolve_alignment(meta: dict, status_detail: str | None) -> str:
    label = meta.get("alignment_label")
    if label in CANONICAL_ALIGNMENT_LABELS:
        return label
    if status_detail and "align:" in status_detail:
        candidate = status_detail.split("align:")[-1].strip()
        if candidate in CANONICAL_ALIGNMENT_LABELS:
            return candidate
    stored_rs = meta.get("relative_strength_label")
    if stored_rs in CANONICAL_ALIGNMENT_LABELS:
        return stored_rs
    return "BTC_NEUTRAL"


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
