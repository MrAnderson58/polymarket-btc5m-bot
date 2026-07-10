"""Counterfactual reversal study — read-only on observations, no market_events mutation."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.event_report import _days_ago_ts
from bot.research.market_events.event_types import SHOCK_DIRECTION_DOWN, SHOCK_DIRECTION_UP
from bot.research.market_events.shock_profiles import profile_for_symbol, profile_name_for_symbol

FORWARD_HORIZONS = (30, 60, 180, 300, 600, 900)
ENTRY_MODES = ("immediate", "reclaim_25", "reclaim_50", "velocity_decay", "delayed_survival")
MAX_STUDY_SEC = 900


@dataclass
class EpisodeMetrics:
    symbol: str
    profile_name: str
    episode_ts: int
    direction: str
    shock_return_pct: float
    max_continuation_pct: float
    time_to_extreme_sec: int
    max_reversal_pct: float
    time_to_25_reclaim_sec: int | None
    time_to_50_reclaim_sec: int | None
    time_to_full_reclaim_sec: int | None
    forward_returns: dict[str, float | None]
    mae_mfe: dict[str, dict[str, float]]
    session_regime: str | None
    has_context: int
    classification: str | None


def _find_shock_episodes(
    series: list[tuple[int, float]],
    symbol: str,
    *,
    dedup_sec: int = 300,
) -> list[tuple[int, int, float, str]]:
    """Returns list of (idx, ts, return_pct, direction)."""
    profile = profile_for_symbol(symbol)
    hits: list[tuple[int, int, float, str]] = []
    last_ts = -dedup_sec * 2
    for i in range(20, len(series)):
        ts = series[i][0]
        if ts - last_ts < dedup_sec:
            continue
        best_ret = 0.0
        best_window = 30
        for window_sec, threshold in profile.windows_pct.items():
            target = ts - window_sec
            ref_px = None
            for j in range(i, -1, -1):
                if series[j][0] <= target:
                    ref_px = series[j][1]
                    break
            if ref_px is None or ref_px <= 0:
                continue
            ret = (series[i][1] / ref_px - 1.0) * 100.0
            if abs(ret) >= threshold and abs(ret) >= profile.min_abs_return_pct:
                if abs(ret) > abs(best_ret):
                    best_ret = ret
                    best_window = window_sec
        if abs(best_ret) >= profile.min_abs_return_pct:
            direction = SHOCK_DIRECTION_UP if best_ret > 0 else SHOCK_DIRECTION_DOWN
            hits.append((i, ts, best_ret, direction))
            last_ts = ts
    return hits


def _signed_move(from_px: float, to_px: float, direction: str) -> float:
    if from_px <= 0:
        return 0.0
    if direction == SHOCK_DIRECTION_UP:
        return (to_px / from_px - 1.0) * 100.0
    return (from_px / to_px - 1.0) * 100.0


def _compute_metrics(
    series: list[tuple[int, float]],
    idx: int,
    shock_ret: float,
    direction: str,
    symbol: str,
) -> EpisodeMetrics:
    ts0, px0 = series[idx]
    profile_name = profile_name_for_symbol(symbol)
    end_ts = ts0 + MAX_STUDY_SEC

    extreme_px = px0
    extreme_ts = ts0
    max_cont = 0.0
    max_rev = 0.0
    t25: int | None = None
    t50: int | None = None
    tfull: int | None = None
    shock_abs = abs(shock_ret)

    forward: dict[str, float | None] = {}
    for h in FORWARD_HORIZONS:
        target = ts0 + h
        fwd_px = None
        for j in range(idx, len(series)):
            if series[j][0] >= target:
                fwd_px = series[j][1]
                break
        forward[f"{h}s"] = _signed_move(px0, fwd_px, direction) if fwd_px else None

    for j in range(idx, len(series)):
        ts, px = series[j]
        if ts > end_ts:
            break
        cont = _signed_move(px0, px, direction)
        max_cont = max(max_cont, cont)
        if direction == SHOCK_DIRECTION_DOWN:
            if px < extreme_px:
                extreme_px, extreme_ts = px, ts
        else:
            if px > extreme_px:
                extreme_px, extreme_ts = px, ts

    for j in range(idx, len(series)):
        ts, px = series[j]
        if ts > end_ts:
            break
        rev = _signed_move(extreme_px, px, _flip_dir(direction))
        max_rev = max(max_rev, rev)
        reclaim_frac = rev / shock_abs if shock_abs > 0 else 0
        elapsed = ts - ts0
        if t25 is None and reclaim_frac >= 0.25:
            t25 = elapsed
        if t50 is None and reclaim_frac >= 0.50:
            t50 = elapsed
        if tfull is None and reclaim_frac >= 1.0:
            tfull = elapsed

    mae_mfe = _hypothetical_entries(series, idx, direction, extreme_px, extreme_ts, ts0, px0)

    return EpisodeMetrics(
        symbol=symbol,
        profile_name=profile_name,
        episode_ts=ts0,
        direction=direction,
        shock_return_pct=shock_ret,
        max_continuation_pct=max_cont,
        time_to_extreme_sec=extreme_ts - ts0,
        max_reversal_pct=max_rev,
        time_to_25_reclaim_sec=t25,
        time_to_50_reclaim_sec=t50,
        time_to_full_reclaim_sec=tfull,
        forward_returns=forward,
        mae_mfe=mae_mfe,
        session_regime=None,
        has_context=0,
        classification=None,
    )


def _flip_dir(d: str) -> str:
    return SHOCK_DIRECTION_DOWN if d == SHOCK_DIRECTION_UP else SHOCK_DIRECTION_UP


def _hypothetical_entries(
    series: list[tuple[int, float]],
    idx: int,
    shock_dir: str,
    extreme_px: float,
    extreme_ts: int,
    ts0: int,
    px0: float,
) -> dict[str, dict[str, float]]:
    fade_dir = _flip_dir(shock_dir)
    entries: dict[str, tuple[int, float] | None] = {
        "immediate": (ts0, px0),
        "reclaim_25": None,
        "reclaim_50": None,
        "velocity_decay": None,
        "delayed_survival": None,
    }
    shock_abs = abs(_signed_move(px0, extreme_px, shock_dir))
    r1_confirm_ts: int | None = None

    for j in range(idx, len(series)):
        ts, px = series[j]
        if ts > ts0 + MAX_STUDY_SEC:
            break
        rev = _signed_move(extreme_px, px, fade_dir)
        reclaim_frac = rev / shock_abs if shock_abs > 0 else 0
        if entries["reclaim_25"] is None and reclaim_frac >= 0.25:
            entries["reclaim_25"] = (ts, px)
        if entries["reclaim_50"] is None and reclaim_frac >= 0.50:
            entries["reclaim_50"] = (ts, px)
        if r1_confirm_ts is None and reclaim_frac >= 0.35:
            r1_confirm_ts = ts
        if entries["delayed_survival"] is None and r1_confirm_ts and ts - r1_confirm_ts >= 30:
            entries["delayed_survival"] = (ts, px)
        if j > idx + 2:
            ret30 = _signed_move(series[max(idx, j - 3)][1], px, shock_dir)
            ret10 = _signed_move(series[max(idx, j - 1)][1], px, shock_dir)
            if abs(ret10) < abs(ret30) * 0.5 and entries["velocity_decay"] is None:
                fade_move = _signed_move(px0, px, fade_dir)
                if fade_move >= 0.15:
                    entries["velocity_decay"] = (ts, px)

    out: dict[str, dict[str, float]] = {}
    for mode, ent in entries.items():
        if ent is None:
            continue
        ent_ts, ent_px = ent
        mfe = 0.0
        mae = 0.0
        for j in range(idx, len(series)):
            ts, px = series[j]
            if ts < ent_ts or ts > ent_ts + MAX_STUDY_SEC:
                continue
            ret = _signed_move(ent_px, px, fade_dir)
            mfe = max(mfe, ret)
            mae = min(mae, ret)
        out[mode] = {"mfe": mfe, "mae": mae, "entry_ts": float(ent_ts), "entry_price": ent_px}
    return out


def run_counterfactual_study(conn: Any, *, days: int = 1, persist: bool = True) -> int:
    since = _days_ago_ts(days)
    rows = conn.execute(
        """
        SELECT i.canonical_asset, o.obs_ts, o.trade_price, o.session_regime
        FROM market_events_price_observations o
        JOIN market_events_instruments i ON i.id = o.instrument_id
        WHERE o.obs_ts >= ? AND o.trade_price IS NOT NULL AND o.trade_price > 0
        ORDER BY i.canonical_asset, o.obs_ts
        """,
        (since,),
    ).fetchall()
    by_sym: dict[str, list[tuple[int, float]]] = defaultdict(list)
    sessions: dict[tuple[str, int], str | None] = {}
    for r in rows:
        sym = r["canonical_asset"]
        ts = int(r["obs_ts"])
        by_sym[sym].append((ts, float(r["trade_price"])))
        sessions[(sym, ts)] = r["session_regime"]

    count = 0
    for sym, series in by_sym.items():
        episodes = _find_shock_episodes(series, sym)
        for idx, ts, shock_ret, direction in episodes:
            m = _compute_metrics(series, idx, shock_ret, direction, sym)
            m.session_regime = sessions.get((sym, ts))
            count += 1
            if persist:
                insert_returning_id(
                    conn,
                    """
                    INSERT INTO market_events_counterfactual_studies (
                      symbol, profile_name, episode_ts, direction, shock_return_pct,
                      max_continuation_pct, time_to_extreme_sec, max_reversal_pct,
                      time_to_25_reclaim_sec, time_to_50_reclaim_sec, time_to_full_reclaim_sec,
                      forward_returns_json, mae_mfe_json, session_regime, has_context,
                      classification, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        m.symbol, m.profile_name, m.episode_ts, m.direction, m.shock_return_pct,
                        m.max_continuation_pct, m.time_to_extreme_sec, m.max_reversal_pct,
                        m.time_to_25_reclaim_sec, m.time_to_50_reclaim_sec, m.time_to_full_reclaim_sec,
                        json.dumps(m.forward_returns), json.dumps(m.mae_mfe),
                        m.session_regime, m.has_context, m.classification, int(time.time()),
                    ),
                )
    return count


