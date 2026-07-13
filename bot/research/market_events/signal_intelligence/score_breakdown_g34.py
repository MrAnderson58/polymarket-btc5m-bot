"""Phase G.3.4 — score breakdown, calibration diagnostics, and weight recommendations."""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.config import G3_DAILY_REPORT_HOUR_LOCAL
from bot.research.market_events.signal_intelligence.dominance_context_f7 import classify_dominance
from bot.research.market_events.signal_intelligence.market_score_f7 import (
    WEIGHTS as F7_WEIGHTS,
    compute_market_score,
)
from bot.research.market_events.signal_intelligence.reversal_learning_g1 import lookup_historical_reversal_rate
from bot.research.market_events.signal_intelligence.whale_activity_f7 import analyze_whale_activity

logger = logging.getLogger(__name__)

_BREAKDOWN_TABLE = "market_score_breakdown_g34"
_CONFLICTS_TABLE = "market_score_conflicts_g34"
_DAILY_TABLE = "market_calibration_daily_g34"
_CANDIDATE_TABLE = "market_candidate_g31"

CONF_BASE = 2.0
CONF_FACTOR_CAPS: dict[str, float] = {
    "Trend": 2.0,
    "Liquidity": 1.8,
    "Funding": 1.0,
    "OI": 1.4,
    "Volume": 1.0,
    "ATR": 0.7,
    "History": 1.2,
    "Claude": 1.5,
}
CONF_FACTOR_WEIGHTS: dict[str, float] = {
    k: round(v / sum(CONF_FACTOR_CAPS.values()), 4) for k, v in CONF_FACTOR_CAPS.items()
}

MS_EXTRA_WEIGHTS: dict[str, float] = {
    "whales": 0.11,
    "fear_greed": 0.06,
}

MS_LABELS: dict[str, str] = {
    "btc_trend": "BTC",
    "eth_trend": "ETH",
    "total3": "TOTAL3",
    "funding": "Funding",
    "open_interest": "OI",
    "btc_dominance": "Dominance",
    "volume_regime": "Volume",
    "liquidations": "Liquidations",
    "volatility_atr": "ATR",
    "whales": "Whales",
    "fear_greed": "FearGreed",
}

DEFAULT_CALIBRATION_WEIGHTS: dict[str, float] = {
    **{k: v for k, v in CONF_FACTOR_WEIGHTS.items()},
    **{MS_LABELS.get(k, k): v for k, v in F7_WEIGHTS.items()},
    "Whales": MS_EXTRA_WEIGHTS["whales"],
    "FearGreed": MS_EXTRA_WEIGHTS["fear_greed"],
}


@dataclass(frozen=True)
class BreakdownRowG34:
    score_type: str
    factor: str
    raw_value: float | None
    normalized_value: float | None
    weight: float
    contribution: float


@dataclass(frozen=True)
class ScoreBreakdownG34:
    confidence_rows: tuple[BreakdownRowG34, ...]
    market_score_rows: tuple[BreakdownRowG34, ...]
    conflicts: tuple[dict[str, str], ...]


def _norm_0_1(value: float | None, *, cap: float = 100.0) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(1.0, float(value) / cap))


def _fear_greed_component(fg: float | None) -> float:
    """0–100 component; low FG (fear) helps reversal shorts, high FG hurts."""
    if fg is None:
        return 50.0
    if fg <= 25:
        return 72.0
    if fg <= 40:
        return 62.0
    if fg >= 75:
        return 28.0
    if fg >= 60:
        return 38.0
    return 50.0


def _fear_greed_signed_contribution(fg: float | None) -> float:
    comp = _fear_greed_component(fg)
    return round((comp - 50.0) / 50.0 * MS_EXTRA_WEIGHTS["fear_greed"] * 100.0, 1)


