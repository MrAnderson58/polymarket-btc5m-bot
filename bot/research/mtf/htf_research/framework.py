"""Shared HTF bidirectional research framework — no 5m/15m parameter transfer."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from bot.research.mtf.snapshots import TABLE, ensure_tables

Obs = Any
FamilyFn = Callable[[Obs, list[Obs], int, dict[str, float]], str | None]
BuildObsFn = Callable[[sqlite3.Connection, sqlite3.Row], Obs]
WindowFn = Callable[[str], tuple[int, int] | None]


@dataclass
class HtfResearchSpec:
    timeframe: str
    slug_column: str
    prefix: str
    window_fn: WindowFn
    build_obs: BuildObsFn
    families: dict[str, FamilyFn]
    calibrate: Callable[[list[Obs]], dict[str, float]]
    exit_modes: tuple[str, ...]
    simulate_exit: Callable
    try_entry: Callable
    min_markets: int
    min_trades_family: int
    min_trades_verdict: int
    wf_train_ratio: float
    ready_verdict: str
    pf_strong: float = 1.25
    oos_min: float = 1.1
    bootstrap_min: float = 0.6
    regime_fn: Callable[[Any], str] | None = None


@dataclass
class FamilyResult:
    family: str
    side: str
    exit_mode: str
    n: int = 0
    pf: float = 0.0
    wr: float = 0.0
    avg_pnl: float = 0.0
    max_dd: float = 0.0
    max_cl: int = 0
    stress_pp_01: dict[str, float] = field(default_factory=dict)
    bootstrap_p_pf_gt1: float | None = None
    oos_pf: float | None = None
    temporal_stable: bool = False
    regime_slice: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family, "side": self.side, "exit_mode": self.exit_mode,
            "n": self.n, "pf": self.pf, "wr": self.wr, "avg_pnl": self.avg_pnl,
            "max_dd": self.max_dd, "max_cl": self.max_cl,
            "stress_pp_01": self.stress_pp_01,
            "bootstrap_p_pf_gt1": self.bootstrap_p_pf_gt1,
            "oos_pf": self.oos_pf, "temporal_stable": self.temporal_stable,
            "regime_slice": self.regime_slice,
        }


def load_market_paths(
    conn: sqlite3.Connection,
    spec: HtfResearchSpec,
) -> dict[str, list[Obs]]:
    rows = conn.execute(
        f"""
        SELECT * FROM {TABLE}
        WHERE {spec.slug_column} IS NOT NULL
          AND market_{spec.prefix}_yes_ask IS NOT NULL
        ORDER BY timestamp ASC
        """
    ).fetchall()

    raw: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        slug = row[spec.slug_column]
        bounds = spec.window_fn(slug)
        if bounds is None:
            continue
        ws, we = bounds
        ts = int(row["timestamp"])
        if not (ws <= ts < we):
            continue
        raw[slug].append(row)

    paths: dict[str, list[Obs]] = {}
    for slug, snap_rows in raw.items():
        obs_list = [spec.build_obs(conn, r) for r in snap_rows]
        obs_list.sort(key=lambda o: o.timestamp)
        if len(obs_list) >= 3:
            paths[slug] = obs_list
    return paths


def list_markets_chronological(paths: dict[str, list[Obs]]) -> list[str]:
    return sorted(paths.keys(), key=lambda s: paths[s][0].window_start_ts)


def coverage_audit(conn: sqlite3.Connection, spec: HtfResearchSpec, paths: dict) -> dict[str, Any]:
    total = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE} WHERE {spec.slug_column} IS NOT NULL"
    ).fetchone()[0]
    with_strike = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE} WHERE {spec.slug_column} IS NOT NULL AND market_{spec.prefix}_strike IS NOT NULL"
    ).fetchone()[0]
    with_sl = conn.execute(
        f"SELECT COUNT(*) FROM {TABLE} WHERE {spec.slug_column} IS NOT NULL AND market_{spec.prefix}_seconds_left IS NOT NULL"
    ).fetchone()[0]
    return {
        "timeframe": spec.timeframe,
        "snapshot_rows": total,
        "markets": len(paths),
        "observations": sum(len(p) for p in paths.values()),
        "strike_coverage_pct": round(with_strike / total * 100, 1) if total else 0,
        "seconds_left_coverage_pct": round(with_sl / total * 100, 1) if total else 0,
    }


def concentration_analysis(trades: list) -> dict[str, Any]:
    by_market: dict[str, float] = defaultdict(float)
    for t in trades:
        if t.pnl_pct is not None:
            by_market[t.market_slug] += t.pnl_pct
    if not by_market:
        return {"top_market_pct": 0}
    total = sum(by_market.values())
    top = max(by_market.values()) if by_market else 0
    return {
        "unique_markets": len(by_market),
        "top_market_pct": round(top / total * 100, 1) if total else 0,
    }
