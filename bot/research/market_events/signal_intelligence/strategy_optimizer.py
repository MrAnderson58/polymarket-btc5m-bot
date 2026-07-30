"""Adaptive Strategy Optimizer V1.

Learns from completed paper trades and recommends (or safely applies)
better filters: symbol disable/recovery, confidence cutoff, exploration rate,
and exit-parameter suggestions.

Observe-first. Auto-apply only when sample size, confidence, and PF improvement
all clear configurable thresholds. Never hardcodes symbols.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.feature_lab_s59 import (
    lab_bucket_metrics,
    load_lab_trades,
)
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)

logger = logging.getLogger(__name__)

REPORT_PATH = BASE_DIR / "OPTIMIZER_REPORT.md"
STATE_PATH = BASE_DIR / "data" / "strategy_optimizer_state.json"

# --- configurable knobs (env-overridable) ---
OPT_MIN_SAMPLE = 20
OPT_PF_DISABLE = 1.0
OPT_SYMBOL_RECOVERY_PF = 1.15
OPT_SYMBOL_RECOVERY_EXPECTANCY = 0.0
OPT_CONFIDENCE_STEP = 0.05
OPT_CONFIDENCE_MIN = 0.50
OPT_CONFIDENCE_MAX = 0.95
OPT_EXPLORE_MIN = 0.02
OPT_EXPLORE_MAX = 0.25
OPT_EXPLORE_BASE = 0.10
OPT_AUTO_APPLY = False
OPT_AUTO_MIN_SAMPLE = 50
OPT_AUTO_MIN_CONFIDENCE = 0.70
OPT_AUTO_MIN_PF_IMPROVEMENT = 0.10
OPT_ROLLING_LIMIT = 500  # most recent closed trades for rolling stats

GATE_SYMBOL_DISABLED = "SYMBOL_DISABLED"
GATE_CONFIDENCE_BLOCK = "CONFIDENCE_BLOCK"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def refresh_optimizer_config_from_env() -> None:
    global OPT_MIN_SAMPLE, OPT_PF_DISABLE, OPT_SYMBOL_RECOVERY_PF
    global OPT_SYMBOL_RECOVERY_EXPECTANCY, OPT_CONFIDENCE_STEP
    global OPT_CONFIDENCE_MIN, OPT_CONFIDENCE_MAX
    global OPT_EXPLORE_MIN, OPT_EXPLORE_MAX, OPT_EXPLORE_BASE
    global OPT_AUTO_APPLY, OPT_AUTO_MIN_SAMPLE, OPT_AUTO_MIN_CONFIDENCE
    global OPT_AUTO_MIN_PF_IMPROVEMENT, OPT_ROLLING_LIMIT
    try:
        OPT_MIN_SAMPLE = max(5, int(os.environ.get("OPT_MIN_SAMPLE", OPT_MIN_SAMPLE)))
    except (TypeError, ValueError):
        pass
    for name, attr, cast, lo, hi in (
        ("OPT_PF_DISABLE", "OPT_PF_DISABLE", float, 0.1, 5.0),
        ("OPT_SYMBOL_RECOVERY_PF", "OPT_SYMBOL_RECOVERY_PF", float, 0.5, 5.0),
        ("OPT_SYMBOL_RECOVERY_EXPECTANCY", "OPT_SYMBOL_RECOVERY_EXPECTANCY", float, -10.0, 10.0),
        ("OPT_CONFIDENCE_STEP", "OPT_CONFIDENCE_STEP", float, 0.01, 0.2),
        ("OPT_CONFIDENCE_MIN", "OPT_CONFIDENCE_MIN", float, 0.0, 1.0),
        ("OPT_CONFIDENCE_MAX", "OPT_CONFIDENCE_MAX", float, 0.0, 1.0),
        ("OPT_EXPLORE_MIN", "OPT_EXPLORE_MIN", float, 0.0, 1.0),
        ("OPT_EXPLORE_MAX", "OPT_EXPLORE_MAX", float, 0.0, 1.0),
        ("OPT_EXPLORE_BASE", "OPT_EXPLORE_BASE", float, 0.0, 1.0),
        ("OPT_AUTO_MIN_CONFIDENCE", "OPT_AUTO_MIN_CONFIDENCE", float, 0.0, 1.0),
        ("OPT_AUTO_MIN_PF_IMPROVEMENT", "OPT_AUTO_MIN_PF_IMPROVEMENT", float, 0.0, 5.0),
    ):
        if name in os.environ:
            try:
                val = cast(os.environ[name])
                globals()[attr] = max(lo, min(hi, val))
            except (TypeError, ValueError):
                pass
    if "OPT_AUTO_APPLY" in os.environ:
        OPT_AUTO_APPLY = _env_bool("OPT_AUTO_APPLY", False)
    if "OPT_AUTO_MIN_SAMPLE" in os.environ:
        try:
            OPT_AUTO_MIN_SAMPLE = max(10, int(os.environ["OPT_AUTO_MIN_SAMPLE"]))
        except (TypeError, ValueError):
            pass
    if "OPT_ROLLING_LIMIT" in os.environ:
        try:
            OPT_ROLLING_LIMIT = max(20, int(os.environ["OPT_ROLLING_LIMIT"]))
        except (TypeError, ValueError):
            pass


refresh_optimizer_config_from_env()


def _row(r: Any) -> dict[str, Any]:
    if r is None:
        return {}
    try:
        return dict(r)
    except Exception:
        return {}


def _pattern_label(r: dict[str, Any]) -> str:
    raw = r.get("pattern_json") or r.get("pattern") or r.get("snapshot_pattern_json")
    if isinstance(raw, dict):
        for key in ("name", "pattern", "label", "type"):
            if raw.get(key):
                return str(raw[key])
        return "dict"
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                for key in ("name", "pattern", "label", "type"):
                    if parsed.get(key):
                        return str(parsed[key])
        except Exception:
            return raw[:40]
        return raw[:40]
    return "NULL"


def _confidence(r: dict[str, Any]) -> float | None:
    for key in ("decision_confidence", "ai_score", "confidence"):
        v = _safe_float(r.get(key))
        if v is not None:
            # ai_score sometimes 0-100
            if v > 1.5:
                return max(0.0, min(1.0, v / 100.0))
            return max(0.0, min(1.0, v))
    return None


def _hold_sec(r: dict[str, Any]) -> float | None:
    for key in ("holding_seconds", "duration_sec", "hold_sec"):
        v = _safe_float(r.get(key))
        if v is not None:
            return v
    return None


def _mfe(r: dict[str, Any]) -> float | None:
    return _safe_float(r.get("mfe_pct") if r.get("mfe_pct") is not None else r.get("f_mfe"))


def _mae(r: dict[str, Any]) -> float | None:
    return _safe_float(r.get("mae_pct") if r.get("mae_pct") is not None else r.get("f_mae"))


def extended_bucket_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """lab_bucket_metrics + avg pnl%, hold, MFE, MAE."""
    base = lab_bucket_metrics(rows)
    if not rows:
        base.update(
            {
                "avg_pnl_pct": None,
                "avg_hold_sec": None,
                "avg_mfe": None,
                "avg_mae": None,
                "winrate_frac": None,
            }
        )
        return base
    pnl_pcts = [_safe_float(r.get("pnl_pct")) or 0.0 for r in rows]
    holds = [h for h in (_hold_sec(r) for r in rows) if h is not None]
    mfes = [m for m in (_mfe(r) for r in rows) if m is not None]
    maes = [m for m in (_mae(r) for r in rows) if m is not None]
    base["avg_pnl_pct"] = round(sum(pnl_pcts) / len(pnl_pcts), 4)
    base["avg_hold_sec"] = round(sum(holds) / len(holds), 1) if holds else None
    base["avg_mfe"] = round(sum(mfes) / len(mfes), 4) if mfes else None
    base["avg_mae"] = round(sum(maes) / len(maes), 4) if maes else None
    wr = base.get("winrate")
    base["winrate_frac"] = round(float(wr) / 100.0, 4) if wr is not None else None
    return base


def load_optimizer_trades(conn: Any, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Prefer LIVE S42 paper book; fall back to research lab trades (S56)."""
    limit = OPT_ROLLING_LIMIT if limit is None else int(limit)
    rows: list[dict[str, Any]] = []
    try:
        sql = """
            SELECT p.*,
                   f.gate_decision AS gate_decision,
                   f.market_regime AS market_regime,
                   f.ai_score AS ai_score,
                   COALESCE(p.mfe_pct, f.mfe_pct) AS mfe_pct,
                   COALESCE(p.mae_pct, f.mae_pct) AS mae_pct,
                   COALESCE(p.holding_seconds, f.duration_sec) AS holding_seconds,
                   COALESCE(p.exit_reason, f.exit_reason) AS exit_reason
            FROM market_events_paper_trades_s42 p
            LEFT JOIN market_events_trade_features_s55 f
              ON f.paper_trade_id = p.id
            WHERE p.status = 'CLOSED' AND p.pnl_pct IS NOT NULL
            ORDER BY COALESCE(p.closed_at, p.updated_at, p.id) DESC
            LIMIT ?
            """
        rows = [_row(r) for r in conn.execute(sql, (limit,)).fetchall()]
    except Exception as exc:
        logger.info("optimizer: S42 load failed (%s); trying lab trades", exc)
        rows = []

    if not rows:
        try:
            lab = load_lab_trades(conn)
            rows = lab[-limit:] if limit and len(lab) > limit else lab
        except Exception as exc:
            logger.warning("optimizer: lab load failed: %s", exc)
            rows = []

    for r in rows:
        r["pattern"] = _pattern_label(r)
        r["news_category"] = str(r.get("news_category") or "NULL")
        r["gate_decision"] = str(r.get("gate_decision") or "NULL")
        r["market_regime"] = str(r.get("market_regime") or "NULL")
        r["direction"] = str(r.get("direction") or "").upper() or "NULL"
        r["symbol"] = str(r.get("symbol") or "NULL").upper()
        conf = _confidence(r)
        r["_confidence"] = conf
    return rows


