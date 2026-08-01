"""Build candle-movie frames + snapshot recovery for one trade."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)
from bot.research.market_events.signal_intelligence.market_replay_v1.dataset import (
    nearest_by_ts,
)
from bot.research.market_events.signal_intelligence.market_replay_v1.timeline import (
    REPLAY_OFFSETS_MIN,
    SNAPSHOT_FIELDS,
    offset_label,
)


def _blend(a: float | None, b: float | None, w: float) -> float | None:
    if a is None and b is None:
        return None
    if a is None:
        return b
    if b is None:
        return a
    return (1.0 - w) * float(a) + w * float(b)


def recover_frame(
    trade: dict[str, Any],
    *,
    offset_min: int,
    candle: dict[str, Any] | None,
    snap: dict[str, Any] | None,
    pre_candle: dict[str, Any] | None = None,
    pre_snap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recover one timeline frame from lake features + nearest candle/snap."""
    entry_ts = int(trade.get("entry_ts") or 0)
    ts = entry_ts + int(offset_min) * 60
    # Weight toward entry features near ENTRY; toward market data farther away.
    w_entry = max(0.0, 1.0 - abs(offset_min) / 60.0)

    price = _safe_float((candle or {}).get("close"))
    if price is None:
        price = _safe_float(trade.get("entry")) if offset_min <= 0 else _safe_float(trade.get("exit"))
    volume = _safe_float((candle or {}).get("volume"))
    if volume is None:
        volume = _safe_float(trade.get("volume"))

    oi = _safe_float((snap or {}).get("oi"))
    if oi is None:
        oi = _safe_float(trade.get("open_interest") or trade.get("oi"))
    funding = _safe_float((snap or {}).get("funding"))
    if funding is None:
        funding = _safe_float(trade.get("funding"))
    funding_delta = _safe_float(trade.get("funding_delta"))
    if funding_delta is None and snap and pre_snap:
        f0 = _safe_float(pre_snap.get("funding"))
        f1 = _safe_float(snap.get("funding"))
        if f0 is not None and f1 is not None:
            funding_delta = f1 - f0

    fear = _safe_float((snap or {}).get("fear_greed"))
    if fear is None:
        fear = _safe_float(trade.get("fear_greed"))
    dom = _safe_float((snap or {}).get("dominance"))
    if dom is None:
        dom = _safe_float(trade.get("btc_dominance"))

    atr = _blend(_safe_float(trade.get("atr")), _safe_float((snap or {}).get("atr")), 1.0 - w_entry)
    # Distances on lake are relative; store as feature proxies for EMA/VWAP levels
    ema20 = _safe_float(trade.get("ema20_distance"))
    ema50 = _safe_float(trade.get("ema50_distance"))
    ema200 = _safe_float(trade.get("ema200_distance"))
    vwap = _safe_float(trade.get("vwap_distance"))
    # If we have price, synthesize absolute-ish proxies
    if price is not None:
        if ema20 is not None:
            ema20 = price * (1.0 + float(ema20) / 100.0) if abs(float(ema20)) < 50 else price + float(ema20)
        if ema50 is not None:
            ema50 = price * (1.0 + float(ema50) / 100.0) if abs(float(ema50)) < 50 else price + float(ema50)
        if ema200 is not None:
            ema200 = price * (1.0 + float(ema200) / 100.0) if abs(float(ema200)) < 50 else price + float(ema200)
        if vwap is not None:
            vwap = price * (1.0 + float(vwap) / 100.0) if abs(float(vwap)) < 50 else price + float(vwap)

    frame = {
        "label": offset_label(offset_min),
        "offset_min": offset_min,
        "ts": ts,
        "price": price,
        "volume": volume,
        "oi": oi,
        "funding": funding,
        "funding_delta": funding_delta,
        "fear_greed": fear,
        "btc_dominance": dom,
        "atr": atr if atr is not None else _safe_float(trade.get("atr_pct")),
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "vwap": vwap,
        "macd": _safe_float(trade.get("macd")),
        "adx": _safe_float(trade.get("adx")),
        "rsi": _safe_float(trade.get("rsi")),
        "stochastic": _safe_float(trade.get("stoch_k") or trade.get("stochastic")),
        "news_score": _safe_float(trade.get("news_score")),
        "ai_score": _safe_float(trade.get("ai_score")),
        "pattern": trade.get("pattern") or None,
        "optimizer_state": trade.get("optimizer_state") if offset_min == 0 else None,
        "gate_decision": trade.get("gate_decision") if offset_min == 0 else None,
        "alpha_cluster": trade.get("alpha_cluster") if offset_min == 0 else None,
        "edge_cluster": trade.get("edge_cluster") if offset_min == 0 else None,
        "regime": trade.get("regime") or None,
        "candle": {
            "open": _safe_float((candle or {}).get("open")),
            "high": _safe_float((candle or {}).get("high")),
            "low": _safe_float((candle or {}).get("low")),
            "close": _safe_float((candle or {}).get("close")),
            "volume": volume,
        } if candle else None,
        "source": {
            "candle": bool(candle),
            "snapshot_g3": bool(snap),
            "lake_features": True,
        },
    }

    missing = [f for f in SNAPSHOT_FIELDS if frame.get(f) is None]
    # optimizer/gate/alpha/edge only expected at ENTRY
    if offset_min != 0:
        for f in ("optimizer_state", "gate_decision", "alpha_cluster", "edge_cluster"):
            if f in missing:
                missing.remove(f)
    frame["missing"] = missing
    frame["coverage"] = round(1.0 - len(missing) / max(1, len(SNAPSHOT_FIELDS)), 4)
    return frame


def build_market_frames(
    trade: dict[str, Any],
    *,
    candles: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    offsets: tuple[int, ...] = REPLAY_OFFSETS_MIN,
) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    entry_ts = int(trade.get("entry_ts") or 0)
    for off in offsets:
        ts = entry_ts + int(off) * 60
        candle = nearest_by_ts(candles, ts, key="open_ts") if candles else None
        snap = nearest_by_ts(snapshots, ts, key="snapshot_ts") if snapshots else None
        pre_ts = ts - 300
        pre_candle = nearest_by_ts(candles, pre_ts, key="open_ts") if candles else None
        pre_snap = nearest_by_ts(snapshots, pre_ts, key="snapshot_ts") if snapshots else None
        # Reject candle/snap if too far (>2h)
        if candle and abs(int(candle.get("open_ts") or 0) - ts) > 7200:
            candle = None
        if snap and abs(int(snap.get("snapshot_ts") or 0) - ts) > 7200:
            snap = None
        frames.append(recover_frame(
            trade,
            offset_min=off,
            candle=candle,
            snap=snap,
            pre_candle=pre_candle,
            pre_snap=pre_snap,
        ))
    return frames


def future_path(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"label": f["label"], "offset_min": f["offset_min"], "price": f.get("price"), "ts": f.get("ts")}
        for f in frames if int(f.get("offset_min") or 0) >= 0
    ]


def timeline_index(frames: list[dict[str, Any]]) -> dict[str, Any]:
    return {str(f["label"]): {"ts": f.get("ts"), "price": f.get("price"), "coverage": f.get("coverage")} for f in frames}


__all__ = ["build_market_frames", "future_path", "recover_frame", "timeline_index"]
