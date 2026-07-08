"""Snapshot timestamp integrity audit."""

from __future__ import annotations

import json
from typing import Any

from bot.research.futures_agent.config import CONTEXT_SYMBOL_BTC
from bot.research.futures_agent.features import candles_at_or_before, latest_candle_ts
from bot.research.futures_agent.market_provider import BinanceMarketProvider, MarketDataProvider, symbol_pair


def format_snapshot_audit(conn: Any, signal_id: int, *, provider: MarketDataProvider | None = None) -> str:
    row = conn.execute(
        """
        SELECT s.symbol, i.received_at
        FROM futures_agent_signals s
        JOIN futures_agent_inputs i ON i.id = s.input_id
        WHERE s.id = ?
        """,
        (signal_id,),
    ).fetchone()
    if not row:
        return f"Signal {signal_id} not found."

    signal_t = int(row["received_at"])
    pair = symbol_pair(row["symbol"] or "BTC")

    alt_snap = conn.execute(
        """
        SELECT snapshot_ts, symbol, raw_metadata_json
        FROM futures_agent_market_snapshots
        WHERE signal_id = ? AND symbol = ?
        ORDER BY id DESC LIMIT 1
        """,
        (signal_id, pair),
    ).fetchone()
    if not alt_snap:
        alt_snap = conn.execute(
            """
            SELECT snapshot_ts, symbol, raw_metadata_json
            FROM futures_agent_market_snapshots
            WHERE signal_id = ?
            ORDER BY id LIMIT 1
            """,
            (signal_id,),
        ).fetchone()

    btc_ctx = conn.execute(
        "SELECT snapshot_ts FROM futures_agent_btc_context WHERE signal_id = ?",
        (signal_id,),
    ).fetchone()

    alt_meta = _parse_meta(alt_snap)
    stored_alt_latest = alt_meta.get("latest_market_timestamp")
    stored_btc_latest = alt_meta.get("btc_latest_market_timestamp")

    provider = provider or BinanceMarketProvider()
    alt_candles = provider.fetch_spot_klines(pair, "1m", signal_t, limit=1500)
    if not alt_candles:
        alt_candles = provider.fetch_futures_klines(pair, "1m", signal_t, limit=1500)
    btc_candles = provider.fetch_spot_klines("BTCUSDT", "1m", signal_t, limit=1500)

    alt_latest = latest_candle_ts(alt_candles, signal_t) or stored_alt_latest
    btc_latest = latest_candle_ts(btc_candles, signal_t) or stored_btc_latest

    alt_lag = signal_t - alt_latest if alt_latest else None
    btc_lag = signal_t - btc_latest if btc_latest else None

    lookahead = _lookahead_violations(alt_candles, btc_candles, signal_t)
    stale_flags = _stale_flags(signal_t, alt_latest, btc_latest, alt_lag, btc_lag, alt_meta)

    lines = [
        "SNAPSHOT AUDIT",
        f"signal_id: {signal_id}",
        "",
        f"SIGNAL T: {signal_t}",
        f"ALT latest data T: {alt_latest or '—'}",
        f"BTC latest data T: {btc_latest or '—'}",
        f"ALT lag: {alt_lag if alt_lag is not None else '—'}s",
        f"BTC lag: {btc_lag if btc_lag is not None else '—'}s",
        "",
        "LOOKAHEAD VIOLATIONS",
    ]
    if lookahead:
        for v in lookahead:
            lines.append(f"  {v}")
    else:
        lines.append("  none")

    lines.extend(["", "STALE DATA FLAGS"])
    if stale_flags:
        for f in stale_flags:
            lines.append(f"  {f}")
    else:
        lines.append("  none")

    lines.extend([
        "",
        "METADATA",
        f"  provider: {alt_meta.get('provider', '—')}",
        f"  requested_at: {alt_meta.get('requested_at', '—')}",
        f"  observations_used: {alt_meta.get('observations_used', '—')}",
        f"  correlation_sample_n: {alt_meta.get('correlation_sample_n', '—')}",
        f"  beta_sample_n: {alt_meta.get('beta_sample_n', '—')}",
        f"  signal_market_status: {alt_meta.get('signal_market_status', '—')}",
        f"  alignment_label: {alt_meta.get('alignment_label', '—')}",
    ])
    return "\n".join(lines)


def _parse_meta(row: Any) -> dict:
    from bot.research.futures_agent.research_utils import normalize_json_object

    if not row:
        return {}
    return normalize_json_object(row["raw_metadata_json"])


def _lookahead_violations(alt_candles: list, btc_candles: list, signal_t: int) -> list[str]:
    violations: list[str] = []
    for label, candles in (("ALT", alt_candles), ("BTC", btc_candles)):
        for c in candles:
            ts = int(c[0] // 1000)
            if ts > signal_t:
                violations.append(f"{label} candle ts={ts} > signal T={signal_t}")
    return violations[:10]


def _stale_flags(
    signal_t: int,
    alt_latest: int | None,
    btc_latest: int | None,
    alt_lag: int | None,
    btc_lag: int | None,
    meta: dict,
) -> list[str]:
    flags: list[str] = []
    max_lag = 120
    if alt_lag is not None and alt_lag > max_lag:
        flags.append(f"ALT data stale: lag {alt_lag}s > {max_lag}s")
    if btc_lag is not None and btc_lag > max_lag:
        flags.append(f"BTC data stale: lag {btc_lag}s > {max_lag}s")
    if meta.get("signal_market_status") == "STALE_SIGNAL":
        flags.append("signal entry far from market (STALE_SIGNAL)")
    if meta.get("return_4h_insufficient_history"):
        flags.append("4h return computed with insufficient candle history")
    return flags
