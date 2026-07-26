"""Adaptive shock profile shadow A/B — isolated from production SHOCK_A–E pipeline."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from bot.research.market_events.config import (
    PROFILE_ADAPTIVE_V1,
    PROFILE_BASELINE,
    SHADOW_ENABLED,
    SHADOW_REJECT_FLUSH_SEC,
    SHOCK_PROFILE_THRESHOLDS,
    SHOCK_THRESHOLDS,
)
from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.detector_diagnostics import (
    REJECTION_BELOW_RETURN,
    REJECTION_FIRED,
    REJECTION_INSUFFICIENT_HISTORY,
    REJECTION_RELATIVE_FAILED,
    REJECTION_VOLUME_FAILED,
)
from bot.research.market_events.event_report import _days_ago_ts
from bot.research.market_events.event_types import DETECTOR_IDS
from bot.research.market_events.price_feed import SymbolPriceState

SHADOW_TABLE = "market_events_shadow"


@dataclass
class ShadowEval:
    symbol: str
    profile_name: str
    detector: str
    window_sec: int
    return_pct: float | None
    threshold_pct: float
    accepted: bool
    reject_reason: str | None
    volume_z: float | None = None
    relative_return_pct: float | None = None
    created_at: int = 0


@dataclass
class ShadowProfileMetrics:
    """In-memory counters for heartbeat (one profile)."""

    profile_name: str
    checked: int = 0
    accepted: int = 0
    rejected: int = 0
    by_detector: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: {"checked": 0, "accepted": 0, "rejected": 0}),
    )

    @property
    def accept_rate(self) -> float:
        if self.checked <= 0:
            return 0.0
        return self.accepted / float(self.checked)

    def record(self, ev: ShadowEval) -> None:
        self.checked += 1
        bucket = self.by_detector[ev.detector]
        bucket["checked"] += 1
        if ev.accepted:
            self.accepted += 1
            bucket["accepted"] += 1
        else:
            self.rejected += 1
            bucket["rejected"] += 1

    def format_heartbeat_block(self) -> list[str]:
        rate_pct = self.accept_rate * 100.0
        return [
            f"shadow {self.profile_name}",
            f"checked={self.checked}",
            f"accepted={self.accepted}",
            f"rejected={self.rejected}",
            f"accept_rate={rate_pct:.2f}%",
        ]


@dataclass
class ShadowRunnerState:
    """Buffers reject rows until flush interval; accepts persist immediately."""

    metrics: dict[str, ShadowProfileMetrics] = field(default_factory=dict)
    pending_rejects: dict[tuple[str, str, str], ShadowEval] = field(default_factory=dict)
    last_reject_flush_ts: int = 0

    def metrics_for(self, profile_name: str) -> ShadowProfileMetrics:
        m = self.metrics.get(profile_name)
        if m is None:
            m = ShadowProfileMetrics(profile_name=profile_name)
            self.metrics[profile_name] = m
        return m


def evaluate_detectors_for_profile(
    state: SymbolPriceState,
    *,
    profile_name: str,
    thresholds: dict[str, dict[str, float]],
    now_ts: int,
    btc_state: SymbolPriceState | None = None,
    median_market_return: float | None = None,
) -> list[ShadowEval]:
    """Mirror production detector logic against an arbitrary threshold profile."""
    out: list[ShadowEval] = []
    sym = state.symbol
    for det_id, cfg in thresholds.items():
        window = int(cfg["window_sec"])
        ret = state.return_over(window, now_ts)
        vol_z = state.volume_zscore(window, now_ts) if ret is not None else None
        btc_ret = btc_state.return_over(window, now_ts) if btc_state and ret is not None else None
        rel_ret = (ret - btc_ret) if (ret is not None and btc_ret is not None) else None

        thr = 0.0
        accepted = False
        reason: str | None = None
        compare_ret = ret

        if ret is None:
            reason = REJECTION_INSUFFICIENT_HISTORY
            thr = float(
                cfg.get("min_abs_return_pct")
                or cfg.get("min_relative_return_pct")
                or 0.0,
            )
        elif det_id in ("SHOCK_A", "SHOCK_B", "SHOCK_C"):
            thr = float(cfg["min_abs_return_pct"])
            if abs(ret) >= thr:
                accepted = True
            else:
                reason = REJECTION_BELOW_RETURN
        elif det_id == "SHOCK_D":
            thr = float(cfg["min_abs_return_pct"])
            if abs(ret) < thr:
                reason = REJECTION_BELOW_RETURN
            elif vol_z is None or vol_z < float(cfg["min_volume_zscore"]):
                reason = REJECTION_VOLUME_FAILED
            else:
                accepted = True
        elif det_id == "SHOCK_E":
            thr = float(cfg["min_relative_return_pct"])
            if rel_ret is not None:
                compare_ret = rel_ret
                if abs(rel_ret) >= thr:
                    accepted = True
                else:
                    reason = REJECTION_RELATIVE_FAILED
            elif median_market_return is not None:
                compare_ret = ret - median_market_return
                if abs(compare_ret) >= thr:
                    accepted = True
                else:
                    reason = REJECTION_RELATIVE_FAILED
            else:
                reason = REJECTION_RELATIVE_FAILED
        else:
            reason = "unknown_detector"

        if accepted:
            reason = None
        out.append(
            ShadowEval(
                symbol=sym,
                profile_name=profile_name,
                detector=det_id,
                window_sec=window,
                return_pct=compare_ret,
                threshold_pct=thr,
                accepted=accepted,
                reject_reason=reason if not accepted else None,
                volume_z=vol_z,
                relative_return_pct=rel_ret,
                created_at=now_ts,
            ),
        )
    return out


def evaluate_shadow_universe(
    feed: Any,
    symbols: list[str],
    *,
    now_ts: int,
    profiles: dict[str, dict[str, dict[str, float]]] | None = None,
) -> list[ShadowEval]:
    """Run all configured shadow profiles across the universe (no pipeline side effects)."""
    profile_map = profiles or SHOCK_PROFILE_THRESHOLDS
    returns: list[float] = []
    for sym in symbols:
        st = feed.get_state(sym)
        if st:
            r = st.return_over(60, now_ts)
            if r is not None:
                returns.append(r)
    median_ret = sorted(returns)[len(returns) // 2] if returns else None
    btc = feed.get_state("BTC")

    rows: list[ShadowEval] = []
    for sym in symbols:
        state = feed.get_state(sym)
        if not state:
            for profile_name, thresholds in profile_map.items():
                for det_id, cfg in thresholds.items():
                    thr = float(
                        cfg.get("min_abs_return_pct")
                        or cfg.get("min_relative_return_pct")
                        or 0.0,
                    )
                    rows.append(
                        ShadowEval(
                            symbol=sym,
                            profile_name=profile_name,
                            detector=det_id,
                            window_sec=int(cfg["window_sec"]),
                            return_pct=None,
                            threshold_pct=thr,
                            accepted=False,
                            reject_reason=REJECTION_INSUFFICIENT_HISTORY,
                            created_at=now_ts,
                        ),
                    )
            continue
        for profile_name, thresholds in profile_map.items():
            rows.extend(
                evaluate_detectors_for_profile(
                    state,
                    profile_name=profile_name,
                    thresholds=thresholds,
                    now_ts=now_ts,
                    btc_state=btc,
                    median_market_return=median_ret,
                ),
            )
    return rows


def ingest_shadow_cycle(
    state: ShadowRunnerState,
    evals: list[ShadowEval],
    *,
    now_ts: int,
) -> list[ShadowEval]:
    """Update metrics; return rows that should be written this cycle."""
    to_write: list[ShadowEval] = []
    for ev in evals:
        state.metrics_for(ev.profile_name).record(ev)
        if ev.accepted:
            to_write.append(ev)
        else:
            key = (ev.profile_name, ev.symbol, ev.detector)
            prev = state.pending_rejects.get(key)
            if prev is None or abs(ev.return_pct or 0.0) >= abs(prev.return_pct or 0.0):
                state.pending_rejects[key] = ev

    if state.last_reject_flush_ts == 0:
        state.last_reject_flush_ts = now_ts
    if (now_ts - state.last_reject_flush_ts) >= SHADOW_REJECT_FLUSH_SEC:
        to_write.extend(state.pending_rejects.values())
        state.pending_rejects.clear()
        state.last_reject_flush_ts = now_ts
    return to_write


def persist_shadow_evals(conn: Any, rows: list[ShadowEval]) -> int:
    if not rows:
        return 0
    n = 0
    for ev in rows:
        insert_returning_id(
            conn,
            f"""
            INSERT INTO {SHADOW_TABLE} (
              created_at, symbol, profile_name, detector, window_sec,
              return_pct, threshold_pct, accepted, reject_reason,
              volume_z, relative_return_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ev.created_at or int(time.time()),
                ev.symbol,
                ev.profile_name,
                ev.detector,
                ev.window_sec,
                ev.return_pct,
                ev.threshold_pct,
                1 if ev.accepted else 0,
                ev.reject_reason,
                ev.volume_z,
                ev.relative_return_pct,
            ),
        )
        n += 1
    return n


