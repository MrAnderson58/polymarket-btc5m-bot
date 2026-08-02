"""Compute DNA features from lake rows + historical 5m candles."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)

DNA_NUMERIC = (
    "rsi",
    "atr",
    "atr_pct",
    "funding",
    "oi_delta",
    "ema20",
    "ema50",
    "ema200",
    "vwap",
    "adx",
    "macd",
    "confidence",
    "news_score",
)

DNA_CATEGORICAL = (
    "hour",
    "weekday",
    "direction",
    "symbol",
    "regime",
    "gate",
    "pattern",
    "funding_sign",
    "oi_sign",
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


def _ema(arr: np.ndarray, span: int) -> np.ndarray:
    out = np.full_like(arr, np.nan, dtype=float)
    if len(arr) == 0:
        return out
    alpha = 2.0 / (span + 1.0)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full(len(close), np.nan)
    if len(close) < period + 1:
        return out
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_g = np.mean(gain[1 : period + 1])
    avg_l = np.mean(loss[1 : period + 1])
    if avg_l <= 1e-12:
        out[period] = 100.0
    else:
        rs = avg_g / avg_l
        out[period] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(period + 1, len(close)):
        avg_g = (avg_g * (period - 1) + gain[i]) / period
        avg_l = (avg_l * (period - 1) + loss[i]) / period
        if avg_l <= 1e-12:
            out[i] = 100.0
        else:
            rs = avg_g / avg_l
            out[i] = 100.0 - (100.0 / (1.0 + rs))
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
    out[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(close)
    out = np.full(n, np.nan)
    if n < period + 2:
        return out
    up = high[1:] - high[:-1]
    down = low[:-1] - low[1:]
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    atr = _atr(high, low, close, period)
    plus_di = np.full(n, np.nan)
    minus_di = np.full(n, np.nan)
    # smooth DM
    pdm = np.concatenate([[0.0], plus_dm])
    mdm = np.concatenate([[0.0], minus_dm])
    sp = np.full(n, np.nan)
    sm = np.full(n, np.nan)
    if n > period:
        sp[period] = np.sum(pdm[1 : period + 1])
        sm[period] = np.sum(mdm[1 : period + 1])
        for i in range(period + 1, n):
            sp[i] = sp[i - 1] - sp[i - 1] / period + pdm[i]
            sm[i] = sm[i - 1] - sm[i - 1] / period + mdm[i]
        for i in range(period, n):
            a = atr[i]
            if a is None or not np.isfinite(a) or a <= 1e-12:
                continue
            plus_di[i] = 100.0 * sp[i] / a
            minus_di[i] = 100.0 * sm[i] / a
            s = plus_di[i] + minus_di[i]
            dx = 100.0 * abs(plus_di[i] - minus_di[i]) / s if s > 1e-12 else 0.0
            if i == period:
                out[i] = dx
            else:
                prev = out[i - 1]
                out[i] = ((prev * (period - 1)) + dx) / period if np.isfinite(prev) else dx
    return out


def _macd_line(close: np.ndarray) -> np.ndarray:
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    return ema12 - ema26


def _vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    typical = (high + low + close) / 3.0
    vol = np.where(np.isfinite(volume) & (volume > 0), volume, 1.0)
    cum_pv = np.cumsum(typical * vol)
    cum_v = np.cumsum(vol)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = cum_pv / cum_v
    return out


def load_candle_book(conn: Any) -> dict[str, dict[str, np.ndarray]]:
    """Load 5m candles and precompute indicator series per symbol."""
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
        sym = str(r[0])
        by_sym.setdefault(sym, []).append(
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
        with np.errstate(divide="ignore", invalid="ignore"):
            atr_pct = np.where(c > 0, 100.0 * atr / c, np.nan)
        book[sym] = {
            "ts": ts,
            "high": h,
            "low": l,
            "close": c,
            "volume": v,
            "rsi": _rsi(c, 14),
            "atr": atr,
            "atr_pct": atr_pct,
            "ema20": _ema(c, 20),
            "ema50": _ema(c, 50),
            "ema200": _ema(c, 200),
            "vwap": _vwap(h, l, c, v),
            "adx": _adx(h, l, c, 14),
            "macd": _macd_line(c),
        }
    return book


def _lookup_idx(ts_arr: np.ndarray, ts: int) -> int | None:
    if len(ts_arr) == 0:
        return None
    i = int(np.searchsorted(ts_arr, ts, side="right") - 1)
    if i < 0:
        return None
    # require candle not older than 2 days
    if ts - int(ts_arr[i]) > 2 * 86400:
        return None
    return i


def _sign_label(v: float | None, *, eps: float = 1e-12) -> str:
    if v is None:
        return "UNK"
    if v > eps:
        return "+"
    if v < -eps:
        return "-"
    return "0"


def enrich_trade(trade: dict[str, Any], book: dict[str, dict[str, np.ndarray]]) -> dict[str, Any]:
    """Flatten DNA feature vector for one CLOSED trade."""
    feats = _parse(trade.get("features_json"))
    macro = trade.get("macro") if isinstance(trade.get("macro"), dict) else _parse(trade.get("macro_json"))
    news = trade.get("news") if isinstance(trade.get("news"), dict) else _parse(trade.get("news_json"))
    patterns = trade.get("patterns") if isinstance(trade.get("patterns"), dict) else _parse(trade.get("patterns_json"))

    symbol = str(trade.get("symbol") or "")
    direction = str(trade.get("direction") or "").upper()
    pnl = _safe_float(trade.get("pnl") if trade.get("pnl") is not None else trade.get("pnl_pct"))
    opened = trade.get("opened_at") or trade.get("closed_at")
    try:
        opened_i = int(opened) if opened is not None else None
    except Exception:
        opened_i = None

    hour = None
    weekday = None
    if opened_i:
        dt = datetime.fromtimestamp(opened_i, tz=timezone.utc)
        hour = dt.hour
        weekday = dt.weekday()  # 0=Mon

    # Pattern / regime cleanup
    raw_pattern = trade.get("pattern") or patterns.get("pattern")
    if isinstance(raw_pattern, str) and raw_pattern.strip().startswith("{"):
        try:
            nested = json.loads(raw_pattern)
            raw_pattern = (
                nested.get("candidate_state")
                or nested.get("pattern")
                or nested.get("name")
            )
        except Exception:
            raw_pattern = None
    g31 = patterns.get("g31") if isinstance(patterns.get("g31"), dict) else {}
    if not raw_pattern and g31:
        raw_pattern = "g31"
    if not raw_pattern or str(raw_pattern) in ("nested", "None", ""):
        raw_pattern = "UNK"
    regime = (
        trade.get("regime")
        or trade.get("market_regime")
        or patterns.get("market_regime")
        or feats.get("regime")
        or "UNK"
    )
    if not regime or str(regime) in ("None", ""):
        regime = "UNK"

    out: dict[str, Any] = {
        "trade_id": int(trade.get("trade_id") or trade.get("id") or 0),
        "symbol": symbol,
        "direction": direction if direction else "UNK",
        "pnl": float(pnl or 0.0),
        "closed_at": trade.get("closed_at"),
        "opened_at": opened_i,
        "created_at": trade.get("created_at") or opened_i,
        "regime": str(regime),
        "gate": str(trade.get("gate") or trade.get("gate_decision") or feats.get("gate") or "UNK"),
        "pattern": str(raw_pattern)[:64],
        "confidence": _safe_float(trade.get("confidence") if trade.get("confidence") is not None else feats.get("confidence") or g31.get("confidence")),
        "news_score": _safe_float(trade.get("news_score") if trade.get("news_score") is not None else news.get("news_score")),
        "funding": _safe_float(trade.get("funding") if trade.get("funding") is not None else feats.get("funding") or macro.get("funding")),
        "oi_delta": _safe_float(trade.get("oi_delta") if trade.get("oi_delta") is not None else feats.get("oi_delta")),
        "hour": hour,
        "weekday": weekday,
        "rsi": _safe_float(trade.get("rsi") or feats.get("rsi")),
        "atr": _safe_float(trade.get("atr") or feats.get("atr")),
        "atr_pct": _safe_float(trade.get("atr_pct") or feats.get("atr_pct")),
        "ema20": None,
        "ema50": None,
        "ema200": None,
        "vwap": None,
        "adx": None,
        "macd": None,
        "candle_hit": False,
    }

    series = book.get(symbol)
    if series is not None and opened_i is not None:
        idx = _lookup_idx(series["ts"], opened_i)
        if idx is not None:
            out["candle_hit"] = True
            for k in ("rsi", "atr", "atr_pct", "ema20", "ema50", "ema200", "vwap", "adx", "macd"):
                val = float(series[k][idx]) if np.isfinite(series[k][idx]) else None
                # Prefer candle-derived for technicals
                out[k] = val
            # Distances vs entry if present
            entry = _safe_float(trade.get("entry"))
            if entry and out.get("ema20"):
                out["ema20_dist"] = (entry - float(out["ema20"])) / entry * 100.0
            if entry and out.get("vwap"):
                out["vwap_dist"] = (entry - float(out["vwap"])) / entry * 100.0

    out["funding_sign"] = _sign_label(out.get("funding"))
    out["oi_sign"] = _sign_label(out.get("oi_delta"))
    if out["regime"] in ("", "None"):
        out["regime"] = "UNK"
    if out["gate"] in ("", "None"):
        out["gate"] = "UNK"
    if out["pattern"] in ("", "None"):
        out["pattern"] = "UNK"
    return out


def enrich_trades(trades: list[dict[str, Any]], conn: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    book = load_candle_book(conn)
    enriched = [enrich_trade(t, book) for t in trades]
    hit = sum(1 for t in enriched if t.get("candle_hit"))
    cov = {}
    for k in list(DNA_NUMERIC) + list(DNA_CATEGORICAL):
        cov[k] = sum(1 for t in enriched if t.get(k) is not None and t.get(k) != "UNK")
    stats = {
        "n_trades": len(enriched),
        "n_symbols_candles": len(book),
        "candle_hits": hit,
        "candle_hit_rate": round(hit / len(enriched), 4) if enriched else 0.0,
        "coverage": cov,
    }
    return enriched, stats


__all__ = [
    "DNA_CATEGORICAL",
    "DNA_NUMERIC",
    "enrich_trade",
    "enrich_trades",
    "load_candle_book",
]
