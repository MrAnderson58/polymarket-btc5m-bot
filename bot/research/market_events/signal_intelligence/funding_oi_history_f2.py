"""Phase F.2 Task B — funding and open interest history regimes."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

TIMEFRAMES = ("5m", "15m", "1h")
TF_SECONDS = {"5m": 300, "15m": 900, "1h": 3600}


@dataclass(frozen=True)
class FundingOiHistoryResult:
    funding_regime: str
    oi_regime: str
    snapshots: list[dict[str, Any]]


def _fetch_current_funding_oi(symbol: str, venue: str = "binance") -> tuple[float | None, float | None]:
    try:
        import requests
        if venue == "binance":
            prem = requests.get(
                "https://fapi.binance.com/fapi/v1/premiumIndex",
                params={"symbol": f"{symbol}USDT"},
                timeout=6,
            ).json()
            funding = float(prem.get("lastFundingRate", 0) or 0) * 100.0
            oi_resp = requests.get(
                "https://fapi.binance.com/fapi/v1/openInterest",
                params={"symbol": f"{symbol}USDT"},
                timeout=6,
            ).json()
            oi = float(oi_resp.get("openInterest", 0) or 0)
            return funding, oi
    except Exception:
        pass
    return None, None


def _historical_from_db(
    conn: Any,
    *,
    symbol: str,
    lookback_sec: int,
    now: int,
) -> tuple[float | None, float | None]:
    since = now - lookback_sec
    row = conn.execute(
        """
        SELECT c.funding, c.open_interest
        FROM market_event_exchange_context c
        JOIN market_events e ON e.id = c.event_id
        WHERE e.symbol = ? AND c.created_at <= ? AND c.created_at >= ?
          AND (c.funding IS NOT NULL OR c.open_interest IS NOT NULL)
        ORDER BY c.created_at DESC LIMIT 1
        """,
        (symbol, now, since),
    ).fetchone()
    if not row:
        return None, None
    return (
        float(row["funding"]) if row["funding"] is not None else None,
        float(row["open_interest"]) if row["open_interest"] is not None else None,
    )


def _regime_funding(current: float | None, prev: float | None, older: float | None) -> str:
    vals = [v for v in (current, prev, older) if v is not None]
    if len(vals) < 2:
        return "unknown"
    cur, ref = abs(vals[0]), abs(vals[-1])
    if cur > ref * 1.15:
        return "accelerating"
    if cur < ref * 0.85:
        return "flattening"
    return "stable"


def _regime_oi(current: float | None, prev: float | None, older: float | None) -> str:
    vals = [v for v in (current, prev, older) if v is not None and v > 0]
    if len(vals) < 2:
        return "unknown"
    if vals[0] > vals[-1] * 1.02:
        return "rising"
    if vals[0] < vals[-1] * 0.98:
        return "falling"
    return "stable"


def _detect_divergence(
    shock_ret: float,
    funding_regime: str,
    oi_regime: str,
) -> str | None:
    if funding_regime == "accelerating" and oi_regime == "falling":
        return "divergence"
    if shock_ret > 0 and funding_regime == "flattening" and oi_regime == "rising":
        return "divergence"
    if shock_ret < 0 and funding_regime == "accelerating" and oi_regime == "falling":
        return "divergence"
    return None


def analyze_funding_oi_history(
    conn: Any,
    *,
    symbol: str,
    shock_return_pct: float,
    event_id: int | None = None,
) -> FundingOiHistoryResult:
    now = int(time.time())
    cur_f, cur_oi = _fetch_current_funding_oi(symbol)

    exch = None
    if event_id:
        exch = conn.execute(
            "SELECT funding, open_interest FROM market_event_exchange_context WHERE event_id = ? LIMIT 1",
            (event_id,),
        ).fetchone()
    if exch:
        if exch["funding"] is not None:
            cur_f = float(exch["funding"])
        if exch["open_interest"] is not None and float(exch["open_interest"]) > 0:
            cur_oi = float(exch["open_interest"])

    f_15, oi_15 = _historical_from_db(conn, symbol=symbol, lookback_sec=900, now=now)
    f_1h, oi_1h = _historical_from_db(conn, symbol=symbol, lookback_sec=3600, now=now)

    snapshots: list[dict[str, Any]] = []
    tf_vals: dict[str, tuple[float | None, float | None]] = {
        "5m": (cur_f, cur_oi),
        "15m": (f_15, oi_15),
        "1h": (f_1h, oi_1h),
    }

    for tf in TIMEFRAMES:
        f_val, oi_val = tf_vals[tf]
        prev_f = tf_vals.get("15m", (None, None))[0] if tf == "5m" else f_1h
        prev_oi = tf_vals.get("15m", (None, None))[1] if tf == "5m" else oi_1h
        fd = (f_val - prev_f) if f_val is not None and prev_f is not None else None
        od = (oi_val - prev_oi) if oi_val is not None and prev_oi is not None else None
        snapshots.append({
            "timeframe": tf,
            "funding": round(f_val, 6) if f_val is not None else None,
            "open_interest": round(oi_val, 2) if oi_val is not None else None,
            "funding_delta": round(fd, 6) if fd is not None else None,
            "oi_delta": round(od, 2) if od is not None else None,
        })

    funding_regime = _regime_funding(cur_f, f_15, f_1h)
    oi_regime = _regime_oi(cur_oi, oi_15, oi_1h)
    div = _detect_divergence(shock_return_pct, funding_regime, oi_regime)
    if div:
        oi_regime = "divergence"

    return FundingOiHistoryResult(
        funding_regime=funding_regime,
        oi_regime=oi_regime,
        snapshots=snapshots,
    )


def persist_funding_oi_history(
    conn: Any,
    *,
    event_id: int,
    result: FundingOiHistoryResult,
    venue: str = "binance_futures",
) -> None:
    now = int(time.time())
    for snap in result.snapshots:
        conn.execute(
            """
            INSERT INTO market_events_funding_oi_history_f2 (
              event_id, venue, timeframe, funding, open_interest,
              funding_delta, oi_delta, funding_regime, oi_regime, raw_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, venue, timeframe) DO UPDATE SET
              funding = excluded.funding,
              open_interest = excluded.open_interest,
              funding_delta = excluded.funding_delta,
              oi_delta = excluded.oi_delta,
              funding_regime = excluded.funding_regime,
              oi_regime = excluded.oi_regime,
              raw_json = excluded.raw_json,
              created_at = excluded.created_at
            """,
            (
                event_id, venue, snap["timeframe"],
                snap.get("funding"), snap.get("open_interest"),
                snap.get("funding_delta"), snap.get("oi_delta"),
                result.funding_regime, result.oi_regime,
                json.dumps(snap), now,
            ),
        )
