"""SHOCK_F — volatility-normalized shadow detector (research only, no paper runs)."""

from __future__ import annotations

import json
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.config import SHOCK_THRESHOLDS
from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.event_report import _days_ago_ts
from bot.research.market_events.event_types import SHOCK_DIRECTION_DOWN, SHOCK_DIRECTION_UP

SHOCK_F_ID = "SHOCK_F"
SHOCK_F_VERSION = "shadow_v1"
SHOCK_F_MIN_WARMUP = 20
SHOCK_F_WINDOW_SEC = 60
SHOCK_F_Z_THRESHOLD = 2.5
FORWARD_HORIZONS_SEC = (15, 30, 60, 180, 300, 900)


@dataclass
class ShockFCandidate:
    symbol: str
    direction: str
    event_ts: int
    return_pct: float
    vol_scale_pct: float
    z_score: float
    overlap_detectors: list[str] = field(default_factory=list)


def _realized_vol_pct(prices: list[float]) -> float | None:
    if len(prices) < SHOCK_F_MIN_WARMUP:
        return None
    rets = []
    for i in range(1, len(prices)):
        if prices[i - 1] > 0:
            rets.append((prices[i] / prices[i - 1] - 1.0) * 100.0)
    if len(rets) < SHOCK_F_MIN_WARMUP - 1:
        return None
    return statistics.pstdev(rets) or None


def detect_shock_f_at_index(
    series: list[tuple[int, float]],
    idx: int,
    *,
    window_sec: int = SHOCK_F_WINDOW_SEC,
) -> ShockFCandidate | None:
    if idx < SHOCK_F_MIN_WARMUP:
        return None
    ts, px = series[idx]
    target = ts - window_sec
    ref_px = None
    for j in range(idx, -1, -1):
        if series[j][0] <= target:
            ref_px = series[j][1]
            break
    if ref_px is None or ref_px <= 0:
        return None
    ret = (px / ref_px - 1.0) * 100.0
    lookback_prices = [p for _, p in series[max(0, idx - SHOCK_F_MIN_WARMUP * 3): idx + 1]]
    vol = _realized_vol_pct(lookback_prices)
    if vol is None or vol < 1e-6:
        return None
    z = ret / vol
    if abs(z) < SHOCK_F_Z_THRESHOLD:
        return None
    direction = SHOCK_DIRECTION_UP if z > 0 else SHOCK_DIRECTION_DOWN
    return ShockFCandidate(
        symbol="",
        direction=direction,
        event_ts=ts,
        return_pct=ret,
        vol_scale_pct=vol,
        z_score=z,
    )


def _fixed_detector_overlap(series: list[tuple[int, float]], idx: int, ret: float) -> list[str]:
    ts = series[idx][0]
    overlaps = []
    for det_id, cfg in SHOCK_THRESHOLDS.items():
        window = int(cfg["window_sec"])
        target = ts - window
        ref_px = None
        for j in range(idx, -1, -1):
            if series[j][0] <= target:
                ref_px = series[j][1]
                break
        if ref_px is None or ref_px <= 0:
            continue
        wret = (series[idx][1] / ref_px - 1.0) * 100.0
        if det_id in ("SHOCK_A", "SHOCK_B", "SHOCK_C"):
            if abs(wret) >= float(cfg["min_abs_return_pct"]):
                overlaps.append(det_id)
    return overlaps


def _forward_returns(series: list[tuple[int, float]], idx: int) -> dict[str, float | None]:
    ts, px = series[idx]
    out: dict[str, float | None] = {}
    for h in FORWARD_HORIZONS_SEC:
        target = ts + h
        fwd_px = None
        for j in range(idx, len(series)):
            if series[j][0] >= target:
                fwd_px = series[j][1]
                break
        out[f"{h}s"] = ((fwd_px / px - 1.0) * 100.0) if fwd_px and px > 0 else None
    return out


def _classify_path(fwd: dict[str, float | None], direction: str) -> str:
    r60 = fwd.get("60s")
    r180 = fwd.get("180s")
    if r60 is None:
        return "unknown"
    if direction == SHOCK_DIRECTION_DOWN:
        if r60 > 0.1:
            return "reversal"
        if r180 is not None and r180 < -0.1:
            return "continuation"
    else:
        if r60 < -0.1:
            return "reversal"
        if r180 is not None and r180 > 0.1:
            return "continuation"
    return "mixed"


def run_shock_f_shadow_audit(conn: Any, *, days: int = 1, persist: bool = True) -> str:
    since = _days_ago_ts(days)
    rows = conn.execute(
        """
        SELECT i.canonical_asset, o.obs_ts, o.trade_price
        FROM market_events_price_observations o
        JOIN market_events_instruments i ON i.id = o.instrument_id
        WHERE o.obs_ts >= ? AND o.trade_price IS NOT NULL AND o.trade_price > 0
        ORDER BY i.canonical_asset, o.obs_ts
        """,
        (since,),
    ).fetchall()
    by_sym: dict[str, list[tuple[int, float]]] = {}
    for r in rows:
        by_sym.setdefault(r["canonical_asset"], []).append((int(r["obs_ts"]), float(r["trade_price"])))

    lines = [
        "SHOCK_F SHADOW AUDIT (research only — no paper runs)",
        f"formula: return_{SHOCK_F_WINDOW_SEC}s / rolling_realized_vol, |z| >= {SHOCK_F_Z_THRESHOLD}",
        f"min_warmup: {SHOCK_F_MIN_WARMUP} ticks",
        f"version: {SHOCK_F_VERSION}",
        "",
    ]
    total = 0
    overlap_count = 0
    unique_count = 0
    path_counts: dict[str, int] = defaultdict(int)

    for sym, series in sorted(by_sym.items()):
        sym_candidates = 0
        for i in range(SHOCK_F_MIN_WARMUP, len(series)):
            cand = detect_shock_f_at_index(series, i)
            if not cand:
                continue
            cand.symbol = sym
            overlaps = _fixed_detector_overlap(series, i, cand.return_pct)
            cand.overlap_detectors = overlaps
            fwd = _forward_returns(series, i)
            path = _classify_path(fwd, cand.direction)
            path_counts[path] += 1
            total += 1
            sym_candidates += 1
            if overlaps:
                overlap_count += 1
            else:
                unique_count += 1
            if persist:
                insert_returning_id(
                    conn,
                    """
                    INSERT INTO market_events_shadow_candidates (
                      symbol, event_ts, detector_id, direction, return_pct, z_score,
                      vol_scale_pct, overlap_detectors_json, forward_returns_json,
                      path_class, detector_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sym, cand.event_ts, SHOCK_F_ID, cand.direction, cand.return_pct,
                        cand.z_score, cand.vol_scale_pct, json.dumps(overlaps),
                        json.dumps(fwd), path, SHOCK_F_VERSION, int(time.time()),
                    ),
                )
        if sym_candidates:
            lines.append(f"  {sym}: SHOCK_F candidates={sym_candidates}")

    lines.extend([
        "",
        f"total_SHOCK_F_candidates: {total}",
        f"overlap_with_SHOCK_A-E: {overlap_count}",
        f"unique_to_SHOCK_F: {unique_count}",
        f"path_classification: {dict(path_counts)}",
        f"forward_horizons: {FORWARD_HORIZONS_SEC}",
        "",
        "SHOCK_F does NOT create market_events or paper_strategy_runs.",
    ])
    return "\n".join(lines)
