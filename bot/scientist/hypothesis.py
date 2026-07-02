"""Hypothesis Generator — pattern discovery and experiment ideas."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import statistics
from datetime import datetime, timezone
from typing import Any

from bot.report.analytics import _metrics
from bot.scientist.constants import SOURCE_TABLE
from bot.scientist.dataset import load_enriched_trades
from bot.trading_brain.knowledge import load_knowledge


def _fingerprint(hypothesis_type: str, params: dict[str, Any]) -> str:
    raw = json.dumps({"type": hypothesis_type, "params": params}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _metrics_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return _metrics([float(r.get("pnl") or 0) for r in rows])


def _confidence(n: int, effect: float, baseline_n: int) -> float:
    base = min(85.0, 35.0 + n * 1.5)
    if abs(effect) > 5:
        base += 8
    if n >= baseline_n * 0.15:
        base += 5
    return min(99.0, max(5.0, base))


def _entry_near(row: dict[str, Any], entry: float, tol: float = 0.006) -> bool:
    return abs(float(row.get("entry_price", 0)) - entry) < tol


def _generate_entry_btc_hypotheses(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hypotheses: list[dict[str, Any]] = []
    baseline_pf = _metrics_from_rows(rows).get("profit_factor", 1.0)

    combos: dict[tuple[float, str], list[dict[str, Any]]] = {}
    for row in rows:
        entry = round(float(row.get("entry_price", 0)), 2)
        direction = row.get("btc_direction", "flat")
        combos.setdefault((entry, direction), []).append(row)

    for (entry, direction), subset in combos.items():
        m = _metrics_from_rows(subset)
        if m["trades"] < 8:
            continue
        pf = m["profit_factor"]
        if pf == float("inf"):
            pf = 3.0
        if pf >= 1.0:
            continue

        proposed_entry = round(max(0.34, entry - 0.01), 2)
        proposed_subset = [
            r
            for r in rows
            if _entry_near(r, proposed_entry) and r.get("btc_direction") == direction
        ]
        proposed_m = _metrics_from_rows(proposed_subset)
        proposed_pf = proposed_m["profit_factor"]
        if proposed_pf == float("inf"):
            proposed_pf = baseline_pf * 1.5
        improvement = (
            (proposed_m["avg_pnl"] - m["avg_pnl"])
            if proposed_m["trades"] >= 5
            else (proposed_pf - pf) * 10
        )

        params = {
            "current_entry": entry,
            "proposed_entry": proposed_entry,
            "btc_direction": direction,
        }
        hypotheses.append(
            {
                "fingerprint": _fingerprint("entry_btc", params),
                "hypothesis_type": "entry_btc",
                "description": (
                    f"Entry {entry:.2f} with BTC {direction.upper()} shows PF {pf:.2f} "
                    f"(n={m['trades']}). Try Entry {proposed_entry:.2f} when BTC {direction.upper()}."
                ),
                "source": "entry_btc_scan",
                "sample_n": m["trades"],
                "confidence": _confidence(m["trades"], improvement, len(rows)),
                "expected_improvement": round(improvement, 2),
                "expected_pf": round(proposed_pf if proposed_m["trades"] >= 5 else pf * 1.15, 2),
                "expected_wr": round(
                    proposed_m["win_rate"] if proposed_m["trades"] >= 5 else m["win_rate"] * 1.05,
                    3,
                ),
                "params": params,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return hypotheses


def _generate_delayed_stop_hypotheses(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    from bot.analytics.intelligence_context import build_intelligence_context
    from bot.analytics.recovery_analyzer import build_recovery_analyzer
    from bot.report.analytics import fetch_v2_trades

    closed = fetch_v2_trades(conn, closed_only=True)
    ctx = build_intelligence_context(conn, closed)
    recovery = build_recovery_analyzer(closed, ctx=ctx)
    hypotheses: list[dict[str, Any]] = []

    for hold in recovery.get("alternative_holds", []):
        sec = int(hold["hold_sec"])
        if sec not in (30, 45, 60):
            continue
        trades_n = int(hold.get("trades", 0))
        if trades_n < 8:
            continue
        wr = float(hold.get("win_rate", 0))
        pf = float(hold.get("profit_factor", 0))
        if pf == float("inf"):
            pf = 2.0

        recovery_rate = wr
        if recovery_rate < 0.45:
            continue

        params = {"hold_sec": sec, "recovery_rate": round(recovery_rate, 3)}
        improvement = (pf - 1.0) * 12 if pf > 1 else recovery_rate * 8
        hypotheses.append(
            {
                "fingerprint": _fingerprint("delayed_stop", params),
                "hypothesis_type": "delayed_stop",
                "description": (
                    f"STOP LOSS → {recovery_rate:.0%} recovery within {sec}s "
                    f"(n={trades_n}). Test delayed stop {sec} seconds."
                ),
                "source": "stop_recovery_analyzer",
                "sample_n": trades_n,
                "confidence": _confidence(trades_n, improvement, len(closed)),
                "expected_improvement": round(improvement, 2),
                "expected_pf": round(max(pf, 1.0 + improvement / 20), 2),
                "expected_wr": round(wr, 3),
                "params": params,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return hypotheses


def _generate_brain_hypotheses(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    hypotheses: list[dict[str, Any]] = []
    for row in load_knowledge(conn, limit=30):
        direction = row["causal_direction"]
        if direction == "neutral":
            continue
        effect = float(row["effect_value"])
        n = int(row["sample_n"])
        if n < 8:
            continue

        params = {
            "feature": row["feature"],
            "condition": row["condition_text"],
            "effect": effect,
        }
        action = "avoid" if direction == "negative" else "favor"
        hypotheses.append(
            {
                "fingerprint": _fingerprint("brain_knowledge", params),
                "hypothesis_type": "brain_knowledge",
                "description": (
                    f"Trading Brain: {row['condition_text']} → {direction} effect "
                    f"{effect:+.1f}% (n={n}). Experiment: {action} this condition."
                ),
                "source": "trading_brain",
                "sample_n": n,
                "confidence": float(row["confidence"]),
                "expected_improvement": round(abs(effect), 2),
                "expected_pf": round(1.0 + abs(effect) / 25, 2),
                "expected_wr": 0.55 if direction == "positive" else 0.45,
                "params": params,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return hypotheses


def _generate_trailing_impulse_hypotheses(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trailing = [r for r in rows if (r.get("exit_reason") or "").upper() == "TRAILING_STOP"]
    if len(trailing) < 8:
        return []

    impulse = [
        r
        for r in trailing
        if abs(float(r.get("btc_move_30s") or 0)) >= 20
    ]
    calm = [
        r
        for r in trailing
        if abs(float(r.get("btc_move_30s") or 0)) < 10
    ]
    if len(impulse) < 5 or len(calm) < 5:
        return []

    impulse_m = _metrics_from_rows(impulse)
    calm_m = _metrics_from_rows(calm)
    if impulse_m["avg_pnl"] >= calm_m["avg_pnl"]:
        return []

    effect = calm_m["avg_pnl"] - impulse_m["avg_pnl"]
    params = {"impulse_threshold": 20, "comparison": "trailing_after_btc_impulse"}
    return [
        {
            "fingerprint": _fingerprint("trailing_impulse", params),
            "hypothesis_type": "trailing_impulse",
            "description": (
                f"Trailing Stop underperforms after strong BTC impulse "
                f"(avg {impulse_m['avg_pnl']:+.1f}% vs {calm_m['avg_pnl']:+.1f}%, "
                f"n={len(impulse)}). Test wider trail or skip after impulse."
            ),
            "source": "trailing_btc_scan",
            "sample_n": len(impulse),
            "confidence": _confidence(len(impulse), effect, len(trailing)),
            "expected_improvement": round(effect, 2),
            "expected_pf": round(calm_m["profit_factor"], 2),
            "expected_wr": round(calm_m["win_rate"], 3),
            "params": params,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]


def is_duplicate(conn: sqlite3.Connection, fingerprint: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM scientist_hypotheses WHERE fingerprint = ? LIMIT 1",
        (fingerprint,),
    ).fetchone()
    if row:
        return True
    mem = conn.execute(
        """
        SELECT 1 FROM brain_memory
        WHERE category = 'scientist_hypothesis' AND ref_key = ?
        LIMIT 1
        """,
        (fingerprint,),
    ).fetchone()
    return mem is not None


def mark_hypothesis_seen(conn: sqlite3.Connection, hypothesis: dict[str, Any]) -> None:
    from bot.trading_brain.memory import upsert_memory

    upsert_memory(
        conn,
        category="scientist_hypothesis",
        ref_key=hypothesis["fingerprint"],
        payload={
            "fingerprint": hypothesis["fingerprint"],
            "description": hypothesis["description"],
            "hypothesis_type": hypothesis["hypothesis_type"],
            "created_at": hypothesis["created_at"],
        },
    )


def generate_hypotheses(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = load_enriched_trades(conn)
    candidates: list[dict[str, Any]] = []
    candidates.extend(_generate_entry_btc_hypotheses(rows))
    candidates.extend(_generate_delayed_stop_hypotheses(conn))
    candidates.extend(_generate_brain_hypotheses(conn))
    candidates.extend(_generate_trailing_impulse_hypotheses(rows))

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for h in candidates:
        fp = h["fingerprint"]
        if fp in seen or is_duplicate(conn, fp):
            continue
        seen.add(fp)
        unique.append(h)
    return unique
