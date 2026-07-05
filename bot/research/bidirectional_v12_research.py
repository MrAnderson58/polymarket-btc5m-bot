"""V1.2 research — asymmetric YES/NO, time window, Q4 degradation (READ-ONLY).

Does NOT modify runtime, strategy params, execution, or DB writes.

Hypotheses to explore for V1.2:
  - NO continuation vs YES momentum asymmetry
  - YES entry-price gating (ask buckets) vs flat NO max_ask
  - Time window edge effects (15-250s live vs replay 180s cap)
  - Q4 performance degradation drivers

Usage:
  python -m bot.research.bidirectional_v12_research
  python -m bot.research.bidirectional_v12_research --corrected
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
from dataclasses import dataclass
from typing import Any

from bot.research.bidirectional_live_audit import (
    LiveTrade,
    WINDOW_SECONDS,
    _pf,
    analyze_buckets,
    analyze_btc_moves,
    bucket_seconds_left,
    compute_metrics,
    dedupe_first_trade_per_market,
    load_closed_trades,
    load_observation_stats,
    temporal_quarters,
)
from bot.strategy.bidirectional_momentum import EntryConfig
from bot.strategy.bidirectional_shadow import SHADOW_ENTRY_CONFIG

REPORT_WIDTH = 72


@dataclass
class SkipReasonCount:
    reason: str
    side_hint: str
    count: int


def _metrics_subset(trades: list[LiveTrade]) -> dict[str, Any]:
    m = compute_metrics(trades)
    return m if m.get("trades", 0) else {"trades": 0}


def _continuation_label(trade: LiveTrade) -> str:
    """Classify entry as momentum vs continuation based on btc_move_30s sign vs side."""
    move = trade.btc_move_30s
    if move is None:
        return "unknown"
    if trade.side == "YES":
        if move >= 5:
            return "yes_momentum"
        if move <= -5:
            return "yes_countertrend"
        return "yes_weak"
    if trade.side == "NO":
        if move <= -5:
            return "no_momentum"
        if move >= 5:
            return "no_continuation_against_move"
        return "no_weak"
    return "other"


def analyze_no_continuation(trades: list[LiveTrade]) -> dict[str, dict]:
    """NO trades where BTC still moving up (potential continuation trap)."""
    buckets: dict[str, list[LiveTrade]] = {}
    for t in trades:
        if t.side != "NO":
            continue
        label = _continuation_label(t)
        buckets.setdefault(label, []).append(t)
    return {k: _metrics_subset(v) for k, v in sorted(buckets.items())}


def analyze_yes_entry_gating(trades: list[LiveTrade]) -> dict[str, Any]:
    yes = [t for t in trades if t.side == "YES"]
    no = [t for t in trades if t.side == "NO"]
    yes_buckets = analyze_buckets(trades, "YES")
    no_buckets = analyze_buckets(trades, "NO")

    cfg = SHADOW_ENTRY_CONFIG
    yes_expensive = [t for t in yes if t.entry_price > cfg.yes_max_ask - 0.05]
    yes_cheap = [t for t in yes if t.entry_price <= cfg.yes_max_ask - 0.10]

    return {
        "yes_buckets": yes_buckets,
        "no_buckets": no_buckets,
        "yes_expensive_tail": _metrics_subset(yes_expensive),
        "yes_cheap": _metrics_subset(yes_cheap),
        "yes_all": _metrics_subset(yes),
        "no_all": _metrics_subset(no),
        "yes_max_ask_threshold": cfg.yes_max_ask,
    }


def analyze_time_window(trades: list[LiveTrade]) -> dict[str, Any]:
    cfg = SHADOW_ENTRY_CONFIG
    by_sec_left: dict[str, list[LiveTrade]] = {}
    by_sec_from_start: dict[str, list[LiveTrade]] = {}
    replay_cap = 180

    for t in trades:
        sl = bucket_seconds_left(t.seconds_left)
        by_sec_left.setdefault(sl, []).append(t)
        if t.window_start_ts is not None:
            sfs = t.entry_ts - int(t.window_start_ts)
            if sfs <= 60:
                band = "15-60"
            elif sfs <= 120:
                band = "60-120"
            elif sfs <= replay_cap:
                band = "120-180"
            elif sfs <= cfg.max_seconds_from_start:
                band = "180-250_live_only"
            else:
                band = "out_of_window"
            by_sec_from_start.setdefault(band, []).append(t)

    return {
        "seconds_left": {k: _metrics_subset(v) for k, v in sorted(by_sec_left.items())},
        "seconds_from_start": {k: _metrics_subset(v) for k, v in sorted(by_sec_from_start.items())},
        "live_only_window_trades": sum(
            1 for t in trades
            if t.window_start_ts and (t.entry_ts - int(t.window_start_ts)) > replay_cap
        ),
        "replay_cap_seconds": replay_cap,
        "live_max_seconds": cfg.max_seconds_from_start,
    }


def analyze_q4_degradation(trades: list[LiveTrade]) -> dict[str, Any]:
    quarters = temporal_quarters(trades)
    q4_trades: list[LiveTrade] = []
    q13_trades: list[LiveTrade] = []
    if not trades:
        return {"quarters": quarters}

    n = len(trades)
    q_size = max(1, n // 4)
    q13_trades = trades[: 3 * q_size]
    q4_trades = trades[3 * q_size :]

    def _profile(chunk: list[LiveTrade]) -> dict[str, Any]:
        if not chunk:
            return {"trades": 0}
        yes_n = sum(1 for t in chunk if t.side == "YES")
        no_n = len(chunk) - yes_n
        regimes: dict[str, int] = {}
        for t in chunk:
            r = t.entry_regime or "UNKNOWN"
            regimes[r] = regimes.get(r, 0) + 1
        avg_entry = statistics.mean(t.entry_price for t in chunk)
        avg_sl = statistics.mean(t.seconds_left or 0 for t in chunk)
        cont = analyze_no_continuation(chunk)
        return {
            "metrics": compute_metrics(chunk),
            "yes_pct": round(100 * yes_n / len(chunk), 1),
            "no_pct": round(100 * no_n / len(chunk), 1),
            "avg_entry_price": round(avg_entry, 3),
            "avg_seconds_left": round(avg_sl, 1),
            "regime_mix": regimes,
            "no_continuation_breakdown": cont,
        }

    q4m = quarters.get("Q4", {})
    q3m = quarters.get("Q3", {})
    pf_delta = None
    if q4m.get("trades") and q3m.get("pf"):
        pf_delta = round(q4m.get("pf", 0) - q3m.get("pf", 0), 3)

    return {
        "quarters": quarters,
        "q4_vs_q13": {
            "q13": _profile(q13_trades),
            "q4": _profile(q4_trades),
            "pf_q4_minus_q3": pf_delta,
        },
    }


def load_skip_reasons(conn: sqlite3.Connection) -> list[SkipReasonCount]:
    rows = conn.execute(
        """
        SELECT reason, decision, COUNT(*) AS n
        FROM bidirectional_shadow_observations
        WHERE decision = 'SKIP'
        GROUP BY reason, decision
        ORDER BY n DESC
        """
    ).fetchall()
    out: list[SkipReasonCount] = []
    for r in rows:
        reason = r["reason"] or "unknown"
        side_hint = "NO" if "no_ask" in reason else "YES" if "yes_ask" in reason else "-"
        out.append(SkipReasonCount(reason=reason, side_hint=side_hint, count=int(r["n"])))
    return out


def run_v12_research(conn: sqlite3.Connection, *, corrected: bool = True) -> dict[str, Any]:
    raw = load_closed_trades(conn)
    if corrected:
        trades, dedup = dedupe_first_trade_per_market(raw)
    else:
        trades, dedup = raw, {"raw": len(raw), "corrected": len(raw), "duplicates_removed": 0}

    obs = load_observation_stats(conn)
    skip_reasons = load_skip_reasons(conn)

    return {
        "corrected": corrected,
        "dedup": dedup,
        "trade_count": len(trades),
        "observations": obs,
        "skip_reasons": skip_reasons,
        "side_metrics": {
            "YES": compute_metrics([t for t in trades if t.side == "YES"]),
            "NO": compute_metrics([t for t in trades if t.side == "NO"]),
        },
        "no_continuation": analyze_no_continuation(trades),
        "yes_entry_gating": analyze_yes_entry_gating(trades),
        "time_window": analyze_time_window(trades),
        "q4_degradation": analyze_q4_degradation(trades),
        "btc_move_yes": analyze_btc_moves(trades, "YES"),
        "btc_move_no": analyze_btc_moves(trades, "NO"),
        "v12_hypotheses": _build_hypotheses(trades),
    }


def _build_hypotheses(trades: list[LiveTrade]) -> list[str]:
    cfg = SHADOW_ENTRY_CONFIG
    hypotheses: list[str] = []
    yes_m = compute_metrics([t for t in trades if t.side == "YES"])
    no_m = compute_metrics([t for t in trades if t.side == "NO"])
    if yes_m.get("trades") and no_m.get("trades"):
        if no_m.get("pf", 0) > yes_m.get("pf", 0) + 0.3:
            hypotheses.append(
                "NO outperforms YES — consider NO-specific continuation filter or tighter YES gating."
            )
        elif yes_m.get("pf", 0) > no_m.get("pf", 0) + 0.3:
            hypotheses.append(
                "YES outperforms NO — NO avoid zone may be insufficient; check no_continuation trades."
            )

    no_cont = [t for t in trades if t.side == "NO" and (t.btc_move_30s or 0) > 5]
    if no_cont:
        pnls = [t.pnl_pct or 0 for t in no_cont]
        if _pf(pnls) < 1.0:
            hypotheses.append(
                f"NO entries against positive btc_move_30s (n={len(no_cont)}) lose — "
                "V1.2 candidate: skip NO when move_30s > 0."
            )

    live_only = [
        t for t in trades
        if t.window_start_ts and (t.entry_ts - int(t.window_start_ts)) > 180
    ]
    if live_only:
        pnls = [t.pnl_pct or 0 for t in live_only]
        hypotheses.append(
            f"Live-only window 180-250s: n={len(live_only)} PF={_pf(pnls):.2f} — "
            "replay understates this slice."
        )

    q = temporal_quarters(trades)
    q4 = q.get("Q4", {})
    q1 = q.get("Q1", {})
    if q4.get("trades", 0) >= 10 and q1.get("pf", 0) - q4.get("pf", 0) > 0.5:
        hypotheses.append(
            f"Q4 PF ({q4.get('pf')}) << Q1 ({q1.get('pf')}) — check regime mix, side mix, entry price drift."
        )

    if not hypotheses:
        hypotheses.append("No strong V1.2 asymmetry signal in current sample — continue shadow collection.")

    hypotheses.append(
        f"Current config: yes_max_ask={cfg.yes_max_ask} no_max_ask={cfg.no_max_ask} "
        f"window={cfg.min_seconds_from_start}-{cfg.max_seconds_from_start}s "
        "(research only — NOT applied to runtime)."
    )
    return hypotheses


def render_report(research: dict[str, Any]) -> str:
    lines: list[str] = []
    w = REPORT_WIDTH

    def h(title: str) -> None:
        lines.append("")
        lines.append("=" * w)
        lines.append(title)
        lines.append("=" * w)

    def row(label: str, m: dict) -> None:
        if not m or m.get("trades", 0) == 0:
            lines.append(f"  {label}: no trades")
            return
        lines.append(
            f"  {label}: N={m['trades']} WR={m['wr']}% PF={m['pf']} avg={m['avg_pnl']}%"
        )

    h("BIDIRECTIONAL V1.2 RESEARCH (READ-ONLY — NOT APPLIED TO RUNTIME)")
    if research.get("corrected"):
        lines.append("Mode: corrected (first trade per market)")
    lines.append(f"Closed trades: {research['trade_count']}")

    h("1. SIDE ASYMMETRY (YES vs NO)")
    for side in ("YES", "NO"):
        row(side, research["side_metrics"].get(side, {}))

    h("2. NO CONTINUATION / COUNTER-TREND")
    lines.append("  NO entries by btc_move_30s vs side alignment:")
    for label, m in research["no_continuation"].items():
        row(f"    {label}", m)

    h("3. YES ENTRY-PRICE GATING")
    yg = research["yes_entry_gating"]
    lines.append(f"  yes_max_ask threshold: {yg['yes_max_ask_threshold']}")
    row("  YES all", yg["yes_all"])
    row("  YES cheap (ask <= max-0.10)", yg["yes_cheap"])
    row("  YES expensive tail (ask > max-0.05)", yg["yes_expensive_tail"])
    row("  NO all", yg["no_all"])
    lines.append("  YES buckets:")
    for bucket, m in yg["yes_buckets"].items():
        lines.append(
            f"    {bucket:<10} N={m['n']:>3} PF={m['pf']:>5.3f} WR={m['wr']:>5.1f}%"
        )
    lines.append("  NO buckets:")
    for bucket, m in yg["no_buckets"].items():
        lines.append(
            f"    {bucket:<10} N={m['n']:>3} PF={m['pf']:>5.3f} WR={m['wr']:>5.1f}%"
        )

    h("4. TIME WINDOW")
    tw = research["time_window"]
    lines.append(
        f"  Replay cap: {tw['replay_cap_seconds']}s | Live max: {tw['live_max_seconds']}s | "
        f"Live-only entries: {tw['live_only_window_trades']}"
    )
    lines.append("  By seconds_left at entry:")
    for band, m in tw["seconds_left"].items():
        row(f"    {band}", m)
    lines.append("  By seconds_from_start:")
    for band, m in tw["seconds_from_start"].items():
        row(f"    {band}", m)

    h("5. Q4 DEGRADATION")
    qd = research["q4_degradation"]
    for q, m in qd.get("quarters", {}).items():
        row(f"  {q}", m)
    comp = qd.get("q4_vs_q13", {})
    if comp:
        lines.append("  Q4 vs Q1-Q3 profile:")
        for label in ("q13", "q4"):
            p = comp.get(label, {})
            if p.get("trades", 0) == 0 and not p.get("metrics"):
                continue
            met = p.get("metrics", p)
            lines.append(
                f"    {label.upper()}: PF={met.get('pf', '?')} "
                f"YES%={p.get('yes_pct', '?')} avg_entry={p.get('avg_entry_price', '?')} "
                f"avg_sec_left={p.get('avg_seconds_left', '?')}"
            )
            if p.get("regime_mix"):
                lines.append(f"      regimes: {p['regime_mix']}")
        if comp.get("pf_q4_minus_q3") is not None:
            lines.append(f"  PF delta Q4-Q3: {comp['pf_q4_minus_q3']:+.3f}")

    h("6. SKIP REASONS (observation funnel)")
    for sr in research["skip_reasons"][:15]:
        lines.append(f"  {sr.reason:<28} n={sr.count:>5}  side_hint={sr.side_hint}")

    h("7. V1.2 HYPOTHESES (research only)")
    for hyp in research["v12_hypotheses"]:
        lines.append(f"  • {hyp}")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    from bot.database import connect, init_db
    from bot.strategy.bidirectional_shadow import ensure_tables

    parser = argparse.ArgumentParser(description="V1.2 research report (read-only)")
    parser.add_argument(
        "--corrected",
        action="store_true",
        help="First trade per market only (default unless --raw)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Include duplicate markets",
    )
    args = parser.parse_args()
    corrected = not args.raw

    init_db()
    with connect() as conn:
        ensure_tables(conn)
        research = run_v12_research(conn, corrected=corrected)
        print(render_report(research))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
