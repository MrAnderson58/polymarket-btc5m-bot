"""Walk-forward, rolling, OOS, Monte Carlo, stability checks."""

from __future__ import annotations

import math
import os
from typing import Any

import numpy as np

from bot.research.market_events.signal_intelligence.alpha_discovery_engine_v1.stats import (
    bootstrap_expectancy_ci,
    effective_pf,
    permutation_pvalue,
    pnl_list,
    trade_metrics,
)
from bot.research.market_events.signal_intelligence.alpha_validation_v2.rules import (
    FrozenAlphaRule,
)

STATUS_PASSED = "PASSED"
STATUS_REJECTED = "REJECTED"
STATUS_INSUFFICIENT = "INSUFFICIENT"


def sort_chrono(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda r: (
            int(r.get("closed_at") or r.get("created_at") or 0),
            str(r.get("id") or r.get("symbol") or ""),
        ),
    )


def match_rows(rows: list[dict[str, Any]], rule: FrozenAlphaRule) -> list[dict[str, Any]]:
    return [r for r in rows if rule.pred(r)]


def _has_edge(metrics: dict[str, Any], *, min_n: int) -> bool | None:
    """True if positive edge; False if lost; None if thin."""
    n = int(metrics.get("n") or 0)
    if n < min_n:
        return None
    ev = metrics.get("expectancy")
    pf = effective_pf(metrics)
    if ev is None:
        return None
    return float(ev) > 0.0 and pf >= 1.0


def _window_report(matched: list[dict[str, Any]], *, min_n: int) -> dict[str, Any]:
    m = trade_metrics(pnl_list(matched))
    edge = _has_edge(m, min_n=min_n)
    return {
        "n": m["n"],
        "winrate": m["winrate"],
        "expectancy": m["expectancy"],
        "pf": m["pf"] if not m.get("pf_inf") else "inf",
        "sharpe": m["sharpe"],
        "has_edge": edge,
        "ok": edge is True,
        "thin": edge is None,
    }


