"""Per-trade multi-horizon timeline snapshots (research-only)."""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.market_fingerprint_v1.snapshots import (
    _idx_at,
    _safe,
    _window_stats,
    load_candle_book,
)

# Full timeline checkpoints relative to entry (Entry = 0).
TIMELINE_WINDOWS: tuple[tuple[str, int], ...] = (
    ("24h", 24 * 3600),
    ("12h", 12 * 3600),
    ("8h", 8 * 3600),
    ("4h", 4 * 3600),
    ("2h", 2 * 3600),
    ("1h", 3600),
    ("30m", 1800),
    ("15m", 900),
    ("10m", 600),
    ("5m", 300),
    ("3m", 180),
    ("1m", 60),
    ("Entry", 0),
)

# Chain fingerprint uses a compact path through formation.
CHAIN_WINDOWS: tuple[str, ...] = ("24h", "12h", "4h", "1h", "15m", "5m", "Entry")

# Numeric vector keys per checkpoint (compact).
POINT_KEYS: tuple[str, ...] = (
    "ret",
    "slope",
    "atr_pct",
    "bb_width",
    "rsi",
    "macd_hist",
    "stoch",
    "ema20_dist",
    "ema50_dist",
    "rvol",
    "vol_delta",
)


def _parse(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def _point_state(
    book: dict[str, np.ndarray],
    idx: int,
    *,
    lookback_sec: int = 3600,
) -> dict[str, Any]:
    """Indicators as-of candle idx, plus short lookback price/volume stats."""
    def _at(key: str) -> float | None:
        arr = book.get(key)
        if arr is None or idx >= len(arr) or idx < 0:
            return None
        v = float(arr[idx])
        return None if v != v else v

    close = _at("close")
    ema20, ema50, ema200 = _at("ema20"), _at("ema50"), _at("ema200")
    with np.errstate(divide="ignore", invalid="ignore"):
        ema20_dist = None if not close or not ema20 else 100.0 * (close - ema20) / close
        ema50_dist = None if not close or not ema50 else 100.0 * (close - ema50) / close
        ema200_dist = None if not close or not ema200 else 100.0 * (close - ema200) / close

    lb = max(300, int(lookback_sec))
    w = _window_stats(book, idx, lb)
    return {
        "price": close,
        "ret": w.get("change_pct"),
        "high": w.get("high"),
        "low": w.get("low"),
        "range": w.get("range"),
        "slope": w.get("slope"),
        "accel": w.get("accel"),
        "volatility": w.get("volatility"),
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "ema20_gt_ema50": (
            True if ema20 is not None and ema50 is not None and ema20 > ema50
            else (False if ema20 is not None and ema50 is not None else None)
        ),
        "ema50_gt_ema200": (
            True if ema50 is not None and ema200 is not None and ema50 > ema200
            else (False if ema50 is not None and ema200 is not None else None)
        ),
        "distance_ema20": ema20_dist,
        "distance_ema50": ema50_dist,
        "distance_ema200": ema200_dist,
        "atr": _at("atr"),
        "atr_pct": _at("atr_pct"),
        "bb_width": _at("bb_width"),
        "bb_pos": _at("bb_pos"),
        "adx": None,  # not in candle book; filled from trade feats at Entry
        "rsi": _at("rsi"),
        "macd": _at("macd"),
        "macd_hist": _at("macd_hist"),
        "stochastic": _at("stoch"),
        "vwap_distance": None,
        "volume": w.get("volume"),
        "volume_delta": w.get("delta_volume"),
        "relative_volume": w.get("rel_volume"),
        "delta_volume": w.get("delta_volume"),
        # compact aliases for vectors
        "ema20_dist": ema20_dist,
        "ema50_dist": ema50_dist,
        "rvol": w.get("rel_volume"),
        "vol_delta": w.get("delta_volume"),
        "stoch": _at("stoch"),
    }


def timeline_trade(
    trade: dict[str, Any],
    book_by_sym: dict[str, dict[str, np.ndarray]],
) -> dict[str, Any] | None:
    """Rebuild market timeline BEFORE and AT entry for one CLOSED trade."""
    symbol = str(trade.get("symbol") or "")
    opened = trade.get("opened_at") or trade.get("closed_at")
    try:
        opened_i = int(opened) if opened is not None else None
    except Exception:
        opened_i = None
    if not symbol or opened_i is None:
        return None
    book = book_by_sym.get(symbol)
    if not book:
        return None
    entry_idx = _idx_at(book["ts"], opened_i)
    if entry_idx is None:
        return None

    feats = _parse(trade.get("features_json"))
    macro = trade.get("macro") if isinstance(trade.get("macro"), dict) else _parse(trade.get("macro_json"))
    news = trade.get("news") if isinstance(trade.get("news"), dict) else _parse(trade.get("news_json"))

    funding = _safe(trade.get("funding") if trade.get("funding") is not None else feats.get("funding") or macro.get("funding"))
    funding_delta = _safe(feats.get("funding_delta") or macro.get("funding_delta"))
    oi = _safe(feats.get("open_interest") or macro.get("open_interest") or trade.get("open_interest"))
    oi_delta = _safe(trade.get("oi_delta") if trade.get("oi_delta") is not None else feats.get("oi_delta"))
    ls_ratio = _safe(feats.get("long_short_ratio") or macro.get("long_short_ratio"))
    fear = _safe(trade.get("fear_greed") if trade.get("fear_greed") is not None else feats.get("fear_greed") or macro.get("fear_greed"))
    news_score = _safe(trade.get("news_score") if trade.get("news_score") is not None else news.get("news_score"))
    narrative = str(news.get("narrative") or feats.get("narrative") or trade.get("narrative") or "")[:80] or None
    btc_dom = _safe(trade.get("btc_dominance") if trade.get("btc_dominance") is not None else feats.get("btc_dominance") or macro.get("btc_dominance"))
    btc_trend = _safe(feats.get("btc_trend") or macro.get("btc_trend"))
    eth_trend = _safe(feats.get("eth_trend") or macro.get("eth_trend"))
    adx_entry = _safe(feats.get("adx") or trade.get("adx"))
    liq = _safe(feats.get("liquidations") or macro.get("liquidations"))
    nearest_liq = _safe(feats.get("nearest_liquidity") or macro.get("nearest_liquidity"))
    liq_dist = _safe(feats.get("liquidity_distance") or macro.get("liquidity_distance"))
    sweep = _safe(feats.get("sweep") or macro.get("sweep"))

    market_ctx = {
        "funding": funding,
        "funding_delta": funding_delta,
        "open_interest": oi,
        "oi_delta": oi_delta,
        "long_short_ratio": ls_ratio,
        "btc_trend": btc_trend,
        "eth_trend": eth_trend,
        "btc_dominance": btc_dom,
        "fear_greed": fear,
        "news_score": news_score,
        "narrative": narrative,
        "regime": str(trade.get("regime") or trade.get("market_regime") or feats.get("regime") or "UNK"),
        "nearest_liquidity": nearest_liq,
        "liquidity_distance": liq_dist,
        "sweep": sweep,
        "liquidations": liq,
        "adx": adx_entry,
    }

    windows: dict[str, dict[str, Any]] = {}
    for name, offset in TIMELINE_WINDOWS:
        ts_target = opened_i - int(offset)
        idx = _idx_at(book["ts"], ts_target)
        if idx is None:
            continue
        # lookback for ret/slope scales with remaining time to entry (min 15m)
        lookback = max(900, min(offset if offset > 0 else 900, 4 * 3600))
        state = _point_state(book, idx, lookback_sec=lookback)
        # attach market/derivatives context (point-in-time unavailable historically → entry feats)
        state.update({
            "funding": funding,
            "funding_delta": funding_delta,
            "open_interest": oi,
            "oi_delta": oi_delta,
            "long_short_ratio": ls_ratio,
            "btc_trend": btc_trend,
            "eth_trend": eth_trend,
            "btc_dominance": btc_dom,
            "fear_greed": fear,
            "news_score": news_score,
            "narrative": narrative,
            "regime": market_ctx["regime"],
            "nearest_liquidity": nearest_liq,
            "liquidity_distance": liq_dist,
            "sweep": sweep,
            "liquidations": liq,
            "adx": adx_entry if name == "Entry" else None,
            "ts": int(book["ts"][idx]),
            "offset_sec": int(offset),
        })
        windows[name] = state

    if "Entry" not in windows:
        return None

    pnl = _safe(trade.get("pnl") if trade.get("pnl") is not None else trade.get("pnl_pct")) or 0.0
    result = str(trade.get("result") or "").upper()
    if not result:
        result = "WIN" if pnl > 0.05 else ("LOSS" if pnl < -0.05 else "BE")
    direction = str(trade.get("direction") or "").upper()
    hold = _safe(trade.get("holding_seconds") or feats.get("duration_sec"))
    exit_reason = str(trade.get("exit_reason") or feats.get("exit_reason") or "")[:64] or None

    # Compact numeric chain vector (concat of CHAIN_WINDOWS × POINT_KEYS)
    vec: list[float | None] = []
    for wname in CHAIN_WINDOWS:
        st = windows.get(wname) or {}
        for k in POINT_KEYS:
            vec.append(st.get(k) if isinstance(st.get(k), (int, float)) else None)

    return {
        "trade_id": int(trade.get("trade_id") or trade.get("id") or 0),
        "symbol": symbol,
        "direction": direction,
        "opened_at": opened_i,
        "closed_at": int(trade.get("closed_at") or opened_i),
        "pnl": pnl,
        "result": result,
        "regime": market_ctx["regime"],
        "hold_sec": hold,
        "exit_reason": exit_reason,
        "windows": windows,
        "market": market_ctx,
        "vector": vec,
        "candle_hit": True,
    }


def vector_matrix(rows: list[dict[str, Any]]) -> np.ndarray:
    dim = len(CHAIN_WINDOWS) * len(POINT_KEYS)
    if not rows:
        return np.zeros((0, dim))
    mat = np.array(
        [[(np.nan if v is None else float(v)) for v in r["vector"]] for r in rows],
        dtype=float,
    )
    for j in range(mat.shape[1]):
        col = mat[:, j]
        med = float(np.nanmedian(col)) if np.any(np.isfinite(col)) else 0.0
        col[~np.isfinite(col)] = med
        mat[:, j] = col
    return mat


__all__ = [
    "CHAIN_WINDOWS",
    "POINT_KEYS",
    "TIMELINE_WINDOWS",
    "load_candle_book",
    "timeline_trade",
    "vector_matrix",
]
