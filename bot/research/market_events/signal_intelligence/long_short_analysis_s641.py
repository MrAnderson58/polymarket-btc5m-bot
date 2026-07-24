"""S64.1 — LONG vs SHORT Deep Analyzer (research-only).

Where does LONG hurt and where does SHORT shine?
Builds on S64 dimensions. Statistics only — no strategy changes.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from bot.research.market_events.signal_intelligence.drift_analyzer_s622 import (
    _enrich_from_snapshot_json,
)
from bot.research.market_events.signal_intelligence.feature_lab_s59 import load_lab_trades
from bot.research.market_events.signal_intelligence.pnl_killers_s64 import (
    DIM_KEY_FNS,
    DIM_LABEL,
    _card,
    _segment_metrics,
    dimension_value,
    pnl_report_dir,
)
from bot.research.market_events.signal_intelligence.trading_intelligence_report_s621 import (
    _enrich_decisions,
)

logger = logging.getLogger(__name__)

TOP_N = 10

# Direction is the split axis — analyze the rest
DEEP_DIMS = (
    "strategy",
    "coin",
    "regime",
    "hour",
    "weekday",
    "ai_score",
    "funding",
    "confidence",
    "entry_reason",
)


def _fmt(v: Any, *, digits: int = 4) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f) >= 100:
        return f"{f:.2f}"
    return f"{f:.{digits}g}"


def _gross_loss(rows: list[dict[str, Any]]) -> float:
    return float(
        sum(abs(float(r.get("pnl_usd") or 0.0)) for r in rows if float(r.get("pnl_usd") or 0.0) < 0)
    )


def _gross_win(rows: list[dict[str, Any]]) -> float:
    return float(
        sum(float(r.get("pnl_usd") or 0.0) for r in rows if float(r.get("pnl_usd") or 0.0) > 0)
    )


def build_side_tables(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for dim in DEEP_DIMS:
        if dim not in DIM_KEY_FNS:
            continue
        by: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            by[dimension_value(r, dim)].append(r)
        cards: list[dict[str, Any]] = []
        for key, members in by.items():
            if not members:
                continue
            card = _card(dim, key, members)
            card["gross_loss"] = round(_gross_loss(members), 4)
            card["gross_win"] = round(_gross_win(members), 4)
            cards.append(card)
        cards.sort(key=lambda c: (-float(c.get("net_pnl") or 0.0), -(c.get("trades") or 0)))
        out[dim] = cards
    return out


def annotate_loss_contribution(
    cards: list[dict[str, Any]],
    *,
    parent_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach contribution_to_side_loss_pct for losing segments (gross-loss share)."""
    parent_gl = _gross_loss(parent_rows)
    annotated: list[dict[str, Any]] = []
    for c in cards:
        item = dict(c)
        seg_gl = float(item.get("gross_loss") or 0.0)
        net = float(item.get("net_pnl") or 0.0)
        if seg_gl > 0 and parent_gl > 1e-12:
            contrib = round(100.0 * seg_gl / parent_gl, 2)
        elif net < 0 and parent_gl > 1e-12:
            contrib = round(100.0 * abs(net) / parent_gl, 2)
        elif net < 0:
            contrib = 100.0
        else:
            contrib = 0.0
        item["contribution_to_side_loss_pct"] = contrib
        item["is_loss_segment"] = net < 0
        annotated.append(item)
    return annotated