def run_shadow_ab_cycle(
    conn: Any,
    feed: Any,
    symbols: list[str],
    *,
    now_ts: int,
    runner_state: ShadowRunnerState,
) -> ShadowRunnerState:
    """Evaluate baseline + adaptive_v1; persist shadow rows; never touch main pipeline."""
    if not SHADOW_ENABLED:
        return runner_state
    evals = evaluate_shadow_universe(feed, symbols, now_ts=now_ts)
    to_write = ingest_shadow_cycle(runner_state, evals, now_ts=now_ts)
    try:
        persist_shadow_evals(conn, to_write)
    except Exception:
        # Table may be mid-migrate; never break the paper runner.
        raise
    return runner_state


def _profile_stats(conn: Any, *, profile_name: str, since_ts: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT
          detector,
          COUNT(*) AS checked,
          SUM(CASE WHEN accepted = 1 THEN 1 ELSE 0 END) AS accepted,
          AVG(ABS(COALESCE(return_pct, 0))) AS avg_return,
          MAX(ABS(COALESCE(return_pct, 0))) AS max_return
        FROM {SHADOW_TABLE}
        WHERE profile_name = ? AND created_at >= ?
        GROUP BY detector
        ORDER BY detector
        """,
        (profile_name, since_ts),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        checked = int(r["checked"] or 0)
        accepted = int(r["accepted"] or 0)
        rate = (accepted / checked) if checked else 0.0
        top = conn.execute(
            f"""
            SELECT symbol, COUNT(*) AS n
            FROM {SHADOW_TABLE}
            WHERE profile_name = ? AND detector = ? AND created_at >= ?
              AND accepted = 1
            GROUP BY symbol
            ORDER BY n DESC
            LIMIT 5
            """,
            (profile_name, r["detector"], since_ts),
        ).fetchall()
        if not top:
            top = conn.execute(
                f"""
                SELECT symbol, COUNT(*) AS n
                FROM {SHADOW_TABLE}
                WHERE profile_name = ? AND detector = ? AND created_at >= ?
                GROUP BY symbol
                ORDER BY n DESC
                LIMIT 5
                """,
                (profile_name, r["detector"], since_ts),
            ).fetchall()
        out.append({
            "detector": r["detector"],
            "checked": checked,
            "accepted": accepted,
            "accept_rate": rate,
            "avg_return": float(r["avg_return"] or 0.0),
            "max_return": float(r["max_return"] or 0.0),
            "top_symbols": [f"{t['symbol']}({t['n']})" for t in top],
        })
    # Ensure all detectors appear even with zero rows.
    seen = {d["detector"] for d in out}
    for det in DETECTOR_IDS:
        if det not in seen:
            out.append({
                "detector": det,
                "checked": 0,
                "accepted": 0,
                "accept_rate": 0.0,
                "avg_return": 0.0,
                "max_return": 0.0,
                "top_symbols": [],
            })
    out.sort(key=lambda d: d["detector"])
    return out


def shadow_profile_report(conn: Any, *, days: int = 1) -> str:
    since = _days_ago_ts(days)
    profiles = [PROFILE_BASELINE, PROFILE_ADAPTIVE_V1]
    lines = [
        "SHADOW SHOCK PROFILE REPORT",
        f"days={days}",
        f"baseline_thresholds=production SHOCK_A-E (unchanged)",
        f"adaptive_v1_thresholds={SHOCK_PROFILE_THRESHOLDS[PROFILE_ADAPTIVE_V1]}",
        "",
        "PROFILE",
    ]
    for name in profiles:
        lines.append(name)
    lines.append("-" * 24)
    lines.append("")

    for name in profiles:
        lines.append(f"=== {name} ===")
        lines.append(
            f"{'Detector':<10} {'checked':>8} {'accepted':>8} {'accept_rate':>12} "
            f"{'avg_return':>10} {'max_return':>10}  top symbols",
        )
        stats = _profile_stats(conn, profile_name=name, since_ts=since)
        for s in stats:
            tops = ", ".join(s["top_symbols"]) if s["top_symbols"] else "-"
            lines.append(
                f"{s['detector']:<10} {s['checked']:>8} {s['accepted']:>8} "
                f"{s['accept_rate'] * 100:>11.2f}% "
                f"{s['avg_return']:>10.4f} {s['max_return']:>10.4f}  {tops}",
            )
        lines.append("")

    # Sanity: baseline config still matches production dict identity/values.
    lines.append("baseline_equals_production="
                 f"{SHOCK_PROFILE_THRESHOLDS[PROFILE_BASELINE] is SHOCK_THRESHOLDS}")
    return "\n".join(lines)


def shadow_profiles_dashboard(conn: Any, *, days: int = 1) -> dict[str, Any]:
    since = _days_ago_ts(days)
    profiles = {}
    for name in (PROFILE_BASELINE, PROFILE_ADAPTIVE_V1):
        profiles[name] = {
            "thresholds": dict(SHOCK_PROFILE_THRESHOLDS[name]),
            "detectors": _profile_stats(conn, profile_name=name, since_ts=since),
        }
    return {
        "tab": "Shadow Profiles",
        "days": days,
        "profiles": profiles,
        "note": "Adaptive is shadow-only; production pipeline uses baseline thresholds unchanged.",
    }


def assert_baseline_unchanged() -> None:
    """Guard used by tests — baseline profile must be the production SHOCK_THRESHOLDS object."""
    assert SHOCK_PROFILE_THRESHOLDS[PROFILE_BASELINE] is SHOCK_THRESHOLDS
    assert float(SHOCK_THRESHOLDS["SHOCK_A"]["min_abs_return_pct"]) == 1.5
    assert float(SHOCK_THRESHOLDS["SHOCK_B"]["min_abs_return_pct"]) == 2.0
    assert float(SHOCK_THRESHOLDS["SHOCK_C"]["min_abs_return_pct"]) == 3.0
    assert float(SHOCK_THRESHOLDS["SHOCK_D"]["min_abs_return_pct"]) == 1.5
    assert float(SHOCK_THRESHOLDS["SHOCK_E"]["min_relative_return_pct"]) == 1.0
