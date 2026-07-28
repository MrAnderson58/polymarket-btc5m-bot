"""Research Pack 01 — trade statistics, buckets, pairs, playbook."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from bot.research.market_events.expectancy_intelligence.stats import (
    mean,
    median,
    profit_factor_from_pnls,
    safe_float,
    trade_outcome_stats,
)
from bot.research.market_events.research_pack_01.historical import load_closed_s55_trades

BUCKET_FEATURES: tuple[tuple[str, str], ...] = (
    ("funding", "Funding"),
    ("oi_delta", "OI"),
    ("trend", "Trend"),
    ("volatility", "Volatility"),
    ("fear_greed", "Fear & Greed"),
    ("ai_score", "AI Score"),
    ("confidence", "Confidence"),
)

PAIR_FEATURES: tuple[tuple[str, str, str], ...] = (
    ("trend", "funding", "Trend × Funding"),
    ("trend", "oi_delta", "Trend × OI"),
    ("trend", "volatility", "Trend × Volatility"),
    ("funding", "oi_delta", "Funding × OI"),
    ("fear_greed", "trend", "FearGreed × Trend"),
)

WINNER_LOSER_KEYS: tuple[tuple[str, str], ...] = (
    ("funding", "Funding"),
    ("trend", "Trend"),
    ("oi_delta", "OI"),
    ("volatility", "Volatility"),
    ("fear_greed", "FearGreed"),
    ("confidence", "Confidence"),
    ("ai_score", "AI Score"),
    ("market_regime", "Market regime"),
)


def _feature_value(trade: dict[str, Any], key: str) -> Any:
    if key == "market_regime":
        v = trade.get("market_regime")
        return str(v).strip() if v is not None and str(v).strip() else None
    return safe_float(trade.get(key))


def build_trade_statistics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(t["pnl_pct"]) for t in trades]
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    mfes = [float(t["mfe_pct"]) for t in trades if t.get("mfe_pct") is not None]
    maes = [float(t["mae_pct"]) for t in trades if t.get("mae_pct") is not None]
    holds = [float(t["duration_sec"]) for t in trades if t.get("duration_sec") is not None]
    tp_hits = [t for t in trades if int(t.get("reached_tp1") or 0) == 1]
    stop_hits = [t for t in trades if int(t.get("stopped") or 0) == 1]
    med = median(pnls)
    pf = profit_factor_from_pnls(pnls)
    agg = trade_outcome_stats(trades)
    return {
        "total_trades": n,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": agg["win_rate"],
        "average_pnl": agg["avg_pnl"],
        "median_pnl": round(med, 4) if med is not None else None,
        "profit_factor": pf,
        "expectancy": agg["expectancy"],
        "average_mfe": round(mean(mfes), 4) if mfes else None,
        "average_mae": round(mean(maes), 4) if maes else None,
        "largest_win": round(max(pnls), 4) if pnls else None,
        "largest_loss": round(min(pnls), 4) if pnls else None,
        "average_holding_sec": round(mean(holds), 1) if holds else None,
        "tp_reached_pct": round(100.0 * len(tp_hits) / n, 1) if n else 0.0,
        "stop_reached_pct": round(100.0 * len(stop_hits) / n, 1) if n else 0.0,
    }


def _equal_width_edges(values: list[float], n_buckets: int = 5) -> list[tuple[float, float]]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if lo == hi:
        return [(lo, hi)]
    step = (hi - lo) / n_buckets
    edges: list[tuple[float, float]] = []
    for i in range(n_buckets):
        a = lo + i * step
        b = lo + (i + 1) * step if i < n_buckets - 1 else hi
        edges.append((round(a, 4), round(b, 4)))
    return edges


def _bucket_label(lo: float, hi: float, *, scale_100: bool) -> str:
    if scale_100 and 0 <= lo and hi <= 100:
        return f"{lo:.0f}-{hi:.0f}"
    return f"{lo:.2f}-{hi:.2f}"


def _assign_bucket(val: float, edges: list[tuple[float, float]], *, key: str = "") -> str | None:
    scale_100 = key == "fear_greed"
    for i, (lo, hi) in enumerate(edges):
        is_last = i == len(edges) - 1
        if is_last:
            if lo <= val <= hi:
                return _bucket_label(lo, hi, scale_100=scale_100)
        elif lo <= val < hi:
            return _bucket_label(lo, hi, scale_100=scale_100)
    return None


def build_bucket_analysis(
    trades: list[dict[str, Any]],
    *,
    n_buckets: int = 5,
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for key, label in BUCKET_FEATURES:
        vals: list[float] = []
        for t in trades:
            v = _feature_value(t, key)
            if v is not None and isinstance(v, float):
                vals.append(v)
        edges = _equal_width_edges(vals, n_buckets=n_buckets)
        buckets: list[dict[str, Any]] = []
        for lo, hi in edges:
            blabel = _bucket_label(lo, hi, scale_100=key == "fear_greed")
            subset = []
            for t in trades:
                v = _feature_value(t, key)
                if v is None or not isinstance(v, float):
                    continue
                if _assign_bucket(v, edges, key=key) == blabel:
                    subset.append(t)
            stats = trade_outcome_stats(subset)
            buckets.append(
                {
                    "bucket": blabel,
                    "lo": lo,
                    "hi": hi,
                    **stats,
                }
            )
        sections.append({"feature": label, "key": key, "buckets": buckets})
    return sections


def build_pair_analysis(
    trades: list[dict[str, Any]],
    *,
    n_bins: int = 3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    combo_rows: list[dict[str, Any]] = []
    for k1, k2, title in PAIR_FEATURES:
        v1 = [float(_feature_value(t, k1)) for t in trades if isinstance(_feature_value(t, k1), float)]
        v2 = [float(_feature_value(t, k2)) for t in trades if isinstance(_feature_value(t, k2), float)]
        if not v1 or not v2:
            continue
        e1 = _equal_width_edges(v1, n_buckets=n_bins)
        e2 = _equal_width_edges(v2, n_buckets=n_bins)
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for t in trades:
            a = _feature_value(t, k1)
            b = _feature_value(t, k2)
            if not isinstance(a, float) or not isinstance(b, float):
                continue
            la = _assign_bucket(a, e1, key=k1)
            lb = _assign_bucket(b, e2, key=k2)
            if la is None or lb is None:
                continue
            grouped.setdefault((la, lb), []).append(t)
        for (b1, b2), subset in grouped.items():
            stats = trade_outcome_stats(subset)
            combo_rows.append(
                {
                    "pair": title,
                    "combination": f"{b1} / {b2}",
                    "bucket_a": b1,
                    "bucket_b": b2,
                    **stats,
                }
            )
    combo_rows.sort(key=lambda x: (-x["expectancy"], -x["trades"]))
    return combo_rows, combo_rows[:20]


def build_winner_loser_comparison(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not trades:
        return []
    sorted_t = sorted(trades, key=lambda t: float(t["pnl_pct"]))
    n = len(sorted_t)
    k = max(1, int(n * 0.2))
    losers = sorted_t[:k]
    winners = sorted_t[-k:]
    rows: list[dict[str, Any]] = []
    for key, label in WINNER_LOSER_KEYS:
        if key == "market_regime":
            def _mode_group(group: list[dict[str, Any]]) -> str | None:
                counts: dict[str, int] = {}
                for t in group:
                    r = t.get("market_regime")
                    if r is None:
                        continue
                    s = str(r)
                    counts[s] = counts.get(s, 0) + 1
                if not counts:
                    return None
                return max(counts.items(), key=lambda x: x[1])[0]

            w_mode = _mode_group(winners)
            l_mode = _mode_group(losers)
            rows.append(
                {
                    "feature": label,
                    "winner_avg": w_mode,
                    "loser_avg": l_mode,
                    "difference": f"mode {w_mode} vs {l_mode}",
                }
            )
            continue
        w_vals = [_feature_value(t, key) for t in winners]
        l_vals = [_feature_value(t, key) for t in losers]
        w_nums = [float(v) for v in w_vals if isinstance(v, float)]
        l_nums = [float(v) for v in l_vals if isinstance(v, float)]
        w_avg = round(mean(w_nums), 4) if w_nums else None
        l_avg = round(mean(l_nums), 4) if l_nums else None
        diff = round(w_avg - l_avg, 4) if w_avg is not None and l_avg is not None else None
        rows.append(
            {
                "feature": label,
                "winner_avg": w_avg,
                "loser_avg": l_avg,
                "difference": diff,
            }
        )
    return rows


def _oi_label(trade: dict[str, Any]) -> str:
    d = trade.get("oi_delta")
    if d is None:
        return "OI unknown"
    if d > 0:
        return "OI rising"
    if d < 0:
        return "OI falling"
    return "OI flat"


def build_market_playbook(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Multi-feature condition strings ranked by performance."""
    trend_vals = [float(t["trend"]) for t in trades if t.get("trend") is not None]
    fund_vals = [float(t["funding"]) for t in trades if t.get("funding") is not None]
    t_edges = _equal_width_edges(trend_vals, 5) if trend_vals else []
    f_edges = _equal_width_edges(fund_vals, 5) if fund_vals else []

    grouped: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        parts: list[str] = []
        if t.get("trend") is not None and t_edges:
            lb = _assign_bucket(float(t["trend"]), t_edges, key="trend")
            if lb:
                parts.append(f"Trend {lb}")
        if t.get("funding") is not None and f_edges:
            lb = _assign_bucket(float(t["funding"]), f_edges, key="funding")
            if lb:
                parts.append(f"Funding {lb}")
        parts.append(_oi_label(t))
        reg = t.get("market_regime")
        if reg:
            parts.append(str(reg))
        key = " | ".join(parts) if parts else "unknown"
        grouped.setdefault(key, []).append(t)

    conditions: list[dict[str, Any]] = []
    for label, subset in grouped.items():
        stats = trade_outcome_stats(subset)
        if stats["trades"] < 1:
            continue
        conditions.append({"condition": label, **stats})

    profitable = sorted(conditions, key=lambda x: (-x["expectancy"], -x["trades"]))[:20]
    losing = sorted(conditions, key=lambda x: (x["expectancy"], -x["trades"]))[:20]
    return {"profitable": profitable, "losing": losing, "all": conditions}


