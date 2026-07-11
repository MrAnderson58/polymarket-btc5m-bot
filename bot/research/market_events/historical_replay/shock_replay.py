"""Task C/D — historical shock replay and reversal path metrics."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Any

from bot.research.market_events.config import SHOCK_THRESHOLDS
from bot.research.market_events.counterfactual_reversal import (
    _compute_metrics,
    _find_shock_episodes,
    _signed_move,
)
from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.event_types import SHOCK_DIRECTION_DOWN, SHOCK_DIRECTION_UP
from bot.research.market_events.historical_replay.constants import (
    PATH_HORIZONS_SEC,
    REPLAY_DATA_SOURCE,
    REPLAY_DETECTOR_VERSION,
    REPLAY_PROFILE_VERSION,
    REPLAY_RUN_TAG_DEFAULT,
)
from bot.research.market_events.historical_replay.splits import persist_replay_split
from bot.research.market_events.shock_f_v2_shadow import detect_shock_f_v2_at_index
from bot.research.market_events.shock_profiles import profile_name_for_symbol


def _ensure_run(conn: Any, *, run_tag: str, start_ts: int, end_ts: int) -> int:
    row = conn.execute(
        "SELECT id FROM market_events_replay_runs WHERE run_tag = ?",
        (run_tag,),
    ).fetchone()
    if row:
        return int(row["id"])
    return insert_returning_id(
        conn,
        """
        INSERT INTO market_events_replay_runs (
          run_tag, data_source, detector_version, profile_version,
          start_ts, end_ts, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?)
        """,
        (
            run_tag, REPLAY_DATA_SOURCE, REPLAY_DETECTOR_VERSION, REPLAY_PROFILE_VERSION,
            start_ts, end_ts, int(time.time()),
        ),
    )


def _detect_production_shocks(
    series: list[tuple[int, float]],
    symbol: str,
) -> list[tuple[int, int, float, str, str]]:
    """Return (idx, ts, return_pct, direction, detector_id)."""
    hits: list[tuple[int, int, float, str, str]] = []
    last_ts = -9999
    for i in range(20, len(series)):
        ts = series[i][0]
        if ts - last_ts < 300:
            continue
        px = series[i][1]
        for det_id, cfg in SHOCK_THRESHOLDS.items():
            window = int(cfg["window_sec"])
            target = ts - window
            ref = None
            for j in range(i, -1, -1):
                if series[j][0] <= target:
                    ref = series[j][1]
                    break
            if not ref or ref <= 0:
                continue
            ret = (px / ref - 1.0) * 100.0
            fired = False
            if det_id in ("SHOCK_A", "SHOCK_B", "SHOCK_C"):
                fired = abs(ret) >= float(cfg["min_abs_return_pct"])
            elif det_id == "SHOCK_D":
                fired = abs(ret) >= float(cfg["min_abs_return_pct"])
            elif det_id == "SHOCK_E":
                fired = abs(ret) >= float(cfg.get("min_relative_return_pct", 1.0))
            if fired:
                direction = SHOCK_DIRECTION_UP if ret > 0 else SHOCK_DIRECTION_DOWN
                hits.append((i, ts, ret, direction, det_id))
                last_ts = ts
                break
    return hits


def _path_class(max_cont: float, max_rev: float, shock_abs: float) -> str:
    if shock_abs <= 0:
        return "UNKNOWN"
    if max_rev >= shock_abs * 0.75:
        return "V_SHAPE"
    if max_rev >= shock_abs * 0.35:
        return "SLOW_GRIND"
    if max_cont >= shock_abs * 0.5:
        return "CONTINUATION"
    return "MIXED"


def _persist_path_metrics(conn: Any, *, shock_id: int, series: list[tuple[int, float]],
                          idx: int, direction: str, shock_ret: float) -> None:
    ts0, px0 = series[idx]
    shock_abs = abs(shock_ret)
    fade_dir = SHOCK_DIRECTION_DOWN if direction == SHOCK_DIRECTION_UP else SHOCK_DIRECTION_UP

    for h in PATH_HORIZONS_SEC:
        target = ts0 + h
        px_h = None
        for j in range(idx, len(series)):
            if series[j][0] >= target:
                px_h = series[j][1]
                break
        if px_h is None:
            continue
        cont = _signed_move(px0, px_h, direction)
        rev = _signed_move(px0, px_h, fade_dir)
        reclaim = (rev / shock_abs * 100.0) if shock_abs else 0
        mfe = mae = 0.0
        for j in range(idx, len(series)):
            ts, px = series[j]
            if ts > ts0 + h:
                break
            r = _signed_move(px0, px, fade_dir)
            mfe = max(mfe, r)
            mae = min(mae, r)
        conn.execute(
            """
            INSERT OR REPLACE INTO market_events_replay_path_metrics (
              shock_id, horizon_sec, continuation_pct, reversal_pct,
              mfe_fade_pct, mae_fade_pct, path_class, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                shock_id, h, cont, rev, mfe, mae,
                _path_class(cont, rev, shock_abs),
                json.dumps({"reclaim_pct_of_shock": reclaim}),
            ),
        )