def _scale_contributions(
    rows: list[BreakdownRowG34],
    *,
    target_total: float,
    base: float = 0.0,
) -> tuple[BreakdownRowG34, ...]:
    raw_sum = base + sum(r.contribution for r in rows)
    if raw_sum <= 0 or target_total is None:
        return tuple(rows)
    scale = target_total / raw_sum if base == 0 else (target_total - base) / max(1e-9, sum(r.contribution for r in rows))
    scaled: list[BreakdownRowG34] = []
    for r in rows:
        scaled.append(BreakdownRowG34(
            score_type=r.score_type,
            factor=r.factor,
            raw_value=r.raw_value,
            normalized_value=r.normalized_value,
            weight=r.weight,
            contribution=round(r.contribution * scale, 2),
        ))
    return tuple(scaled)


def compute_confidence_breakdown(
    *,
    confidence: float | None,
    trend_score: float | None,
    liquidity_score: float | None,
    funding_score: float | None,
    oi_score: float | None,
    volume_score: float | None,
    atr_score: float | None,
    history_rate: float | None,
    claude_conf: float | None,
    coverage_pct: float = 100.0,
) -> tuple[BreakdownRowG34, ...]:
    target = float(confidence or 0.0)
    if target <= 0:
        return ()

    raw_factors: list[tuple[str, float | None, float]] = [
        ("Trend", trend_score, CONF_FACTOR_CAPS["Trend"]),
        ("Liquidity", liquidity_score, CONF_FACTOR_CAPS["Liquidity"]),
        ("Funding", funding_score, CONF_FACTOR_CAPS["Funding"]),
        ("OI", oi_score, CONF_FACTOR_CAPS["OI"]),
        ("Volume", volume_score, CONF_FACTOR_CAPS["Volume"]),
        ("ATR", atr_score, CONF_FACTOR_CAPS["ATR"]),
        ("History", (history_rate or 0) * 100 if history_rate is not None else None, CONF_FACTOR_CAPS["History"]),
    ]
    rows: list[BreakdownRowG34] = []
    for factor, raw, cap in raw_factors:
        norm = _norm_0_1(raw)
        if coverage_pct < 100.0 and factor == "Trend":
            norm *= 0.7 + 0.3 * coverage_pct / 100.0
        rows.append(BreakdownRowG34(
            score_type="confidence",
            factor=factor,
            raw_value=raw,
            normalized_value=round(norm, 4),
            weight=CONF_FACTOR_WEIGHTS[factor],
            contribution=round(norm * cap, 2),
        ))

    claude_raw = claude_conf
    if claude_raw is not None:
        claude_norm = _norm_0_1(claude_raw, cap=10.0)
        rows.append(BreakdownRowG34(
            score_type="confidence",
            factor="Claude",
            raw_value=claude_raw,
            normalized_value=round(claude_norm, 4),
            weight=CONF_FACTOR_WEIGHTS["Claude"],
            contribution=round(claude_norm * CONF_FACTOR_CAPS["Claude"], 2),
        ))

    return _scale_contributions(rows, target_total=target, base=CONF_BASE)


