"""Historical analysis, hypotheses, walk-forward A/B/C — read-only."""

from __future__ import annotations

import random
import statistics
from typing import Any

from bot.research.bidirectional_live_audit import _max_consecutive_losses, _max_drawdown, _pf
from bot.research.mtf.config import (
    BOOTSTRAP_MIN_N,
    BOOTSTRAP_SAMPLES,
    WF_TRAIN_RATIO,
)
from bot.research.mtf.models import ContextMetrics, TradeContext


def _bootstrap_pp(pnls: list[float]) -> float | None:
    if len(pnls) < BOOTSTRAP_MIN_N:
        return None
    rng = random.Random(42)
    n = len(pnls)
    hits = sum(1 for _ in range(BOOTSTRAP_SAMPLES) if _pf([pnls[rng.randrange(n)] for _ in range(n)]) > 1)
    return round(hits / BOOTSTRAP_SAMPLES, 3)


def metrics_for_group(label: str, contexts: list[TradeContext], total_profit: float) -> ContextMetrics:
    if not contexts:
        return ContextMetrics(label=label)

    pnls = [c.pnl_pct or 0 for c in contexts]
    yes_pnls = [c.pnl_pct or 0 for c in contexts if c.side == "YES"]
    no_pnls = [c.pnl_pct or 0 for c in contexts if c.side == "NO"]
    wins = sum(1 for p in pnls if p > 0)
    gp = sum(p for p in pnls if p > 0)

    return ContextMetrics(
        label=label,
        n=len(contexts),
        yes_n=len(yes_pnls),
        no_n=len(no_pnls),
        pf=round(_pf(pnls), 3),
        yes_pf=round(_pf(yes_pnls), 3) if yes_pnls else 0,
        no_pf=round(_pf(no_pnls), 3) if no_pnls else 0,
        wr=round(wins / len(pnls) * 100, 1),
        avg_pnl=round(statistics.mean(pnls), 2),
        max_dd=round(_max_drawdown(pnls), 2),
        max_cl=_max_consecutive_losses(pnls),
        profit_contribution_pct=round(gp / total_profit * 100, 1) if total_profit > 0 else 0,
        bootstrap_pp_gt1=_bootstrap_pp(pnls),
    )


def context_performance_matrix(
    contexts: list[TradeContext],
    label_key: str,
) -> dict[str, ContextMetrics]:
    total_profit = sum(c.pnl_pct or 0 for c in contexts if (c.pnl_pct or 0) > 0)
    groups: dict[str, list[TradeContext]] = {}
    for c in contexts:
        key = getattr(c, label_key, "UNKNOWN")
        groups.setdefault(key, []).append(c)
    return {
        label: metrics_for_group(label, group, total_profit)
        for label, group in sorted(groups.items())
    }


