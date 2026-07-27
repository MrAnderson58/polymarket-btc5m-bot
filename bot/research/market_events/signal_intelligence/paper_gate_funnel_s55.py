"""S55 paper-gate funnel report — candidates vs reject reasons with EV/probability."""

from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any


_FEATURES = "market_events_trade_features_s55"

# Display order for funnel stages (remaining / terminal counts).
_FUNNEL_ORDER = (
    ("candidates", "Всего событий (logged gates)"),
    ("passed_regime", "Прошли regime"),
    ("passed_max_open", "Прошли max_open"),
    ("had_similar", "Прошли similar/history"),
    ("rejected_negative_expectancy", "Отсеяно NEGATIVE_EXPECTANCY"),
    ("rejected_regime", "Отсеяно REGIME_BLOCK"),
    ("rejected_max_open", "Отсеяно MAX_OPEN"),
    ("opened", "Открыто сделок"),
    ("cold_start_allowed", "Открыто (INSUFFICIENT_HISTORY / cold-start)"),
    ("allowed", "Открыто (ALLOWED)"),
)


def _has(row: Any, key: str) -> bool:
    try:
        if hasattr(row, "keys") and key not in row.keys():
            return False
        _ = row[key]
        return True
    except Exception:
        return False


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_estimate(row: Any) -> dict[str, Any]:
    """Recover EV / probability from columns + features_json.gate_estimate."""
    out: dict[str, Any] = {
        "expected_pnl_pct": (
            _safe_float(row["gate_expected_pnl_pct"])
            if _has(row, "gate_expected_pnl_pct")
            else None
        ),
        "winrate": None,
        "p_tp1": None,
        "p_sl": None,
        "similar_count": int(row["similar_count"] or 0) if _has(row, "similar_count") else 0,
        "n": None,
    }
    raw = row["features_json"] if _has(row, "features_json") else None
    if not raw:
        return out
    try:
        blob = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (json.JSONDecodeError, TypeError):
        return out
    est = blob.get("gate_estimate") if isinstance(blob, dict) else None
    if not isinstance(est, dict):
        return out
    if est.get("expected_pnl_pct") is not None:
        out["expected_pnl_pct"] = _safe_float(est.get("expected_pnl_pct"))
    if est.get("winrate") is not None:
        out["winrate"] = _safe_float(est.get("winrate"))
    if est.get("p_tp1") is not None:
        out["p_tp1"] = _safe_float(est.get("p_tp1"))
    if est.get("p_sl") is not None:
        out["p_sl"] = _safe_float(est.get("p_sl"))
    if est.get("n") is not None:
        try:
            out["n"] = int(est["n"])
        except (TypeError, ValueError):
            pass
    if est.get("similar_count") is not None:
        try:
            out["similar_count"] = int(est["similar_count"])
        except (TypeError, ValueError):
            pass
    return out


def _ev_from_estimate(est: dict[str, Any]) -> float | None:
    """EV proxy: expected_pnl_pct (model expectancy)."""
    return est.get("expected_pnl_pct")


def _probability_from_estimate(est: dict[str, Any]) -> float | None:
    """Win probability in [0,1] from winrate percent if present."""
    wr = est.get("winrate")
    if wr is None:
        return None
    try:
        return round(float(wr) / 100.0, 4)
    except (TypeError, ValueError):
        return None


