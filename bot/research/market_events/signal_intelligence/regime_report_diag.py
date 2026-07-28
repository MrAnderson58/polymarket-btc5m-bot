"""Diagnostic REGIME REPORT — distribution + reject factor breakdown (read-only)."""

from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any


_FEATURES = "market_events_trade_features_s55"

# Aggregate buckets for the headline mix.
_BULL = frozenset({"STRONG_BULL", "WEAK_BULL"})
_BEAR = frozenset({"STRONG_BEAR", "WEAK_BEAR"})
_NEUTRAL = frozenset({"RANGE", "NEUTRAL", ""})

# Soft diagnostic thresholds (display only — not trading gates).
_FEAR_MACRO_RISK = 35.0          # fear_greed below → macro risk
_VOLUME_LOW = 1e-9               # ~0 volume column
_VOL_MID_LOW = 45.0
_VOL_MID_HIGH = 55.0
_REGIME_SCORE_NEUTRAL_ABS = 0.5  # |score| < 0.5 → RANGE/neutral band


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        blob = json.loads(raw) if isinstance(raw, str) else {}
        return blob if isinstance(blob, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _bucket_regime(reg: str | None) -> str:
    r = str(reg or "").strip().upper()
    if r in _BULL:
        return "Bull"
    if r in _BEAR:
        return "Bear"
    return "Neutral"


def _is_reject(gate: str, paper_trade_id: Any) -> bool:
    g = str(gate or "")
    if g in ("NEGATIVE_EXPECTANCY", "REGIME_BLOCK", "MAX_OPEN", "MAX_OPEN_TRADES"):
        return True
    if paper_trade_id is None and g not in (
        "ALLOWED", "INSUFFICIENT_HISTORY", "DISABLED", "cold_start", "",
    ):
        return True
    return False


def diagnose_block_reason(row: Any, blob: dict[str, Any]) -> tuple[str, dict[str, float]]:
    """Pick a primary diagnostic factor for a rejected / weak candidate.

    Returns (primary_label, score_map). Higher score = more likely explanation.
    Diagnostic only — does not change gates.
    """
    scores: dict[str, float] = {
        "low volatility": 0.0,
        "macro risk": 0.0,
        "funding": 0.0,
        "btc regime": 0.0,
        "volume": 0.0,
    }

    vol = _safe_float(row["volatility"] if "volatility" in row.keys() else None)
    if vol is None:
        vol = _safe_float(blob.get("volatility"))
    atr = _safe_float(blob.get("atr"))
    if vol is not None and _VOL_MID_LOW <= vol <= _VOL_MID_HIGH:
        scores["low volatility"] += 2.0
    if atr is not None and atr <= 0:
        scores["low volatility"] += 1.5
    if vol is not None and vol <= 0:
        scores["low volatility"] += 2.5

    fear = _safe_float(blob.get("fear_greed"))
    if fear is None and "fear_greed" not in blob:
        # column may exist on row via features table — not always selected
        pass
    macro = _safe_float(blob.get("macro_score"))
    inputs = blob.get("regime_inputs") if isinstance(blob.get("regime_inputs"), dict) else {}
    comps = inputs.get("component_scores") if isinstance(inputs.get("component_scores"), dict) else {}
    if fear is None:
        fear = _safe_float(inputs.get("fear_greed"))
    if fear is not None and fear < _FEAR_MACRO_RISK:
        scores["macro risk"] += 2.5 + (0.5 if fear < 25 else 0.0)
    if macro is not None and macro < 0:
        scores["macro risk"] += 1.5
    fg_comp = _safe_float(comps.get("fear_greed"))
    if fg_comp is not None and fg_comp < 0:
        scores["macro risk"] += abs(fg_comp) * 2.0

    funding = _safe_float(blob.get("funding"))
    if funding is None:
        funding = _safe_float(inputs.get("funding"))
    fund_comp = _safe_float(comps.get("funding"))
    if fund_comp is not None and fund_comp < 0:
        scores["funding"] += abs(fund_comp) * 3.0
    if funding is not None and abs(funding) > 20:  # stored scale varies; extreme → flag
        scores["funding"] += 1.0

    regime = str(
        blob.get("market_regime")
        or (row["market_regime"] if "market_regime" in row.keys() else "")
        or ""
    )
    regime_score = _safe_float(blob.get("regime_score"))
    btc_comp = _safe_float(comps.get("btc"))
    btc_ret = _safe_float(inputs.get("btc_return_pct"))
    if regime.upper() in _BEAR or (regime_score is not None and regime_score <= -0.5):
        scores["btc regime"] += 2.0
    if btc_comp is not None and btc_comp < 0:
        scores["btc regime"] += abs(btc_comp) * 2.0
    if btc_ret is not None and btc_ret < 0:
        scores["btc regime"] += min(2.0, abs(btc_ret))
    if regime.upper() in ("RANGE",) and (regime_score is not None and abs(regime_score) < _REGIME_SCORE_NEUTRAL_ABS):
        scores["btc regime"] += 1.0  # flat / unclear BTC tape

    volume = _safe_float(row["volume"] if "volume" in row.keys() else None)
    if volume is None:
        volume = _safe_float(blob.get("volume"))
    if volume is not None and volume <= _VOLUME_LOW:
        scores["volume"] += 2.5
    elif volume is not None and volume < 1.0:
        scores["volume"] += 1.0

    # Always pick a primary (even if all weak).
    primary = max(scores.items(), key=lambda kv: kv[1])[0]
    if scores[primary] <= 0:
        primary = "btc regime"
    return primary, scores


def _trend_score(row: Any, blob: dict[str, Any]) -> float | None:
    t = _safe_float(row["trend"] if "trend" in row.keys() else None)
    if t is None:
        t = _safe_float(blob.get("trend"))
    inputs = blob.get("regime_inputs") if isinstance(blob.get("regime_inputs"), dict) else {}
    comps = inputs.get("component_scores") if isinstance(inputs.get("component_scores"), dict) else {}
    if t is None:
        t = _safe_float(comps.get("trend"))
    return t


def _volatility_score(row: Any, blob: dict[str, Any]) -> float | None:
    v = _safe_float(row["volatility"] if "volatility" in row.keys() else None)
    if v is None:
        v = _safe_float(blob.get("volatility"))
    return v


def _regime_score(row: Any, blob: dict[str, Any]) -> float | None:
    s = _safe_float(blob.get("regime_score"))
    if s is not None:
        return s
    return None


def _threshold_text(gate: str, blob: dict[str, Any], exp_pnl: float | None) -> str:
    """Human threshold that applied (diagnostic)."""
    g = str(gate or "")
    if g == "REGIME_BLOCK":
        return "S57: expectancy<min AND profit_factor<1 (regime×direction)"
    if g == "NEGATIVE_EXPECTANCY":
        thr = 0.0
        est = blob.get("gate_estimate") if isinstance(blob.get("gate_estimate"), dict) else {}
        # S55 default min expected pnl
        return f"S55: expected_pnl_pct < {thr:.2f} (got {exp_pnl if exp_pnl is not None else '—'})"
    if g == "MAX_OPEN":
        return "S55: open trades >= max_open"
    rs = _safe_float(blob.get("regime_score"))
    if rs is not None:
        return f"regime_score band |s|<{_REGIME_SCORE_NEUTRAL_ABS:.1f} → Neutral/RANGE (s={rs:.3f})"
    return "—"


def build_regime_diagnostic(
    conn: Any,
    *,
    since_ts: int,
    limit_details: int = 50,
) -> dict[str, Any]:
    rows = conn.execute(
        f"""
        SELECT symbol, direction, gate_decision, market_regime,
               volatility, volume, trend, funding, atr, fear_greed, macro_score,
               paper_trade_id, features_json, gate_expected_pnl_pct,
               similar_count, created_at
        FROM {_FEATURES}
        WHERE created_at >= ?
        ORDER BY created_at DESC
        """,
        (int(since_ts),),
    ).fetchall()

    mix = Counter()
    factor_counts = Counter()
    rejects: list[dict[str, Any]] = []
    gate_counts = Counter()

    for r in rows:
        gate = str(r["gate_decision"] or "")
        gate_counts[gate] += 1
        blob = _parse_json(r["features_json"])
        if not blob.get("market_regime") and r["market_regime"]:
            blob["market_regime"] = r["market_regime"]
        # Prefer live columns when JSON omits them.
        for col in ("fear_greed", "macro_score", "funding", "trend", "volatility", "volume", "atr"):
            try:
                if col not in blob or blob.get(col) is None:
                    if col in r.keys() and r[col] is not None:
                        blob[col] = r[col]
            except Exception:
                pass
        reg = str(blob.get("market_regime") or r["market_regime"] or "")
        mix[_bucket_regime(reg)] += 1

        if not _is_reject(gate, r["paper_trade_id"]):
            continue

        primary, factor_scores = diagnose_block_reason(r, blob)
        factor_counts[primary] += 1
        exp = _safe_float(r["gate_expected_pnl_pct"])
        rejects.append(
            {
                "symbol": str(r["symbol"] or ""),
                "direction": str(r["direction"] or ""),
                "gate": gate,
                "market_regime": reg or "—",
                "trend_score": _trend_score(r, blob),
                "volatility_score": _volatility_score(r, blob),
                "regime_score": _regime_score(r, blob),
                "threshold": _threshold_text(gate, blob, exp),
                "reason": gate,
                "block_factor": primary,
                "expected_pnl_pct": exp,
                "similar_count": int(r["similar_count"] or 0),
                "factor_scores": factor_scores,
                "created_at": int(r["created_at"] or 0),
            }
        )

    total = len(rows)
    n_rej = len(rejects)

    def _pct(n: int, d: int) -> float:
        return round(100.0 * n / d, 1) if d else 0.0

    return {
        "since_ts": int(since_ts),
        "total": total,
        "rejected": n_rej,
        "mix": {
            "Bull": mix.get("Bull", 0),
            "Bear": mix.get("Bear", 0),
            "Neutral": mix.get("Neutral", 0),
        },
        "mix_pct": {
            "Bull": _pct(mix.get("Bull", 0), total),
            "Bear": _pct(mix.get("Bear", 0), total),
            "Neutral": _pct(mix.get("Neutral", 0), total),
        },
        "blocked_factors": dict(factor_counts),
        "blocked_factors_pct": {
            k: _pct(v, n_rej) for k, v in factor_counts.items()
        },
        "gate_counts": dict(gate_counts),
        "rejects": rejects[: max(1, int(limit_details))],
        "rejects_truncated": max(0, n_rej - int(limit_details)),
    }


def format_regime_diagnostic_report(
    conn: Any,
    *,
    since_hours: float = 24.0,
    limit_details: int = 40,
) -> str:
    since_ts = int(time.time() - float(since_hours) * 3600)
    data = build_regime_diagnostic(conn, since_ts=since_ts, limit_details=limit_details)
    mix_pct = data["mix_pct"]
    lines = [
        "REGIME REPORT",
        f"Window: last {since_hours:g}h  (n={data['total']} gated, rejected={data['rejected']})",
        "",
        f"Bull ............ {mix_pct.get('Bull', 0):.0f}%",
        f"Bear ............ {mix_pct.get('Bear', 0):.0f}%",
        f"Neutral ......... {mix_pct.get('Neutral', 0):.0f}%",
        "",
        "Blocked because:",
    ]

    # Stable order matching the requested template.
    order = ("low volatility", "macro risk", "funding", "btc regime", "volume")
    factor_pct = data["blocked_factors_pct"]
    if data["rejected"] == 0:
        lines.append("  (no rejected gates in window)")
    else:
        for label in order:
            pct = float(factor_pct.get(label) or 0.0)
            # Also show factors that appeared but not in template? keep template + extras
            pad = label + " "
            lines.append(f"{pad:.<28} {pct:.0f}%")
        for label, pct in sorted(factor_pct.items()):
            if label not in order:
                pad = label + " "
                lines.append(f"{pad:.<28} {pct:.0f}%")

    lines.extend(["", "Gate decisions", "-" * 40])
    for g, n in sorted(data["gate_counts"].items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"  {g:<28} {n:>5}")

    lines.extend(["", "Rejected signals (detail)", "=" * 40])
    if not data["rejects"]:
        lines.append("(none)")
    for r in data["rejects"]:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(r["created_at"])))
        trend = r["trend_score"]
        vol = r["volatility_score"]
        rscore = r["regime_score"]
        lines.extend(
            [
                "",
                str(r["symbol"] or "—"),
                f"  Direction .......... {r['direction']}",
                f"  Time ............... {ts}",
                f"  Trend score ........ {trend if trend is not None else '—'}",
                f"  Volatility score ... {vol if vol is not None else '—'}",
                f"  Regime score ....... {rscore if rscore is not None else '—'}",
                f"  Regime ............. {r['market_regime']}",
                f"  Threshold .......... {r['threshold']}",
                f"  Reason ............. {r['reason']}",
                f"  Block factor ....... {r['block_factor']}",
                f"  Exp PnL% ........... "
                f"{r['expected_pnl_pct'] if r['expected_pnl_pct'] is not None else '—'}"
                f"  (sim={r['similar_count']})",
            ]
        )
    if data.get("rejects_truncated"):
        lines.append(f"\n… {data['rejects_truncated']} more rejects omitted")

    lines.extend(
        [
            "",
            "Notes (diagnostic only):",
            "  Bull/Bear/Neutral = S57 regimes collapsed (STRONG/WEAK_* → Bull/Bear, RANGE → Neutral).",
            "  Block factors attribute stress on rejected rows (not a change to S55/S57 gates).",
            "  REGIME_BLOCK is rare; most rejects may be NEGATIVE_EXPECTANCY with Neutral/RANGE tape.",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "build_regime_diagnostic",
    "format_regime_diagnostic_report",
]
