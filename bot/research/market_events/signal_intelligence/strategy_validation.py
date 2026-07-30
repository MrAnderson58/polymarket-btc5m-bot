"""Adaptive Strategy Validation V2 — offline replay of optimizer recommendations.

Every candidate parameter set is replayed on the *same* historical S42 trades
as the current set (A/B). Cross-validation (70/30) and walk-forward reject
overfit. Auto-apply only when sample, confidence, PF/expectancy lifts, and
drawdown limits all clear.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence import strategy_optimizer as opt
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)

logger = logging.getLogger(__name__)

VALIDATION_REPORT_PATH = BASE_DIR / "VALIDATION_REPORT.md"
VALIDATION_STATE_PATH = BASE_DIR / "data" / "strategy_validation_state.json"

# --- configurable gates ---
VAL_TRAIN_FRAC = 0.70
VAL_MIN_SAMPLE = 30
VAL_MIN_CONFIDENCE = 0.65
VAL_MIN_PF_INCREASE = 0.05
VAL_MIN_EXPECTANCY_INCREASE = 0.0
VAL_MAX_DRAWDOWN_INCREASE = 5.0  # percentage points of cumulative pnl%
VAL_BOOTSTRAP_N = 200
VAL_P_VALUE_MAX = 0.10
VAL_WF_TRAIN = 40
VAL_WF_TEST = 15
VAL_AUTO_APPLY = False
VAL_OVERFIT_EXP_GAP = 0.5  # train expectancy lift − val lift; reject if larger
VAL_RANDOM_SEED = 42


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def refresh_validation_config_from_env() -> None:
    global VAL_TRAIN_FRAC, VAL_MIN_SAMPLE, VAL_MIN_CONFIDENCE
    global VAL_MIN_PF_INCREASE, VAL_MIN_EXPECTANCY_INCREASE, VAL_MAX_DRAWDOWN_INCREASE
    global VAL_BOOTSTRAP_N, VAL_P_VALUE_MAX, VAL_WF_TRAIN, VAL_WF_TEST
    global VAL_AUTO_APPLY, VAL_OVERFIT_EXP_GAP, VAL_RANDOM_SEED
    try:
        VAL_TRAIN_FRAC = max(0.5, min(0.9, float(os.environ.get("VAL_TRAIN_FRAC", VAL_TRAIN_FRAC))))
    except (TypeError, ValueError):
        pass
    for name, attr, cast, lo, hi in (
        ("VAL_MIN_SAMPLE", "VAL_MIN_SAMPLE", int, 5, 10_000),
        ("VAL_MIN_CONFIDENCE", "VAL_MIN_CONFIDENCE", float, 0.0, 1.0),
        ("VAL_MIN_PF_INCREASE", "VAL_MIN_PF_INCREASE", float, -1.0, 10.0),
        ("VAL_MIN_EXPECTANCY_INCREASE", "VAL_MIN_EXPECTANCY_INCREASE", float, -10.0, 10.0),
        ("VAL_MAX_DRAWDOWN_INCREASE", "VAL_MAX_DRAWDOWN_INCREASE", float, 0.0, 1000.0),
        ("VAL_BOOTSTRAP_N", "VAL_BOOTSTRAP_N", int, 20, 5000),
        ("VAL_P_VALUE_MAX", "VAL_P_VALUE_MAX", float, 0.0, 1.0),
        ("VAL_WF_TRAIN", "VAL_WF_TRAIN", int, 10, 100_000),
        ("VAL_WF_TEST", "VAL_WF_TEST", int, 5, 50_000),
        ("VAL_OVERFIT_EXP_GAP", "VAL_OVERFIT_EXP_GAP", float, 0.0, 100.0),
        ("VAL_RANDOM_SEED", "VAL_RANDOM_SEED", int, 0, 2**31 - 1),
    ):
        if name in os.environ:
            try:
                globals()[attr] = max(lo, min(hi, cast(os.environ[name])))
            except (TypeError, ValueError):
                pass
    if "VAL_AUTO_APPLY" in os.environ:
        VAL_AUTO_APPLY = _env_bool("VAL_AUTO_APPLY", False)


refresh_validation_config_from_env()


@dataclass
class FilterParams:
    """Replayable entry filters (identical trade universe, different keep set)."""

    disabled_symbols: set[str] = field(default_factory=set)
    confidence_threshold: float | None = None
    label: str = "params"

    def normalized(self) -> "FilterParams":
        return FilterParams(
            disabled_symbols={str(s).upper() for s in self.disabled_symbols if s},
            confidence_threshold=(
                None
                if self.confidence_threshold is None
                else float(self.confidence_threshold)
            ),
            label=self.label,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "disabled_symbols": sorted(self.disabled_symbols),
            "confidence_threshold": self.confidence_threshold,
        }


def _trade_sort_key(r: dict[str, Any]) -> tuple:
    return (
        int(r.get("closed_at") or r.get("updated_at") or r.get("created_at") or 0),
        int(r.get("id") or 0),
    )


def sort_trades(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=_trade_sort_key)


def _pnl(r: dict[str, Any]) -> float:
    v = _safe_float(r.get("pnl_usd"))
    if v is not None:
        return float(v)
    return float(_safe_float(r.get("pnl_pct")) or 0.0)


def _conf(r: dict[str, Any]) -> float | None:
    if r.get("_confidence") is not None:
        return float(r["_confidence"])
    return opt._confidence(r)


def apply_filter(rows: list[dict[str, Any]], params: FilterParams) -> list[dict[str, Any]]:
    """Keep trades that would still be taken under ``params``."""
    p = params.normalized()
    kept: list[dict[str, Any]] = []
    for r in rows:
        sym = str(r.get("symbol") or "").upper()
        if sym in p.disabled_symbols:
            continue
        if p.confidence_threshold is not None:
            c = _conf(r)
            if c is not None and float(c) < float(p.confidence_threshold):
                continue
            # Missing confidence: fail-open (same as runtime gate)
        kept.append(r)
    return kept


def replay_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """PF, expectancy, winrate, max drawdown, Sharpe-like on a trade list."""
    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "pf": None,
            "pf_inf": False,
            "expectancy": None,
            "winrate": None,
            "avg_pnl": None,
            "max_drawdown": None,
            "sharpe": None,
            "total_pnl": 0.0,
        }
    pnls = [_pnl(r) for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 1e-12:
        pf: float | None = gross_win / gross_loss
        pf_inf = False
    elif gross_win > 0:
        pf = None
        pf_inf = True
    else:
        pf = 0.0
        pf_inf = False

    mean = sum(pnls) / n
    var = sum((x - mean) ** 2 for x in pnls) / n
    sharpe = None
    if var > 1e-18:
        sharpe = round(mean / math.sqrt(var), 4)
    elif abs(mean) < 1e-12:
        sharpe = 0.0

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return {
        "n": n,
        "pf": None if pf_inf else (round(pf, 4) if pf is not None else None),
        "pf_inf": pf_inf,
        "expectancy": round(mean, 4),
        "winrate": round(100.0 * len(wins) / n, 2),
        "avg_pnl": round(mean, 4),
        "max_drawdown": round(max_dd, 4),
        "sharpe": sharpe,
        "total_pnl": round(sum(pnls), 4),
    }


def _effective_pf(m: dict[str, Any]) -> float:
    if m.get("pf_inf"):
        return 10.0
    if m.get("pf") is None:
        return 0.0
    return float(m["pf"])


def metrics_delta(current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Candidate − current on key metrics (same dataset semantics)."""
    exp_c = current.get("expectancy")
    exp_k = candidate.get("expectancy")
    dd_c = current.get("max_drawdown")
    dd_k = candidate.get("max_drawdown")
    return {
        "pf_delta": round(_effective_pf(candidate) - _effective_pf(current), 4),
        "expectancy_delta": (
            None
            if exp_c is None or exp_k is None
            else round(float(exp_k) - float(exp_c), 4)
        ),
        "winrate_delta": (
            None
            if current.get("winrate") is None or candidate.get("winrate") is None
            else round(float(candidate["winrate"]) - float(current["winrate"]), 4)
        ),
        "drawdown_delta": (
            None
            if dd_c is None or dd_k is None
            else round(float(dd_k) - float(dd_c), 4)
        ),
        "sharpe_delta": (
            None
            if current.get("sharpe") is None or candidate.get("sharpe") is None
            else round(float(candidate["sharpe"]) - float(current["sharpe"]), 4)
        ),
        "n_current": current.get("n"),
        "n_candidate": candidate.get("n"),
    }


