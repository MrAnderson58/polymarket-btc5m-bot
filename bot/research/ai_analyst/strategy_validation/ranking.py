"""S48 — AI / heuristic signal ranking from outcome history."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _group_stats(outcomes: list[dict[str, Any]], key_fn) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for o in outcomes:
        k = key_fn(o)
        if not k:
            continue
        buckets[str(k)].append(o)

    ranked: list[dict[str, Any]] = []
    for name, rows in buckets.items():
        n = len(rows)
        if n < 1:
            continue
        wins = sum(1 for r in rows if float(r.get("pnl_usd") or 0) > 0)
        avg_r = sum(float(r.get("r_multiple") or 0) for r in rows) / n
        pnl = sum(float(r.get("pnl_usd") or 0) for r in rows)
        ranked.append({
            "name": name,
            "trades": n,
            "win_rate": round(100.0 * wins / n, 2),
            "avg_r": round(avg_r, 4),
            "total_pnl_usd": round(pnl, 4),
            "score": round(avg_r * n + (wins / n) * 2, 4),
        })
    ranked.sort(key=lambda x: (x["avg_r"], x["win_rate"], x["trades"]), reverse=True)
    return ranked


def rank_reason_tags(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for o in outcomes:
        tags = o.get("tags") or []
        reasons = o.get("reasons") or []
        items = list(tags) if tags else []
        if not items:
            # fallback: first reason phrase
            for r in reasons[:2]:
                items.append(str(r)[:48])
        for tag in items:
            row = dict(o)
            row["_factor"] = tag
            expanded.append(row)
    return _group_stats(expanded, lambda o: o.get("_factor"))


def rank_strategies(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _group_stats(outcomes, lambda o: o.get("strategy") or "unknown")


def rank_setups(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def setup_key(o: dict[str, Any]) -> str:
        tags = [str(t) for t in (o.get("tags") or [])]
        if len(tags) >= 2:
            return " + ".join(tags[:3])
        if tags:
            return tags[0]
        reasons = o.get("reasons") or []
        if reasons:
            return str(reasons[0])[:60]
        return "Untagged"

    return _group_stats(outcomes, setup_key)


def false_signal_news(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reasons/tags that appear often in losses."""
    losses = [o for o in outcomes if float(o.get("pnl_usd") or 0) < 0]
    ranked = rank_reason_tags(losses)
    return ranked[:8]


def build_ranking_report(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    strategies = rank_strategies(outcomes)
    reasons = rank_reason_tags(outcomes)
    setups = rank_setups(outcomes)
    false_news = false_signal_news(outcomes)
    best = setups[0] if setups else None
    worst = setups[-1] if setups else None
    # Prefer worst by lowest avg_r among setups with trades
    if setups:
        worst = min(setups, key=lambda x: (x["avg_r"], -x["trades"]))

    payload = {
        "best_strategies": strategies[:5],
        "best_reasons": reasons[:5],
        "best_setups": setups[:5],
        "false_signal_factors": false_news,
        "best_setup": best,
        "worst_setup": worst,
        "n_outcomes": len(outcomes),
    }
    return payload


def format_ranking(payload: dict[str, Any]) -> str:
    lines = [
        "AI SIGNAL RANKING",
        "",
        f"Outcomes analyzed: {payload.get('n_outcomes', 0)}",
        "",
        "Best strategies:",
    ]
    for row in payload.get("best_strategies") or []:
        lines.append(
            f"• {row['name']}: WR={row['win_rate']}% AvgR={row['avg_r']} "
            f"n={row['trades']} PnL=${row['total_pnl_usd']}"
        )
    if not payload.get("best_strategies"):
        lines.append("• (insufficient data)")

    lines.extend(["", "Reasons that lead to profit:"])
    for row in payload.get("best_reasons") or []:
        if float(row.get("avg_r") or 0) <= 0:
            continue
        lines.append(
            f"• {row['name']}: WR={row['win_rate']}% AvgR={row['avg_r']} n={row['trades']}"
        )
    if not any(float(r.get("avg_r") or 0) > 0 for r in (payload.get("best_reasons") or [])):
        lines.append("• (insufficient profitable reason clusters)")

    lines.extend(["", "False-signal factors (loss clusters):"])
    for row in payload.get("false_signal_factors") or []:
        lines.append(
            f"• {row['name']}: WR={row['win_rate']}% AvgR={row['avg_r']} n={row['trades']}"
        )
    if not payload.get("false_signal_factors"):
        lines.append("• (none)")

    best = payload.get("best_setup")
    worst = payload.get("worst_setup")
    lines.extend(["", "Most profitable combinations:"])
    if best:
        lines.append(
            f"• BEST: {best['name']} (WR={best['win_rate']}% AvgR={best['avg_r']})"
        )
    else:
        lines.append("• BEST: n/a")
    if worst:
        lines.append(
            f"• WORST: {worst['name']} (WR={worst['win_rate']}% AvgR={worst['avg_r']})"
        )
    else:
        lines.append("• WORST: n/a")

    return "\n".join(lines)


def format_leaderboard(payload: dict[str, Any]) -> str:
    lines = ["LEADERBOARD", "", "Top setups:"]
    for i, row in enumerate(payload.get("best_setups") or [], 1):
        lines.append(
            f"{i}. {row['name']} — WR {row['win_rate']}% | "
            f"{row['avg_r']}R | n={row['trades']}"
        )
    if not payload.get("best_setups"):
        lines.append("(no ranked setups yet)")
    return "\n".join(lines)
