"""Experiment Runner V1 — side-by-side historical replay of strategy ideas.

Every experiment receives the same trade universe. Comparison is always vs
Current Production Strategy only (never experiment-vs-experiment for promotion).

Safety: read-only. Never writes production strategy / optimizer state / paper book.
Never auto-deploys.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence import strategy_optimizer as opt
from bot.research.market_events.signal_intelligence import strategy_validation as val
from bot.research.market_events.signal_intelligence.experiment_registry import (
    STATUS_ACTIVE,
    Experiment,
    ExperimentRegistry,
    build_default_registry,
)
from bot.research.market_events.signal_intelligence.strategy_validation import (
    FilterParams,
)

logger = logging.getLogger(__name__)

LEADERBOARD_PATH = BASE_DIR / "EXPERIMENT_LEADERBOARD.md"
RESULT_STATE_PATH = BASE_DIR / "data" / "experiment_framework_state.json"

# Promotion gates (aligned with Validation V2 defaults; env-overridable)
EXP_MIN_SAMPLE = 30
EXP_MIN_CONFIDENCE = 0.65
EXP_MIN_PF_INCREASE = 0.05
EXP_MIN_EXPECTANCY_INCREASE = 0.0
EXP_MAX_DRAWDOWN_INCREASE = 5.0
EXP_BOOTSTRAP_N = 100
EXP_P_VALUE_MAX = 0.10
EXP_AUTO_DEPLOY = False  # hard safety — never True in V1

ROLE_BASELINE = "BASELINE"
ROLE_CHAMPION = "CHAMPION"
ROLE_RUNNER_UP = "RUNNER_UP"
ROLE_QUALIFIED = "QUALIFIED"
ROLE_REJECTED = "REJECTED"

PRODUCTION_BASELINE_ID = "production_baseline"


def refresh_experiment_config_from_env() -> None:
    global EXP_MIN_SAMPLE, EXP_MIN_CONFIDENCE, EXP_MIN_PF_INCREASE
    global EXP_MIN_EXPECTANCY_INCREASE, EXP_MAX_DRAWDOWN_INCREASE
    global EXP_BOOTSTRAP_N, EXP_P_VALUE_MAX, EXP_AUTO_DEPLOY
    for name, attr, cast, lo, hi in (
        ("EXP_MIN_SAMPLE", "EXP_MIN_SAMPLE", int, 5, 10_000),
        ("EXP_MIN_CONFIDENCE", "EXP_MIN_CONFIDENCE", float, 0.0, 1.0),
        ("EXP_MIN_PF_INCREASE", "EXP_MIN_PF_INCREASE", float, -1.0, 10.0),
        ("EXP_MIN_EXPECTANCY_INCREASE", "EXP_MIN_EXPECTANCY_INCREASE", float, -10.0, 10.0),
        ("EXP_MAX_DRAWDOWN_INCREASE", "EXP_MAX_DRAWDOWN_INCREASE", float, 0.0, 1000.0),
        ("EXP_BOOTSTRAP_N", "EXP_BOOTSTRAP_N", int, 20, 5000),
        ("EXP_P_VALUE_MAX", "EXP_P_VALUE_MAX", float, 0.0, 1.0),
    ):
        if name in os.environ:
            try:
                globals()[attr] = max(lo, min(hi, cast(os.environ[name])))
            except (TypeError, ValueError):
                pass
    # Auto-deploy is permanently disabled in V1 regardless of env
    EXP_AUTO_DEPLOY = False


def production_baseline_params() -> FilterParams:
    """Current Production Strategy filters from optimizer applied state."""
    state = opt.load_optimizer_state()
    applied = state.get("applied") or {}
    return FilterParams(
        disabled_symbols=set(applied.get("disabled_symbols") or []),
        confidence_threshold=applied.get("confidence_threshold"),
        label=PRODUCTION_BASELINE_ID,
    ).normalized()


def experiment_to_filter_params(exp: Experiment) -> FilterParams:
    d = exp.filter_params_dict()
    return FilterParams(
        disabled_symbols=set(d.get("disabled_symbols") or []),
        confidence_threshold=d.get("confidence_threshold"),
        label=exp.id,
    ).normalized()


def _hold_sec(r: dict[str, Any]) -> float | None:
    return opt._hold_sec(r)


def _mfe(r: dict[str, Any]) -> float | None:
    return opt._mfe(r)


def _mae(r: dict[str, Any]) -> float | None:
    return opt._mae(r)


def compute_experiment_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Trades, PF, expectancy, WR, avg PnL, max DD, Sharpe-like, MFE, MAE, hold."""
    base = val.replay_metrics(rows)
    if not rows:
        base.update(
            {
                "mfe": None,
                "mae": None,
                "holding_time": None,
                "avg_mfe": None,
                "avg_mae": None,
                "avg_holding_sec": None,
            }
        )
        return base
    holds = [h for h in (_hold_sec(r) for r in rows) if h is not None]
    mfes = [m for m in (_mfe(r) for r in rows) if m is not None]
    maes = [m for m in (_mae(r) for r in rows) if m is not None]
    avg_hold = round(sum(holds) / len(holds), 1) if holds else None
    avg_mfe = round(sum(mfes) / len(mfes), 4) if mfes else None
    avg_mae = round(sum(maes) / len(maes), 4) if maes else None
    base["holding_time"] = avg_hold
    base["avg_holding_sec"] = avg_hold
    base["mfe"] = avg_mfe
    base["mae"] = avg_mae
    base["avg_mfe"] = avg_mfe
    base["avg_mae"] = avg_mae
    # Alias trades
    base["trades"] = base.get("n")
    return base