def compute_market_score_breakdown(
    conn: Any,
    *,
    symbol: str,
    market_score: float | None,
    volume_score: float | None,
    funding_score: float | None,
    oi_score: float | None,
    fear_greed: float | None,
    trend: Any | None = None,
) -> tuple[BreakdownRowG34, ...]:
    target = float(market_score or 0.0)
    if target <= 0:
        return ()

    dom = classify_dominance(conn, shock_symbol=symbol)
    liq_intel = None
    try:
        row = conn.execute(
            """
            SELECT liquidation_intel_json FROM market_events_market_intelligence_f7
            ORDER BY created_at DESC LIMIT 1
            """,
        ).fetchone()
        if row and row["liquidation_intel_json"]:
            liq_intel = json.loads(row["liquidation_intel_json"])
    except Exception:
        liq_intel = None

    trend_dict = {"funding": None, "open_interest_delta": None, "atr_multiple": None}
    if trend and hasattr(trend, "details"):
        trend_dict["funding"] = trend.details.get("funding")

    ms = compute_market_score(
        conn,
        report=None,
        trend=trend_dict,
        dominance_regime=dom.regime,
        liq_intel=liq_intel,
    )

    rows: list[BreakdownRowG34] = []
    for key, comp_score in ms.components.items():
        label = MS_LABELS.get(key, key)
        weight = F7_WEIGHTS.get(key, 0.0)
        contrib = round(comp_score * weight, 1)
        rows.append(BreakdownRowG34(
            score_type="market_score",
            factor=label,
            raw_value=comp_score,
            normalized_value=round(comp_score / 100.0, 4),
            weight=weight,
            contribution=contrib,
        ))

    whale = analyze_whale_activity(conn, symbol=symbol, event_ts=int(time.time()))
    whale_norm = _norm_0_1(whale.score, cap=100.0)
    whale_contrib = round(whale_norm * MS_EXTRA_WEIGHTS["whales"] * 100.0, 1)
    rows.append(BreakdownRowG34(
        score_type="market_score",
        factor="Whales",
        raw_value=whale.score,
        normalized_value=round(whale_norm, 4),
        weight=MS_EXTRA_WEIGHTS["whales"],
        contribution=whale_contrib,
    ))

    fg_comp = _fear_greed_component(fear_greed)
    fg_contrib = _fear_greed_signed_contribution(fear_greed)
    rows.append(BreakdownRowG34(
        score_type="market_score",
        factor="FearGreed",
        raw_value=fear_greed,
        normalized_value=round(fg_comp / 100.0, 4),
        weight=MS_EXTRA_WEIGHTS["fear_greed"],
        contribution=fg_contrib,
    ))

    if volume_score is not None and "Volume" not in {r.factor for r in rows}:
        rows.append(BreakdownRowG34(
            score_type="market_score",
            factor="Volume",
            raw_value=volume_score,
            normalized_value=round(_norm_0_1(volume_score), 4),
            weight=F7_WEIGHTS.get("volume_regime", 0.10),
            contribution=round(volume_score * F7_WEIGHTS.get("volume_regime", 0.10), 1),
        ))

    raw_total = sum(r.contribution for r in rows)
    if raw_total > 0 and abs(raw_total - target) > 0.5:
        return _scale_contributions(rows, target_total=target)
    return tuple(rows)


def detect_score_conflicts(
    *,
    confidence: float | None,
    market_score: float | None,
    conf_rows: tuple[BreakdownRowG34, ...],
    ms_rows: tuple[BreakdownRowG34, ...],
    liquidity_state: str | None = None,
    funding_score: float | None = None,
    oi_score: float | None = None,
) -> tuple[dict[str, str], ...]:
    conflicts: list[dict[str, str]] = []
    conf = float(confidence or 0)
    ms = float(market_score or 0)

    if conf > 8.0 and ms < 60.0:
        conflicts.append({
            "conflict_type": "high_conf_low_market",
            "description": f"Confidence {conf:.1f} > 8 but Market Score {ms:.0f} < 60",
            "severity": "high",
        })
    if ms > 80.0 and conf < 6.0:
        conflicts.append({
            "conflict_type": "high_market_low_conf",
            "description": f"Market Score {ms:.0f} > 80 but Confidence {conf:.1f} < 6",
            "severity": "high",
        })

    claude = next((r for r in conf_rows if r.factor == "Claude"), None)
    liq = next((r for r in conf_rows if r.factor == "Liquidity"), None)
    if claude and liq and claude.contribution >= 1.0 and liq.contribution <= 0.8:
        bearish_states = ("Distribution", "Euphoria", "Overheated")
        if liquidity_state in bearish_states:
            conflicts.append({
                "conflict_type": "claude_vs_liquidity",
                "description": "Claude bullish but Liquidity bearish",
                "severity": "medium",
            })

    if funding_score is not None and oi_score is not None:
        if funding_score >= 60 and oi_score <= 45:
            conflicts.append({
                "conflict_type": "funding_vs_oi",
                "description": "Funding bullish but OI bearish",
                "severity": "medium",
            })
        if funding_score <= 45 and oi_score >= 65:
            conflicts.append({
                "conflict_type": "oi_vs_funding",
                "description": "OI bullish but Funding bearish",
                "severity": "medium",
            })

    return tuple(conflicts)