def run_shock_replay(
    conn: Any,
    *,
    run_tag: str = REPLAY_RUN_TAG_DEFAULT,
    days: int | None = None,
    symbols: list[str] | None = None,
) -> dict[str, int]:
    """Replay detectors over observations — writes to replay tables ONLY."""
    since = int(time.time()) - (days or 30) * 86400 if days else 0
    q = """
        SELECT i.canonical_asset, i.asset_class, o.obs_ts, o.trade_price, o.session_regime
        FROM market_events_price_observations o
        JOIN market_events_instruments i ON i.id = o.instrument_id
        WHERE o.trade_price IS NOT NULL AND o.trade_price > 0
    """
    params: list[Any] = []
    if since:
        q += " AND o.obs_ts >= ?"
        params.append(since)
    if symbols:
        q += f" AND i.canonical_asset IN ({','.join('?' for _ in symbols)})"
        params.extend(symbols)
    q += " ORDER BY i.canonical_asset, o.obs_ts"
    rows = conn.execute(q, params).fetchall()

    by_sym: dict[str, list[tuple[int, float]]] = defaultdict(list)
    meta_sym: dict[str, dict[str, Any]] = {}
    for r in rows:
        sym = r["canonical_asset"]
        by_sym[sym].append((int(r["obs_ts"]), float(r["trade_price"])))
        meta_sym[sym] = {"asset_class": r["asset_class"], "session": r["session_regime"]}

    if not by_sym:
        return {"run_id": 0, "shocks": 0}

    all_ts = [t for s in by_sym.values() for t, _ in s]
    run_id = _ensure_run(conn, run_tag=run_tag, start_ts=min(all_ts), end_ts=max(all_ts))
    shock_ts: list[int] = []
    stats = {"run_id": run_id, "shocks": 0, "paths": 0}

    for sym, series in by_sym.items():
        prod = _detect_production_shocks(series, sym)
        profile_eps = _find_shock_episodes(series, sym)
        f2_hits: list[tuple[int, int, float, str, str]] = []
        for i in range(60, len(series)):
            cand = detect_shock_f_v2_at_index(series, i, symbol=sym)
            if cand:
                f2_hits.append((i, cand.event_ts, cand.return_pct, cand.direction, "SHOCK_F_v2"))

        seen_ts: set[int] = set()
        for idx, ts, ret, direction, det_id in prod + [
            (i, t, r, d, "PROFILE_" + profile_name_for_symbol(sym))
            for i, t, r, d in profile_eps
        ] + f2_hits:
            if ts in seen_ts:
                continue
            seen_ts.add(ts)
            m = _compute_metrics(series, idx, ret, direction, sym)
            shock_id = insert_returning_id(
                conn,
                """
                INSERT INTO market_events_replay_shocks (
                  run_id, symbol, asset_class, event_ts, direction, detector_id,
                  impulse_pct, impulse_duration_sec, session_regime, source_provenance,
                  pre_volatility, raw_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, sym, meta_sym[sym].get("asset_class"), ts, direction, det_id,
                    ret, 60, meta_sym[sym].get("session"), "price_observations",
                    None, json.dumps({"forward": m.forward_returns, "profile": m.profile_name}),
                    int(time.time()),
                ),
            )
            _persist_path_metrics(conn, shock_id=shock_id, series=series, idx=idx,
                                  direction=direction, shock_ret=ret)
            shock_ts.append(ts)
            stats["shocks"] += 1
            stats["paths"] += 1

    persist_replay_split(conn, run_tag=run_tag, event_timestamps=shock_ts)
    conn.execute(
        "UPDATE market_events_replay_runs SET status = 'complete' WHERE id = ?",
        (run_id,),
    )
    return stats
