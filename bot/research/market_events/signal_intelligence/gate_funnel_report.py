"""Unified paper gate funnel — single source of truth for stage PASS/FAIL.

Stages (aligned with should_open_trade / open_paper_trades_from_s40):

  EVENT → REGIME → EXPECTANCY → RISK → OPEN

Each row is attributed to exactly one terminal stage from stored ``gate_decision``
so REGIME_BLOCK and NEGATIVE_EXPECTANCY never contradict across reports.

Also includes a research section: closed-trade outcomes by Fear & Greed buckets
(diagnostic only — does not change filters).
"""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from typing import Any

from bot.research.market_events.signal_intelligence.regime_report_diag import (
    diagnose_block_reason,
)


_FEATURES = "market_events_trade_features_s55"

_STAGE_EVENT = "EVENT"
_STAGE_REGIME = "REGIME"
_STAGE_EXPECTANCY = "EXPECTANCY"
_STAGE_RISK = "RISK"
_STAGE_OPEN = "OPEN"

_FG_BUCKETS = (
    ("<25", lambda x: x < 25),
    ("25–50", lambda x: 25 <= x < 50),
    ("50–75", lambda x: 50 <= x < 75),
    (">75", lambda x: x >= 75),
)


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        blob = json.loads(raw) if isinstance(raw, str) else {}
        return dict(blob) if isinstance(blob, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _enrich_blob(row: Any, blob: dict[str, Any]) -> dict[str, Any]:
    out = dict(blob)
    for col in (
        "fear_greed", "macro_score", "funding", "trend", "volatility",
        "volume", "atr", "market_regime",
    ):
        try:
            if (col not in out or out.get(col) is None) and col in row.keys() and row[col] is not None:
                out[col] = row[col]
        except Exception:
            pass
    return out


def _estimate(row: Any, blob: dict[str, Any]) -> dict[str, Any]:
    est = blob.get("gate_estimate") if isinstance(blob.get("gate_estimate"), dict) else {}
    return {
        "expected_pnl_pct": _safe_float(
            est.get("expected_pnl_pct", row["gate_expected_pnl_pct"] if "gate_expected_pnl_pct" in row.keys() else None)
        ),
        "winrate": _safe_float(est.get("winrate")),
        "similar_count": int(
            est.get("similar_count")
            or (row["similar_count"] if "similar_count" in row.keys() else 0)
            or 0
        ),
    }


def classify_terminal_stage(gate: str, paper_trade_id: Any) -> tuple[str, str]:
    """Map stored gate_decision → (terminal_stage, pass_or_fail_label).

    One decision → one stage. This is the anti-contradiction rule.
    """
    g = str(gate or "").strip()
    opened = paper_trade_id is not None

    if g == "REGIME_BLOCK":
        return _STAGE_REGIME, "FAIL"
    if g == "NEGATIVE_EXPECTANCY":
        return _STAGE_EXPECTANCY, "FAIL"
    if g in ("MAX_OPEN", "MAX_OPEN_TRADES"):
        return _STAGE_RISK, "FAIL"
    if opened or g in ("ALLOWED", "INSUFFICIENT_HISTORY", "DISABLED", "cold_start"):
        return _STAGE_OPEN, "PASS"
    # Unknown reject → risk bucket for visibility
    if not opened:
        return _STAGE_RISK, "FAIL"
    return _STAGE_OPEN, "PASS"


def build_unified_gate_funnel(
    conn: Any,
    *,
    since_ts: int,
    limit_fail_details: int = 40,
) -> dict[str, Any]:
    rows = conn.execute(
        f"""
        SELECT symbol, direction, gate_decision, market_regime,
               volatility, volume, trend, funding, atr, fear_greed, macro_score,
               paper_trade_id, features_json, gate_expected_pnl_pct,
               similar_count, created_at, pnl_pct, result, closed_at
        FROM {_FEATURES}
        WHERE created_at >= ?
        ORDER BY created_at DESC
        """,
        (int(since_ts),),
    ).fetchall()

    n_event = len(rows)
    # Progressive remaining after each stage (derived from terminal attribution).
    fail_regime = 0
    fail_exp = 0
    fail_risk = 0
    opened = 0
    reason_regime: Counter[str] = Counter()
    reason_exp: Counter[str] = Counter()
    reason_risk: Counter[str] = Counter()
    open_by_gate: Counter[str] = Counter()
    fail_details: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for r in rows:
        gate = str(r["gate_decision"] or "")
        blob = _enrich_blob(r, _parse_json(r["features_json"]))
        stage, outcome = classify_terminal_stage(gate, r["paper_trade_id"])
        est = _estimate(r, blob)

        if stage == _STAGE_REGIME and outcome == "FAIL":
            fail_regime += 1
            primary, _ = diagnose_block_reason(r, blob)
            reason_regime[f"{gate} / {primary}"] += 1
            if len(fail_details[_STAGE_REGIME]) < limit_fail_details:
                fail_details[_STAGE_REGIME].append(
                    {
                        "symbol": r["symbol"],
                        "direction": r["direction"],
                        "gate": gate,
                        "why": primary,
                        "regime": blob.get("market_regime") or r["market_regime"],
                        "regime_score": blob.get("regime_score"),
                        "ev": est["expected_pnl_pct"],
                    }
                )
        elif stage == _STAGE_EXPECTANCY and outcome == "FAIL":
            fail_exp += 1
            ev = est["expected_pnl_pct"]
            reason_exp[f"{gate} (EV={ev if ev is not None else '—'})"] += 1
            # also bucket EV sign for summary
            if ev is not None and ev < 0:
                reason_exp["EV < 0"] += 1
            if len(fail_details[_STAGE_EXPECTANCY]) < limit_fail_details:
                fail_details[_STAGE_EXPECTANCY].append(
                    {
                        "symbol": r["symbol"],
                        "direction": r["direction"],
                        "gate": gate,
                        "ev": ev,
                        "winrate": est["winrate"],
                        "similar_count": est["similar_count"],
                        "regime": blob.get("market_regime") or r["market_regime"],
                    }
                )
        elif stage == _STAGE_RISK and outcome == "FAIL":
            fail_risk += 1
            reason_risk[gate or "UNKNOWN"] += 1
            if len(fail_details[_STAGE_RISK]) < limit_fail_details:
                fail_details[_STAGE_RISK].append(
                    {
                        "symbol": r["symbol"],
                        "direction": r["direction"],
                        "gate": gate,
                        "why": "capacity / risk gate",
                    }
                )
        else:
            opened += 1
            open_by_gate[gate or "OPEN"] += 1

    pass_regime = n_event - fail_regime
    pass_exp = pass_regime - fail_exp
    pass_risk = pass_exp - fail_risk

    stages = [
        {
            "stage": _STAGE_EVENT,
            "entered": n_event,
            "passed": n_event,
            "failed": 0,
            "reasons": {},
        },
        {
            "stage": _STAGE_REGIME,
            "entered": n_event,
            "passed": pass_regime,
            "failed": fail_regime,
            "reasons": dict(reason_regime),
            "fail_details": fail_details[_STAGE_REGIME],
        },
        {
            "stage": _STAGE_EXPECTANCY,
            "entered": pass_regime,
            "passed": pass_exp,
            "failed": fail_exp,
            "reasons": dict(reason_exp),
            "fail_details": fail_details[_STAGE_EXPECTANCY],
        },
        {
            "stage": _STAGE_RISK,
            "entered": pass_exp,
            "passed": pass_risk,
            "failed": fail_risk,
            "reasons": dict(reason_risk),
            "fail_details": fail_details[_STAGE_RISK],
        },
        {
            "stage": _STAGE_OPEN,
            "entered": pass_risk,
            "passed": opened,
            "failed": max(0, pass_risk - opened),
            "reasons": dict(open_by_gate),
        },
    ]

    return {
        "since_ts": int(since_ts),
        "n_event": n_event,
        "stages": stages,
        "opened": opened,
        "open_by_gate": dict(open_by_gate),
    }


def build_fear_greed_bucket_report(
    conn: Any,
    *,
    since_ts: int,
) -> dict[str, Any]:
    """Closed S55 outcomes by Fear & Greed buckets (research / confirmation)."""
    rows = conn.execute(
        f"""
        SELECT fear_greed, features_json, pnl_pct, pnl_usd, result, closed_at,
               gate_decision, symbol, direction
        FROM {_FEATURES}
        WHERE closed_at IS NOT NULL
          AND closed_at >= ?
          AND pnl_pct IS NOT NULL
        """,
        (int(since_ts),),
    ).fetchall()

    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name, _ in _FG_BUCKETS}
    missing_fg = 0

    for r in rows:
        fg = _safe_float(r["fear_greed"])
        if fg is None:
            blob = _parse_json(r["features_json"])
            fg = _safe_float(blob.get("fear_greed"))
            inputs = blob.get("regime_inputs") if isinstance(blob.get("regime_inputs"), dict) else {}
            if fg is None:
                fg = _safe_float(inputs.get("fear_greed"))
        if fg is None:
            missing_fg += 1
            continue
        placed = False
        for name, pred in _FG_BUCKETS:
            if pred(fg):
                buckets[name].append(
                    {
                        "fear_greed": fg,
                        "pnl_pct": float(r["pnl_pct"]),
                        "pnl_usd": _safe_float(r["pnl_usd"]),
                        "result": str(r["result"] or ""),
                    }
                )
                placed = True
                break
        if not placed:
            missing_fg += 1

    def _agg(items: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(items)
        if n == 0:
            return {
                "n": 0,
                "winrate": None,
                "avg_pnl_pct": None,
                "sum_pnl_usd": None,
                "expectancy_pct": None,
            }
        wins = sum(1 for x in items if str(x.get("result") or "").upper() == "WIN")
        pnls = [float(x["pnl_pct"]) for x in items]
        usd = [float(x["pnl_usd"]) for x in items if x.get("pnl_usd") is not None]
        return {
            "n": n,
            "winrate": round(100.0 * wins / n, 1),
            "avg_pnl_pct": round(sum(pnls) / n, 4),
            "sum_pnl_usd": round(sum(usd), 2) if usd else None,
            "expectancy_pct": round(sum(pnls) / n, 4),
        }

    return {
        "since_ts": int(since_ts),
        "closed_with_pnl": len(rows),
        "missing_fear_greed": missing_fg,
        "buckets": {name: _agg(buckets[name]) for name, _ in _FG_BUCKETS},
    }


def format_unified_gate_funnel(
    conn: Any,
    *,
    since_hours: float = 24.0,
    fear_greed_months: float = 6.0,
    limit_fail_details: int = 25,
) -> str:
    since_ts = int(time.time() - float(since_hours) * 3600)
    fg_since = int(time.time() - float(fear_greed_months) * 30.5 * 86400)
    funnel = build_unified_gate_funnel(
        conn, since_ts=since_ts, limit_fail_details=limit_fail_details,
    )
    fg = build_fear_greed_bucket_report(conn, since_ts=fg_since)

    lines = [
        "UNIFIED GATE FUNNEL",
        f"Source of truth: market_events_trade_features_s55.gate_decision",
        f"Window: last {since_hours:g}h  (n={funnel['n_event']})",
        "",
        "EVENT",
        f"  entered ............. {funnel['n_event']}",
    ]

    for st in funnel["stages"]:
        name = st["stage"]
        if name == _STAGE_EVENT:
            continue
        lines.append("")
        lines.append(f"↓ {name}")
        lines.append(f"  entered ............. {st['entered']}")
        lines.append(f"  PASS ................ {st['passed']}")
        lines.append(f"  FAIL ................ {st['failed']}")
        reasons = st.get("reasons") or {}
        if st["failed"] and reasons:
            lines.append("  FAIL reasons:")
            for reason, n in sorted(reasons.items(), key=lambda x: (-x[1], x[0])):
                if reason.startswith("EV <"):
                    continue  # shown as aggregate helper; prefer full gate lines
                lines.append(f"    • {reason}: {n}")
        if name == _STAGE_OPEN and reasons:
            lines.append("  OPEN by gate:")
            for reason, n in sorted(reasons.items(), key=lambda x: (-x[1], x[0])):
                lines.append(f"    • {reason}: {n}")
        details = st.get("fail_details") or []
        if details and name in (_STAGE_REGIME, _STAGE_EXPECTANCY, _STAGE_RISK):
            lines.append(f"  FAIL samples (up to {limit_fail_details}):")
            for d in details[:10]:
                if name == _STAGE_EXPECTANCY:
                    lines.append(
                        f"    - {d.get('symbol')} {d.get('direction')}  "
                        f"EV={d.get('ev')}  sim={d.get('similar_count')}  "
                        f"regime={d.get('regime')}"
                    )
                elif name == _STAGE_REGIME:
                    lines.append(
                        f"    - {d.get('symbol')} {d.get('direction')}  "
                        f"why={d.get('why')}  regime={d.get('regime')}  "
                        f"score={d.get('regime_score')}"
                    )
                else:
                    lines.append(
                        f"    - {d.get('symbol')} {d.get('direction')}  "
                        f"gate={d.get('gate')}"
                    )

    lines.extend(
        [
            "",
            "=" * 52,
            "FEAR & GREED RESEARCH (closed trades — filter not changed)",
            f"Lookback: ~{fear_greed_months:g} months  "
            f"(closed_with_pnl={fg['closed_with_pnl']}, missing_fg={fg['missing_fear_greed']})",
            "",
            f"{'F&G bucket':<12} {'n':>5} {'WR%':>7} {'avg PnL%':>10} {'Σ PnL$':>10}",
            "-" * 52,
        ]
    )
    for name, _ in _FG_BUCKETS:
        b = fg["buckets"][name]
        wr = f"{b['winrate']:.1f}" if b["winrate"] is not None else "—"
        avg = f"{b['avg_pnl_pct']:.4f}" if b["avg_pnl_pct"] is not None else "—"
        usd = f"{b['sum_pnl_usd']:.2f}" if b["sum_pnl_usd"] is not None else "—"
        lines.append(f"{name:<12} {b['n']:>5} {wr:>7} {avg:>10} {usd:>10}")

    lines.extend(
        [
            "",
            "How to read F&G:",
            "  If F&G <25 has clearly worse expectancy than mid/high buckets →",
            "  macro-risk stress at fear≈22 is supported by history.",
            "  If not — argument to revisit how fear_greed weights regime/diagnostics",
            "  (still do not auto-change S55/S57 gates from this report).",
            "",
            "Aliases: paper-gate-funnel / regime-report → this unified report.",
        ]
    )
    return "\n".join(lines)


# Back-compat names used by older imports / CLI.
def format_paper_gate_funnel(conn: Any, **kwargs: Any) -> str:
    hours = kwargs.get("since_hours")
    if hours is None and kwargs.get("since_ts") is not None:
        hours = max(0.1, (time.time() - float(kwargs["since_ts"])) / 3600.0)
    return format_unified_gate_funnel(
        conn,
        since_hours=float(hours if hours is not None else 24.0),
        fear_greed_months=float(kwargs.get("fear_greed_months") or 6.0),
        limit_fail_details=int(kwargs.get("limit_details") or 25),
    )


__all__ = [
    "build_unified_gate_funnel",
    "build_fear_greed_bucket_report",
    "format_unified_gate_funnel",
    "format_paper_gate_funnel",
    "classify_terminal_stage",
]
