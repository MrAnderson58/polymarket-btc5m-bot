"""SHOCK_F_v2 — deduplicated volatility-normalized shadow detector (research only)."""

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
from bot.research.market_events.shock_profiles import profile_for_symbol

SHOCK_F_V2_ID = "SHOCK_F_v2"
SHOCK_F_V2_VERSION = "shadow_v2"
SHOCK_F_MIN_WARMUP = 20
SHOCK_F_WINDOW_SEC = 60
SHOCK_F_Z_THRESHOLD = 3.5
SHOCK_F_VOL_FLOOR_PCT = 0.05
SHOCK_F_COOLDOWN_SEC = 300
FORWARD_HORIZONS_SEC = (15, 30, 60, 180, 300, 900)


@dataclass
class ShockFv2Candidate:
    symbol: str
    direction: str
    event_ts: int
    return_pct: float
    vol_scale_pct: float
    z_score: float
    profile_name: str
    min_abs_floor_pct: float
    episode_id: str
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
    vol = statistics.pstdev(rets)
    return max(vol, SHOCK_F_VOL_FLOOR_PCT)


def detect_shock_f_v2_at_index(
    series: list[tuple[int, float]],
    idx: int,
    *,
    symbol: str,
    window_sec: int = SHOCK_F_WINDOW_SEC,
) -> ShockFv2Candidate | None:
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
    profile = profile_for_symbol(symbol)
    if abs(ret) < profile.min_abs_return_pct:
        return None
    lookback_prices = [p for _, p in series[max(0, idx - SHOCK_F_MIN_WARMUP * 3): idx + 1]]
    vol = _realized_vol_pct(lookback_prices)
    if vol is None:
        return None
    z = ret / vol
    if abs(z) < SHOCK_F_Z_THRESHOLD:
        return None
    direction = SHOCK_DIRECTION_UP if z > 0 else SHOCK_DIRECTION_DOWN
    episode_id = f"{symbol}:{direction}:{ts // SHOCK_F_COOLDOWN_SEC}"
    return ShockFv2Candidate(
        symbol=symbol,
        direction=direction,
        event_ts=ts,
        return_pct=ret,
        vol_scale_pct=vol,
        z_score=z,
        profile_name=profile.name,
        min_abs_floor_pct=profile.min_abs_return_pct,
        episode_id=episode_id,
    )


def _dedupe_episodes(candidates: list[ShockFv2Candidate]) -> tuple[list[ShockFv2Candidate], int]:
    """Keep first candidate per episode_id (cooldown bucket)."""
    seen: set[str] = set()
    deduped: list[ShockFv2Candidate] = []
    for c in sorted(candidates, key=lambda x: x.event_ts):
        if c.episode_id in seen:
            continue
        seen.add(c.episode_id)
        deduped.append(c)
    return deduped, len(candidates) - len(deduped)


def _fixed_detector_overlap(series: list[tuple[int, float]], idx: int) -> list[str]:
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


def run_shock_f_v2_shadow_audit(conn: Any, *, days: int = 1, persist: bool = True) -> str:
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
        "SHOCK_F_v2 SHADOW AUDIT (research only — no paper runs)",
        f"formula: return_{SHOCK_F_WINDOW_SEC}s / max(realized_vol, {SHOCK_F_VOL_FLOOR_PCT}%), |z| >= {SHOCK_F_Z_THRESHOLD}",
        f"episode_cooldown: {SHOCK_F_COOLDOWN_SEC}s | min_abs_return: per asset profile",
        f"version: {SHOCK_F_V2_VERSION}",
        "",
    ]
    total_raw = 0
    total_deduped = 0
    overlap_count = 0
    by_profile: dict[str, int] = defaultdict(int)

    for sym, series in sorted(by_sym.items()):
        raw_candidates: list[ShockFv2Candidate] = []
        for i in range(SHOCK_F_MIN_WARMUP, len(series)):
            cand = detect_shock_f_v2_at_index(series, i, symbol=sym)
            if cand:
                cand.overlap_detectors = _fixed_detector_overlap(series, i)
                raw_candidates.append(cand)
        deduped, dropped = _dedupe_episodes(raw_candidates)
        total_raw += len(raw_candidates)
        total_deduped += len(deduped)
        for c in deduped:
            by_profile[c.profile_name] += 1
            if c.overlap_detectors:
                overlap_count += 1
            if persist:
                fwd = _forward_returns(series, next(
                    i for i in range(len(series)) if series[i][0] == c.event_ts
                ))
                insert_returning_id(
                    conn,
                    """
                    INSERT INTO market_events_shadow_candidates (
                      symbol, event_ts, detector_id, direction, return_pct, z_score,
                      vol_scale_pct, overlap_detectors_json, forward_returns_json,
                      path_class, detector_version, profile_name, raw_tick_count,
                      episode_id, deduped, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sym, c.event_ts, SHOCK_F_V2_ID, c.direction, c.return_pct,
                        c.z_score, c.vol_scale_pct, json.dumps(c.overlap_detectors),
                        json.dumps(fwd), "v2_episode", SHOCK_F_V2_VERSION,
                        c.profile_name, len(raw_candidates), c.episode_id, 1,
                        int(time.time()),
                    ),
                )
        if raw_candidates:
            lines.append(
                f"  {sym}: raw_ticks={len(raw_candidates)} deduped_episodes={len(deduped)} "
                f"dropped={dropped} profile={profile_for_symbol(sym).name}",
            )

    lines.extend([
        "",
        f"total_raw_tick_candidates: {total_raw}",
        f"total_deduped_episodes: {total_deduped}",
        f"dedup_reduction: {total_raw - total_deduped} ({100 * (1 - total_deduped / max(total_raw, 1)):.1f}%)",
        f"by_profile: {dict(by_profile)}",
        f"overlap_with_SHOCK_A-C: {overlap_count}",
        "",
        "SHOCK_F_v2 does NOT create market_events or paper_strategy_runs.",
    ])
    return "\n".join(lines)