def _confidence_score(sample_n: int, pf_delta: float | None, p_value: float | None) -> float:
    """0..1 confidence from sample size, PF lift, and bootstrap p-value."""
    n_score = min(1.0, float(sample_n) / max(float(EXP_MIN_SAMPLE), 1.0))
    pf_score = 0.5
    if pf_delta is not None:
        pf_score = max(0.0, min(1.0, 0.5 + float(pf_delta) / 2.0))
    p_score = 0.5
    if p_value is not None:
        p_score = max(0.0, min(1.0, 1.0 - float(p_value)))
    return round(0.4 * n_score + 0.35 * pf_score + 0.25 * p_score, 4)


def validation_score(
    *,
    promotion_ok: bool,
    confidence: float,
    pf_delta: float | None,
    expectancy_delta: float | None,
    sample_n: int,
) -> float:
    """Composite ranking score (higher is better). Rejected get soft penalty."""
    pf_d = float(pf_delta or 0.0)
    exp_d = float(expectancy_delta or 0.0)
    n_term = min(1.0, float(sample_n) / max(float(EXP_MIN_SAMPLE), 1.0))
    raw = (
        40.0 * float(confidence)
        + 25.0 * max(-1.0, min(2.0, pf_d))
        + 20.0 * max(-2.0, min(2.0, exp_d))
        + 15.0 * n_term
    )
    if not promotion_ok:
        raw -= 100.0
    return round(raw, 4)


def promotion_decision(
    *,
    sample_n: int,
    confidence: float,
    pf_delta: float | None,
    expectancy_delta: float | None,
    drawdown_delta: float | None,
    p_value: float | None,
    validation_passed: bool,
) -> tuple[bool, list[str]]:
    """Recommend promotion only when all gates clear. Otherwise REJECTED."""
    refresh_experiment_config_from_env()
    reasons: list[str] = []
    if not validation_passed:
        reasons.append("validation_failed")
    if sample_n < EXP_MIN_SAMPLE:
        reasons.append(f"sample_n {sample_n} < EXP_MIN_SAMPLE {EXP_MIN_SAMPLE}")
    if confidence < EXP_MIN_CONFIDENCE:
        reasons.append(
            f"confidence {confidence} < EXP_MIN_CONFIDENCE {EXP_MIN_CONFIDENCE}",
        )
    if pf_delta is None or float(pf_delta) < float(EXP_MIN_PF_INCREASE):
        reasons.append(
            f"pf_delta {pf_delta} < EXP_MIN_PF_INCREASE {EXP_MIN_PF_INCREASE}",
        )
    if expectancy_delta is None or float(expectancy_delta) < float(EXP_MIN_EXPECTANCY_INCREASE):
        reasons.append(
            f"expectancy_delta {expectancy_delta} < EXP_MIN_EXPECTANCY_INCREASE "
            f"{EXP_MIN_EXPECTANCY_INCREASE}",
        )
    if drawdown_delta is not None and float(drawdown_delta) > float(EXP_MAX_DRAWDOWN_INCREASE):
        reasons.append(
            f"drawdown_delta {drawdown_delta} > EXP_MAX_DRAWDOWN_INCREASE "
            f"{EXP_MAX_DRAWDOWN_INCREASE}",
        )
    if p_value is not None and float(p_value) > float(EXP_P_VALUE_MAX):
        reasons.append(f"p_value {p_value} > EXP_P_VALUE_MAX {EXP_P_VALUE_MAX}")
    return (len(reasons) == 0, reasons)


def may_auto_deploy() -> bool:
    """V1 safety: always False. Experiments never deploy."""
    refresh_experiment_config_from_env()
    return False