def _group_metrics(rows: list[dict[str, Any]], key_fn) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        buckets[str(key_fn(r))].append(r)
    return {k: extended_bucket_metrics(v) for k, v in sorted(buckets.items(), key=lambda x: -len(x[1]))}


def confidence_bucket(conf: float | None, step: float = 0.05) -> str:
    if conf is None:
        return "NULL"
    step = step or OPT_CONFIDENCE_STEP
    lo = math.floor(conf / step) * step
    hi = lo + step
    return f"{lo:.2f}-{hi:.2f}"


def compute_segment_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "overall": extended_bucket_metrics(rows),
        "by_symbol": _group_metrics(rows, lambda r: r.get("symbol") or "NULL"),
        "by_direction": _group_metrics(rows, lambda r: r.get("direction") or "NULL"),
        "by_gate_decision": _group_metrics(rows, lambda r: r.get("gate_decision") or "NULL"),
        "by_pattern": _group_metrics(rows, lambda r: r.get("pattern") or "NULL"),
        "by_news_category": _group_metrics(rows, lambda r: r.get("news_category") or "NULL"),
        "by_confidence_bucket": _group_metrics(
            rows, lambda r: confidence_bucket(r.get("_confidence")),
        ),
        "by_market_regime": _group_metrics(rows, lambda r: r.get("market_regime") or "NULL"),
    }