def build_score_breakdown_g34(
    conn: Any,
    *,
    candidate: Any,
    claude_conf: float | None = None,
    liquidity_state: str | None = None,
) -> ScoreBreakdownG34:
    history_rate = None
    if candidate.trend:
        key = f"{candidate.symbol}|{candidate.trend.pattern_type}|{candidate.trend.window_minutes}m"
        history_rate = lookup_historical_reversal_rate(conn, key)

    conf_rows = compute_confidence_breakdown(
        confidence=candidate.confidence,
        trend_score=candidate.trend_score,
        liquidity_score=candidate.liquidity_score,
        funding_score=candidate.funding_score,
        oi_score=candidate.oi_score,
        volume_score=candidate.volume_score,
        atr_score=candidate.atr_score,
        history_rate=history_rate,
        claude_conf=claude_conf,
        coverage_pct=float(candidate.trend_coverage_pct or 100.0),
    )
    ms_rows = compute_market_score_breakdown(
        conn,
        symbol=candidate.symbol,
        market_score=candidate.market_score,
        volume_score=candidate.volume_score,
        funding_score=candidate.funding_score,
        oi_score=candidate.oi_score,
        fear_greed=candidate.fear_greed,
        trend=candidate.trend,
    )
    conflicts = detect_score_conflicts(
        confidence=candidate.confidence,
        market_score=candidate.market_score,
        conf_rows=conf_rows,
        ms_rows=ms_rows,
        liquidity_state=liquidity_state,
        funding_score=candidate.funding_score,
        oi_score=candidate.oi_score,
    )
    return ScoreBreakdownG34(confidence_rows=conf_rows, market_score_rows=ms_rows, conflicts=conflicts)


def persist_score_breakdown_g34(
    conn: Any,
    *,
    candidate_id: int,
    breakdown: ScoreBreakdownG34,
) -> int:
    now = int(time.time())
    n = 0
    for row in (*breakdown.confidence_rows, *breakdown.market_score_rows):
        insert_returning_id(
            conn,
            f"""
            INSERT INTO {_BREAKDOWN_TABLE} (
              candidate_id, score_type, factor, raw_value, normalized_value, weight, contribution, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id, row.score_type, row.factor, row.raw_value,
                row.normalized_value, row.weight, row.contribution, now,
            ),
        )
        n += 1

    for conflict in breakdown.conflicts:
        insert_returning_id(
            conn,
            f"""
            INSERT INTO {_CONFLICTS_TABLE} (
              candidate_id, conflict_type, description, severity, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                candidate_id, conflict["conflict_type"], conflict["description"],
                conflict["severity"], now,
            ),
        )
    return n


def _latest_cycle_ts(conn: Any) -> int | None:
    row = conn.execute(f"SELECT MAX(candidate_ts) AS ts FROM {_CANDIDATE_TABLE}").fetchone()
    return int(row["ts"]) if row and row["ts"] else None