def evaluate_experiment_vs_baseline(
    rows: list[dict[str, Any]],
    baseline: FilterParams,
    experiment: Experiment,
) -> dict[str, Any]:
    """Run one experiment on ``rows``; compare only to production baseline."""
    refresh_experiment_config_from_env()
    cand = experiment_to_filter_params(experiment)
    # Lightweight validation (A/B + bootstrap); never auto-apply
    pair = val.validate_recommendation_pair(
        rows, baseline, cand, name=experiment.id,
    )
    ab = pair.get("full_ab") or val.ab_replay(rows, baseline, cand)
    base_kept = val.apply_filter(val.sort_trades(rows), baseline)
    cand_kept = val.apply_filter(val.sort_trades(rows), cand)
    base_m = compute_experiment_metrics(base_kept)
    cand_m = compute_experiment_metrics(cand_kept)
    delta = val.metrics_delta(base_m, cand_m)

    boot = (pair.get("cross_validation") or {}).get("bootstrap") or {}
    p_value = pair.get("p_value")
    if p_value is None:
        p_value = boot.get("p_value")

    sample_n = int(cand_m.get("n") or 0)
    # Prefer Validation V2 bootstrap confidence when present
    conf = float(pair.get("confidence") or 0.0)
    if conf <= 0.0:
        conf = _confidence_score(sample_n, delta.get("pf_delta"), p_value)
    validation_passed = bool(pair.get("accepted"))
    promote_ok, reject_reasons = promotion_decision(
        sample_n=sample_n,
        confidence=conf,
        pf_delta=delta.get("pf_delta"),
        expectancy_delta=delta.get("expectancy_delta"),
        drawdown_delta=delta.get("drawdown_delta"),
        p_value=p_value if p_value is not None else None,
        validation_passed=validation_passed,
    )
    # Also surface validation rejection reasons
    for r in pair.get("rejection_reasons") or []:
        tag = f"validation:{r}"
        if tag not in reject_reasons and not promote_ok:
            reject_reasons.append(tag)

    vscore = validation_score(
        promotion_ok=promote_ok,
        confidence=conf,
        pf_delta=delta.get("pf_delta"),
        expectancy_delta=delta.get("expectancy_delta"),
        sample_n=sample_n,
    )

    return {
        "experiment": experiment.to_dict(),
        "baseline_id": PRODUCTION_BASELINE_ID,
        "baseline_params": baseline.to_dict(),
        "candidate_params": cand.to_dict(),
        "universe_n": len(rows),
        "baseline_metrics": base_m,
        "metrics": cand_m,
        "delta_vs_baseline": delta,
        "compared_to": PRODUCTION_BASELINE_ID,
        "compared_to_experiments": False,
        "p_value": p_value,
        "confidence": conf,
        "validation_passed": validation_passed,
        "validation_detail": {
            "accepted": pair.get("accepted"),
            "rejection_reasons": pair.get("rejection_reasons"),
            "overfit": (pair.get("cross_validation") or {}).get("overfit"),
        },
        "promotion_recommended": promote_ok,
        "rejection_reasons": reject_reasons,
        "validation_score": vscore,
        "auto_deploy": False,
        "may_auto_deploy": may_auto_deploy(),
        "read_only": True,
    }


def _leaderboard_sort_key(row: dict[str, Any]) -> tuple:
    """Sort by Validation Score, PF, Expectancy, Sample Size, Confidence.

    Promotion-ready rows sort above rejected so Champion/Runner-up lead the board.
    """
    m = row.get("metrics") or {}
    pf = val._effective_pf(m)
    exp = float(m.get("expectancy") or -1e9)
    n = int(m.get("n") or 0)
    conf = float(row.get("confidence") or 0.0)
    vs = float(row.get("validation_score") or -1e9)
    promote = 1 if row.get("promotion_recommended") else 0
    return (promote, vs, pf, exp, n, conf)