# ---------------------------------------------------------------------------
# Adaptive symbol filter
# ---------------------------------------------------------------------------

def evaluate_symbol_filter(
    by_symbol: dict[str, dict[str, Any]],
    *,
    previous_disabled: set[str] | None = None,
    min_sample: int | None = None,
    pf_disable: float | None = None,
) -> dict[str, Any]:
    """Mark symbols DISABLED / ENABLED from rolling stats. No hardcoded symbols."""
    refresh_optimizer_config_from_env()
    min_sample = OPT_MIN_SAMPLE if min_sample is None else int(min_sample)
    pf_disable = OPT_PF_DISABLE if pf_disable is None else float(pf_disable)
    prev = {s.upper() for s in (previous_disabled or set())}
    disabled: set[str] = set()
    decisions: list[dict[str, Any]] = []

    for sym, m in by_symbol.items():
        sym_u = str(sym).upper()
        if sym_u in ("NULL", ""):
            continue
        n = int(m.get("n") or 0)
        pf = m.get("pf")
        pf_inf = bool(m.get("pf_inf"))
        exp = m.get("expectancy")
        effective_pf = float("inf") if pf_inf else (float(pf) if pf is not None else 0.0)
        was_disabled = sym_u in prev

        if n < min_sample:
            # Insufficient evidence: keep prior state (fail-open for new symbols).
            if was_disabled:
                disabled.add(sym_u)
            decisions.append({
                "symbol": sym_u,
                "action": "KEEP_DISABLED" if was_disabled else "INSUFFICIENT_SAMPLE",
                "n": n,
                "pf": pf,
                "expectancy": exp,
            })
            continue

        bad = (effective_pf < pf_disable) and (exp is not None and float(exp) < 0)
        if bad:
            disabled.add(sym_u)
            decisions.append({
                "symbol": sym_u,
                "action": "DISABLE",
                "n": n,
                "pf": None if pf_inf else pf,
                "expectancy": exp,
                "reason": f"PF<{pf_disable} and expectancy<0",
            })
            continue

        recovered = (
            was_disabled
            and effective_pf >= float(OPT_SYMBOL_RECOVERY_PF)
            and exp is not None
            and float(exp) >= float(OPT_SYMBOL_RECOVERY_EXPECTANCY)
        )
        if recovered:
            decisions.append({
                "symbol": sym_u,
                "action": "RE_ENABLE",
                "n": n,
                "pf": None if pf_inf else pf,
                "expectancy": exp,
            })
            continue

        if was_disabled and not bad:
            # Not fully recovered — stay disabled until recovery thresholds met.
            disabled.add(sym_u)
            decisions.append({
                "symbol": sym_u,
                "action": "KEEP_DISABLED",
                "n": n,
                "pf": None if pf_inf else pf,
                "expectancy": exp,
            })
            continue

        decisions.append({
            "symbol": sym_u,
            "action": "KEEP_ENABLED",
            "n": n,
            "pf": None if pf_inf else pf,
            "expectancy": exp,
        })

    # Symbols previously disabled but absent from current sample stay disabled.
    for sym_u in prev:
        if sym_u not in {d["symbol"] for d in decisions}:
            disabled.add(sym_u)
            decisions.append({
                "symbol": sym_u,
                "action": "KEEP_DISABLED",
                "n": 0,
                "pf": None,
                "expectancy": None,
                "reason": "no recent sample",
            })

    return {
        "disabled_symbols": sorted(disabled),
        "decisions": decisions,
        "min_sample": min_sample,
        "pf_disable": pf_disable,
    }