def annotate_win_contribution(
    cards: list[dict[str, Any]],
    *,
    parent_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    parent_gw = _gross_win(parent_rows)
    annotated: list[dict[str, Any]] = []
    for c in cards:
        item = dict(c)
        seg_gw = float(item.get("gross_win") or 0.0)
        net = float(item.get("net_pnl") or 0.0)
        if seg_gw > 0 and parent_gw > 1e-12:
            contrib = round(100.0 * seg_gw / parent_gw, 2)
        elif net > 0 and parent_gw > 1e-12:
            contrib = round(100.0 * net / parent_gw, 2)
        elif net > 0:
            contrib = 100.0
        else:
            contrib = 0.0
        item["contribution_to_side_win_pct"] = contrib
        item["is_win_segment"] = net > 0
        annotated.append(item)
    return annotated


def top_losses_by_dim(
    by_dimension: dict[str, list[dict[str, Any]]],
    *,
    top_n: int = TOP_N,
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for dim, cards in by_dimension.items():
        losses = [c for c in cards if float(c.get("net_pnl") or 0.0) < 0]
        losses.sort(key=lambda c: (float(c.get("net_pnl") or 0.0), -(c.get("trades") or 0)))
        out[dim] = losses[:top_n]
    return out


def top_wins_by_dim(
    by_dimension: dict[str, list[dict[str, Any]]],
    *,
    top_n: int = TOP_N,
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for dim, cards in by_dimension.items():
        wins = [c for c in cards if float(c.get("net_pnl") or 0.0) > 0]
        wins.sort(key=lambda c: (-float(c.get("net_pnl") or 0.0), -(c.get("trades") or 0)))
        out[dim] = wins[:top_n]
    return out


def flatten_loss_root_causes(
    top_losses: dict[str, list[dict[str, Any]]],
    *,
    side: str,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Rank all losing segments by |contribution| / severity."""
    all_cards: list[dict[str, Any]] = []
    for dim, cards in top_losses.items():
        for c in cards:
            item = dict(c)
            item["side"] = side
            item["path"] = f"{side} → {c.get('key')}"
            item["path_label"] = f"{side} → {DIM_LABEL.get(dim, dim)}={c.get('key')}"
            all_cards.append(item)
    all_cards.sort(
        key=lambda c: (
            -float(c.get("contribution_to_side_loss_pct") or 0.0),
            float(c.get("net_pnl") or 0.0),
        ),
    )
    return all_cards[:limit]


def analyze_side(rows: list[dict[str, Any]], *, side: str, top_n: int = TOP_N) -> dict[str, Any]:
    overall = _segment_metrics(rows)
    overall["gross_loss"] = round(_gross_loss(rows), 4)
    overall["gross_win"] = round(_gross_win(rows), 4)

    raw_tables = build_side_tables(rows)
    by_dimension: dict[str, list[dict[str, Any]]] = {}
    for dim, cards in raw_tables.items():
        with_loss = annotate_loss_contribution(cards, parent_rows=rows)
        by_dimension[dim] = annotate_win_contribution(with_loss, parent_rows=rows)

    top_loss = top_losses_by_dim(by_dimension, top_n=top_n)
    top_win = top_wins_by_dim(by_dimension, top_n=top_n)
    root_causes = flatten_loss_root_causes(top_loss, side=side)

    return {
        "side": side,
        "overall": overall,
        "by_dimension": by_dimension,
        "top_losses": top_loss,
        "top_wins": top_win,
        "loss_root_causes": root_causes,
    }


def format_long_short_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# LONG vs SHORT Deep Analyzer (S64.1)",
        "",
        f"_trades={report.get('n_trades')} elapsed={report.get('elapsed_sec')}s_",
        "",
        "Where LONG hurts and SHORT helps. Statistics only. No strategy changes.",
        "",
    ]

    def _side_header(block: dict[str, Any]) -> None:
        o = block.get("overall") or {}
        lines.extend([
            f"## Total {block.get('side')}",
            "",
            f"- Trades: **{o.get('trades')}**",
            f"- Net PnL: **{_fmt(o.get('net_pnl'))}**",
            f"- Win Rate: **{_fmt(o.get('win_rate'))}%**",
            f"- Profit Factor: **{'∞' if o.get('pf_inf') else _fmt(o.get('profit_factor'))}**",
            f"- Avg PnL: **{_fmt(o.get('avg_pnl'))}**",
            f"- Max DD: **{_fmt(o.get('max_dd'))}**",
            f"- Gross loss: **{_fmt(o.get('gross_loss'))}**",
            f"- Gross win: **{_fmt(o.get('gross_win'))}**",
            "",
        ])

    def _top_table(title: str, cards: list[dict[str, Any]], *, contrib_key: str) -> None:
        lines.extend([
            f"### {title}",
            "",
            "| # | Segment | Trades | Net PnL | WR | PF | Contribution |",
            "|---:|---|---:|---:|---:|---:|---:|",
        ])
        if not cards:
            lines.append("| — | _none_ | | | | | |")
        for i, c in enumerate(cards, 1):
            pf = "∞" if c.get("pf_inf") else _fmt(c.get("profit_factor"))
            lines.append(
                f"| {i} | {c.get('key')} | {c.get('trades')} | {_fmt(c.get('net_pnl'))} | "
                f"{_fmt(c.get('win_rate'))} | {pf} | {_fmt(c.get(contrib_key))}% |"
            )
        lines.append("")

    long_b = report.get("long") or {}
    short_b = report.get("short") or {}

    _side_header(long_b)
    lines.extend(["## LONG LOSS ROOT CAUSES", ""])
    top_loss = long_b.get("top_losses") or {}
    _top_table("Top Coin Losses", top_loss.get("coin") or [], contrib_key="contribution_to_side_loss_pct")
    _top_table("Top Hour Losses", top_loss.get("hour") or [], contrib_key="contribution_to_side_loss_pct")
    _top_table("Top Strategy Losses", top_loss.get("strategy") or [], contrib_key="contribution_to_side_loss_pct")
    _top_table("Top Regime Losses", top_loss.get("regime") or [], contrib_key="contribution_to_side_loss_pct")
    _top_table("Top Weekday Losses", top_loss.get("weekday") or [], contrib_key="contribution_to_side_loss_pct")
    _top_table("Top AI Score Losses", top_loss.get("ai_score") or [], contrib_key="contribution_to_side_loss_pct")
    _top_table("Top Funding Losses", top_loss.get("funding") or [], contrib_key="contribution_to_side_loss_pct")
    _top_table("Top Confidence Losses", top_loss.get("confidence") or [], contrib_key="contribution_to_side_loss_pct")
    _top_table("Top Entry Reason Losses", top_loss.get("entry_reason") or [], contrib_key="contribution_to_side_loss_pct")

    lines.extend(["### Loss contribution detail", ""])
    roots = long_b.get("loss_root_causes") or []
    if not roots:
        lines.append("_No LONG loss segments._\n")
    for c in roots:
        lines.extend([
            f"#### {c.get('path_label')}",
            "",
            f"- Trades: **{c.get('trades')}**",
            f"- Net PnL: **{_fmt(c.get('net_pnl'))}**",
            f"- Contribution to total LONG loss: **{_fmt(c.get('contribution_to_side_loss_pct'))}%**",
            f"- WR: {_fmt(c.get('win_rate'))}% · PF: "
            f"{'∞' if c.get('pf_inf') else _fmt(c.get('profit_factor'))} · "
            f"Avg: {_fmt(c.get('avg_pnl'))} · MaxDD: {_fmt(c.get('max_dd'))}",
            "",
        ])

    _side_header(short_b)
    lines.extend(["## SHORT SUCCESS FACTORS", ""])
    top_win = short_b.get("top_wins") or {}
    _top_table("Top Coins", top_win.get("coin") or [], contrib_key="contribution_to_side_win_pct")
    _top_table("Top Hours", top_win.get("hour") or [], contrib_key="contribution_to_side_win_pct")
    _top_table("Top Strategies", top_win.get("strategy") or [], contrib_key="contribution_to_side_win_pct")
    _top_table("Top Regimes", top_win.get("regime") or [], contrib_key="contribution_to_side_win_pct")
    _top_table("Top Weekdays", top_win.get("weekday") or [], contrib_key="contribution_to_side_win_pct")
    _top_table("Top AI Scores", top_win.get("ai_score") or [], contrib_key="contribution_to_side_win_pct")
    _top_table("Top Funding", top_win.get("funding") or [], contrib_key="contribution_to_side_win_pct")
    _top_table("Top Confidence", top_win.get("confidence") or [], contrib_key="contribution_to_side_win_pct")
    _top_table("Top Entry Reasons", top_win.get("entry_reason") or [], contrib_key="contribution_to_side_win_pct")

    # Also SHORT loss roots + LONG success for completeness
    lines.extend(["## SHORT LOSS ROOT CAUSES (secondary)", ""])
    short_loss = short_b.get("top_losses") or {}
    _top_table("Top Coin Losses", short_loss.get("coin") or [], contrib_key="contribution_to_side_loss_pct")
    _top_table("Top Hour Losses", short_loss.get("hour") or [], contrib_key="contribution_to_side_loss_pct")
    _top_table("Top Strategy Losses", short_loss.get("strategy") or [], contrib_key="contribution_to_side_loss_pct")
    _top_table("Top Regime Losses", short_loss.get("regime") or [], contrib_key="contribution_to_side_loss_pct")

    lines.extend(["## LONG SUCCESS FACTORS (secondary)", ""])
    long_win = long_b.get("top_wins") or {}
    _top_table("Top Coins", long_win.get("coin") or [], contrib_key="contribution_to_side_win_pct")
    _top_table("Top Hours", long_win.get("hour") or [], contrib_key="contribution_to_side_win_pct")
    _top_table("Top Strategies", long_win.get("strategy") or [], contrib_key="contribution_to_side_win_pct")
    _top_table("Top Regimes", long_win.get("regime") or [], contrib_key="contribution_to_side_win_pct")

    return "\n".join(lines).rstrip() + "\n"


def format_long_short_summary(report: dict[str, Any]) -> str:
    long_o = (report.get("long") or {}).get("overall") or {}
    short_o = (report.get("short") or {}).get("overall") or {}
    lines = [
        "S64.1 LONG vs SHORT Deep Analyzer",
        f"  trades={report.get('n_trades')} elapsed={report.get('elapsed_sec')}s",
        f"  LONG: n={long_o.get('trades')} pnl={long_o.get('net_pnl')} "
        f"WR={long_o.get('win_rate')} PF={long_o.get('profit_factor')}",
        f"  SHORT: n={short_o.get('trades')} pnl={short_o.get('net_pnl')} "
        f"WR={short_o.get('win_rate')} PF={short_o.get('profit_factor')}",
    ]
    roots = (report.get("long") or {}).get("loss_root_causes") or []
    for c in roots[:5]:
        lines.append(
            f"  LONG loss: {c.get('path_label')} pnl={c.get('net_pnl')} "
            f"contrib={c.get('contribution_to_side_loss_pct')}%"
        )
    wins = ((report.get("short") or {}).get("top_wins") or {}).get("coin") or []
    for c in wins[:3]:
        lines.append(
            f"  SHORT win coin: {c.get('key')} pnl={c.get('net_pnl')} "
            f"contrib={c.get('contribution_to_side_win_pct')}%"
        )
    for k, p in (report.get("export_paths") or {}).items():
        lines.append(f"  {k}: {p}")
    return "\n".join(lines)


def run_long_short_analysis(
    conn: Any,
    *,
    report_dir: Path | None = None,
    top_n: int = TOP_N,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    rows = load_lab_trades(conn)
    _enrich_decisions(conn, rows)
    _enrich_from_snapshot_json(rows)
    closed = [r for r in rows if r.get("pnl_usd") is not None]

    long_rows = [r for r in closed if str(r.get("direction") or "").upper() == "LONG"]
    short_rows = [r for r in closed if str(r.get("direction") or "").upper() == "SHORT"]

    long_block = analyze_side(long_rows, side="LONG", top_n=top_n)
    short_block = analyze_side(short_rows, side="SHORT", top_n=top_n)

    out_dir = Path(report_dir) if report_dir else pnl_report_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "long_short_analysis.md"
    json_path = out_dir / "long_short_analysis.json"

    report: dict[str, Any] = {
        "ok": True,
        "stage": "S64.1",
        "n_trades": len(closed),
        "n_long": len(long_rows),
        "n_short": len(short_rows),
        "overall": _segment_metrics(closed),
        "long": long_block,
        "short": short_block,
        "elapsed_sec": round(time.perf_counter() - t0, 3),
        "export_paths": {
            "markdown": str(md_path),
            "json": str(json_path),
        },
    }
    md_path.write_text(format_long_short_markdown(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


__all__ = [
    "format_long_short_markdown",
    "format_long_short_summary",
    "run_long_short_analysis",
]
