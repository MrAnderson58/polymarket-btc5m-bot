"""Asset-class profile shadow candidates — research only, no paper trades."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.event_report import _days_ago_ts
from bot.research.market_events.event_types import SHOCK_DIRECTION_DOWN, SHOCK_DIRECTION_UP
from bot.research.market_events.shock_profiles import PROFILE_VERSION, SHOCK_PROFILES, profile_name_for_symbol


def _return_at(series: list[tuple[int, float]], idx: int, window_sec: int) -> float | None:
    ts, px = series[idx]
    target = ts - window_sec
    ref_px = None
    for j in range(idx, -1, -1):
        if series[j][0] <= target:
            ref_px = series[j][1]
            break
    if ref_px is None or ref_px <= 0:
        return None
    return (px / ref_px - 1.0) * 100.0


def scan_profile_shadow_candidates(
    conn: Any,
    *,
    days: int = 1,
    persist: bool = True,
) -> dict[str, int]:
    since = _days_ago_ts(days)
    rows = conn.execute(
        """
        SELECT i.canonical_asset, i.asset_class, o.obs_ts, o.trade_price, o.session_regime
        FROM market_events_price_observations o
        JOIN market_events_instruments i ON i.id = o.instrument_id
        WHERE o.obs_ts >= ? AND o.trade_price IS NOT NULL AND o.trade_price > 0
        ORDER BY i.canonical_asset, o.obs_ts
        """,
        (since,),
    ).fetchall()
    by_sym: dict[str, list[tuple[int, float, str | None, str]]] = defaultdict(list)
    for r in rows:
        by_sym[r["canonical_asset"]].append((
            int(r["obs_ts"]), float(r["trade_price"]),
            r["session_regime"], r["asset_class"],
        ))

    counts: dict[str, int] = defaultdict(int)
    for sym, obs in by_sym.items():
        profile_name = profile_name_for_symbol(sym)
        profile = SHOCK_PROFILES[profile_name]
        series = [(ts, px) for ts, px, _, _ in obs]
        for i in range(20, len(series)):
            ts = series[i][0]
            session = obs[i][2]
            asset_class = obs[i][3]
            for window_sec, threshold in profile.windows_pct.items():
                ret = _return_at(series, i, window_sec)
                if ret is None or abs(ret) < profile.min_abs_return_pct:
                    continue
                if abs(ret) < threshold:
                    continue
                direction = SHOCK_DIRECTION_UP if ret > 0 else SHOCK_DIRECTION_DOWN
                counts[profile_name] += 1
                if persist:
                    insert_returning_id(
                        conn,
                        """
                        INSERT INTO market_events_profile_shadow_candidates (
                          symbol, profile_name, profile_version, event_ts, window_sec,
                          direction, return_pct, threshold_pct, min_abs_floor_pct,
                          session_regime, asset_class, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sym, profile_name, PROFILE_VERSION, ts, window_sec,
                            direction, ret, threshold, profile.min_abs_return_pct,
                            session, asset_class, int(time.time()),
                        ),
                    )
    return dict(counts)


def shock_profile_report(conn: Any, *, days: int = 1) -> str:
    rows = conn.execute(
        """
        SELECT profile_name, symbol, direction, session_regime, asset_class, COUNT(*) AS n
        FROM market_events_profile_shadow_candidates
        WHERE created_at >= ?
        GROUP BY profile_name, symbol, direction, session_regime, asset_class
        ORDER BY profile_name, n DESC
        """,
        (_days_ago_ts(days),),
    ).fetchall()
    lines = [
        "SHOCK PROFILE SHADOW REPORT (research only — production SHOCK_A-E unchanged)",
        f"profile_version: {PROFILE_VERSION}",
        f"days: {days}",
        "",
    ]
    if not rows:
        lines.append("No profile shadow candidates persisted. Run scan first via shock-profile-report --persist.")
        lines.append("")
        lines.append("Configured profiles:")
        for name, prof in SHOCK_PROFILES.items():
            lines.append(f"  {name}: windows={prof.windows_pct} min_abs={prof.min_abs_return_pct}%")
        return "\n".join(lines)

    by_profile: dict[str, list] = defaultdict(list)
    for r in rows:
        by_profile[r["profile_name"]].append(r)

    for prof_name, prof_rows in sorted(by_profile.items()):
        total = sum(r["n"] for r in prof_rows)
        lines.append(f"=== {prof_name} (candidates={total}) ===")
        for r in prof_rows[:20]:
            lines.append(
                f"  {r['symbol']} {r['direction']} session={r['session_regime'] or 'unknown'} "
                f"class={r['asset_class']} n={r['n']}",
            )
        lines.append("")
    return "\n".join(lines)
