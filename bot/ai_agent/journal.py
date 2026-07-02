"""AI Trade Journal — per-trade markdown logs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.config import BASE_DIR


def journal_root() -> Path:
    return BASE_DIR / "ai_journal"


def write_trade_journal(
    *,
    trade_id: int,
    payload: dict[str, Any],
    closed_at: str | None = None,
) -> Path:
    if closed_at:
        day = str(closed_at)[:10]
    else:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = journal_root() / day
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"trade_{trade_id}.md"

    expl = payload.get("explanation", {})
    pos = expl.get("positive", [])
    neg = expl.get("negative", [])
    lines = [
        f"# Trade #{trade_id}",
        "",
        f"**Strategy:** {payload.get('strategy_name', '?')}",
        f"**Decision:** {payload.get('decision', '?')} (observe-only)",
        f"**AI Score:** {payload.get('ai_score', 0)}",
        f"**Confidence:** {payload.get('confidence', 0)}%",
        "",
        "## Similar Trades",
        f"- Count: {payload.get('similar_count', 0)}",
        f"- Historical PF: {payload.get('historical_pf', 'n/a')}",
        f"- Historical WR: {payload.get('historical_wr', 'n/a')}",
        "",
        "## Market",
        f"- Regime: {payload.get('market_regime', '?')}",
        f"- BTC Move 30s: {payload.get('btc_move_30s', 'n/a')}",
        f"- Spread: {payload.get('spread', 'n/a')}",
        "",
        "## Result",
        f"- Outcome: {payload.get('outcome', '?')}",
        f"- PnL: {payload.get('pnl', 0):+.2f}%",
        f"- Counterfactual: {payload.get('counterfactual_result', '?')}",
        "",
        "## Explanation",
    ]
    for r in pos:
        lines.append(f"- {r}")
    for r in neg:
        lines.append(f"- {r}")

    lesson = payload.get("lesson", "Continue observe-only validation.")
    lines += ["", "## Lesson", "", lesson, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def lesson_for_counterfactual(kind: str) -> str:
    return {
        "false_allow": "Potential false allow. Needs shadow validation.",
        "false_skip": "Potential missed winner. Review similarity features.",
        "true_allow": "Decision aligned with positive outcome.",
        "true_skip": "Skip correctly avoided weak outcome.",
        "shadow": "Uncertain band — accumulate more similar samples.",
    }.get(kind, "Continue observe-only validation.")