def test_hypotheses(contexts: list[TradeContext]) -> dict[str, Any]:
    """Test hypotheses A–E on corrected V1.1 trades."""

    def pf_filter(pred) -> float:
        subset = [c for c in contexts if pred(c)]
        if not subset:
            return 0.0
        return _pf([c.pnl_pct or 0 for c in subset])

    def n_filter(pred) -> int:
        return sum(1 for c in contexts if pred(c))

    # A: 5m NO + 15m NO + 1h NO alignment
    all_no_aligned = [
        c for c in contexts
        if c.side == "NO"
        and c.pm_15m.prob_direction == "DOWN"
        and c.pm_1h.prob_direction == "DOWN"
    ]
    no_baseline = [c for c in contexts if c.side == "NO"]

    # B: 5m YES against falling 15m+1h
    countertrend_yes = [
        c for c in contexts
        if c.side == "YES"
        and c.btc.return_15m is not None and c.btc.return_15m < 0
        and c.btc.return_1h is not None and c.btc.return_1h < 0
    ]

    # C: reversal YES — daily DOWN, 1h DOWN, 15m turning UP
    reversal_yes = [
        c for c in contexts
        if c.side == "YES"
        and c.pm_daily.prob_direction == "DOWN"
        and c.pm_1h.prob_direction == "DOWN"
        and c.pm_15m.prob_direction == "UP"
    ]

    # D: PM HTF vs BTC spot trend
    pm_available = [c for c in contexts if c.pm_15m.available or c.pm_1h.available]
    spot_aligned = [
        c for c in pm_available
        if (c.side == "YES" and c.btc.return_1h and c.btc.return_1h > 0)
        or (c.side == "NO" and c.btc.return_1h and c.btc.return_1h < 0)
    ]
    pm_aligned = [
        c for c in pm_available
        if (c.side == "YES" and c.pm_15m.prob_direction == "UP")
        or (c.side == "NO" and c.pm_15m.prob_direction == "DOWN")
    ]

    # E: divergence BTC down + PM 15m YES rising
    divergence = [
        c for c in contexts
        if c.btc.return_1h is not None and c.btc.return_1h < 0
        and c.pm_15m.prob_direction == "UP"
    ]

    return {
        "A_no_triple_align": {
            "n": len(all_no_aligned),
            "pf": round(_pf([c.pnl_pct or 0 for c in all_no_aligned]), 3) if all_no_aligned else 0,
            "baseline_no_pf": round(_pf([c.pnl_pct or 0 for c in no_baseline]), 3) if no_baseline else 0,
            "pass": (
                len(all_no_aligned) >= 10
                and _pf([c.pnl_pct or 0 for c in all_no_aligned])
                > _pf([c.pnl_pct or 0 for c in no_baseline])
            ) if all_no_aligned and no_baseline else None,
        },
        "B_countertrend_yes_loses": {
            "n": len(countertrend_yes),
            "pf": round(_pf([c.pnl_pct or 0 for c in countertrend_yes]), 3) if countertrend_yes else 0,
            "pass": (
                len(countertrend_yes) >= 10
                and _pf([c.pnl_pct or 0 for c in countertrend_yes]) < 1.0
            ) if countertrend_yes else None,
        },
        "C_reversal_yes": {
            "n": len(reversal_yes),
            "pf": round(_pf([c.pnl_pct or 0 for c in reversal_yes]), 3) if reversal_yes else 0,
            "pass": (
                len(reversal_yes) >= 5
                and _pf([c.pnl_pct or 0 for c in reversal_yes]) > 1.0
            ) if reversal_yes else None,
        },
        "D_pm_vs_spot": {
            "pm_available_n": len(pm_available),
            "spot_aligned_pf": round(_pf([c.pnl_pct or 0 for c in spot_aligned]), 3) if spot_aligned else 0,
            "pm_aligned_pf": round(_pf([c.pnl_pct or 0 for c in pm_aligned]), 3) if pm_aligned else 0,
            "pm_beats_spot": (
                _pf([c.pnl_pct or 0 for c in pm_aligned]) > _pf([c.pnl_pct or 0 for c in spot_aligned])
            ) if pm_aligned and spot_aligned else None,
        },
        "E_divergence": {
            "n": len(divergence),
            "pf": round(_pf([c.pnl_pct or 0 for c in divergence]), 3) if divergence else 0,
        },
    }


def _model_filter_a(ctx: TradeContext) -> bool:
    return True


def _model_filter_b(ctx: TradeContext) -> bool:
    """BTC spot HTF aligns with trade side."""
    htf = ctx.htf_label
    if ctx.side == "YES":
        return htf in ("HTF_UP", "HTF_STRONG_UP", "HTF_MIXED")
    return htf in ("HTF_DOWN", "HTF_STRONG_DOWN", "HTF_MIXED")


def _model_filter_c(ctx: TradeContext) -> bool:
    """BTC + Polymarket HTF alignment."""
    if not _model_filter_b(ctx):
        return False
    if ctx.side == "YES":
        return ctx.pm_15m.prob_direction in ("UP", "NEUTRAL", None) or not ctx.pm_15m.available
    return ctx.pm_15m.prob_direction in ("DOWN", "NEUTRAL", None) or not ctx.pm_15m.available