def walk_forward_validate(
    rows: list[dict[str, Any]],
    rule: FrozenAlphaRule,
    *,
    min_n: int = 5,
    train_frac: float = 0.60,
    val_frac: float = 0.20,
) -> dict[str, Any]:
    """Chronological train/val/test; reject if any independent fold loses edge."""
    ordered = sort_chrono(rows)
    n = len(ordered)
    if n < max(12, min_n * 3):
        return {
            "passed": False,
            "reason": "corpus_thin",
            "splits": {},
            "independent_failures": [],
        }
    i_train = max(1, int(n * train_frac))
    i_val = max(i_train + 1, int(n * (train_frac + val_frac)))
    i_val = min(i_val, n - 1) if n > i_train + 1 else i_train
    parts = {
        "train": ordered[:i_train],
        "validation": ordered[i_train:i_val],
        "test": ordered[i_val:],
    }
    splits: dict[str, Any] = {}
    independent_failures: list[str] = []
    for name, subset in parts.items():
        matched = match_rows(subset, rule)
        # Independent OOS windows: validation + test (train is discovery-contaminated)
        fold_min = max(3, min_n // 2) if name != "train" else min_n
        rep = _window_report(matched, min_n=fold_min)
        rep["universe_n"] = len(subset)
        splits[name] = rep
        if name in ("validation", "test") and rep.get("has_edge") is False:
            independent_failures.append(name)
    # Require every non-thin independent window to keep edge
    independent = [k for k in ("validation", "test") if not (splits.get(k) or {}).get("thin")]
    if not independent:
        passed = False
        reason = "no_independent_windows"
    elif independent_failures:
        passed = False
        reason = "lost_edge:" + ",".join(independent_failures)
    else:
        # all independent non-thin windows have edge
        passed = all((splits[k].get("has_edge") is True) for k in independent)
        reason = "ok" if passed else "edge_incomplete"
    return {
        "passed": passed,
        "reason": reason,
        "splits": splits,
        "independent_failures": independent_failures,
    }


def rolling_windows_validate(
    rows: list[dict[str, Any]],
    rule: FrozenAlphaRule,
    *,
    min_n: int = 4,
    n_windows: int = 4,
) -> dict[str, Any]:
    """Non-overlapping chronological rolling windows on the full book."""
    ordered = sort_chrono(rows)
    n = len(ordered)
    n_windows = max(2, int(n_windows))
    if n < n_windows * max(3, min_n):
        # shrink window count for tiny books
        n_windows = max(2, n // max(4, min_n))
    if n_windows < 2 or n < 8:
        return {
            "passed": False,
            "reason": "corpus_thin",
            "windows": [],
            "independent_failures": [],
        }
    size = n // n_windows
    windows: list[dict[str, Any]] = []
    failures: list[str] = []
    for i in range(n_windows):
        start = i * size
        end = n if i == n_windows - 1 else (i + 1) * size
        subset = ordered[start:end]
        matched = match_rows(subset, rule)
        rep = _window_report(matched, min_n=min_n)
        rep["window"] = i
        rep["universe_n"] = len(subset)
        windows.append(rep)
        if rep.get("has_edge") is False:
            failures.append(f"w{i}")
    evaluable = [w for w in windows if not w.get("thin")]
    if not evaluable:
        passed = False
        reason = "all_windows_thin"
    elif failures:
        passed = False
        reason = "lost_edge:" + ",".join(failures)
    else:
        passed = all(w.get("has_edge") is True for w in evaluable)
        reason = "ok" if passed else "edge_incomplete"
    return {
        "passed": passed,
        "reason": reason,
        "windows": windows,
        "independent_failures": failures,
        "n_windows": n_windows,
    }


def oos_replay(
    rows: list[dict[str, Any]],
    rule: FrozenAlphaRule,
    *,
    train_frac: float = 0.70,
    min_n: int = 4,
) -> dict[str, Any]:
    """Freeze rule; score IS vs pure OOS tail."""
    ordered = sort_chrono(rows)
    n = len(ordered)
    cut = max(1, int(n * train_frac))
    if cut >= n - 1:
        return {"passed": False, "reason": "corpus_thin", "is": {}, "oos": {}}
    is_rows = ordered[:cut]
    oos_rows = ordered[cut:]
    is_rep = _window_report(match_rows(is_rows, rule), min_n=min_n)
    oos_rep = _window_report(match_rows(oos_rows, rule), min_n=max(3, min_n // 2))
    is_rep["universe_n"] = len(is_rows)
    oos_rep["universe_n"] = len(oos_rows)
    if oos_rep.get("thin"):
        passed = False
        reason = "oos_thin"
    elif oos_rep.get("has_edge") is False:
        passed = False
        reason = "oos_lost_edge"
    elif oos_rep.get("has_edge") is True:
        passed = True
        reason = "ok"
    else:
        passed = False
        reason = "oos_incomplete"
    return {
        "passed": passed,
        "reason": reason,
        "is": is_rep,
        "oos": oos_rep,
    }


def monte_carlo_null(
    matched_pnls: list[float],
    baseline_pnls: list[float],
    *,
    n_sims: int = 200,
    seed: int = 0,
) -> dict[str, Any]:
    """Monte Carlo: how often random subsets beat observed expectancy."""
    if len(matched_pnls) < 3 or len(baseline_pnls) < 5:
        return {"p_value": None, "n_sims": 0, "obs_ev": None, "passed": False, "reason": "thin"}
    n = len(matched_pnls)
    if n >= len(baseline_pnls):
        return {"p_value": None, "n_sims": 0, "obs_ev": None, "passed": False, "reason": "full_book"}
    base = np.asarray(baseline_pnls, dtype=float)
    obs = float(np.mean(matched_pnls))
    rng = np.random.default_rng(seed)
    beat = 0
    for _ in range(n_sims):
        idx = rng.choice(len(base), size=n, replace=False)
        if float(base[idx].mean()) >= obs - 1e-15:
            beat += 1
    p = (beat + 1) / (n_sims + 1)
    # Prefer edge unlikely under null
    passed = p <= 0.10
    return {
        "p_value": round(p, 6),
        "n_sims": n_sims,
        "obs_ev": round(obs, 4),
        "passed": passed,
        "reason": "ok" if passed else "null_not_rejected",
    }


def stability_over_time(
    rows: list[dict[str, Any]],
    rule: FrozenAlphaRule,
    *,
    n_buckets: int = 4,
    min_n: int = 3,
) -> dict[str, Any]:
    """PF/EV/WR stability across chronological buckets."""
    ordered = sort_chrono(rows)
    n = len(ordered)
    n_buckets = max(2, min(n_buckets, max(2, n // max(3, min_n))))
    size = max(1, n // n_buckets)
    buckets: list[dict[str, Any]] = []
    for i in range(n_buckets):
        start = i * size
        end = n if i == n_buckets - 1 else (i + 1) * size
        matched = match_rows(ordered[start:end], rule)
        rep = _window_report(matched, min_n=min_n)
        rep["bucket"] = i
        buckets.append(rep)
    evaluable = [b for b in buckets if not b.get("thin") and b.get("expectancy") is not None]
    failures = [f"b{b['bucket']}" for b in evaluable if b.get("has_edge") is False]
    evs = [float(b["expectancy"]) for b in evaluable]
    pfs = [
        10.0 if b.get("pf") == "inf" else float(b.get("pf") or 0.0)
        for b in evaluable
    ]
    wrs = [float(b["winrate"]) for b in evaluable if b.get("winrate") is not None]
    def _std(xs: list[float]) -> float | None:
        if len(xs) < 2:
            return None
        m = sum(xs) / len(xs)
        return round(math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs)), 4)

    if not evaluable:
        passed = False
        reason = "all_buckets_thin"
    elif failures:
        passed = False
        reason = "lost_edge:" + ",".join(failures)
    else:
        passed = all(b.get("has_edge") is True for b in evaluable)
        reason = "ok" if passed else "unstable"
    return {
        "passed": passed,
        "reason": reason,
        "buckets": buckets,
        "independent_failures": failures,
        "ev_std": _std(evs),
        "pf_std": _std(pfs),
        "wr_std": _std(wrs),
        "n_buckets": n_buckets,
    }


def validate_candidate(
    rows: list[dict[str, Any]],
    rule: FrozenAlphaRule,
    *,
    min_n: int | None = None,
    seed: int = 0,
) -> dict[str, Any]:
    """Full V2 suite; reject if any independent window loses edge."""
    min_n = int(min_n or os.environ.get("ALPHA_VALIDATE_MIN_N", "5"))
    n_mc = int(os.environ.get("ALPHA_VALIDATE_N_MC", "200"))
    n_boot = int(os.environ.get("ALPHA_VALIDATE_N_BOOT", "200"))
    n_perm = int(os.environ.get("ALPHA_VALIDATE_N_PERM", "150"))
    n_roll = int(os.environ.get("ALPHA_VALIDATE_N_WINDOWS", "4"))

    matched = match_rows(rows, rule)
    baseline = pnl_list(rows)
    matched_pnls = pnl_list(matched)
    overall = trade_metrics(matched_pnls)

    if overall["n"] < min_n:
        return {
            "rule_id": rule.id,
            "rule_label": rule.label,
            "features": list(rule.features),
            "status": STATUS_INSUFFICIENT,
            "reject_reason": f"n_matched<{min_n}",
            "n_total": len(rows),
            "n_matched": overall["n"],
            "metrics": overall,
            "walk_forward": {},
            "rolling": {},
            "oos": {},
            "monte_carlo": {},
            "stability": {},
            "ci_ev": (None, None),
            "p_value": None,
        }

    wf = walk_forward_validate(rows, rule, min_n=min_n)
    rolling = rolling_windows_validate(rows, rule, min_n=max(3, min_n // 2), n_windows=n_roll)
    oos = oos_replay(rows, rule, min_n=min_n)
    stability = stability_over_time(rows, rule, n_buckets=n_roll, min_n=max(3, min_n // 2))
    mc = monte_carlo_null(matched_pnls, baseline, n_sims=n_mc, seed=seed)
    ci = bootstrap_expectancy_ci(matched_pnls, n_boot=n_boot, seed=seed)
    # Prefer OOS-matched bootstrap when available
    oos_matched = match_rows(sort_chrono(rows)[max(1, int(len(rows) * 0.70)):], rule)
    if len(oos_matched) >= 4:
        ci = bootstrap_expectancy_ci(pnl_list(oos_matched), n_boot=n_boot, seed=seed + 1)
    pval = permutation_pvalue(matched_pnls, baseline, n_perm=n_perm, seed=seed)

    gate_checks = {
        "walk_forward": wf.get("passed"),
        "rolling": rolling.get("passed"),
        "oos": oos.get("passed"),
        "stability": stability.get("passed"),
    }
    # Hard reject: lose edge in any independent window family
    hard_fail_reasons: list[str] = []
    for name, block in (
        ("walk_forward", wf),
        ("rolling", rolling),
        ("oos", oos),
        ("stability", stability),
    ):
        fails = block.get("independent_failures") or []
        if name == "oos" and block.get("reason") == "oos_lost_edge":
            fails = ["oos"]
        if fails or block.get("reason", "").startswith("lost_edge"):
            hard_fail_reasons.append(f"{name}:{block.get('reason')}")

    if hard_fail_reasons:
        status = STATUS_REJECTED
        reject_reason = "; ".join(hard_fail_reasons)
    elif not all(gate_checks.values()):
        status = STATUS_REJECTED
        reject_reason = "failed_gates:" + ",".join(
            k for k, v in gate_checks.items() if not v
        )
    else:
        # Soft statistical gates (informational if hard windows passed)
        status = STATUS_PASSED
        reject_reason = None
        # Still reject if CI entirely <= 0 or permutation clearly null
        if ci[0] is not None and ci[1] is not None and ci[1] <= 0:
            status = STATUS_REJECTED
            reject_reason = "bootstrap_ci_nonpositive"
        elif pval is not None and pval > 0.20 and not mc.get("passed"):
            status = STATUS_REJECTED
            reject_reason = "weak_significance"

    return {
        "rule_id": rule.id,
        "rule_label": rule.label,
        "features": list(rule.features),
        "status": status,
        "reject_reason": reject_reason,
        "n_total": len(rows),
        "n_matched": overall["n"],
        "expectancy": overall.get("expectancy"),
        "pf": overall.get("pf") if not overall.get("pf_inf") else "inf",
        "winrate": overall.get("winrate"),
        "sharpe": overall.get("sharpe"),
        "metrics": overall,
        "walk_forward": wf,
        "rolling": rolling,
        "oos": oos,
        "monte_carlo": mc,
        "stability": stability,
        "ci_ev": ci,
        "p_value": pval,
        "gates": gate_checks,
    }


__all__ = [
    "STATUS_INSUFFICIENT",
    "STATUS_PASSED",
    "STATUS_REJECTED",
    "match_rows",
    "monte_carlo_null",
    "oos_replay",
    "rolling_windows_validate",
    "sort_chrono",
    "stability_over_time",
    "validate_candidate",
    "walk_forward_validate",
]