def assign_leaderboard_roles(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Champion / Runner-up among promotion-ready; others REJECTED (or QUALIFIED)."""
    ranked = sorted(results, key=_leaderboard_sort_key, reverse=True)
    promote_ids = [
        (r.get("experiment") or {}).get("id")
        for r in ranked
        if r.get("promotion_recommended")
    ]
    champ_id = promote_ids[0] if promote_ids else None
    runner_id = promote_ids[1] if len(promote_ids) > 1 else None
    out: list[dict[str, Any]] = []
    for r in ranked:
        row = dict(r)
        eid = (r.get("experiment") or {}).get("id")
        if not r.get("promotion_recommended"):
            row["role"] = ROLE_REJECTED
        elif eid == champ_id:
            row["role"] = ROLE_CHAMPION
        elif eid == runner_id:
            row["role"] = ROLE_RUNNER_UP
        else:
            row["role"] = ROLE_QUALIFIED
        row["status_label"] = row["role"]
        out.append(row)
    return out


def format_leaderboard(run: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Experiment Leaderboard V1")
    lines.append("")
    lines.append("_Offline side-by-side replay. Read-only. No paper-trading impact._")
    lines.append("")
    lines.append(f"- Universe trades: **{run.get('universe_n')}**")
    lines.append(f"- Baseline: **{PRODUCTION_BASELINE_ID}** (Current Production Strategy)")
    lines.append(f"- Experiments run: **{len(run.get('results') or [])}**")
    lines.append(f"- Auto-deploy: **{bool(run.get('auto_deploy'))}** (always false)")
    lines.append("")

    base_m = run.get("baseline_metrics") or {}
    lines.append("## Baseline — Current Production Strategy")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    for k in (
        "trades", "n", "pf", "expectancy", "winrate", "avg_pnl",
        "max_drawdown", "sharpe", "mfe", "mae", "holding_time",
    ):
        if k == "n" and "trades" in base_m:
            continue
        if k in base_m:
            lines.append(f"| {k} | {base_m.get(k)} |")
    bp = run.get("baseline_params") or {}
    lines.append("")
    lines.append(f"Params: `{json.dumps(bp, sort_keys=True)}`")
    lines.append("")

    results = run.get("results") or []
    champion = next((r for r in results if r.get("role") == ROLE_CHAMPION), None)
    runner = next((r for r in results if r.get("role") == ROLE_RUNNER_UP), None)
    rejected = [r for r in results if r.get("role") == ROLE_REJECTED]

    lines.append("## Summary")
    lines.append("")
    if champion:
        eid = (champion.get("experiment") or {}).get("id")
        lines.append(f"- **Champion:** `{eid}` (score={champion.get('validation_score')})")
    else:
        lines.append("- **Champion:** _(none — no experiment cleared promotion gates)_")
    if runner:
        eid = (runner.get("experiment") or {}).get("id")
        lines.append(f"- **Runner-up:** `{eid}` (score={runner.get('validation_score')})")
    else:
        lines.append("- **Runner-up:** _(none)_")
    lines.append(f"- **Rejected:** {len(rejected)}")
    lines.append("")

    lines.append("## Leaderboard")
    lines.append("")
    lines.append(
        "| Rank | Role | Experiment | ValScore | PF | Exp | N | Conf | "
        "PFΔ | ExpΔ | DDΔ | Promote |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(results, 1):
        exp = r.get("experiment") or {}
        m = r.get("metrics") or {}
        d = r.get("delta_vs_baseline") or {}
        lines.append(
            "| {rank} | {role} | `{eid}` | {vs} | {pf} | {exp} | {n} | {conf} | "
            "{pfd} | {exd} | {ddd} | {prom} |".format(
                rank=i,
                role=r.get("role"),
                eid=exp.get("id"),
                vs=r.get("validation_score"),
                pf=m.get("pf"),
                exp=m.get("expectancy"),
                n=m.get("n"),
                conf=r.get("confidence"),
                pfd=d.get("pf_delta"),
                exd=d.get("expectancy_delta"),
                ddd=d.get("drawdown_delta"),
                prom="yes" if r.get("promotion_recommended") else "no",
            )
        )
    lines.append("")

    lines.append("## Per-experiment detail")
    lines.append("")
    for r in results:
        exp = r.get("experiment") or {}
        m = r.get("metrics") or {}
        lines.append(f"### `{exp.get('id')}` — {r.get('role')}")
        lines.append("")
        lines.append(f"- Name: {exp.get('name')}")
        lines.append(f"- Compared to: **baseline only** (`{PRODUCTION_BASELINE_ID}`)")
        lines.append(f"- Promotion recommended: `{r.get('promotion_recommended')}`")
        if r.get("rejection_reasons"):
            lines.append(f"- Rejection: {', '.join(r['rejection_reasons'][:8])}")
        lines.append(
            f"- Metrics: trades={m.get('n')} PF={m.get('pf')} "
            f"exp={m.get('expectancy')} WR={m.get('winrate')} "
            f"avgPnL={m.get('avg_pnl')} maxDD={m.get('max_drawdown')} "
            f"sharpe={m.get('sharpe')} MFE={m.get('mfe')} MAE={m.get('mae')} "
            f"hold={m.get('holding_time')}"
        )
        lines.append("")

    lines.append("## Safety")
    lines.append("")
    lines.append("- Experiments are **read-only**.")
    lines.append("- No writes to production strategy / optimizer applied state.")
    lines.append("- No auto deployment.")
    lines.append("- Paper trading is unaffected.")
    lines.append("")
    return "\n".join(lines)


def run_experiments(
    conn: Any | None = None,
    *,
    registry: ExperimentRegistry | None = None,
    rows: list[dict[str, Any]] | None = None,
    write_leaderboard: bool = True,
    leaderboard_path: Path | None = None,
) -> dict[str, Any]:
    """Run every active experiment on the same historical dataset."""
    refresh_experiment_config_from_env()
    reg = registry if registry is not None else build_default_registry()
    if rows is None:
        if conn is None:
            raise ValueError("conn or rows required")
        rows = opt.load_optimizer_trades(conn)
    ordered = val.sort_trades(list(rows))
    baseline = production_baseline_params()
    base_kept = val.apply_filter(ordered, baseline)
    baseline_metrics = compute_experiment_metrics(base_kept)

    experiments = [e for e in reg.list() if e.status == STATUS_ACTIVE]
    raw_results: list[dict[str, Any]] = []
    for exp in experiments:
        raw_results.append(evaluate_experiment_vs_baseline(ordered, baseline, exp))

    ranked = assign_leaderboard_roles(raw_results)
    path = Path(
        os.environ.get("EXP_LEADERBOARD_PATH", str(leaderboard_path or LEADERBOARD_PATH))
    )
    run = {
        "ok": True,
        "universe_n": len(ordered),
        "baseline_id": PRODUCTION_BASELINE_ID,
        "baseline_params": baseline.to_dict(),
        "baseline_metrics": baseline_metrics,
        "n_experiments": len(ranked),
        "results": ranked,
        "auto_deploy": False,
        "may_auto_deploy": False,
        "read_only": True,
        "paper_trading_affected": False,
        "champion": next(
            ((r.get("experiment") or {}).get("id") for r in ranked if r.get("role") == ROLE_CHAMPION),
            None,
        ),
        "runner_up": next(
            ((r.get("experiment") or {}).get("id") for r in ranked if r.get("role") == ROLE_RUNNER_UP),
            None,
        ),
        "rejected_ids": [
            (r.get("experiment") or {}).get("id")
            for r in ranked
            if r.get("role") == ROLE_REJECTED
        ],
    }
    md = format_leaderboard(run)
    run["leaderboard_markdown"] = md
    if write_leaderboard:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(md, encoding="utf-8")
        run["leaderboard_path"] = str(path)
        # Persist last run summary (research artifact only — not production strategy)
        state_path = Path(os.environ.get("EXP_STATE_PATH", str(RESULT_STATE_PATH)))
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            slim = {
                "universe_n": run["universe_n"],
                "champion": run["champion"],
                "runner_up": run["runner_up"],
                "rejected_ids": run["rejected_ids"],
                "baseline_params": run["baseline_params"],
                "auto_deploy": False,
                "read_only": True,
                "results": [
                    {
                        "id": (r.get("experiment") or {}).get("id"),
                        "role": r.get("role"),
                        "validation_score": r.get("validation_score"),
                        "promotion_recommended": r.get("promotion_recommended"),
                        "confidence": r.get("confidence"),
                        "metrics": r.get("metrics"),
                        "delta_vs_baseline": r.get("delta_vs_baseline"),
                    }
                    for r in ranked
                ],
            }
            state_path.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
            run["state_path"] = str(state_path)
        except Exception as exc:
            logger.warning("experiment state write skipped: %s", exc)
    return run


def read_leaderboard(path: Path | None = None) -> str:
    p = Path(os.environ.get("EXP_LEADERBOARD_PATH", str(path or LEADERBOARD_PATH)))
    if not p.exists():
        return "No EXPERIMENT_LEADERBOARD.md yet — run: python -m bot.research.market_events run-experiments"
    return p.read_text(encoding="utf-8")


__all__ = [
    "EXP_AUTO_DEPLOY",
    "LEADERBOARD_PATH",
    "PRODUCTION_BASELINE_ID",
    "ROLE_BASELINE",
    "ROLE_CHAMPION",
    "ROLE_QUALIFIED",
    "ROLE_REJECTED",
    "ROLE_RUNNER_UP",
    "assign_leaderboard_roles",
    "compute_experiment_metrics",
    "evaluate_experiment_vs_baseline",
    "format_leaderboard",
    "may_auto_deploy",
    "production_baseline_params",
    "promotion_decision",
    "read_leaderboard",
    "refresh_experiment_config_from_env",
    "run_experiments",
    "validation_score",
]