def walk_forward_abc(contexts: list[TradeContext]) -> dict[str, Any]:
    if len(contexts) < 30:
        return {"error": "insufficient trades", "n": len(contexts)}

    split = max(1, int(len(contexts) * WF_TRAIN_RATIO))
    train = contexts[:split]
    test = contexts[split:]

    models = {
        "MODEL_A_5m_only": _model_filter_a,
        "MODEL_B_btc_spot": _model_filter_b,
        "MODEL_C_btc_pm": _model_filter_c,
    }

    results: dict[str, Any] = {"train_n": len(train), "test_n": len(test), "models": {}}

    for name, filt in models.items():
        train_sub = [c for c in train if filt(c)]
        test_sub = [c for c in test if filt(c)]
        train_pf = _pf([c.pnl_pct or 0 for c in train_sub]) if train_sub else 0
        test_pf = _pf([c.pnl_pct or 0 for c in test_sub]) if test_sub else 0
        baseline_test_pf = _pf([c.pnl_pct or 0 for c in test])
        results["models"][name] = {
            "train_n": len(train_sub),
            "test_n": len(test_sub),
            "train_pf": round(train_pf, 3),
            "test_pf": round(test_pf, 3),
            "test_lift_vs_a": round(test_pf - baseline_test_pf, 3),
            "retention_pct": round(len(test_sub) / len(test) * 100, 1) if test else 0,
        }

    b_lift = results["models"]["MODEL_B_btc_spot"]["test_lift_vs_a"]
    c_lift = results["models"]["MODEL_C_btc_pm"]["test_lift_vs_a"]
    results["model_c_beats_b_oos"] = c_lift > b_lift and c_lift > 0
    results["model_b_beats_a_oos"] = b_lift > 0

    return results


def predictive_value_by_timeframe(contexts: list[TradeContext]) -> dict[str, Any]:
    """Measure HTF predictive power per timeframe."""
    out: dict[str, Any] = {}
    pm_coverage = {
        "15m": sum(1 for c in contexts if c.pm_15m.available) / len(contexts) if contexts else 0,
        "1h": sum(1 for c in contexts if c.pm_1h.available) / len(contexts) if contexts else 0,
        "daily": sum(1 for c in contexts if c.pm_daily.available) / len(contexts) if contexts else 0,
    }

    for tf, attr in [("15m", "pm_15m"), ("1h", "pm_1h"), ("daily", "pm_daily")]:
        aligned = []
        misaligned = []
        for c in contexts:
            pm = getattr(c, attr)
            if not pm.available:
                continue
            if (c.side == "YES" and pm.prob_direction == "UP") or (c.side == "NO" and pm.prob_direction == "DOWN"):
                aligned.append(c)
            else:
                misaligned.append(c)
        out[tf] = {
            "coverage": round(pm_coverage[tf], 3),
            "aligned_n": len(aligned),
            "misaligned_n": len(misaligned),
            "aligned_pf": round(_pf([c.pnl_pct or 0 for c in aligned]), 3) if aligned else 0,
            "misaligned_pf": round(_pf([c.pnl_pct or 0 for c in misaligned]), 3) if misaligned else 0,
            "conditional_lift": round(
                (_pf([c.pnl_pct or 0 for c in aligned]) - _pf([c.pnl_pct or 0 for c in misaligned]))
                if aligned and misaligned else 0,
                3,
            ),
        }

    # BTC spot redundancy: correlation proxy via aligned PF lift
    btc_aligned = [c for c in contexts if _model_filter_b(c)]
    btc_misaligned = [c for c in contexts if not _model_filter_b(c)]
    out["btc_spot"] = {
        "aligned_pf": round(_pf([c.pnl_pct or 0 for c in btc_aligned]), 3) if btc_aligned else 0,
        "misaligned_pf": round(_pf([c.pnl_pct or 0 for c in btc_misaligned]), 3) if btc_misaligned else 0,
        "conditional_lift": round(
            _pf([c.pnl_pct or 0 for c in btc_aligned]) - _pf([c.pnl_pct or 0 for c in btc_misaligned]),
            3,
        ) if btc_aligned and btc_misaligned else 0,
    }

    return out