def build_research_pack_01(conn: Any, *, limit: int = 50000) -> dict[str, Any]:
    trades = load_closed_s55_trades(conn, limit=limit)
    pair_all, pair_top = build_pair_analysis(trades)
    return {
        "trades": trades,
        "trade_statistics": build_trade_statistics(trades),
        "bucket_analysis": build_bucket_analysis(trades),
        "pair_analysis": pair_all,
        "pair_top20": pair_top,
        "winner_loser": build_winner_loser_comparison(trades),
        "playbook": build_market_playbook(trades),
    }


def write_research_pack_files(data: dict[str, Any], root: Path | None = None) -> dict[str, Path]:
    out_dir = root or Path("reports/research")
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    stats = data["trade_statistics"]
    stats_md = out_dir / "statistics.md"
    lines = [
        "# Trade Statistics (S55 closed paper)",
        "",
        f"- Total trades: {stats['total_trades']}",
        f"- Winning: {stats['winning_trades']}  Losing: {stats['losing_trades']}",
        f"- Win rate: {stats['win_rate']}%",
        f"- Average PnL: {stats['average_pnl']}%",
        f"- Median PnL: {stats['median_pnl']}%",
        f"- Profit factor: {stats['profit_factor']}",
        f"- Expectancy: {stats['expectancy']}%",
        f"- Average MFE: {stats['average_mfe']}%",
        f"- Average MAE: {stats['average_mae']}%",
        f"- Largest win: {stats['largest_win']}%",
        f"- Largest loss: {stats['largest_loss']}%",
        f"- Average holding (sec): {stats['average_holding_sec']}",
        f"- TP reached: {stats['tp_reached_pct']}%",
        f"- Stop reached: {stats['stop_reached_pct']}%",
    ]
    stats_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    paths["statistics"] = stats_md

    bucket_csv = out_dir / "bucket_analysis.csv"
    with bucket_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["feature", "bucket", "trades", "win_rate", "avg_pnl", "profit_factor", "expectancy"])
        for sec in data["bucket_analysis"]:
            for b in sec["buckets"]:
                w.writerow(
                    [
                        sec["feature"],
                        b["bucket"],
                        b["trades"],
                        b["win_rate"],
                        b["avg_pnl"],
                        b["profit_factor"],
                        b["expectancy"],
                    ]
                )
    paths["bucket_csv"] = bucket_csv

    pair_csv = out_dir / "pair_analysis.csv"
    with pair_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pair", "combination", "trades", "win_rate", "avg_pnl", "expectancy"])
        for row in data["pair_analysis"]:
            w.writerow(
                [
                    row["pair"],
                    row["combination"],
                    row["trades"],
                    row["win_rate"],
                    row["avg_pnl"],
                    row["expectancy"],
                ]
            )
    paths["pair_csv"] = pair_csv

    playbook_md = out_dir / "market_playbook.md"
    plines = ["# Market Playbook", "", "## Top 20 profitable conditions", ""]
    for c in data["playbook"]["profitable"]:
        plines.append(
            f"- {c['condition']} — WR {c['win_rate']}%  avg PnL {c['avg_pnl']}%  "
            f"expectancy {c['expectancy']}%  n={c['trades']}"
        )
    plines.extend(["", "## Top 20 losing conditions", ""])
    for c in data["playbook"]["losing"]:
        plines.append(
            f"- {c['condition']} — WR {c['win_rate']}%  avg PnL {c['avg_pnl']}%  "
            f"expectancy {c['expectancy']}%  n={c['trades']}"
        )
    playbook_md.write_text("\n".join(plines) + "\n", encoding="utf-8")
    paths["playbook"] = playbook_md
    return paths