def ab_replay(
    rows: list[dict[str, Any]],
    current: FilterParams,
    candidate: FilterParams,
) -> dict[str, Any]:
    """A/B on identical historical sample — never different datasets."""
    ordered = sort_trades(rows)
    cur_kept = apply_filter(ordered, current)
    cand_kept = apply_filter(ordered, candidate)
    cur_m = replay_metrics(cur_kept)
    cand_m = replay_metrics(cand_kept)
    return {
        "universe_n": len(ordered),
        "current": {"params": current.to_dict(), "metrics": cur_m},
        "candidate": {"params": candidate.to_dict(), "metrics": cand_m},
        "delta": metrics_delta(cur_m, cand_m),
    }


def bootstrap_expectancy_pvalue(
    rows: list[dict[str, Any]],
    current: FilterParams,
    candidate: FilterParams,
    *,
    n_boot: int | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Bootstrap P(candidate_exp <= current_exp) on resampled identical universe."""
    refresh_validation_config_from_env()
    n_boot = VAL_BOOTSTRAP_N if n_boot is None else int(n_boot)
    seed = VAL_RANDOM_SEED if seed is None else int(seed)
    ordered = sort_trades(rows)
    if len(ordered) < 5:
        return {
            "p_value": 1.0,
            "n_boot": 0,
            "observed_expectancy_delta": None,
            "confidence_score": 0.0,
        }

    rng = random.Random(seed)
    observed = ab_replay(ordered, current, candidate)
    obs_delta = observed["delta"].get("expectancy_delta")
    if obs_delta is None:
        return {
            "p_value": 1.0,
            "n_boot": 0,
            "observed_expectancy_delta": None,
            "confidence_score": 0.0,
        }

    worse = 0
    deltas: list[float] = []
    n = len(ordered)
    for _ in range(n_boot):
        sample = [ordered[rng.randrange(n)] for _ in range(n)]
        cur_m = replay_metrics(apply_filter(sample, current))
        cand_m = replay_metrics(apply_filter(sample, candidate))
        if cur_m.get("expectancy") is None or cand_m.get("expectancy") is None:
            worse += 1
            continue
        d = float(cand_m["expectancy"]) - float(cur_m["expectancy"])
        deltas.append(d)
        if d <= 0:
            worse += 1
    p_value = worse / float(n_boot)
    # Confidence: 1 - p, tempered by sample size
    size_factor = min(1.0, len(ordered) / float(max(VAL_MIN_SAMPLE, 1)))
    confidence = round((1.0 - p_value) * (0.5 + 0.5 * size_factor), 4)
    return {
        "p_value": round(p_value, 4),
        "n_boot": n_boot,
        "observed_expectancy_delta": obs_delta,
        "bootstrap_delta_mean": round(sum(deltas) / len(deltas), 4) if deltas else None,
        "confidence_score": confidence,
        "sample_size": len(ordered),
    }


def train_validation_split(
    rows: list[dict[str, Any]],
    *,
    train_frac: float | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Chronological 70/30 split — never optimize on validation."""
    refresh_validation_config_from_env()
    train_frac = VAL_TRAIN_FRAC if train_frac is None else float(train_frac)
    ordered = sort_trades(rows)
    if not ordered:
        return [], []
    cut = max(1, int(len(ordered) * train_frac))
    if cut >= len(ordered):
        cut = len(ordered) - 1
    if cut < 1:
        return ordered, []
    return ordered[:cut], ordered[cut:]


def cross_validate_recommendation(
    rows: list[dict[str, Any]],
    current: FilterParams,
    candidate: FilterParams,
    *,
    train_frac: float | None = None,
) -> dict[str, Any]:
    """Propose evidence on train; accept/reject using validation only for gates."""
    train, valid = train_validation_split(rows, train_frac=train_frac)
    train_ab = ab_replay(train, current, candidate)
    val_ab = ab_replay(valid, current, candidate) if valid else None
    boot = bootstrap_expectancy_pvalue(valid or train, current, candidate)

    train_exp_d = train_ab["delta"].get("expectancy_delta")
    val_exp_d = None if val_ab is None else val_ab["delta"].get("expectancy_delta")
    overfit = False
    overfit_reason = None
    if train_exp_d is not None and val_exp_d is not None:
        gap = float(train_exp_d) - float(val_exp_d)
        if gap > float(VAL_OVERFIT_EXP_GAP) and float(val_exp_d) < float(VAL_MIN_EXPECTANCY_INCREASE):
            overfit = True
            overfit_reason = (
                f"train expectancy lift {train_exp_d} vs val {val_exp_d} "
                f"(gap {gap:.4f} > {VAL_OVERFIT_EXP_GAP})"
            )
    elif train_exp_d is not None and float(train_exp_d) > 0 and (val_ab is None or (val_ab["candidate"]["metrics"].get("n") or 0) < 5):
        overfit = True
        overfit_reason = "validation sample too small to confirm train lift"

    return {
        "train_n": len(train),
        "validation_n": len(valid),
        "train_ab": train_ab,
        "validation_ab": val_ab,
        "bootstrap": boot,
        "overfit": overfit,
        "overfit_reason": overfit_reason,
    }


def walk_forward(
    rows: list[dict[str, Any]],
    current: FilterParams,
    candidate: FilterParams,
    *,
    train_size: int | None = None,
    test_size: int | None = None,
) -> dict[str, Any]:
    """Rolling train/validate windows; aggregate OOS deltas."""
    refresh_validation_config_from_env()
    train_size = VAL_WF_TRAIN if train_size is None else int(train_size)
    test_size = VAL_WF_TEST if test_size is None else int(test_size)
    ordered = sort_trades(rows)
    folds: list[dict[str, Any]] = []
    i = 0
    while i + train_size + test_size <= len(ordered):
        train = ordered[i : i + train_size]
        test = ordered[i + train_size : i + train_size + test_size]
        # Never tune on test — only score fixed candidate vs current
        ab = ab_replay(test, current, candidate)
        folds.append({
            "train_start": i,
            "train_n": len(train),
            "test_n": len(test),
            "delta": ab["delta"],
            "test_metrics_current": ab["current"]["metrics"],
            "test_metrics_candidate": ab["candidate"]["metrics"],
        })
        i += test_size

    if not folds:
        return {
            "folds": [],
            "n_folds": 0,
            "agg": {
                "pf_delta_mean": None,
                "expectancy_delta_mean": None,
                "drawdown_delta_mean": None,
                "positive_exp_fold_rate": None,
            },
        }

    pf_ds = [f["delta"]["pf_delta"] for f in folds if f["delta"].get("pf_delta") is not None]
    exp_ds = [
        f["delta"]["expectancy_delta"]
        for f in folds
        if f["delta"].get("expectancy_delta") is not None
    ]
    dd_ds = [
        f["delta"]["drawdown_delta"]
        for f in folds
        if f["delta"].get("drawdown_delta") is not None
    ]
    pos = sum(1 for e in exp_ds if e is not None and e > 0)
    return {
        "folds": folds,
        "n_folds": len(folds),
        "agg": {
            "pf_delta_mean": round(sum(pf_ds) / len(pf_ds), 4) if pf_ds else None,
            "expectancy_delta_mean": round(sum(exp_ds) / len(exp_ds), 4) if exp_ds else None,
            "drawdown_delta_mean": round(sum(dd_ds) / len(dd_ds), 4) if dd_ds else None,
            "positive_exp_fold_rate": round(pos / len(exp_ds), 4) if exp_ds else None,
        },
    }


def decision_gates(
    *,
    sample_n: int,
    confidence: float,
    pf_delta: float | None,
    expectancy_delta: float | None,
    drawdown_delta: float | None,
    p_value: float | None,
    overfit: bool,
) -> tuple[bool, list[str]]:
    """Return (accepted, rejection_reasons)."""
    refresh_validation_config_from_env()
    reasons: list[str] = []
    if sample_n < VAL_MIN_SAMPLE:
        reasons.append(f"sample_n {sample_n} < VAL_MIN_SAMPLE {VAL_MIN_SAMPLE}")
    if confidence < VAL_MIN_CONFIDENCE:
        reasons.append(f"confidence {confidence} < VAL_MIN_CONFIDENCE {VAL_MIN_CONFIDENCE}")
    if pf_delta is None or float(pf_delta) < float(VAL_MIN_PF_INCREASE):
        reasons.append(
            f"pf_delta {pf_delta} < VAL_MIN_PF_INCREASE {VAL_MIN_PF_INCREASE}",
        )
    if expectancy_delta is None or float(expectancy_delta) < float(VAL_MIN_EXPECTANCY_INCREASE):
        reasons.append(
            f"expectancy_delta {expectancy_delta} < VAL_MIN_EXPECTANCY_INCREASE "
            f"{VAL_MIN_EXPECTANCY_INCREASE}",
        )
    if drawdown_delta is not None and float(drawdown_delta) > float(VAL_MAX_DRAWDOWN_INCREASE):
        reasons.append(
            f"drawdown_delta {drawdown_delta} > VAL_MAX_DRAWDOWN_INCREASE "
            f"{VAL_MAX_DRAWDOWN_INCREASE}",
        )
    if p_value is not None and float(p_value) > float(VAL_P_VALUE_MAX):
        reasons.append(f"p_value {p_value} > VAL_P_VALUE_MAX {VAL_P_VALUE_MAX}")
    if overfit:
        reasons.append("overfit_rejected")
    return (len(reasons) == 0, reasons)


def should_auto_apply_validated(accepted: bool) -> bool:
    refresh_validation_config_from_env()
    if not VAL_AUTO_APPLY:
        return False
    # Also respect optimizer master switch
    opt.refresh_optimizer_config_from_env()
    if not opt.OPT_AUTO_APPLY:
        return False
    return bool(accepted)


def params_from_state_dicts(
    current_raw: dict[str, Any] | None,
    recommended_raw: dict[str, Any] | None,
) -> tuple[FilterParams, FilterParams]:
    cur = current_raw or {}
    rec = recommended_raw or {}
    current = FilterParams(
        disabled_symbols=set(cur.get("disabled_symbols") or []),
        confidence_threshold=cur.get("confidence_threshold"),
        label="current",
    )
    candidate = FilterParams(
        disabled_symbols=set(rec.get("disabled_symbols") or []),
        confidence_threshold=rec.get("confidence_threshold"),
        label="candidate",
    )
    return current.normalized(), candidate.normalized()


def validate_recommendation_pair(
    rows: list[dict[str, Any]],
    current: FilterParams,
    candidate: FilterParams,
    *,
    name: str = "filter_bundle",
) -> dict[str, Any]:
    """Full validation pipeline for one current vs candidate pair."""
    refresh_validation_config_from_env()
    full_ab = ab_replay(rows, current, candidate)
    cv = cross_validate_recommendation(rows, current, candidate)
    wf = walk_forward(rows, current, candidate)
    boot = cv.get("bootstrap") or {}

    # Prefer validation-fold deltas for acceptance; fall back to full-sample.
    val_ab = cv.get("validation_ab")
    if val_ab and (val_ab["candidate"]["metrics"].get("n") or 0) >= 5:
        delta = val_ab["delta"]
        sample_n = int(val_ab["universe_n"])
    else:
        delta = full_ab["delta"]
        sample_n = int(full_ab["universe_n"])

    # Walk-forward aggregate can veto
    wf_exp = (wf.get("agg") or {}).get("expectancy_delta_mean")
    if wf.get("n_folds", 0) >= 2 and wf_exp is not None:
        # Blend: use min of val and wf mean as conservative expectancy delta
        if delta.get("expectancy_delta") is not None:
            delta = dict(delta)
            delta["expectancy_delta_conservative"] = round(
                min(float(delta["expectancy_delta"]), float(wf_exp)), 4,
            )

    confidence = float(boot.get("confidence_score") or 0.0)
    p_value = boot.get("p_value")
    exp_d = delta.get("expectancy_delta_conservative", delta.get("expectancy_delta"))
    pf_d = delta.get("pf_delta")
    # If walk-forward pf mean exists and is worse, use it
    wf_pf = (wf.get("agg") or {}).get("pf_delta_mean")
    if wf.get("n_folds", 0) >= 2 and wf_pf is not None and pf_d is not None:
        pf_d = min(float(pf_d), float(wf_pf))

    accepted, reasons = decision_gates(
        sample_n=sample_n,
        confidence=confidence,
        pf_delta=pf_d,
        expectancy_delta=exp_d,
        drawdown_delta=delta.get("drawdown_delta"),
        p_value=p_value,
        overfit=bool(cv.get("overfit")),
    )
    if cv.get("overfit_reason") and cv.get("overfit"):
        reasons = list(reasons)
        if cv["overfit_reason"] not in reasons:
            reasons.append(cv["overfit_reason"])

    return {
        "name": name,
        "accepted": accepted,
        "rejection_reasons": reasons,
        "sample_size": sample_n,
        "confidence": confidence,
        "p_value": p_value,
        "estimated_improvement": {
            "pf_delta": pf_d,
            "expectancy_delta": exp_d,
            "drawdown_delta": delta.get("drawdown_delta"),
            "sharpe_delta": delta.get("sharpe_delta"),
            "winrate_delta": delta.get("winrate_delta"),
        },
        "full_ab": full_ab,
        "cross_validation": {
            "train_n": cv.get("train_n"),
            "validation_n": cv.get("validation_n"),
            "overfit": cv.get("overfit"),
            "overfit_reason": cv.get("overfit_reason"),
            "train_delta": (cv.get("train_ab") or {}).get("delta"),
            "validation_delta": None if val_ab is None else val_ab.get("delta"),
            "bootstrap": boot,
        },
        "walk_forward": {
            "n_folds": wf.get("n_folds"),
            "agg": wf.get("agg"),
            "folds": wf.get("folds"),
        },
        "current_params": current.to_dict(),
        "candidate_params": candidate.to_dict(),
    }


def run_optimizer_validation(
    conn: Any,
    *,
    write_report: bool = True,
    report_path: Path | None = None,
    rows: list[dict[str, Any]] | None = None,
    current: FilterParams | None = None,
    candidate: FilterParams | None = None,
    run_optimizer_if_needed: bool = True,
) -> dict[str, Any]:
    """Validate optimizer recommendations against historical S42 trades."""
    refresh_validation_config_from_env()

    if rows is None:
        if run_optimizer_if_needed:
            # Ensure recommendations exist
            state = opt.load_optimizer_state()
            if not (state.get("recommended") or {}):
                opt.run_strategy_optimizer(conn, write_report=False)
        rows = opt.load_optimizer_trades(conn)

    state = opt.load_optimizer_state()
    if current is None or candidate is None:
        applied = state.get("applied") or {}
        rec = state.get("recommended") or {}
        current = FilterParams(
            disabled_symbols=set(applied.get("disabled_symbols") or []),
            confidence_threshold=applied.get("confidence_threshold"),
            label="current",
        ).normalized()
        candidate = FilterParams(
            disabled_symbols=set(rec.get("disabled_symbols") or []),
            confidence_threshold=rec.get("confidence_threshold"),
            label="candidate",
        ).normalized()

    assert current is not None and candidate is not None

    # If candidate equals current, nothing to validate
    same = (
        current.disabled_symbols == candidate.disabled_symbols
        and current.confidence_threshold == candidate.confidence_threshold
    )

    results: list[dict[str, Any]] = []
    if same:
        results.append({
            "name": "filter_bundle",
            "accepted": False,
            "rejection_reasons": ["candidate_identical_to_current"],
            "sample_size": len(rows),
            "confidence": 0.0,
            "p_value": None,
            "estimated_improvement": {},
            "current_params": current.to_dict(),
            "candidate_params": candidate.to_dict(),
        })
    else:
        results.append(
            validate_recommendation_pair(rows, current, candidate, name="filter_bundle"),
        )

    # Also validate confidence-only and symbol-only slices when they differ
    if current.confidence_threshold != candidate.confidence_threshold:
        results.append(
            validate_recommendation_pair(
                rows,
                FilterParams(
                    disabled_symbols=current.disabled_symbols,
                    confidence_threshold=current.confidence_threshold,
                    label="current_conf",
                ),
                FilterParams(
                    disabled_symbols=current.disabled_symbols,
                    confidence_threshold=candidate.confidence_threshold,
                    label="candidate_conf",
                ),
                name="confidence_threshold",
            ),
        )
    if current.disabled_symbols != candidate.disabled_symbols:
        results.append(
            validate_recommendation_pair(
                rows,
                FilterParams(
                    disabled_symbols=current.disabled_symbols,
                    confidence_threshold=current.confidence_threshold,
                    label="current_sym",
                ),
                FilterParams(
                    disabled_symbols=candidate.disabled_symbols,
                    confidence_threshold=current.confidence_threshold,
                    label="candidate_sym",
                ),
                name="disabled_symbols",
            ),
        )

    accepted = [r for r in results if r.get("accepted")]
    rejected = [r for r in results if not r.get("accepted")]

    # Auto-apply only the full filter_bundle if accepted
    bundle = next((r for r in results if r.get("name") == "filter_bundle"), None)
    applied = False
    apply_note = "RECOMMEND_ONLY"
    if bundle and should_auto_apply_validated(bool(bundle.get("accepted"))):
        new_applied = dict(state.get("applied") or {})
        new_applied["disabled_symbols"] = list(candidate.disabled_symbols)
        new_applied["confidence_threshold"] = candidate.confidence_threshold
        if (state.get("recommended") or {}).get("exploration_rate") is not None:
            new_applied["exploration_rate"] = state["recommended"]["exploration_rate"]
            os.environ["S57_EXPLORATION_RATE"] = str(new_applied["exploration_rate"])
            try:
                from bot.research.market_events.signal_intelligence import market_regime_s57 as s57
                s57.refresh_s57_config_from_env()
            except Exception as exc:
                logger.warning("validation: S57 refresh failed: %s", exc)
        new_applied["applied_at"] = int(time.time())
        new_applied["validated"] = True
        state["applied"] = new_applied
        state["last_validation"] = {
            "accepted": True,
            "ts": new_applied["applied_at"],
            "name": "filter_bundle",
        }
        opt.save_optimizer_state(state)
        applied = True
        apply_note = "AUTO_APPLIED_AFTER_VALIDATION"

    result = {
        "ok": True,
        "generated_at": int(time.time()),
        "universe_n": len(rows),
        "accepted": accepted,
        "rejected": rejected,
        "results": results,
        "auto_apply": applied,
        "apply_note": apply_note,
        "safety": {
            "VAL_MIN_SAMPLE": VAL_MIN_SAMPLE,
            "VAL_MIN_CONFIDENCE": VAL_MIN_CONFIDENCE,
            "VAL_MIN_PF_INCREASE": VAL_MIN_PF_INCREASE,
            "VAL_MIN_EXPECTANCY_INCREASE": VAL_MIN_EXPECTANCY_INCREASE,
            "VAL_MAX_DRAWDOWN_INCREASE": VAL_MAX_DRAWDOWN_INCREASE,
            "VAL_P_VALUE_MAX": VAL_P_VALUE_MAX,
            "VAL_AUTO_APPLY": VAL_AUTO_APPLY,
            "OPT_AUTO_APPLY": opt.OPT_AUTO_APPLY,
        },
    }
    md = format_validation_report(result)
    result["report_markdown"] = md
    if write_report:
        path = Path(report_path) if report_path else Path(
            os.environ.get("VAL_REPORT_PATH", str(VALIDATION_REPORT_PATH)),
        )
        path.write_text(md, encoding="utf-8")
        result["report_path"] = str(path.resolve())
        # Persist last validation blob
        vpath = Path(os.environ.get("VAL_STATE_PATH", str(VALIDATION_STATE_PATH)))
        vpath.parent.mkdir(parents=True, exist_ok=True)
        slim = {
            "generated_at": result["generated_at"],
            "universe_n": result["universe_n"],
            "apply_note": apply_note,
            "accepted_names": [a.get("name") for a in accepted],
            "rejected_names": [r.get("name") for r in rejected],
            "results_summary": [
                {
                    "name": r.get("name"),
                    "accepted": r.get("accepted"),
                    "rejection_reasons": r.get("rejection_reasons"),
                    "estimated_improvement": r.get("estimated_improvement"),
                    "confidence": r.get("confidence"),
                    "p_value": r.get("p_value"),
                    "sample_size": r.get("sample_size"),
                }
                for r in results
            ],
        }
        vpath.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    return result


def format_validation_report(result: dict[str, Any]) -> str:
    lines = [
        "# VALIDATION_REPORT — Adaptive Strategy Validation V2",
        "",
        f"**Generated:** {result.get('generated_at')}",
        f"**Universe (identical A/B sample):** {result.get('universe_n')}",
        f"**Apply:** {result.get('apply_note')}",
        "",
        "## Safety thresholds",
        "",
        "```json",
        json.dumps(result.get("safety") or {}, indent=2),
        "```",
        "",
        "## Accepted recommendations",
        "",
    ]
    accepted = result.get("accepted") or []
    if not accepted:
        lines.append("_none_")
        lines.append("")
    for a in accepted:
        lines.extend([
            f"### {a.get('name')} — ACCEPTED",
            "",
            f"- sample_size: {a.get('sample_size')}",
            f"- confidence: {a.get('confidence')}",
            f"- p_value: {a.get('p_value')}",
            f"- estimated_improvement: `{json.dumps(a.get('estimated_improvement') or {})}`",
            f"- candidate: `{json.dumps(a.get('candidate_params') or {})}`",
            "",
        ])

    lines.extend(["## Rejected recommendations", ""])
    rejected = result.get("rejected") or []
    if not rejected:
        lines.append("_none_")
        lines.append("")
    for r in rejected:
        lines.extend([
            f"### {r.get('name')} — REJECTED",
            "",
            f"- reasons: {r.get('rejection_reasons')}",
            f"- sample_size: {r.get('sample_size')}",
            f"- confidence: {r.get('confidence')}",
            f"- p_value: {r.get('p_value')}",
            f"- estimated_improvement: `{json.dumps(r.get('estimated_improvement') or {})}`",
            "",
        ])
        cv = r.get("cross_validation") or {}
        if cv:
            lines.extend([
                "#### Cross-validation",
                "",
                f"- train_n={cv.get('train_n')} validation_n={cv.get('validation_n')}",
                f"- overfit={cv.get('overfit')} ({cv.get('overfit_reason')})",
                f"- train_delta=`{json.dumps(cv.get('train_delta') or {})}`",
                f"- validation_delta=`{json.dumps(cv.get('validation_delta') or {})}`",
                "",
            ])
        wf = r.get("walk_forward") or {}
        if wf:
            lines.extend([
                "#### Walk-forward",
                "",
                f"- n_folds={wf.get('n_folds')} agg=`{json.dumps(wf.get('agg') or {})}`",
                "",
            ])

    lines.extend([
        "## Expected improvement (accepted only)",
        "",
    ])
    if accepted:
        for a in accepted:
            lines.append(f"- **{a.get('name')}**: `{json.dumps(a.get('estimated_improvement') or {})}`")
    else:
        lines.append("_no accepted recommendations — no production change_")
    lines.append("")
    return "\n".join(lines)


def read_validation_report(path: Path | None = None) -> str:
    p = Path(path) if path else Path(
        os.environ.get("VAL_REPORT_PATH", str(VALIDATION_REPORT_PATH)),
    )
    if p.exists():
        return p.read_text(encoding="utf-8")
    return (
        "No VALIDATION_REPORT.md yet. Run:\n"
        "  python -m bot.research.market_events validate-optimizer\n"
    )


__all__ = [
    "FilterParams",
    "VALIDATION_REPORT_PATH",
    "ab_replay",
    "apply_filter",
    "bootstrap_expectancy_pvalue",
    "cross_validate_recommendation",
    "decision_gates",
    "format_validation_report",
    "metrics_delta",
    "params_from_state_dicts",
    "read_validation_report",
    "refresh_validation_config_from_env",
    "replay_metrics",
    "run_optimizer_validation",
    "should_auto_apply_validated",
    "sort_trades",
    "train_validation_split",
    "validate_recommendation_pair",
    "walk_forward",
]
