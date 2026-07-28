"""Research Pack 01 — trade statistics, buckets, pairs, playbook."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from bot.research.market_events.expectancy_intelligence.stats import (
    classify_pnl,
    mean,
    median,
    profit_factor_from_pnls,
    safe_float,
    trade_outcome_stats,
)
from bot.research.market_events.research_pack_01.historical import load_closed_s55_trades

MIN_RELIABLE_N = 30

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
    wins = sum(1 for p in pnls if classify_pnl(p) == "win")
    losses = sum(1 for p in pnls if classify_pnl(p) == "loss")
    breakeven = sum(1 for p in pnls if classify_pnl(p) == "breakeven")
    mfes = [float(t["mfe_pct"]) for t in trades if t.get("mfe_pct") is not None]
    maes = [float(t["mae_pct"]) for t in trades if t.get("mae_pct") is not None]
    holds = [float(t["duration_sec"]) for t in trades if t.get("duration_sec") is not None]
    tp_hits = [t for t in trades if int(t.get("reached_tp1") or 0) == 1]
    stop_hits = [t for t in trades if int(t.get("stopped") or 0) == 1]
    med = median(pnls)
    pf = profit_factor_from_pnls(pnls)
    agg = trade_outcome_stats(trades, min_reliable_n=MIN_RELIABLE_N)
    return {
        "total_trades": n,
        "winning_trades": wins,
        "losing_trades": losses,
        "breakeven_trades": breakeven,
        "win_rate": agg["win_rate"],
        "win_rate_ci95_low": agg["win_rate_ci95_low"],
        "win_rate_ci95_high": agg["win_rate_ci95_high"],
        "average_pnl": agg["avg_pnl"],
        "average_pnl_se": agg["avg_pnl_se"],
        "average_pnl_ci95_low": agg["avg_pnl_ci95_low"],
        "average_pnl_ci95_high": agg["avg_pnl_ci95_high"],
        "median_pnl": round(med, 4) if med is not None else None,
        "profit_factor": pf,
        "expectancy": agg["expectancy"],
        "expectancy_ci95_low": agg["expectancy_ci95_low"],
        "expectancy_ci95_high": agg["expectancy_ci95_high"],
        "average_mfe": round(mean(mfes), 4) if mfes else None,
        "average_mae": round(mean(maes), 4) if maes else None,
        "largest_win": round(max(pnls), 4) if pnls else None,
        "largest_loss": round(min(pnls), 4) if pnls else None,
        "average_holding_sec": round(mean(holds), 1) if holds else None,
        "tp_reached_pct": round(100.0 * len(tp_hits) / n, 1) if n else 0.0,
        "stop_reached_pct": round(100.0 * len(stop_hits) / n, 1) if n else 0.0,
    }


def _quantile_bucket_groups(
    trades: list[dict[str, Any]],
    key: str,
    *,
    n_buckets: int = 5,
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Equal-count quantile buckets (~100/n_buckets % of sample each)."""
    scored: list[tuple[float, dict[str, Any]]] = []
    for t in trades:
        v = _feature_value(t, key)
        if isinstance(v, float):
            scored.append((v, t))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0])
    n = len(scored)
    groups: list[list[dict[str, Any]]] = [[] for _ in range(n_buckets)]
    for rank, (_v, t) in enumerate(scored):
        b = min(n_buckets - 1, int(rank * n_buckets / n))
        groups[b].append(t)
    pct = int(100 / n_buckets)
    out: list[tuple[str, list[dict[str, Any]]]] = []
    for i, subset in enumerate(groups):
        if not subset:
            continue
        vals = [float(_feature_value(t, key)) for t in subset if isinstance(_feature_value(t, key), float)]
        lo, hi = min(vals), max(vals)
        q_label = f"Q{i + 1} (~{pct}%ile) {lo:.4g}-{hi:.4g}"
        out.append((q_label, subset))
    return out


