"""Pre-entry market window snapshots for Fingerprint V1."""

from __future__ import annotations

import json
from typing import Any

import numpy as np

WINDOWS_SEC: tuple[tuple[str, int], ...] = (
    ("24h", 24 * 3600),
    ("12h", 12 * 3600),
    ("8h", 8 * 3600),
    ("4h", 4 * 3600),
    ("2h", 2 * 3600),
    ("1h", 3600),
    ("30m", 1800),
    ("15m", 900),
    ("5m", 300),
)

# Compact vector keys used for clustering / similarity (stable order).
VECTOR_KEYS: tuple[str, ...] = (
    # multi-horizon price change
    "chg_24h", "chg_12h", "chg_8h", "chg_4h", "chg_2h", "chg_1h", "chg_30m", "chg_15m", "chg_5m",
    # volatility / range
    "range_4h", "range_1h", "vol_4h", "vol_1h", "atr_pct", "bb_width", "bb_pos",
    # volume
    "rvol_1h", "vol_spike_1h", "dvol_1h",
    # trend
    "ema20_dist", "ema50_dist", "ema200_dist", "ema_cross_20_50", "adx", "slope_4h", "accel_1h",
    # momentum
    "rsi", "macd", "macd_hist", "stoch_k", "roc_1h", "mom_4h",
    # derivatives / market (from lake when present)
    "funding", "funding_delta", "oi_delta", "fear_greed", "news_score", "macro_score",
    "btc_dom",
)


def _safe(v: Any) -> float | None:
    try:
        if v is None:
            return None
        x = float(v)
        if x != x or abs(x) == float("inf"):
            return None
        return x
    except Exception:
        return None


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


def _ema(arr: np.ndarray, span: int) -> np.ndarray:
    out = np.full_like(arr, np.nan, dtype=float)
    if len(arr) == 0:
        return out
    alpha = 2.0 / (span + 1.0)
    out[0] = float(arr[0])
    for i in range(1, len(arr)):
        out[i] = alpha * float(arr[i]) + (1 - alpha) * out[i - 1]
    return out