def is_symbol_disabled(symbol: str | None) -> bool:
    if not symbol:
        return False
    state = load_optimizer_state()
    applied = state.get("applied") or {}
    disabled = {str(s).upper() for s in (applied.get("disabled_symbols") or [])}
    return str(symbol).upper() in disabled


# ---------------------------------------------------------------------------
# Adaptive confidence threshold
# ---------------------------------------------------------------------------

def optimize_confidence_threshold(
    rows: list[dict[str, Any]],
    *,
    step: float | None = None,
    lo: float | None = None,
    hi: float | None = None,
    min_sample: int | None = None,
) -> dict[str, Any]:
    """Grid-search confidence cutoff maximizing expectancy on the kept side."""
    refresh_optimizer_config_from_env()
    step = OPT_CONFIDENCE_STEP if step is None else float(step)
    lo = OPT_CONFIDENCE_MIN if lo is None else float(lo)
    hi = OPT_CONFIDENCE_MAX if hi is None else float(hi)
    min_sample = OPT_MIN_SAMPLE if min_sample is None else int(min_sample)

    scored = [(r, r.get("_confidence")) for r in rows if r.get("_confidence") is not None]
    baseline = extended_bucket_metrics(rows)
    grid: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None

    t = lo
    while t <= hi + 1e-12:
        kept = [r for r, c in scored if c is not None and float(c) >= t]
        m = extended_bucket_metrics(kept)
        entry = {"threshold": round(t, 4), **m}
        grid.append(entry)
        if int(m.get("n") or 0) >= min_sample and m.get("expectancy") is not None:
            if best is None or float(m["expectancy"]) > float(best["expectancy"]):
                best = entry
        t = round(t + step, 10)

    return {
        "baseline": baseline,
        "grid": grid,
        "recommended_threshold": None if best is None else best["threshold"],
        "recommended_metrics": best,
        "scored_n": len(scored),
        "min_sample": min_sample,
    }


def passes_confidence_gate(features: dict[str, Any]) -> tuple[bool, str]:
    """Runtime gate using applied confidence threshold (if any)."""
    state = load_optimizer_state()
    applied = state.get("applied") or {}
    thr = applied.get("confidence_threshold")
    if thr is None:
        return True, "NO_THRESHOLD"
    conf = _confidence(features)
    if conf is None:
        return True, "NO_CONFIDENCE"  # fail-open when missing
    if float(conf) < float(thr):
        return False, GATE_CONFIDENCE_BLOCK
    return True, "PASS"


# ---------------------------------------------------------------------------
# Adaptive exploration rate
# ---------------------------------------------------------------------------