def build_bucket_analysis(
    trades: list[dict[str, Any]],
    *,
    n_buckets: int = 5,
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for key, label in BUCKET_FEATURES:
        buckets: list[dict[str, Any]] = []
        for blabel, subset in _quantile_bucket_groups(trades, key, n_buckets=n_buckets):
            stats = trade_outcome_stats(subset, min_reliable_n=MIN_RELIABLE_N)
            buckets.append({"bucket": blabel, **stats})
        sections.append({"feature": label, "key": key, "buckets": buckets})
    return sections


def _trade_quantile_label(
    trades: list[dict[str, Any]], key: str, trade: dict[str, Any], *, n_buckets: int
) -> str | None:
    groups = _quantile_bucket_groups(trades, key, n_buckets=n_buckets)
    for blabel, subset in groups:
        if trade in subset:
            return blabel
    return None


def build_pair_analysis(
    trades: list[dict[str, Any]],
    *,
    n_bins: int = 5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    combo_rows: list[dict[str, Any]] = []
    for k1, k2, title in PAIR_FEATURES:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for t in trades:
            if not isinstance(_feature_value(t, k1), float) or not isinstance(_feature_value(t, k2), float):
                continue
            la = _trade_quantile_label(trades, k1, t, n_buckets=n_bins)
            lb = _trade_quantile_label(trades, k2, t, n_buckets=n_bins)
            if la is None or lb is None:
                continue
            grouped.setdefault((la, lb), []).append(t)
        for (b1, b2), subset in grouped.items():
            stats = trade_outcome_stats(subset, min_reliable_n=MIN_RELIABLE_N)
            combo_rows.append(
                {
                    "pair": title,
                    "combination": f"{b1} / {b2}",
                    "bucket_a": b1,
                    "bucket_b": b2,
                    **stats,
                }
            )
    combo_rows.sort(key=lambda x: (-x["rank_score"], -x["trades"]))
    reliable = [r for r in combo_rows if r["reliable"]]
    top20 = reliable[:20] if reliable else combo_rows[:20]
    return combo_rows, top20


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
    """Multi-feature condition strings ranked by EV×log(n)."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        parts: list[str] = []
        tl = _trade_quantile_label(trades, "trend", t, n_buckets=5)
        if tl:
            parts.append(f"Trend {tl}")
        fl = _trade_quantile_label(trades, "funding", t, n_buckets=5)
        if fl:
            parts.append(f"Funding {fl}")
        parts.append(_oi_label(t))
        reg = t.get("market_regime")
        if reg:
            parts.append(str(reg))
        key = " | ".join(parts) if parts else "unknown"
        grouped.setdefault(key, []).append(t)

    conditions: list[dict[str, Any]] = []
    for label, subset in grouped.items():
        stats = trade_outcome_stats(subset, min_reliable_n=MIN_RELIABLE_N)
        if stats["trades"] < 1:
            continue
        conditions.append({"condition": label, **stats})

    reliable = [c for c in conditions if c["reliable"]]
    profitable = sorted(reliable, key=lambda x: -x["rank_score"])[:20]
    losing = sorted(reliable, key=lambda x: x["rank_score"])[:20]
    low_n = sorted(
        [c for c in conditions if not c["reliable"]],
        key=lambda x: -abs(x["rank_score"]),
    )
    return {
        "profitable": profitable,
        "losing": losing,
        "low_confidence": low_n,
        "all": conditions,
    }


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


def _fmt_ci(low: Any, high: Any) -> str:
    if low is None or high is None:
        return "CI95: n/a"
    return f"CI95 [{low}, {high}]"


def _bucket_line(b: dict[str, Any]) -> str:
    pf = b["profit_factor"]
    pf_s = "n/a" if pf is None else (f"{pf:.2f}" if pf != float("inf") else "inf")
    flag = "" if b.get("reliable") else " [LOW-N]"
    return (
        f"    {b['bucket']}{flag}: n={b['trades']} WR={b['win_rate']}% "
        f"({_fmt_ci(b.get('win_rate_ci95_low'), b.get('win_rate_ci95_high'))}) "
        f"avg={b['avg_pnl']}% SE={b.get('avg_pnl_se')} {_fmt_ci(b.get('avg_pnl_ci95_low'), b.get('avg_pnl_ci95_high'))} "
        f"PF={pf_s} EV={b['expectancy']}% rank={b.get('rank_score')}"
    )


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
        f"- Winning: {stats['winning_trades']}  Losing: {stats['losing_trades']}  "
        f"Breakeven: {stats['breakeven_trades']}",
        f"- Win rate: {stats['win_rate']}% {_fmt_ci(stats.get('win_rate_ci95_low'), stats.get('win_rate_ci95_high'))}",
        f"- Average PnL: {stats['average_pnl']}% SE={stats.get('average_pnl_se')} "
        f"{_fmt_ci(stats.get('average_pnl_ci95_low'), stats.get('average_pnl_ci95_high'))}",
        f"- Median PnL: {stats['median_pnl']}%",
        f"- Profit factor: {stats['profit_factor']}",
        f"- Expectancy: {stats['expectancy']}% "
        f"{_fmt_ci(stats.get('expectancy_ci95_low'), stats.get('expectancy_ci95_high'))}",
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
        w.writerow(
            [
                "feature",
                "bucket",
                "trades",
                "reliable",
                "win_rate",
                "win_rate_ci95_low",
                "win_rate_ci95_high",
                "avg_pnl",
                "avg_pnl_se",
                "avg_pnl_ci95_low",
                "avg_pnl_ci95_high",
                "profit_factor",
                "expectancy",
                "rank_score",
            ]
        )
        for sec in data["bucket_analysis"]:
            for b in sec["buckets"]:
                w.writerow(
                    [
                        sec["feature"],
                        b["bucket"],
                        b["trades"],
                        b.get("reliable"),
                        b["win_rate"],
                        b.get("win_rate_ci95_low"),
                        b.get("win_rate_ci95_high"),
                        b["avg_pnl"],
                        b.get("avg_pnl_se"),
                        b.get("avg_pnl_ci95_low"),
                        b.get("avg_pnl_ci95_high"),
                        b["profit_factor"],
                        b["expectancy"],
                        b.get("rank_score"),
                    ]
                )
    paths["bucket_csv"] = bucket_csv

    pair_csv = out_dir / "pair_analysis.csv"
    with pair_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "pair",
                "combination",
                "trades",
                "reliable",
                "win_rate",
                "avg_pnl",
                "avg_pnl_se",
                "expectancy",
                "expectancy_ci95_low",
                "expectancy_ci95_high",
                "rank_score",
            ]
        )
        for row in data["pair_analysis"]:
            w.writerow(
                [
                    row["pair"],
                    row["combination"],
                    row["trades"],
                    row.get("reliable"),
                    row["win_rate"],
                    row["avg_pnl"],
                    row.get("avg_pnl_se"),
                    row["expectancy"],
                    row.get("expectancy_ci95_low"),
                    row.get("expectancy_ci95_high"),
                    row.get("rank_score"),
                ]
            )
    paths["pair_csv"] = pair_csv

    playbook_md = out_dir / "market_playbook.md"
    plines = [
        "# Market Playbook",
        "",
        f"Ranked by EV×log(n). Reliable rows require n≥{MIN_RELIABLE_N}.",
        "",
        "## Top 20 profitable conditions (reliable only)",
        "",
    ]
    if not data["playbook"]["profitable"]:
        plines.append("_No combinations with n≥30 — see low-confidence section._")
    for c in data["playbook"]["profitable"]:
        plines.append(
            f"- {c['condition']} — WR {c['win_rate']}%  avg PnL {c['avg_pnl']}%  "
            f"EV {c['expectancy']}% {_fmt_ci(c.get('expectancy_ci95_low'), c.get('expectancy_ci95_high'))}  "
            f"n={c['trades']}  rank={c['rank_score']}"
        )
    plines.extend(["", "## Top 20 losing conditions (reliable only)", ""])
    if not data["playbook"]["losing"]:
        plines.append("_No combinations with n≥30 — see low-confidence section._")
    for c in data["playbook"]["losing"]:
        plines.append(
            f"- {c['condition']} — WR {c['win_rate']}%  avg PnL {c['avg_pnl']}%  "
            f"EV {c['expectancy']}%  n={c['trades']}  rank={c['rank_score']}"
        )
    plines.extend(["", "## Low-confidence conditions (n<30)", ""])
    for c in (data["playbook"].get("low_confidence") or [])[:30]:
        plines.append(
            f"- [LOW-N n={c['trades']}] {c['condition']} — EV {c['expectancy']}%  rank={c['rank_score']}"
        )
    playbook_md.write_text("\n".join(plines) + "\n", encoding="utf-8")
    paths["playbook"] = playbook_md
    return paths


def format_trade_statistics_cli(data: dict[str, Any]) -> str:
    s = data["trade_statistics"]
    lines = [
        "TRADE STATISTICS (S55 closed paper)",
        f"  Total={s['total_trades']}  Wins={s['winning_trades']}  Losses={s['losing_trades']}  "
        f"Breakeven={s['breakeven_trades']}",
        f"  Win rate={s['win_rate']}% {_fmt_ci(s.get('win_rate_ci95_low'), s.get('win_rate_ci95_high'))}",
        f"  Avg PnL={s['average_pnl']}% SE={s.get('average_pnl_se')} "
        f"{_fmt_ci(s.get('average_pnl_ci95_low'), s.get('average_pnl_ci95_high'))}  Median={s['median_pnl']}%",
        f"  Profit factor={s['profit_factor']}  Expectancy={s['expectancy']}% "
        f"{_fmt_ci(s.get('expectancy_ci95_low'), s.get('expectancy_ci95_high'))}",
        f"  Avg MFE={s['average_mfe']}%  Avg MAE={s['average_mae']}%",
        f"  Largest win={s['largest_win']}%  Largest loss={s['largest_loss']}%",
        f"  Avg holding={s['average_holding_sec']}s  TP reached={s['tp_reached_pct']}%  Stop={s['stop_reached_pct']}%",
        "",
        f"BUCKET ANALYSIS (quantile ~20% each; n≥{MIN_RELIABLE_N} shown first)",
    ]
    for sec in data["bucket_analysis"]:
        lines.append(f"  [{sec['feature']}]")
        reliable = [b for b in sec["buckets"] if b.get("reliable")]
        low = [b for b in sec["buckets"] if not b.get("reliable")]
        for b in reliable:
            lines.append(_bucket_line(b))
        for b in low:
            lines.append(_bucket_line(b))
    lines.extend(["", f"PAIR ANALYSIS — Top 20 (reliable n≥{MIN_RELIABLE_N}, rank=EV×log(n))"])
    for row in data["pair_top20"]:
        flag = "" if row.get("reliable") else " [LOW-N]"
        lines.append(
            f"  {row['pair']} {row['combination']}{flag}: n={row['trades']} "
            f"WR={row['win_rate']}% avg={row['avg_pnl']}% SE={row.get('avg_pnl_se')} "
            f"EV={row['expectancy']}% rank={row.get('rank_score')}"
        )
    lines.extend(["", "WINNER vs LOSER (top/bottom 20% by PnL)"])
    lines.append(f"  {'Feature':<16} {'Winner':>12} {'Loser':>12} {'Diff':>12}")
    for r in data["winner_loser"]:
        lines.append(
            f"  {r['feature']:<16} {str(r['winner_avg']):>12} {str(r['loser_avg']):>12} {str(r['difference']):>12}"
        )
    lines.extend(["", "MARKET PLAYBOOK — Top profitable (reliable)"])
    if not data["playbook"]["profitable"]:
        lines.append(f"  (none with n≥{MIN_RELIABLE_N}; see market_playbook.md low-confidence)")
    for c in data["playbook"]["profitable"][:5]:
        lines.append(
            f"  + {c['condition']} WR={c['win_rate']}% EV={c['expectancy']}% "
            f"n={c['trades']} rank={c['rank_score']}"
        )
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