def build_paper_gate_funnel(
    conn: Any,
    *,
    since_ts: int | None = None,
    limit_details: int = 200,
) -> dict[str, Any]:
    """Aggregate S55 gate log into funnel + per-reject detail rows."""
    since = int(since_ts if since_ts is not None else 0)
    rows = conn.execute(
        f"""
        SELECT s40_signal_type, s40_signal_id, symbol, direction,
               gate_decision, gate_expected_pnl_pct, similar_count,
               paper_trade_id, features_json, created_at
        FROM {_FEATURES}
        WHERE created_at >= ?
        ORDER BY created_at DESC
        """,
        (since,),
    ).fetchall()

    by_gate: Counter[str] = Counter()
    rejects: list[dict[str, Any]] = []
    opened = 0
    for r in rows:
        gate = str(r["gate_decision"] or "UNKNOWN")
        by_gate[gate] += 1
        has_trade = r["paper_trade_id"] is not None
        if has_trade:
            opened += 1
        is_reject = gate in (
            "NEGATIVE_EXPECTANCY",
            "MAX_OPEN",
            "REGIME_BLOCK",
            "MAX_OPEN_TRADES",
        ) or (not has_trade and gate not in ("ALLOWED", "INSUFFICIENT_HISTORY", "DISABLED", "cold_start"))
        if is_reject:
            est = _parse_estimate(r)
            rejects.append(
                {
                    "created_at": int(r["created_at"] or 0),
                    "symbol": str(r["symbol"] or ""),
                    "direction": str(r["direction"] or ""),
                    "s40_signal_type": str(r["s40_signal_type"] or ""),
                    "s40_signal_id": int(r["s40_signal_id"] or 0),
                    "reason": gate,
                    "ev": _ev_from_estimate(est),
                    "probability": _probability_from_estimate(est),
                    "expected_pnl_pct": est.get("expected_pnl_pct"),
                    "winrate": est.get("winrate"),
                    "similar_count": est.get("similar_count"),
                }
            )

    total = len(rows)
    rej_neg = by_gate.get("NEGATIVE_EXPECTANCY", 0)
    rej_reg = by_gate.get("REGIME_BLOCK", 0)
    rej_max = by_gate.get("MAX_OPEN", 0) + by_gate.get("MAX_OPEN_TRADES", 0)
    allowed = by_gate.get("ALLOWED", 0)
    cold = by_gate.get("INSUFFICIENT_HISTORY", 0) + by_gate.get("cold_start", 0)
    passed_regime = total - rej_reg
    passed_max = passed_regime - rej_max
    had_similar = passed_max

    funnel = {
        "candidates": total,
        "passed_regime": max(0, passed_regime),
        "passed_max_open": max(0, passed_max),
        "had_similar": max(0, had_similar),
        "rejected_negative_expectancy": rej_neg,
        "rejected_regime": rej_reg,
        "rejected_max_open": rej_max,
        "opened": opened,
        "cold_start_allowed": cold,
        "allowed": allowed,
    }

    return {
        "since_ts": since,
        "total": total,
        "by_gate": dict(by_gate),
        "funnel": funnel,
        "rejects": rejects[: max(1, int(limit_details))],
        "rejects_truncated": max(0, len(rejects) - int(limit_details)),
    }


def format_paper_gate_funnel(
    conn: Any,
    *,
    since_ts: int | None = None,
    since_hours: float | None = None,
    limit_details: int = 100,
) -> str:
    if since_ts is None and since_hours is not None:
        since_ts = int(time.time() - float(since_hours) * 3600)
    if since_ts is None:
        since_ts = int(time.time() - 86400)

    data = build_paper_gate_funnel(conn, since_ts=since_ts, limit_details=limit_details)
    funnel = data["funnel"]
    lines = [
        "Paper Gate Funnel (S55)",
        f"Window: since {data['since_ts']} "
        f"({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(data['since_ts']))})",
        "",
        f"{'Этап':<42} {'N':>8}",
        "-" * 52,
    ]
    for key, label in _FUNNEL_ORDER:
        if key in ("rejected_regime", "rejected_max_open") and int(funnel.get(key) or 0) == 0:
            continue
        lines.append(f"{label:<42} {int(funnel.get(key) or 0):>8}")

    lines.extend(["", "By gate_decision", "-" * 52])
    for gate, n in sorted(data["by_gate"].items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"{gate:<42} {n:>8}")

    lines.extend(
        [
            "",
            "Reject details (EV / probability / expected PnL / reason)",
            "-" * 100,
            f"{'time':<19} {'sym':<10} {'dir':<5} {'reason':<22} "
            f"{'EV%':>8} {'P(win)':>7} {'expPnL%':>9} {'sim':>4}",
        ]
    )
    for r in data["rejects"]:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(r["created_at"] or 0)))
        ev = r["ev"]
        prob = r["probability"]
        exp = r["expected_pnl_pct"]
        lines.append(
            f"{ts:<19} {str(r['symbol'])[:10]:<10} {str(r['direction'])[:5]:<5} "
            f"{str(r['reason'])[:22]:<22} "
            f"{(f'{ev:.4f}' if ev is not None else '—'):>8} "
            f"{(f'{prob:.3f}' if prob is not None else '—'):>7} "
            f"{(f'{exp:.4f}' if exp is not None else '—'):>9} "
            f"{int(r.get('similar_count') or 0):>4}"
        )
    if data.get("rejects_truncated"):
        lines.append(f"… {data['rejects_truncated']} more rejects omitted")

    lines.extend(
        [
            "",
            "Notes:",
            "  EV% = expected_pnl_pct from similar closed trades (model expectancy).",
            "  P(win) = winrate/100 from neighbours (— until gate_estimate stored).",
            "  New rejects store winrate in features_json.gate_estimate.",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "build_paper_gate_funnel",
    "format_paper_gate_funnel",
]