def compute_exploration_rate(
    rows: list[dict[str, Any]],
    *,
    base: float | None = None,
    lo: float | None = None,
    hi: float | None = None,
) -> dict[str, Any]:
    """Dynamic ε from recent PF, volatility proxy, drawdown, sample size."""
    refresh_optimizer_config_from_env()
    base = OPT_EXPLORE_BASE if base is None else float(base)
    lo = OPT_EXPLORE_MIN if lo is None else float(lo)
    hi = OPT_EXPLORE_MAX if hi is None else float(hi)

    m = extended_bucket_metrics(rows)
    n = int(m.get("n") or 0)
    pf = m.get("pf")
    pf_inf = bool(m.get("pf_inf"))
    effective_pf = 2.0 if pf_inf else (float(pf) if pf is not None else 0.5)
    pnls = [_safe_float(r.get("pnl_pct")) or 0.0 for r in rows]
    vol = 0.0
    if len(pnls) >= 2:
        mean = sum(pnls) / len(pnls)
        vol = math.sqrt(sum((x - mean) ** 2 for x in pnls) / len(pnls))
    # Max drawdown on cumulative pnl%
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    rate = base
    # Strong PF → less exploration; weak PF → more
    if effective_pf >= 1.5:
        rate -= 0.04
    elif effective_pf >= 1.1:
        rate -= 0.02
    elif effective_pf < 0.9:
        rate += 0.05
    elif effective_pf < 1.0:
        rate += 0.03

    if vol >= 5.0:
        rate += 0.03
    elif vol >= 2.0:
        rate += 0.01

    if max_dd >= 30:
        rate -= 0.05
    elif max_dd >= 15:
        rate -= 0.02

    if n < OPT_MIN_SAMPLE:
        rate += 0.04  # cold book → explore more
    elif n >= OPT_AUTO_MIN_SAMPLE:
        rate -= 0.01

    rate = max(lo, min(hi, rate))
    return {
        "exploration_rate": round(rate, 4),
        "base": base,
        "min": lo,
        "max": hi,
        "inputs": {
            "n": n,
            "pf": None if pf_inf else pf,
            "pf_inf": pf_inf,
            "volatility_pnl_pct": round(vol, 4),
            "max_drawdown_pnl_pct": round(max_dd, 4),
        },
    }


def get_effective_exploration_rate(default: float | None = None) -> float:
    """Runtime ε for S57 — applied value if present, else module/S57 default."""
    state = load_optimizer_state()
    applied = state.get("applied") or {}
    if applied.get("exploration_rate") is not None:
        try:
            return max(0.0, min(1.0, float(applied["exploration_rate"])))
        except (TypeError, ValueError):
            pass
    if default is not None:
        return float(default)
    return float(OPT_EXPLORE_BASE)


# ---------------------------------------------------------------------------
# Exit optimizer (recommend only)
# ---------------------------------------------------------------------------

