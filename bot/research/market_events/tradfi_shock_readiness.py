"""TradFi shock detector readiness from observation data."""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from bot.research.market_events.config import SHOCK_THRESHOLDS
from bot.research.market_events.event_report import _days_ago_ts
from bot.research.market_events.shock_opportunity_audit import RESEARCH_GRID, _audit_symbol, _return_at

TRADFI_SYMBOLS = ("GOLD", "OIL", "SILVER", "NVDA", "TSLA", "META", "NASDAQ100_PROXY")
CANONICAL_ALIASES = {"XAU": "GOLD", "CL": "OIL", "XAG": "SILVER", "QQQ": "NASDAQ100_PROXY"}


def _load_tradfi_series(conn: Any, since: int) -> tuple[dict[str, list[tuple[int, float]]], dict[str, list]]:
    out: dict[str, list[tuple[int, float]]] = {}
    rows = conn.execute(
        """
        SELECT i.canonical_asset, o.obs_ts, o.trade_price, o.spread_bps, o.basis_bps,
               o.session_regime, o.raw_json
        FROM market_events_price_observations o
        JOIN market_events_instruments i ON i.id = o.instrument_id
        WHERE o.obs_ts >= ? AND i.asset_class != 'CRYPTO' AND o.trade_price IS NOT NULL
        ORDER BY i.canonical_asset, o.obs_ts
        """,
        (since,),
    ).fetchall()
    meta: dict[str, list] = defaultdict(list)
    for r in rows:
        sym = r["canonical_asset"]
        out.setdefault(sym, []).append((int(r["obs_ts"]), float(r["trade_price"])))
        meta[sym].append(r)
    return out, meta


def tradfi_shock_readiness(conn: Any, *, days: int = 1) -> str:
    since = _days_ago_ts(days)
    series_map, meta_map = _load_tradfi_series(conn, since)
    lines = [
        "TRADFI SHOCK READINESS AUDIT",
        f"window_days: {days}",
        "",
    ]
    for sym in TRADFI_SYMBOLS:
        series = series_map.get(sym, [])
        meta_rows = meta_map.get(sym, [])
        lines.append(f"=== {sym} ===")
        if not series:
            lines.append("  no observations in window")
            lines.append("")
            continue
        lines.append(f"  observation_count: {len(series)}")
        if len(series) >= 2:
            gaps = [series[i][0] - series[i - 1][0] for i in range(1, len(series))]
            lines.append(f"  median_interval_sec: {statistics.median(gaps):.1f}")
            sorted_gaps = sorted(gaps)
            p95_idx = int(len(sorted_gaps) * 0.95)
            lines.append(f"  p95_interval_sec: {sorted_gaps[min(p95_idx, len(sorted_gaps) - 1)]:.1f}")
            max_gap = max(gaps)
            lines.append(f"  max_gap_sec: {max_gap}")
        spreads = [float(r["spread_bps"]) for r in meta_rows if r["spread_bps"] is not None]
        bases = [abs(float(r["basis_bps"])) for r in meta_rows if r["basis_bps"] is not None]
        if spreads:
            lines.append(f"  spread_median_bps: {statistics.median(spreads):.2f}")
            lines.append(f"  spread_p95_bps: {sorted(spreads)[int(len(spreads) * 0.95)]:.2f}")
        if bases:
            lines.append(f"  basis_abs_median_bps: {statistics.median(bases):.2f}")
            lines.append(f"  basis_abs_p95_bps: {sorted(bases)[int(len(bases) * 0.95)]:.2f}")
        for window in (30, 60, 180):
            moves = []
            for i in range(len(series)):
                r = _return_at(series, i, window)
                if r is not None:
                    moves.append(abs(r))
            if moves:
                sm = sorted(moves)
                lines.append(
                    f"  |return|_{window}s p95={sm[int(len(sm) * 0.95)]:.3f}% "
                    f"p99={sm[int(len(sm) * 0.99)]:.3f}%",
                )
        sessions: dict[str, int] = defaultdict(int)
        for r in meta_rows:
            sessions[r["session_regime"] or "unknown"] += 1
        lines.append(f"  session_regime_split: {dict(sessions)}")
        prod_hits = 0
        for det_id, cfg in SHOCK_THRESHOLDS.items():
            w = int(cfg["window_sec"])
            thr = float(cfg.get("min_abs_return_pct") or cfg.get("min_relative_return_pct") or 99)
            for i in range(len(series)):
                ret = _return_at(series, i, w)
                if ret is not None and abs(ret) >= thr:
                    prod_hits += 1
        cells = _audit_symbol(series)
        grid_hits = sum(c.candidate_count for c in cells.values())
        if prod_hits == 0:
            verdict = "zero_events_at_crypto_thresholds"
        elif grid_hits < 10:
            verdict = "few_events"
        else:
            verdict = "many_candidates"
        lines.append(f"  crypto_threshold_compatibility: {verdict} (prod_hits={prod_hits})")
        lines.append("")
    lines.append("Do not auto-activate TradFi paper strategies from this audit.")
    return "\n".join(lines)
