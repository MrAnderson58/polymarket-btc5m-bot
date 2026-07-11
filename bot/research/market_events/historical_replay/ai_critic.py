"""Task I — AI critic shadow study (deterministic; does not place trades)."""

from __future__ import annotations

import json
import time
from typing import Any

INTERPRETATIONS = (
    "TECHNICAL_LIQUIDITY_SWEEP",
    "NEWS_DRIVEN_CONTINUATION",
    "MARKET_WIDE_MOVE",
    "ASSET_SPECIFIC_ANOMALY",
    "TOKENIZED_MARKET_DISLOCATION",
    "UNCLEAR",
)

ACTIONS = ("WAIT", "WATCH_REVERSAL", "AVOID_FADE", "CONTEXT_INSUFFICIENT")


def _col(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _deterministic_critic(shock: Any, ctx_count: int, path_rows: list[Any]) -> dict[str, Any]:
    impulse = abs(float(shock["impulse_pct"] or 0))
    btc = _col(shock, "btc_context_pct")
    rel = None
    if btc is not None:
        rel = abs(float(shock["impulse_pct"])) - abs(float(btc))
    h60 = next((r for r in path_rows if int(r["horizon_sec"]) == 60), None)
    cont60 = float(h60["continuation_pct"] or 0) if h60 else 0

    interp = "ASSET_SPECIFIC_ANOMALY"
    if ctx_count == 0:
        interp = "UNCLEAR"
    elif _col(shock, "asset_class") not in (None, "CRYPTO"):
        interp = "TOKENIZED_MARKET_DISLOCATION"
    elif btc is not None and abs(float(btc)) > impulse * 0.7:
        interp = "MARKET_WIDE_MOVE"
    elif rel is not None and rel > 1.0 and ctx_count == 0:
        interp = "TECHNICAL_LIQUIDITY_SWEEP"

    rev_prob = 0.45
    if h60 and float(h60["reversal_pct"] or 0) > impulse * 0.35:
        rev_prob = 0.65
    if cont60 > impulse * 0.5:
        rev_prob = 0.25

    action = "WATCH_REVERSAL"
    if ctx_count == 0:
        action = "CONTEXT_INSUFFICIENT"
    elif rev_prob < 0.35:
        action = "AVOID_FADE"
    elif rev_prob < 0.5:
        action = "WAIT"

    return {
        "interpretation": interp,
        "continuation_prob": round(1.0 - rev_prob, 2),
        "reversal_prob": round(rev_prob, 2),
        "confidence": 0.55 if ctx_count else 0.35,
        "recommended_action": action,
        "evidence_for": [f"impulse={impulse:.2f}%", f"context_links={ctx_count}"],
        "evidence_against": [f"cont60={cont60:.2f}%"] if cont60 > 0 else [],
    }


def run_ai_critic_shadow(conn: Any, *, run_tag: str) -> int:
    run = conn.execute(
        "SELECT id FROM market_events_replay_runs WHERE run_tag = ?",
        (run_tag,),
    ).fetchone()
    if not run:
        return 0
    shocks = conn.execute(
        "SELECT * FROM market_events_replay_shocks WHERE run_id = ?",
        (int(run["id"]),),
    ).fetchall()
    n = 0
    for sh in shocks:
        ctx_n = conn.execute(
            "SELECT COUNT(*) AS n FROM market_events_replay_context_links WHERE shock_id = ?",
            (sh["id"],),
        ).fetchone()
        paths = conn.execute(
            "SELECT * FROM market_events_replay_path_metrics WHERE shock_id = ?",
            (sh["id"],),
        ).fetchall()
        critic = _deterministic_critic(sh, int(ctx_n["n"] if ctx_n else 0), paths)
        conn.execute(
            """
            INSERT OR REPLACE INTO market_events_replay_ai_critic (
              shock_id, interpretation, continuation_prob, reversal_prob,
              confidence, recommended_action, structured_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sh["id"], critic["interpretation"], critic["continuation_prob"],
                critic["reversal_prob"], critic["confidence"], critic["recommended_action"],
                json.dumps(critic), int(time.time()),
            ),
        )
        n += 1
    return n


def ai_critic_replay_report(conn: Any, *, run_tag: str) -> str:
    rows = conn.execute(
        """
        SELECT c.*, s.symbol, s.event_ts
        FROM market_events_replay_ai_critic c
        JOIN market_events_replay_shocks s ON s.id = c.shock_id
        JOIN market_events_replay_runs r ON r.id = s.run_id
        WHERE r.run_tag = ?
        """,
        (run_tag,),
    ).fetchall()
    lines = [
        "AI CRITIC REPLAY REPORT",
        f"run_tag: {run_tag}",
        "mode: SHADOW — does not filter paper or live execution",
        "",
    ]
    if not rows:
        lines.append("No critic analyses.")
        return "\n".join(lines)
    from collections import Counter
    interp = Counter(r["interpretation"] for r in rows)
    actions = Counter(r["recommended_action"] for r in rows)
    lines.append(f"analyses: {len(rows)}")
    lines.append(f"interpretations: {dict(interp)}")
    lines.append(f"actions: {dict(actions)}")
    return "\n".join(lines)