def analyze_exits(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_exit = _group_metrics(rows, lambda r: str(r.get("exit_reason") or "NULL"))
    overall = extended_bucket_metrics(rows)
    avg_mfe = overall.get("avg_mfe")
    avg_mae = overall.get("avg_mae")

    recommendations: list[dict[str, Any]] = []
    trail = by_exit.get("TRAILING") or by_exit.get("TRAIL")
    stop = by_exit.get("STOP") or by_exit.get("SL")
    tp1 = by_exit.get("TP1")
    tp2 = by_exit.get("TP2")

    if avg_mfe is not None and avg_mfe > 0:
        # Activate trailing earlier if MFE >> realized on trail exits
        activate_at = round(max(0.3, min(float(avg_mfe) * 0.45, float(avg_mfe) * 0.7)), 3)
        recommendations.append({
            "param": "trailing_activation_pct",
            "recommended": activate_at,
            "rationale": f"avg MFE={avg_mfe}; activate trail near ~45–70% of typical MFE",
            "evidence_n": overall.get("n"),
        })
        tp1_rec = round(max(0.4, float(avg_mfe) * 0.55), 3)
        tp2_rec = round(max(tp1_rec + 0.2, float(avg_mfe) * 0.85), 3)
        recommendations.append({
            "param": "tp1_pct",
            "recommended": tp1_rec,
            "rationale": f"align TP1 with ~55% of avg MFE ({avg_mfe})",
            "evidence_n": overall.get("n"),
        })
        recommendations.append({
            "param": "tp2_pct",
            "recommended": tp2_rec,
            "rationale": f"align TP2 with ~85% of avg MFE ({avg_mfe})",
            "evidence_n": overall.get("n"),
        })

    if avg_mae is not None and avg_mae < 0:
        stop_rec = round(min(-0.5, float(avg_mae) * 1.05), 3)
        recommendations.append({
            "param": "stop_pct",
            "recommended": stop_rec,
            "rationale": f"avg MAE={avg_mae}; stop slightly beyond typical adverse excursion",
            "evidence_n": overall.get("n"),
        })

    if stop and int(stop.get("n") or 0) >= 5 and (stop.get("expectancy") or 0) < 0:
        recommendations.append({
            "param": "stop_management",
            "recommended": "review_stop_distance",
            "rationale": f"STOP exits n={stop['n']} expectancy={stop.get('expectancy')}",
            "evidence_n": stop.get("n"),
        })

    return {
        "by_exit_reason": by_exit,
        "tp1_metrics": tp1,
        "tp2_metrics": tp2,
        "trailing_metrics": trail,
        "stop_metrics": stop,
        "recommendations": recommendations,
        "auto_apply": False,  # V1: never auto-apply exits
    }


# ---------------------------------------------------------------------------
# Safety / apply
# ---------------------------------------------------------------------------

def _confidence_score(sample_n: int, pf_improvement: float | None) -> float:
    """Heuristic 0..1 confidence from sample size and improvement magnitude."""
    size_part = min(1.0, sample_n / float(max(OPT_AUTO_MIN_SAMPLE, 1)))
    imp = abs(float(pf_improvement or 0.0))
    imp_part = min(1.0, imp / max(float(OPT_AUTO_MIN_PF_IMPROVEMENT), 1e-6))
    return round(0.6 * size_part + 0.4 * imp_part, 4)


def should_auto_apply(
    *,
    sample_n: int,
    confidence: float,
    pf_improvement: float | None,
) -> bool:
    refresh_optimizer_config_from_env()
    if not OPT_AUTO_APPLY:
        return False
    if sample_n < OPT_AUTO_MIN_SAMPLE:
        return False
    if confidence < OPT_AUTO_MIN_CONFIDENCE:
        return False
    if pf_improvement is None or float(pf_improvement) < float(OPT_AUTO_MIN_PF_IMPROVEMENT):
        return False
    return True


def load_optimizer_state() -> dict[str, Any]:
    path = Path(os.environ.get("OPT_STATE_PATH", str(STATE_PATH)))
    if not path.exists():
        return {"applied": {}, "recommended": {}, "history": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"applied": {}, "recommended": {}, "history": []}


def save_optimizer_state(state: dict[str, Any]) -> Path:
    path = Path(os.environ.get("OPT_STATE_PATH", str(STATE_PATH)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Cycle + report
# ---------------------------------------------------------------------------

def run_strategy_optimizer(
    conn: Any,
    *,
    write_report: bool = True,
    report_path: Path | None = None,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Full daily optimization cycle."""
    refresh_optimizer_config_from_env()
    if state_path is not None:
        os.environ["OPT_STATE_PATH"] = str(state_path)

    rows = load_optimizer_trades(conn)
    segments = compute_segment_report(rows)
    prev_state = load_optimizer_state()
    prev_disabled = set((prev_state.get("applied") or {}).get("disabled_symbols") or [])

    symbol_filter = evaluate_symbol_filter(
        segments["by_symbol"], previous_disabled=prev_disabled,
    )
    confidence = optimize_confidence_threshold(rows)
    explore = compute_exploration_rate(rows)
    exits = analyze_exits(rows)

    baseline_pf = segments["overall"].get("pf")
    # Expected improvement: confidence-filter expectancy vs baseline when available
    rec_m = confidence.get("recommended_metrics") or {}
    base_exp = segments["overall"].get("expectancy") or 0.0
    rec_exp = rec_m.get("expectancy")
    exp_lift = None if rec_exp is None else round(float(rec_exp) - float(base_exp), 4)
    pf_lift = None
    if rec_m.get("pf") is not None and baseline_pf is not None:
        pf_lift = round(float(rec_m["pf"]) - float(baseline_pf), 4)
    elif rec_m.get("pf_inf") and baseline_pf is not None:
        pf_lift = round(2.0 - float(baseline_pf), 4)

    sample_n = int(segments["overall"].get("n") or 0)
    conf_score = _confidence_score(sample_n, pf_lift if pf_lift is not None else exp_lift)
    # V1 only recommends. Auto-apply requires Adaptive Validation V2
    # (validate-optimizer) so every change is A/B + CV + walk-forward tested.
    apply_ok = False

    current_params = {
        "disabled_symbols": sorted(prev_disabled),
        "confidence_threshold": (prev_state.get("applied") or {}).get("confidence_threshold"),
        "exploration_rate": (prev_state.get("applied") or {}).get(
            "exploration_rate", OPT_EXPLORE_BASE,
        ),
        "S57_EXPLORATION_RATE_env": os.environ.get("S57_EXPLORATION_RATE"),
    }
    recommended_params = {
        "disabled_symbols": symbol_filter["disabled_symbols"],
        "confidence_threshold": confidence.get("recommended_threshold"),
        "exploration_rate": explore["exploration_rate"],
        "exits": exits["recommendations"],
    }

    applied: dict[str, Any] = dict(prev_state.get("applied") or {})
    apply_actions: list[str] = [
        "RECOMMEND_ONLY — run validate-optimizer before any auto-apply",
    ]
    result = {
        "ok": True,
        "generated_at": int(time.time()),
        "sample_size": sample_n,
        "confidence": conf_score,
        "auto_apply_eligible": apply_ok,
        "apply_actions": apply_actions,
        "current_parameters": current_params,
        "recommended_parameters": recommended_params,
        "expected_improvement": {
            "expectancy_lift": exp_lift,
            "pf_lift": pf_lift,
            "note": "Lift vs baseline if confidence filter applied; exits are recommend-only",
        },
        "segments": segments,
        "symbol_filter": symbol_filter,
        "confidence_optimization": {
            k: v for k, v in confidence.items() if k != "grid"
        },
        "confidence_grid": confidence.get("grid"),
        "exploration": explore,
        "exits": exits,
        "safety": {
            "OPT_AUTO_APPLY": OPT_AUTO_APPLY,
            "OPT_AUTO_MIN_SAMPLE": OPT_AUTO_MIN_SAMPLE,
            "OPT_AUTO_MIN_CONFIDENCE": OPT_AUTO_MIN_CONFIDENCE,
            "OPT_AUTO_MIN_PF_IMPROVEMENT": OPT_AUTO_MIN_PF_IMPROVEMENT,
            "OPT_MIN_SAMPLE": OPT_MIN_SAMPLE,
        },
        "applied": applied if apply_ok else (prev_state.get("applied") or {}),
    }

    new_state = {
        "updated_at": result["generated_at"],
        "recommended": recommended_params,
        "applied": result["applied"],
        "last_result_summary": {
            "sample_size": sample_n,
            "confidence": conf_score,
            "auto_apply_eligible": apply_ok,
            "expected_improvement": result["expected_improvement"],
        },
        "history": (prev_state.get("history") or [])[-20:] + [{
            "ts": result["generated_at"],
            "sample_size": sample_n,
            "applied": apply_ok,
        }],
    }
    save_optimizer_state(new_state)

    md = format_optimizer_report(result)
    result["report_markdown"] = md
    if write_report:
        path = Path(report_path) if report_path else Path(
            os.environ.get("OPT_REPORT_PATH", str(REPORT_PATH)),
        )
        path.write_text(md, encoding="utf-8")
        result["report_path"] = str(path.resolve())
    return result


def _fmt_metrics(m: dict[str, Any] | None) -> str:
    if not m:
        return "n=0"
    parts = [f"n={m.get('n')}"]
    if m.get("winrate") is not None:
        parts.append(f"WR={m['winrate']}%")
    if m.get("expectancy") is not None:
        parts.append(f"E={m['expectancy']}")
    if m.get("pf_inf"):
        parts.append("PF=inf")
    elif m.get("pf") is not None:
        parts.append(f"PF={m['pf']}")
    if m.get("avg_pnl_pct") is not None:
        parts.append(f"avg_pnl%={m['avg_pnl_pct']}")
    if m.get("avg_hold_sec") is not None:
        parts.append(f"hold_s={m['avg_hold_sec']}")
    if m.get("avg_mfe") is not None:
        parts.append(f"MFE={m['avg_mfe']}")
    if m.get("avg_mae") is not None:
        parts.append(f"MAE={m['avg_mae']}")
    return " ".join(parts)


def format_optimizer_report(result: dict[str, Any]) -> str:
    lines: list[str] = [
        "# OPTIMIZER_REPORT — Adaptive Strategy Optimizer V1",
        "",
        f"**Generated:** {result.get('generated_at')}",
        f"**Sample size:** {result.get('sample_size')}",
        f"**Confidence:** {result.get('confidence')}",
        f"**Auto-apply eligible:** {result.get('auto_apply_eligible')}",
        f"**Actions:** {', '.join(result.get('apply_actions') or [])}",
        "",
        "## Current parameters",
        "",
        "```json",
        json.dumps(result.get("current_parameters") or {}, indent=2),
        "```",
        "",
        "## Recommended parameters",
        "",
        "```json",
        json.dumps(result.get("recommended_parameters") or {}, indent=2, default=str),
        "```",
        "",
        "## Expected improvement",
        "",
        "```json",
        json.dumps(result.get("expected_improvement") or {}, indent=2),
        "```",
        "",
        "## Safety thresholds",
        "",
        "```json",
        json.dumps(result.get("safety") or {}, indent=2),
        "```",
        "",
        "## Overall",
        "",
        _fmt_metrics((result.get("segments") or {}).get("overall")),
        "",
    ]

    segs = result.get("segments") or {}
    for title, key in (
        ("By symbol", "by_symbol"),
        ("By direction", "by_direction"),
        ("By gate_decision", "by_gate_decision"),
        ("By pattern", "by_pattern"),
        ("By news_category", "by_news_category"),
        ("By confidence bucket", "by_confidence_bucket"),
        ("By market regime", "by_market_regime"),
    ):
        block = segs.get(key) or {}
        lines.extend([f"## {title}", ""])
        if not block:
            lines.append("_no data_")
            lines.append("")
            continue
        for name, m in list(block.items())[:30]:
            lines.append(f"- **{name}**: {_fmt_metrics(m)}")
        lines.append("")

    lines.extend([
        "## Symbol filter decisions",
        "",
    ])
    for d in (result.get("symbol_filter") or {}).get("decisions") or []:
        lines.append(
            f"- {d.get('action')}: {d.get('symbol')} n={d.get('n')} "
            f"PF={d.get('pf')} E={d.get('expectancy')} {d.get('reason') or ''}".rstrip()
        )
    lines.append("")

    lines.extend([
        "## Confidence optimization",
        "",
        f"Recommended threshold: **{((result.get('confidence_optimization') or {}).get('recommended_threshold'))}**",
        "",
    ])
    for g in (result.get("confidence_grid") or [])[:15]:
        lines.append(f"- thr={g.get('threshold')}: {_fmt_metrics(g)}")
    lines.append("")

    lines.extend([
        "## Exploration rate",
        "",
        "```json",
        json.dumps(result.get("exploration") or {}, indent=2),
        "```",
        "",
        "## Exit recommendations (no auto-apply)",
        "",
    ])
    for rec in ((result.get("exits") or {}).get("recommendations") or []):
        lines.append(
            f"- **{rec.get('param')}** → `{rec.get('recommended')}` "
            f"— {rec.get('rationale')} (n={rec.get('evidence_n')})"
        )
    lines.extend(["", "## Evidence", "", "Source: closed `market_events_paper_trades_s42` (+ S55), fallback S56 lab trades.", ""])
    return "\n".join(lines)


def read_optimizer_report(path: Path | None = None) -> str:
    p = Path(path) if path else Path(os.environ.get("OPT_REPORT_PATH", str(REPORT_PATH)))
    if p.exists():
        return p.read_text(encoding="utf-8")
    return (
        "No OPTIMIZER_REPORT.md yet. Run:\n"
        "  python -m bot.research.market_events optimize-strategy\n"
    )


__all__ = [
    "GATE_CONFIDENCE_BLOCK",
    "GATE_SYMBOL_DISABLED",
    "OPT_AUTO_APPLY",
    "OPT_MIN_SAMPLE",
    "REPORT_PATH",
    "STATE_PATH",
    "analyze_exits",
    "compute_exploration_rate",
    "compute_segment_report",
    "evaluate_symbol_filter",
    "extended_bucket_metrics",
    "format_optimizer_report",
    "get_effective_exploration_rate",
    "is_symbol_disabled",
    "load_optimizer_state",
    "load_optimizer_trades",
    "optimize_confidence_threshold",
    "passes_confidence_gate",
    "read_optimizer_report",
    "refresh_optimizer_config_from_env",
    "run_strategy_optimizer",
    "save_optimizer_state",
    "should_auto_apply",
]
