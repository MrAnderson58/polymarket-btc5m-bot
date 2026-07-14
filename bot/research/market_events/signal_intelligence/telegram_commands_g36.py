"""Phase G.3.6 — Telegram command helpers."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.candidate_g31 import fetch_top_candidates_g31


def parse_command_args(text: str) -> tuple[str, list[str]]:
    parts = text.strip().split()
    if not parts:
        return "", []
    token = parts[0].lower()
    if "@" in token:
        token = token.split("@", 1)[0]
    return token, parts[1:]


def format_top_g36(conn: Any) -> str:
    rows = fetch_top_candidates_g31(conn, limit=20)
    if not rows:
        return "TOP 20 — no candidates yet. Run g3-run."

    lines = ["TOP 20 coins", ""]
    for i, r in enumerate(rows, 1):
        conf = f"{float(r['confidence']):.1f}" if r["confidence"] is not None else "—"
        ms = str(int(r["market_score"])) if r["market_score"] is not None else "—"
        lines.append(f"{i}. {r['symbol']}  score={ms}  conf={conf}")
    return "\n".join(lines)
