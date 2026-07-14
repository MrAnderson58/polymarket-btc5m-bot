"""Phase G.5.0.1 — deterministic quant research from G4/validation data."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.signal_intelligence.auto_validation_g4 import (
    FACTOR_NAMES,
    _pearson,
    _profit_factor,
)
from bot.research.market_events.signal_intelligence.research_prompt_g50 import (
    validate_research_json_g50,
)

_BTC_SYMBOLS = frozenset({"BTC", "BITCOIN"})


def _is_win_record(r: dict[str, Any]) -> bool:
    co = r.get("candidate_outcome") or {}
    if co.get("is_win") or co.get("would_hit_tp"):
        return True
    return float(r.get("final_pnl") or 0) > 0


def _factor_value(record: dict[str, Any], factor: str) -> float | None:
    mapping = {
        "Funding": "funding", "OI": "oi", "Trend": "trend", "Volume": "volume",
        "ATR": "atr", "Dominance": "dominance", "FearGreed": "fear_greed",
        "Liquidity": "liquidity_probability", "Market Score": "market_score",
        "Confidence": "confidence",
    }
    key = mapping.get(factor, factor.lower())
    val = record.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _load_g4_context(conn: Any) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "factor_stats": [],
        "false_rejects": [],
        "false_accepts": [],
        "optimizer": [],
        "recommendations": [],
    }
    try:
        ctx["factor_stats"] = [
            dict(r) for r in conn.execute(
                """
                SELECT factor, win_rate, profit_factor, sample_size, importance, predictor_type
                FROM market_validation_factor_stats_g4
                ORDER BY report_date DESC, rank_order ASC LIMIT 15
                """,
            ).fetchall()
        ]
        ctx["false_rejects"] = [
            dict(r) for r in conn.execute(
                """
                SELECT symbol, pnl_pct, blocking_filter
                FROM market_validation_analysis_g4
                WHERE analysis_type = 'false_reject'
                ORDER BY pnl_pct DESC LIMIT 10
                """,
            ).fetchall()
        ]
        ctx["false_accepts"] = [
            dict(r) for r in conn.execute(
                """
                SELECT symbol, pnl_pct, misleading_factors_json
                FROM market_validation_analysis_g4
                WHERE analysis_type = 'false_accept'
                ORDER BY pnl_pct ASC LIMIT 10
                """,
            ).fetchall()
        ]
        ctx["optimizer"] = [
            dict(r) for r in conn.execute(
                """
                SELECT symbol, min_confidence, min_market_score, min_liquidity, min_rr,
                       win_rate, profit_factor, sample_size
                FROM market_validation_optimizer_g4
                ORDER BY report_date DESC, profit_factor DESC LIMIT 10
                """,
            ).fetchall()
        ]
        ctx["recommendations"] = [
            dict(r) for r in conn.execute(
                """
                SELECT symbol, recommendation, rationale
                FROM market_validation_recommendations_g4
                ORDER BY report_date DESC LIMIT 8
                """,
            ).fetchall()
        ]
    except Exception:
        pass
    return ctx


def build_deterministic_research_g501(
    conn: Any,
    *,
    dataset: dict[str, Any],
) -> dict[str, Any]:
    records = dataset.get("records") or []
    stats = dataset.get("aggregate_stats") or {}
    g4_ctx = _load_g4_context(conn)
    sample = int(stats.get("sample_size") or len(records))

    factor_importance: list[tuple[str, float, int]] = []
    for factor in FACTOR_NAMES:
        pairs: list[tuple[float, float]] = []
        for r in records:
            val = _factor_value(r, factor)
            if val is None:
                continue
            pairs.append((val, 1.0 if _is_win_record(r) else 0.0))
        if len(pairs) < 4:
            continue
        corr = _pearson([p[0] for p in pairs], [p[1] for p in pairs]) or 0.0
        factor_importance.append((factor, corr, len(pairs)))

    factor_importance.sort(key=lambda x: -abs(x[1]))
    g4_by_factor = {str(r.get("factor")): r for r in g4_ctx.get("factor_stats") or []}

    top_factors = []
    for factor, corr, n in factor_importance[:10]:
        g4 = g4_by_factor.get(factor, {})
        top_factors.append({
            "label": factor,
            "evidence": f"corr={corr:+.3f} wr={g4.get('win_rate', '—')} pf={g4.get('profit_factor', '—')}",
            "sample_size": n,
        })
    if not top_factors and g4_ctx.get("factor_stats"):
        for r in g4_ctx["factor_stats"][:8]:
            top_factors.append({
                "label": r.get("factor"),
                "evidence": f"g4 wr={r.get('win_rate')} pf={r.get('profit_factor')} imp={r.get('importance')}",
                "sample_size": r.get("sample_size"),
            })

    weak_factors = []
    for factor, corr, n in sorted(factor_importance, key=lambda x: abs(x[1]))[:10]:
        if abs(corr) < 0.05:
            g4 = g4_by_factor.get(factor, {})
            weak_factors.append({
                "label": factor,
                "evidence": f"corr={corr:+.3f} type={g4.get('predictor_type', 'weak')}",
                "sample_size": n,
            })

    overestimated = [
        {
            "label": r.get("factor"),
            "evidence": f"high avg contribution but negative importance {r.get('importance')}",
            "sample_size": r.get("sample_size"),
        }
        for r in g4_ctx.get("factor_stats") or []
        if str(r.get("predictor_type")) == "negative"
    ][:8]

    wins = [r for r in records if _is_win_record(r)]
    losses = [r for r in records if not _is_win_record(r) and r.get("final_pnl") is not None]

    def _combo_from_group(group: list[dict], *, win: bool) -> dict | None:
        if len(group) < 3:
            return None
        med = {}
        for f in ("funding", "oi", "volume"):
            vals = [float(r[f]) for r in group if r.get(f) is not None]
            if vals:
                med[f] = sum(vals) / len(vals)
        wr = sum(1 for r in group if _is_win_record(r)) / len(group)
        return {
            "factors": [f"{k}{'↑' if med[k] >= 50 else '↓'}" for k in sorted(med)],
            "label": " + ".join(f"{k}{'↑' if med[k] >= 50 else '↓'}" for k in sorted(med)),
            "win_rate": round(wr, 3),
            "sample_size": len(group),
        }

    profitable = []
    if wins:
        c = _combo_from_group(wins, win=True)
        if c:
            profitable.append(c)

    loss_combos = []
    if losses:
        c = _combo_from_group(losses, win=False)
        if c:
            loss_combos.append(c)

    btc_recs = [r for r in records if str(r.get("symbol")).upper() in _BTC_SYMBOLS]
    alt_recs = [r for r in records if str(r.get("symbol")).upper() not in _BTC_SYMBOLS]
    btc_wr = sum(1 for r in btc_recs if _is_win_record(r)) / len(btc_recs) if btc_recs else 0
    alt_wr = sum(1 for r in alt_recs if _is_win_record(r)) / len(alt_recs) if alt_recs else 0
    btc_vs_alt = [{
        "label": "BTC vs alts win rate",
        "evidence": f"BTC {btc_wr:.0%} (n={len(btc_recs)}) vs alts {alt_wr:.0%} (n={len(alt_recs)})",
        "sample_size": len(records),
    }] if btc_recs and alt_recs else []

    symbol_specific = [
        {
            "label": str(o.get("symbol")),
            "evidence": (
                f"conf≥{o.get('min_confidence')} score≥{o.get('min_market_score')} "
                f"liq≥{o.get('min_liquidity')} rr≥{o.get('min_rr')} "
                f"pf={o.get('profit_factor')}"
            ),
            "sample_size": o.get("sample_size"),
        }
        for o in g4_ctx.get("optimizer") or []
    ]

    false_reject_patterns = [
        {
            "label": f"{r.get('symbol')} blocked by {r.get('blocking_filter')}",
            "evidence": f"missed +{float(r.get('pnl_pct') or 0):.1f}%",
            "sample_size": 1,
        }
        for r in g4_ctx.get("false_rejects") or []
    ][:5]

    new_patterns = false_reject_patterns[:3]
    bad_patterns = [
        {
            "label": f"{r.get('symbol')} false accept",
            "evidence": str(r.get("misleading_factors_json") or "")[:120],
            "sample_size": 1,
        }
        for r in g4_ctx.get("false_accepts") or []
    ][:5]

    tomorrow = [
        {
            "label": r.get("recommendation") or "Review threshold optimizer output",
            "evidence": r.get("rationale") or "",
            "sample_size": sample,
        }
        for r in g4_ctx.get("recommendations") or []
    ][:5]
    if not tomorrow and g4_ctx.get("false_rejects"):
        fr = g4_ctx["false_rejects"][0]
        tomorrow.append({
            "label": f"Lower {fr.get('blocking_filter')} threshold for {fr.get('symbol')}",
            "evidence": f"false reject +{float(fr.get('pnl_pct') or 0):.1f}%",
            "sample_size": sample,
        })

    improvements = []
    if len(records) < 50:
        improvements.append({"label": "Need more replay-complete outcomes", "sample_size": sample})
    if not g4_ctx.get("factor_stats"):
        improvements.append({"label": "Run validation-cycle before research", "sample_size": sample})
    if not g4_ctx.get("optimizer"):
        improvements.append({"label": "Run optimizer-report for per-symbol thresholds", "sample_size": sample})

    missing_info = []
    if sample < 100:
        missing_info.append("More completed replay outcomes (target 100+)")
    if not g4_ctx.get("false_rejects"):
        missing_info.append("False reject analysis samples")
    missing_info.extend([
        "5m candles history per symbol",
        "Funding rate history",
        "OI change history",
        "Liquidation cluster history",
    ])

    pf = _profit_factor([{"pnl_pct": r.get("final_pnl")} for r in records if r.get("final_pnl") is not None])
    confidence = min(0.75, 0.25 + sample / 200 + (0.1 if g4_ctx.get("factor_stats") else 0))

    report = validate_research_json_g50({
        "top_factors": top_factors,
        "weak_factors": weak_factors or [{"label": f, "evidence": "low correlation", "sample_size": sample} for f, _, _ in factor_importance[-3:]],
        "overestimated_factors": overestimated,
        "profitable_combinations": profitable,
        "loss_combinations": loss_combos,
        "btc_vs_alt": btc_vs_alt,
        "symbol_specific": symbol_specific,
        "new_patterns": new_patterns,
        "bad_patterns": bad_patterns,
        "tomorrow_hypotheses": tomorrow,
        "research_improvements": improvements or [{"label": "Enable Claude for deeper synthesis", "sample_size": sample}],
        "missing_info": missing_info,
        "confidence": round(confidence, 2),
        "sample_size": sample,
        "research_score": compute_research_score_g501("deterministic_fallback", confidence, sample, retries=0),
        "source": "deterministic_g501",
        "validation_win_rate": stats.get("win_rate"),
        "validation_profit_factor": pf,
    })
    return report


def compute_research_score_g501(
    status: str,
    confidence: float,
    sample_size: int,
    *,
    retries: int = 0,
    raw_only: bool = False,
) -> float:
    if raw_only:
        return 2.1
    if status.startswith("deterministic") or status == "quota_blocked":
        base = 2.5 + min(3.0, sample_size / 80.0) + confidence * 1.5
        return round(min(6.8, max(2.1, base)), 1)
    penalty = retries * 0.6
    score = 4.5 + confidence * 4.0 + min(1.5, sample_size / 150.0) - penalty
    return round(min(9.8, max(3.5, score)), 1)