def reversal_counterfactual_report(conn: Any, *, days: int = 1) -> str:
    since = _days_ago_ts(days)
    rows = conn.execute(
        """
        SELECT profile_name, symbol, direction, session_regime, COUNT(*) AS n,
               AVG(max_reversal_pct) AS avg_rev, AVG(time_to_25_reclaim_sec) AS avg_t25,
               AVG(time_to_50_reclaim_sec) AS avg_t50
        FROM market_events_counterfactual_studies
        WHERE created_at >= ?
        GROUP BY profile_name, symbol, direction, session_regime
        ORDER BY profile_name, n DESC
        """,
        (since,),
    ).fetchall()
    lines = [
        "REVERSAL COUNTERFACTUAL REPORT (research only — no market_events mutation)",
        f"days: {days}",
        "",
    ]
    if not rows:
        lines.append("No counterfactual studies. Studies are computed on demand.")
        return "\n".join(lines)

    by_profile: dict[str, list] = defaultdict(list)
    for r in rows:
        by_profile[r["profile_name"]].append(r)

    for prof, prof_rows in sorted(by_profile.items()):
        lines.append(f"=== {prof} ===")
        for r in prof_rows[:15]:
            lines.append(
                f"  {r['symbol']} {r['direction']} n={r['n']} "
                f"avg_max_rev={r['avg_rev']:.3f}% "
                f"avg_t25={r['avg_t25']:.0f}s avg_t50={r['avg_t50']:.0f}s "
                f"session={r['session_regime'] or 'unknown'}",
            )
        lines.append("")
    return "\n".join(lines)