def format_score_breakdown_report(conn: Any, *, symbol: str | None = None) -> str:
    ts = _latest_cycle_ts(conn)
    if not ts:
        return "No candidate cycles yet."

    if symbol:
        cand = conn.execute(
            f"SELECT * FROM {_CANDIDATE_TABLE} WHERE candidate_ts = ? AND symbol = ?",
            (ts, symbol.upper()),
        ).fetchone()
    else:
        cand = conn.execute(
            f"""
            SELECT * FROM {_CANDIDATE_TABLE} WHERE candidate_ts = ?
            ORDER BY (confidence IS NULL), confidence DESC LIMIT 1
            """,
            (ts,),
        ).fetchone()

    if not cand:
        return "No candidate found."

    rows = conn.execute(
        f"""
        SELECT score_type, factor, contribution FROM {_BREAKDOWN_TABLE}
        WHERE candidate_id = ? ORDER BY score_type, ABS(contribution) DESC
        """,
        (cand["id"],),
    ).fetchall()
    if not rows:
        return f"No breakdown stored for {cand['symbol']} — run g3-run to populate."

    conf_rows = [r for r in rows if r["score_type"] == "confidence"]
    ms_rows = [r for r in rows if r["score_type"] == "market_score"]

    lines = [str(cand["symbol"]), ""]
    lines.extend(["Confidence", f"{float(cand['confidence'] or 0):.1f}", ""])
    for r in conf_rows:
        sign = "+" if float(r["contribution"]) >= 0 else ""
        lines.append(f"{r['factor']}\n{sign}{float(r['contribution']):.2f}")
        lines.append("")
    lines.extend(["-------------------", "", "Market Score", f"{float(cand['market_score'] or 0):.0f}", ""])
    for r in ms_rows:
        sign = "+" if float(r["contribution"]) >= 0 else ""
        lines.append(f"{r['factor']}\n{sign}{float(r['contribution']):.0f}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 4:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return round(num / (den_x * den_y), 3)


def _correlation_window(conn: Any, *, hours: int) -> dict[str, float | None]:
    since = int(time.time()) - hours * 3600
    rows = conn.execute(
        f"""
        SELECT c.confidence, c.market_score, c.liquidity_score, c.rr, c.trend_score,
               o.max_profit_pct, o.would_hit_tp,
               g2.reversal_probability
        FROM {_CANDIDATE_TABLE} c
        LEFT JOIN market_candidate_outcomes_g32 o ON o.candidate_id = c.id
        LEFT JOIN market_events_ai_research_g2 g2 ON g2.event_id = (
            SELECT event_id FROM market_snapshots_g3 s WHERE s.id = c.snapshot_id LIMIT 1
        )
        WHERE c.created_at >= ? AND c.confidence IS NOT NULL
        """,
        (since,),
    ).fetchall()
    if len(rows) < 4:
        return {}

    win = [1.0 if (r["would_hit_tp"] or (r["max_profit_pct"] or 0) > 0) else 0.0 for r in rows]
    fields = {
        "Confidence": [float(r["confidence"] or 0) for r in rows],
        "Market Score": [float(r["market_score"] or 0) for r in rows],
        "Liquidity": [float(r["liquidity_score"] or 0) for r in rows],
        "RR": [float(r["rr"] or 0) for r in rows],
        "Historical Win Rate": win,
        "Claude Probability": [float(r["reversal_probability"] or 0) for r in rows],
        "Trend Score": [float(r["trend_score"] or 0) for r in rows],
    }
    return {k: _pearson(v, win) for k, v in fields.items()}


def format_score_correlation_report(conn: Any) -> str:
    windows = [(1, "1 day"), (7, "7 days"), (30, "30 days")]
    lines = ["G3.4 Score Correlation vs Win Rate", ""]
    for hours, label in windows:
        corr = _correlation_window(conn, hours=hours * 24)
        lines.extend([label, ""])
        if not corr:
            lines.append("  (insufficient data)")
        else:
            for k, v in corr.items():
                vs = f"{v:+.3f}" if v is not None else "—"
                lines.append(f"  {k}: {vs}")
        lines.extend(["", ""])
    return "\n".join(lines).rstrip()


def _factor_win_correlations(conn: Any, *, hours: int = 168) -> dict[str, float]:
    since = int(time.time()) - hours * 3600
    rows = conn.execute(
        f"""
        SELECT b.factor, b.contribution, b.score_type,
               COALESCE(o.would_hit_tp, 0) AS win,
               COALESCE(o.max_profit_pct, 0) AS profit
        FROM {_BREAKDOWN_TABLE} b
        JOIN {_CANDIDATE_TABLE} c ON c.id = b.candidate_id
        LEFT JOIN market_candidate_outcomes_g32 o ON o.candidate_id = c.id
        WHERE c.created_at >= ? AND o.replay_status = 'COMPLETE'
        """,
        (since,),
    ).fetchall()
    by_factor: dict[str, list[tuple[float, float]]] = {}
    for r in rows:
        label = f"{r['score_type']}:{r['factor']}"
        win = 1.0 if (r["win"] or r["profit"] > 0) else 0.0
        by_factor.setdefault(label, []).append((float(r["contribution"]), win))

    out: dict[str, float] = {}
    for factor, pairs in by_factor.items():
        if len(pairs) < 4:
            continue
        corr = _pearson([p[0] for p in pairs], [p[1] for p in pairs])
        if corr is not None:
            out[factor] = corr
    return out


def format_score_recommendations_report(conn: Any) -> str:
    corrs = _factor_win_correlations(conn)
    lines = ["G3.4 Weight Recommendations (advisory only)", ""]
    if not corrs:
        lines.append("Not enough replay data — run candidate-replay first.")
        return "\n".join(lines)

    recs: list[tuple[str, float, float, str]] = []
    for label, corr in sorted(corrs.items(), key=lambda x: -abs(x[1])):
        _, factor = label.split(":", 1)
        current = DEFAULT_CALIBRATION_WEIGHTS.get(factor, 1.0)
        if corr >= 0.12:
            suggested = round(current * 1.15, 2)
            arrow = "↑"
        elif corr <= -0.05:
            suggested = round(current * 0.85, 2)
            arrow = "↓"
        elif corr >= 0.05:
            suggested = round(current * 1.05, 2)
            arrow = "↑"
        else:
            suggested = round(current * 0.95, 2)
            arrow = "↓"
        if abs(suggested - current) < 0.01:
            continue
        recs.append((factor, current, suggested, arrow))

    if not recs:
        lines.append("Current weights look balanced — no changes suggested.")
        return "\n".join(lines)

    for factor, current, suggested, arrow in recs[:12]:
        lines.extend([
            factor,
            f"{current:.1f}",
            arrow,
            f"{suggested:.1f}",
            "",
        ])
    lines.append("Note: recommendations only — weights are NOT auto-applied.")
    return "\n".join(lines)


def _heatmap_bar(score: float | None, *, width: int = 10) -> str:
    s = max(0.0, min(100.0, float(score or 0)))
    filled = int(round(s / 100.0 * width))
    return "█" * filled


def format_market_heatmap(conn: Any, *, limit: int = 8) -> str:
    ts = _latest_cycle_ts(conn)
    if not ts:
        return "No candidate cycles yet."

    symbols = conn.execute(
        f"""
        SELECT symbol, confidence FROM {_CANDIDATE_TABLE}
        WHERE candidate_ts = ? AND confidence IS NOT NULL
        ORDER BY confidence DESC LIMIT ?
        """,
        (ts, limit),
    ).fetchall()

    lines = ["G3.4 Factor Heatmap — latest cycle", ""]
    for sym_row in symbols:
        sym = sym_row["symbol"]
        cand = conn.execute(
            f"SELECT id FROM {_CANDIDATE_TABLE} WHERE candidate_ts = ? AND symbol = ?",
            (ts, sym),
        ).fetchone()
        if not cand:
            continue
        factors = conn.execute(
            f"""
            SELECT factor, raw_value, score_type FROM {_BREAKDOWN_TABLE}
            WHERE candidate_id = ? ORDER BY score_type, factor
            """,
            (cand["id"],),
        ).fetchall()
        if not factors:
            continue
        lines.append(sym)
        for f in factors:
            raw = float(f["raw_value"] or 0)
            if f["factor"] == "FearGreed" and f["raw_value"] is not None:
                raw = _fear_greed_component(float(f["raw_value"]))
            bar = _heatmap_bar(raw)
            lines.append(f"{f['factor']} {bar}")
        lines.extend(["----------------", ""])
    return "\n".join(lines).rstrip()


def score_diagnostics_dashboard(conn: Any, *, hours: int = 24) -> dict[str, Any]:
    since = int(time.time()) - hours * 3600
    conf_dist = conn.execute(
        f"""
        SELECT confidence FROM {_CANDIDATE_TABLE}
        WHERE created_at >= ? AND confidence IS NOT NULL
        """,
        (since,),
    ).fetchall()
    ms_dist = conn.execute(
        f"""
        SELECT market_score FROM {_CANDIDATE_TABLE}
        WHERE created_at >= ? AND market_score IS NOT NULL
        """,
        (since,),
    ).fetchall()

    conflicts = conn.execute(
        f"""
        SELECT conflict_type, description, severity, COUNT(*) AS n
        FROM {_CONFLICTS_TABLE} x
        JOIN {_CANDIDATE_TABLE} c ON c.id = x.candidate_id
        WHERE c.created_at >= ?
        GROUP BY conflict_type ORDER BY n DESC LIMIT 10
        """,
        (since,),
    ).fetchall()

    top_factors = conn.execute(
        f"""
        SELECT factor, score_type, AVG(ABS(contribution)) AS avg_abs
        FROM {_BREAKDOWN_TABLE} b
        JOIN {_CANDIDATE_TABLE} c ON c.id = b.candidate_id
        WHERE c.created_at >= ?
        GROUP BY factor, score_type ORDER BY avg_abs DESC LIMIT 12
        """,
        (since,),
    ).fetchall()

    corr_1d = _correlation_window(conn, hours=24)
    corr_7d = _correlation_window(conn, hours=168)
    corr_30d = _correlation_window(conn, hours=720)

    return {
        "tab": "Score Diagnostics",
        "hours": hours,
        "confidence_distribution": [float(r["confidence"]) for r in conf_dist],
        "market_score_distribution": [float(r["market_score"]) for r in ms_dist],
        "correlation_matrix": {
            "1d": corr_1d,
            "7d": corr_7d,
            "30d": corr_30d,
        },
        "top_conflicts": [dict(r) for r in conflicts],
        "top_factors": [dict(r) for r in top_factors],
    }


def _avg_breakdown(conn: Any, *, start: int, end: int) -> dict[str, float]:
    rows = conn.execute(
        f"""
        SELECT b.factor, AVG(b.contribution) AS avg_c
        FROM {_BREAKDOWN_TABLE} b
        JOIN {_CANDIDATE_TABLE} c ON c.id = b.candidate_id
        WHERE c.created_at BETWEEN ? AND ?
        GROUP BY b.factor
        """,
        (start, end),
    ).fetchall()
    return {str(r["factor"]): float(r["avg_c"] or 0) for r in rows}


def build_calibration_daily_digest_g34(conn: Any) -> tuple[str, dict[str, Any]]:
    now = int(time.time())
    today_start = now - 86400
    yesterday_start = now - 2 * 86400

    avg_conf_today = conn.execute(
        f"SELECT AVG(confidence) AS v FROM {_CANDIDATE_TABLE} WHERE created_at >= ?",
        (today_start,),
    ).fetchone()
    avg_conf_yday = conn.execute(
        f"SELECT AVG(confidence) AS v FROM {_CANDIDATE_TABLE} WHERE created_at BETWEEN ? AND ?",
        (yesterday_start, today_start),
    ).fetchone()
    avg_ms_today = conn.execute(
        f"SELECT AVG(market_score) AS v FROM {_CANDIDATE_TABLE} WHERE created_at >= ?",
        (today_start,),
    ).fetchone()
    avg_ms_yday = conn.execute(
        f"SELECT AVG(market_score) AS v FROM {_CANDIDATE_TABLE} WHERE created_at BETWEEN ? AND ?",
        (yesterday_start, today_start),
    ).fetchone()

    conf_t = float(avg_conf_today["v"] or 0)
    conf_y = float(avg_conf_yday["v"] or 0)
    ms_t = float(avg_ms_today["v"] or 0)
    ms_y = float(avg_ms_yday["v"] or 0)

    bd_today = _avg_breakdown(conn, start=today_start, end=now)
    bd_yday = _avg_breakdown(conn, start=yesterday_start, end=today_start)
    deltas = {f: bd_today.get(f, 0) - bd_yday.get(f, 0) for f in set(bd_today) | set(bd_yday)}
    conf_driver = min(deltas, key=deltas.get) if deltas else "—"
    ms_driver = max(deltas, key=deltas.get) if deltas else "—"

    corrs = _factor_win_correlations(conn)
    useful = max(corrs, key=corrs.get) if corrs else "—"
    useless = min(corrs, key=corrs.get) if corrs else "—"
    if useful != "—":
        useful = useful.split(":", 1)[-1]
    if useless != "—":
        useless = useless.split(":", 1)[-1]

    conf_arrow = "↓" if conf_t < conf_y else "↑"
    ms_arrow = "↓" if ms_t < ms_y else "↑"

    summary = {
        "avg_confidence_today": round(conf_t, 2),
        "avg_confidence_yesterday": round(conf_y, 2),
        "avg_market_score_today": round(ms_t, 1),
        "avg_market_score_yesterday": round(ms_y, 1),
        "confidence_driver": conf_driver,
        "market_score_driver": ms_driver,
        "most_useful_factor": useful,
        "least_useful_factor": useless,
    }

    msg = "\n".join([
        "📊 Score Calibration — daily change",
        "",
        "За сутки",
        "",
        "Средняя Confidence",
        f"{conf_y:.1f}",
        conf_arrow,
        f"{conf_t:.1f}",
        "",
        "Причина",
        f"{conf_driver} ухудшился" if conf_t < conf_y else f"{conf_driver} улучшился",
        "",
        "--------------------",
        "",
        "Market Score",
        f"{ms_y:.0f}",
        ms_arrow,
        f"{ms_t:.0f}",
        "",
        "Причина",
        f"{ms_driver} стал сильнее" if ms_t > ms_y else f"{ms_driver} ослаб",
        "",
        "--------------------",
        "",
        "Самый полезный фактор",
        str(useful),
        "",
        "Самый бесполезный",
        str(useless),
    ])
    return msg, summary


def maybe_send_calibration_daily_g34(conn: Any) -> bool:
    tz_name = __import__("os").getenv("ME_G3_REPORT_TZ", "Europe/Moscow")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now_local = datetime.now(tz)
    if now_local.hour != G3_DAILY_REPORT_HOUR_LOCAL:
        return False

    report_date = now_local.strftime("%Y-%m-%d")
    existing = conn.execute(
        f"SELECT 1 FROM {_DAILY_TABLE} WHERE report_date = ? AND telegram_sent = 1",
        (report_date,),
    ).fetchone()
    if existing:
        return False

    msg, summary = build_calibration_daily_digest_g34(conn)
    from bot.research.market_events.alert_config import alert_shock_enabled
    from bot.research.market_events.market_event_alerts import ALERT_SHOCK, _safe_alert

    sent = False
    if alert_shock_enabled():
        sent = _safe_alert(
            conn,
            event_id=0,
            alert_type=ALERT_SHOCK,
            detail=f"g34-calibration-{report_date}",
            message=msg,
            enabled=True,
        )

    now = int(time.time())
    existing_row = conn.execute(
        f"SELECT report_date FROM {_DAILY_TABLE} WHERE report_date = ?",
        (report_date,),
    ).fetchone()
    if existing_row:
        conn.execute(
            f"""
            UPDATE {_DAILY_TABLE} SET summary_json = ?, telegram_sent = ?, created_at = ?
            WHERE report_date = ?
            """,
            (json.dumps(summary), 1 if sent else 0, now, report_date),
        )
    else:
        insert_returning_id(
            conn,
            f"""
            INSERT INTO {_DAILY_TABLE} (report_date, summary_json, telegram_sent, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (report_date, json.dumps(summary), 1 if sent else 0, now),
        )
    return sent
