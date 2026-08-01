"""Liquidity evolution + event timeline helpers."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)


def liquidity_evolution(frames: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive expansion / imbalance proxies from pre-entry frames."""
    pre = [f for f in frames if int(f.get("offset_min") or 0) < 0]
    entry = next((f for f in frames if int(f.get("offset_min") or 0) == 0), None)
    if not pre:
        return {
            "orderbook_imbalance": None,
            "volume_expansion": None,
            "oi_expansion": None,
            "liquidation_clusters": None,
            "volatility_expansion": None,
            "ok": False,
        }

    vols = [_safe_float(f.get("volume")) for f in pre]
    ois = [_safe_float(f.get("oi")) for f in pre]
    atrs = [_safe_float(f.get("atr")) for f in pre]
    prices = [_safe_float(f.get("price")) for f in pre]

    def _exp(vals: list[float | None]) -> float | None:
        clean = [float(v) for v in vals if v is not None]
        if len(clean) < 2:
            return None
        a, b = clean[0], clean[-1]
        if abs(a) < 1e-12:
            return None
        return round((b - a) / abs(a), 4)

    # Imbalance proxy: signed return vs volume change over pre-window
    vol_exp = _exp(vols)
    oi_exp = _exp(ois)
    atr_exp = _exp(atrs)
    px_clean = [float(v) for v in prices if v is not None]
    ret = None
    if len(px_clean) >= 2 and abs(px_clean[0]) > 1e-12:
        ret = (px_clean[-1] - px_clean[0]) / abs(px_clean[0])
    imbalance = None
    if ret is not None and vol_exp is not None:
        imbalance = round(float(ret) * (1.0 + abs(float(vol_exp))), 4)

    liq_clusters = None
    if entry is not None:
        # Prefer snap liquidations if present on candle source path — use fear swing as proxy
        fears = [_safe_float(f.get("fear_greed")) for f in pre]
        fc = [float(v) for v in fears if v is not None]
        if len(fc) >= 2:
            liq_clusters = round(abs(fc[-1] - fc[0]), 4)

    return {
        "orderbook_imbalance": imbalance,
        "volume_expansion": vol_exp,
        "oi_expansion": oi_exp,
        "liquidation_clusters": liq_clusters,
        "volatility_expansion": atr_exp,
        "ok": True,
    }


def event_timeline(
    trade: dict[str, Any],
    frames: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach news/macro/BTC/funding/fear events onto the replay."""
    events: list[dict[str, Any]] = []
    entry = next((f for f in frames if int(f.get("offset_min") or 0) == 0), None)
    if entry:
        events.append({
            "type": "entry",
            "ts": entry.get("ts"),
            "label": "ENTRY",
            "detail": {
                "symbol": trade.get("symbol"),
                "direction": trade.get("direction"),
                "gate": trade.get("gate_decision"),
                "regime": trade.get("regime"),
            },
        })

    # Funding flips across frames
    prev_f = None
    for f in frames:
        fund = _safe_float(f.get("funding"))
        if fund is None:
            continue
        if prev_f is not None and ((prev_f < 0 <= fund) or (prev_f > 0 >= fund)):
            events.append({
                "type": "funding_flip",
                "ts": f.get("ts"),
                "label": f.get("label"),
                "detail": {"from": prev_f, "to": fund},
            })
        prev_f = fund

    # Fear changes
    prev_fear = None
    for f in frames:
        fear = _safe_float(f.get("fear_greed"))
        if fear is None:
            continue
        if prev_fear is not None and abs(fear - prev_fear) >= 5:
            events.append({
                "type": "fear_change",
                "ts": f.get("ts"),
                "label": f.get("label"),
                "detail": {"from": prev_fear, "to": fear, "delta": round(fear - prev_fear, 2)},
            })
        prev_fear = fear

    # News / AI / macro at entry from lake
    if _safe_float(trade.get("news_score")) is not None:
        events.append({
            "type": "news",
            "ts": trade.get("entry_ts"),
            "label": "ENTRY",
            "detail": {"news_score": trade.get("news_score"), "ai_score": trade.get("ai_score")},
        })
    if _safe_float(trade.get("btc_dominance")) is not None or _safe_float(trade.get("fear_greed")) is not None:
        events.append({
            "type": "macro",
            "ts": trade.get("entry_ts"),
            "label": "ENTRY",
            "detail": {
                "btc_dominance": trade.get("btc_dominance"),
                "fear_greed": trade.get("fear_greed"),
                "funding": trade.get("funding"),
            },
        })
    # BTC-centric marker
    if str(trade.get("symbol") or "").upper() in ("BTC", "BTCUSDT"):
        events.append({
            "type": "btc_event",
            "ts": trade.get("entry_ts"),
            "label": "ENTRY",
            "detail": {"symbol": "BTC", "direction": trade.get("direction")},
        })

    events.sort(key=lambda e: int(e.get("ts") or 0))
    return events


__all__ = ["event_timeline", "liquidity_evolution"]