def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full(len(close), np.nan)
    if len(close) < period + 1:
        return out
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_g = float(np.mean(gain[1 : period + 1]))
    avg_l = float(np.mean(loss[1 : period + 1]))
    out[period] = 100.0 if avg_l <= 1e-12 else 100.0 - (100.0 / (1.0 + avg_g / avg_l))
    for i in range(period + 1, len(close)):
        avg_g = (avg_g * (period - 1) + float(gain[i])) / period
        avg_l = (avg_l * (period - 1) + float(loss[i])) / period
        out[i] = 100.0 if avg_l <= 1e-12 else 100.0 - (100.0 / (1.0 + avg_g / avg_l))
    return out


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full(len(close), np.nan)
    if len(close) < 2:
        return out
    prev = np.roll(close, 1)
    prev[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    if len(tr) < period:
        return out
    out[period - 1] = float(np.mean(tr[:period]))
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def _macd(close: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    line = _ema(close, 12) - _ema(close, 26)
    signal = _ema(np.nan_to_num(line, nan=0.0), 9)
    hist = line - signal
    return line, signal, hist


def _stoch(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full(len(close), np.nan)
    for i in range(period - 1, len(close)):
        hh = float(np.max(high[i - period + 1 : i + 1]))
        ll = float(np.min(low[i - period + 1 : i + 1]))
        if hh - ll <= 1e-12:
            out[i] = 50.0
        else:
            out[i] = 100.0 * (float(close[i]) - ll) / (hh - ll)
    return out


def _bb(close: np.ndarray, period: int = 20) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mid = np.full(len(close), np.nan)
    upper = np.full(len(close), np.nan)
    lower = np.full(len(close), np.nan)
    for i in range(period - 1, len(close)):
        w = close[i - period + 1 : i + 1]
        m = float(np.mean(w))
        s = float(np.std(w))
        mid[i] = m
        upper[i] = m + 2 * s
        lower[i] = m - 2 * s
    return mid, upper, lower


def load_candle_book(conn: Any) -> dict[str, dict[str, np.ndarray]]:
    """Load 5m candles + precomputed series (batch)."""
    try:
        rows = conn.execute(
            """
            SELECT symbol, open_ts, open, high, low, close, COALESCE(volume, 0)
            FROM market_events_historical_candles
            WHERE timeframe = '5m'
            ORDER BY symbol ASC, open_ts ASC
            """
        ).fetchall()
    except Exception:
        return {}
    by_sym: dict[str, list[tuple[int, float, float, float, float, float]]] = {}
    for r in rows:
        by_sym.setdefault(str(r[0]), []).append(
            (int(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6] or 0))
        )
    book: dict[str, dict[str, np.ndarray]] = {}
    for sym, items in by_sym.items():
        ts = np.array([x[0] for x in items], dtype=np.int64)
        h = np.array([x[2] for x in items], dtype=float)
        l = np.array([x[3] for x in items], dtype=float)
        c = np.array([x[4] for x in items], dtype=float)
        v = np.array([x[5] for x in items], dtype=float)
        atr = _atr(h, l, c, 14)
        macd_l, macd_s, macd_h = _macd(c)
        bb_m, bb_u, bb_l = _bb(c, 20)
        with np.errstate(divide="ignore", invalid="ignore"):
            atr_pct = np.where(c > 0, 100.0 * atr / c, np.nan)
            bb_width = np.where(bb_m > 0, (bb_u - bb_l) / bb_m, np.nan)
            bb_pos = np.where((bb_u - bb_l) > 1e-12, (c - bb_l) / (bb_u - bb_l), np.nan)
        book[sym] = {
            "ts": ts,
            "high": h,
            "low": l,
            "close": c,
            "volume": v,
            "ema20": _ema(c, 20),
            "ema50": _ema(c, 50),
            "ema200": _ema(c, 200),
            "rsi": _rsi(c, 14),
            "atr": atr,
            "atr_pct": atr_pct,
            "macd": macd_l,
            "macd_signal": macd_s,
            "macd_hist": macd_h,
            "stoch": _stoch(h, l, c, 14),
            "bb_mid": bb_m,
            "bb_width": bb_width,
            "bb_pos": bb_pos,
        }
    return book


def _idx_at(ts_arr: np.ndarray, ts: int) -> int | None:
    if len(ts_arr) == 0:
        return None
    i = int(np.searchsorted(ts_arr, ts, side="right") - 1)
    if i < 0:
        return None
    if ts - int(ts_arr[i]) > 2 * 86400:
        return None
    return i


def _window_stats(
    book: dict[str, np.ndarray],
    i_end: int,
    seconds: int,
) -> dict[str, float | None]:
    ts = book["ts"]
    t_end = int(ts[i_end])
    t0 = t_end - int(seconds)
    i0 = int(np.searchsorted(ts, t0, side="left"))
    if i0 > i_end:
        i0 = i_end
    c = book["close"][i0 : i_end + 1]
    h = book["high"][i0 : i_end + 1]
    l = book["low"][i0 : i_end + 1]
    v = book["volume"][i0 : i_end + 1]
    if len(c) < 2:
        return {
            "change_pct": None, "high": None, "low": None, "range": None,
            "slope": None, "accel": None, "volatility": None,
            "volume": None, "delta_volume": None, "volume_spike": None, "rel_volume": None,
        }
    c0, c1 = float(c[0]), float(c[-1])
    change = None if abs(c0) < 1e-12 else 100.0 * (c1 - c0) / c0
    hh, ll = float(np.max(h)), float(np.min(l))
    rng = None if abs(c1) < 1e-12 else 100.0 * (hh - ll) / c1
    x = np.arange(len(c), dtype=float)
    slope = float(np.polyfit(x, c, 1)[0]) if len(c) >= 3 else float(c1 - c0)
    if len(c) >= 5:
        mid = len(c) // 2
        s1 = float(np.polyfit(x[:mid], c[:mid], 1)[0]) if mid >= 2 else slope
        s2 = float(np.polyfit(x[mid:], c[mid:], 1)[0]) if len(c) - mid >= 2 else slope
        accel = s2 - s1
    else:
        accel = 0.0
    rets = np.diff(c) / np.where(np.abs(c[:-1]) > 1e-12, c[:-1], np.nan)
    vol = float(np.nanstd(rets) * 100.0) if len(rets) else None
    vol_sum = float(np.sum(v))
    dvol = float(v[-1] - v[0]) if len(v) else None
    mean_v = float(np.mean(v[:-1])) if len(v) > 1 else float(v[0])
    spike = float(v[-1] / mean_v) if mean_v > 1e-12 else None
    # relative volume vs prior equal-length window
    span = i_end - i0 + 1
    j0 = max(0, i0 - span)
    prior = book["volume"][j0:i0]
    prior_sum = float(np.sum(prior)) if len(prior) else None
    rvol = (vol_sum / prior_sum) if prior_sum and prior_sum > 1e-12 else None
    return {
        "change_pct": change,
        "high": hh,
        "low": ll,
        "range": rng,
        "slope": slope,
        "accel": accel,
        "volatility": vol,
        "volume": vol_sum,
        "delta_volume": dvol,
        "volume_spike": spike,
        "rel_volume": rvol,
    }


def snapshot_trade(
    trade: dict[str, Any],
    book_by_sym: dict[str, dict[str, np.ndarray]],
) -> dict[str, Any] | None:
    """Rebuild pre-entry market state for one CLOSED trade."""
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
    idx = _idx_at(book["ts"], opened_i)
    if idx is None:
        return None

    feats = _parse(trade.get("features_json"))
    macro = trade.get("macro") if isinstance(trade.get("macro"), dict) else _parse(trade.get("macro_json"))
    news = trade.get("news") if isinstance(trade.get("news"), dict) else _parse(trade.get("news_json"))

    windows: dict[str, dict[str, float | None]] = {}
    for name, sec in WINDOWS_SEC:
        windows[name] = _window_stats(book, idx, sec)

    def _at(key: str) -> float | None:
        arr = book.get(key)
        if arr is None or idx >= len(arr):
            return None
        v = float(arr[idx])
        return None if v != v else v

    close = _at("close")
    ema20, ema50, ema200 = _at("ema20"), _at("ema50"), _at("ema200")
    with np.errstate(divide="ignore", invalid="ignore"):
        ema20_dist = None if not close or not ema20 else 100.0 * (close - ema20) / close
        ema50_dist = None if not close or not ema50 else 100.0 * (close - ema50) / close
        ema200_dist = None if not close or not ema200 else 100.0 * (close - ema200) / close

    # ROC / momentum from closes
    c_arr = book["close"]
    roc_1h = None
    mom_4h = None
    if idx >= 12 and abs(float(c_arr[idx - 12])) > 1e-12:
        roc_1h = 100.0 * (float(c_arr[idx]) - float(c_arr[idx - 12])) / float(c_arr[idx - 12])
    if idx >= 48 and abs(float(c_arr[idx - 48])) > 1e-12:
        mom_4h = 100.0 * (float(c_arr[idx]) - float(c_arr[idx - 48])) / float(c_arr[idx - 48])

    funding = _safe(trade.get("funding") if trade.get("funding") is not None else feats.get("funding") or macro.get("funding"))
    funding_delta = _safe(feats.get("funding_delta") or macro.get("funding_delta"))
    oi_delta = _safe(trade.get("oi_delta") if trade.get("oi_delta") is not None else feats.get("oi_delta"))
    fear = _safe(trade.get("fear_greed") if trade.get("fear_greed") is not None else feats.get("fear_greed") or macro.get("fear_greed"))
    news_score = _safe(trade.get("news_score") if trade.get("news_score") is not None else news.get("news_score"))
    macro_score = _safe(trade.get("macro_score") if trade.get("macro_score") is not None else macro.get("macro_score") or feats.get("macro_score"))
    btc_dom = _safe(trade.get("btc_dominance") if trade.get("btc_dominance") is not None else feats.get("btc_dominance") or macro.get("btc_dominance"))

    pnl = _safe(trade.get("pnl") if trade.get("pnl") is not None else trade.get("pnl_pct")) or 0.0
    result = str(trade.get("result") or "").upper()
    if not result:
        result = "WIN" if pnl > 0.05 else ("LOSS" if pnl < -0.05 else "BE")

    mae = _safe(trade.get("mae_pct") or feats.get("mae_pct"))
    mfe = _safe(trade.get("mfe_pct") or feats.get("mfe_pct"))
    # fallback from pnl if missing
    if mae is None and pnl < 0:
        mae = pnl
    if mfe is None and pnl > 0:
        mfe = pnl

    hold = _safe(trade.get("holding_seconds") or feats.get("duration_sec"))

    flat: dict[str, float | None] = {
        "chg_24h": windows["24h"]["change_pct"],
        "chg_12h": windows["12h"]["change_pct"],
        "chg_8h": windows["8h"]["change_pct"],
        "chg_4h": windows["4h"]["change_pct"],
        "chg_2h": windows["2h"]["change_pct"],
        "chg_1h": windows["1h"]["change_pct"],
        "chg_30m": windows["30m"]["change_pct"],
        "chg_15m": windows["15m"]["change_pct"],
        "chg_5m": windows["5m"]["change_pct"],
        "range_4h": windows["4h"]["range"],
        "range_1h": windows["1h"]["range"],
        "vol_4h": windows["4h"]["volatility"],
        "vol_1h": windows["1h"]["volatility"],
        "atr_pct": _at("atr_pct") or _safe(feats.get("atr_pct")),
        "bb_width": _at("bb_width"),
        "bb_pos": _at("bb_pos"),
        "rvol_1h": windows["1h"]["rel_volume"],
        "vol_spike_1h": windows["1h"]["volume_spike"],
        "dvol_1h": windows["1h"]["delta_volume"],
        "ema20_dist": ema20_dist,
        "ema50_dist": ema50_dist,
        "ema200_dist": ema200_dist,
        "ema_cross_20_50": (
            1.0 if (ema20 is not None and ema50 is not None and ema20 > ema50) else
            (-1.0 if (ema20 is not None and ema50 is not None) else None)
        ),
        "adx": _safe(feats.get("adx")),  # may be absent; leave None
        "slope_4h": windows["4h"]["slope"],
        "accel_1h": windows["1h"]["accel"],
        "rsi": _at("rsi") or _safe(trade.get("rsi") or feats.get("rsi")),
        "macd": _at("macd"),
        "macd_hist": _at("macd_hist"),
        "stoch_k": _at("stoch"),
        "roc_1h": roc_1h,
        "mom_4h": mom_4h,
        "funding": funding,
        "funding_delta": funding_delta,
        "oi_delta": oi_delta,
        "fear_greed": fear,
        "news_score": news_score,
        "macro_score": macro_score,
        "btc_dom": btc_dom,
    }

    vector = [flat.get(k) for k in VECTOR_KEYS]
    return {
        "trade_id": int(trade.get("trade_id") or trade.get("id") or 0),
        "symbol": symbol,
        "direction": str(trade.get("direction") or "").upper(),
        "opened_at": opened_i,
        "closed_at": int(trade.get("closed_at") or opened_i),
        "pnl": pnl,
        "result": result,
        "regime": str(trade.get("regime") or trade.get("market_regime") or feats.get("regime") or "UNK"),
        "mae": mae,
        "mfe": mfe,
        "hold_sec": hold,
        "windows": windows,
        "features": flat,
        "vector": vector,
        "candle_hit": True,
    }


def vector_matrix(rows: list[dict[str, Any]]) -> np.ndarray:
    """n×d float matrix with NaN→column median imputation."""
    if not rows:
        return np.zeros((0, len(VECTOR_KEYS)))
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
    "VECTOR_KEYS",
    "WINDOWS_SEC",
    "load_candle_book",
    "snapshot_trade",
    "vector_matrix",
]