def format_trade_statistics_cli(data: dict[str, Any]) -> str:
    s = data["trade_statistics"]
    lines = [
        "TRADE STATISTICS (S55 closed paper)",
        f"  Total trades={s['total_trades']}  Wins={s['winning_trades']}  Losses={s['losing_trades']}",
        f"  Win rate={s['win_rate']}%  Avg PnL={s['average_pnl']}%  Median={s['median_pnl']}%",
        f"  Profit factor={s['profit_factor']}  Expectancy={s['expectancy']}%",
        f"  Avg MFE={s['average_mfe']}%  Avg MAE={s['average_mae']}%",
        f"  Largest win={s['largest_win']}%  Largest loss={s['largest_loss']}%",
        f"  Avg holding={s['average_holding_sec']}s  TP reached={s['tp_reached_pct']}%  Stop={s['stop_reached_pct']}%",
        "",
        "BUCKET ANALYSIS",
    ]
    for sec in data["bucket_analysis"]:
        lines.append(f"  [{sec['feature']}]")
        for b in sec["buckets"]:
            pf = b["profit_factor"]
            pf_s = "n/a" if pf is None else (f"{pf:.2f}" if pf != float("inf") else "inf")
            lines.append(
                f"    {b['bucket']}: n={b['trades']} WR={b['win_rate']}% "
                f"avg={b['avg_pnl']}% PF={pf_s} EV={b['expectancy']}%"
            )
    lines.extend(["", "PAIR ANALYSIS — Top 20 combinations"])
    for row in data["pair_top20"]:
        lines.append(
            f"  {row['pair']} {row['combination']}: n={row['trades']} "
            f"WR={row['win_rate']}% avg={row['avg_pnl']}% EV={row['expectancy']}%"
        )
    lines.extend(["", "WINNER vs LOSER (top/bottom 20% by PnL)"])
    lines.append(f"  {'Feature':<16} {'Winner':>12} {'Loser':>12} {'Diff':>12}")
    for r in data["winner_loser"]:
        lines.append(
            f"  {r['feature']:<16} {str(r['winner_avg']):>12} {str(r['loser_avg']):>12} {str(r['difference']):>12}"
        )
    lines.extend(["", "MARKET PLAYBOOK — Top profitable"])
    for c in data["playbook"]["profitable"][:5]:
        lines.append(f"  + {c['condition']} WR={c['win_rate']}% avg={c['avg_pnl']}% n={c['trades']}")
    lines.append("  ... see reports/research/market_playbook.md")
    return "\n".join(lines)


def run_trade_statistics(conn: Any, *, write_reports: bool = True) -> str:
    data = build_research_pack_01(conn)
    paths: dict[str, Path] = {}
    if write_reports:
        paths = write_research_pack_files(data)
    text = format_trade_statistics_cli(data)
    if paths:
        text += "\n\nReports written:\n" + "\n".join(f"  {p}" for p in paths.values())
    return text
