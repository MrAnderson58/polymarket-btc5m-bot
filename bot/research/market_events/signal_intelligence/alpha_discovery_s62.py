"""S62 — Alpha Discovery Engine.

Statistically grounded pattern mining on research snapshots:
  1) Pattern Miner  2) Delta Analyzer  3) Stability Check
  4) Walk-Forward   5) Ranking A/B/C   6) Human Approval only

Observe-only. Never auto-applies strategy changes. Never trades new rules.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
import os
import time
from typing import Any, Callable

from bot.research.market_events.db import execute_with_retry
from bot.research.market_events.signal_intelligence.feature_lab_s59 import (
    lab_bucket_metrics,
    load_lab_trades,
)

logger = logging.getLogger(__name__)

_RUNS = "market_events_alpha_discovery_runs_s62"
_RESULTS = "market_events_alpha_discovery_s62"

S62_MIN_N = 50
S62_TOP_N = 25
S62_MAX_PATTERNS = 500
S62_WRITE_SUGGESTIONS = False
S62_WINDOWS_DAYS = (30, 60, 90)
S62_TRAIN_FRAC = 0.60
S62_VAL_FRAC = 0.20
# test = remainder

Predicate = Callable[[dict[str, Any]], bool | None]
DAY = 86400


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def refresh_s62_config_from_env() -> None:
    global S62_MIN_N, S62_TOP_N, S62_MAX_PATTERNS, S62_WRITE_SUGGESTIONS
    global S62_TRAIN_FRAC, S62_VAL_FRAC
    if "S62_MIN_N" in os.environ:
        try:
            S62_MIN_N = max(10, int(os.environ["S62_MIN_N"]))
        except (TypeError, ValueError):
            pass
    if "S62_TOP_N" in os.environ:
        try:
            S62_TOP_N = max(5, int(os.environ["S62_TOP_N"]))
        except (TypeError, ValueError):
            pass
    if "S62_MAX_PATTERNS" in os.environ:
        try:
            S62_MAX_PATTERNS = max(50, int(os.environ["S62_MAX_PATTERNS"]))
        except (TypeError, ValueError):
            pass
    if "S62_WRITE_SUGGESTIONS" in os.environ:
        S62_WRITE_SUGGESTIONS = _env_bool("S62_WRITE_SUGGESTIONS", False)
    if "S62_TRAIN_FRAC" in os.environ:
        try:
            S62_TRAIN_FRAC = min(0.8, max(0.4, float(os.environ["S62_TRAIN_FRAC"])))
        except (TypeError, ValueError):
            pass
    if "S62_VAL_FRAC" in os.environ:
        try:
            S62_VAL_FRAC = min(0.4, max(0.1, float(os.environ["S62_VAL_FRAC"])))
        except (TypeError, ValueError):
            pass


def _apply_defaults() -> None:
    global S62_MIN_N, S62_TOP_N, S62_MAX_PATTERNS, S62_WRITE_SUGGESTIONS
    global S62_TRAIN_FRAC, S62_VAL_FRAC
    try:
        S62_MIN_N = max(10, int(os.environ.get("S62_MIN_N", "50")))
    except (TypeError, ValueError):
        S62_MIN_N = 50
    try:
        S62_TOP_N = max(5, int(os.environ.get("S62_TOP_N", "25")))
    except (TypeError, ValueError):
        S62_TOP_N = 25
    try:
        S62_MAX_PATTERNS = max(50, int(os.environ.get("S62_MAX_PATTERNS", "500")))
    except (TypeError, ValueError):
        S62_MAX_PATTERNS = 500
    S62_WRITE_SUGGESTIONS = _env_bool("S62_WRITE_SUGGESTIONS", False)
    try:
        S62_TRAIN_FRAC = float(os.environ.get("S62_TRAIN_FRAC", "0.60"))
    except (TypeError, ValueError):
        S62_TRAIN_FRAC = 0.60
    try:
        S62_VAL_FRAC = float(os.environ.get("S62_VAL_FRAC", "0.20"))
    except (TypeError, ValueError):
        S62_VAL_FRAC = 0.20


_apply_defaults()


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _dir(r: dict[str, Any]) -> str:
    return str(r.get("direction") or "").upper()


def _sym(r: dict[str, Any]) -> str:
    return str(r.get("symbol") or "").upper().replace("USDT", "")


def _hour(r: dict[str, Any]) -> int | None:
    h = r.get("hour")
    if h is None:
        return None
    try:
        return int(h)
    except (TypeError, ValueError):
        return None


def _trade_ts(r: dict[str, Any]) -> int:
    for k in ("closed_at", "created_at", "timestamp", "opened_at"):
        v = r.get(k)
        if v is None:
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return 0


def _fingerprint(rule_text: str, mode: str) -> str:
    raw = json.dumps({"rule": rule_text, "mode": mode}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _pf_value(m: dict[str, Any]) -> float | None:
    if m.get("pf_inf"):
        return 99.0
    pf = m.get("pf")
    return float(pf) if pf is not None else None


def _delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return round(float(a) - float(b), 4)


# ---------------------------------------------------------------------------
# Extended metrics (Block 1)
# ---------------------------------------------------------------------------


def pattern_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Trades / WR / Expectancy / PF / Sharpe / Avg DD / Avg Hold."""
    base = lab_bucket_metrics(rows)
    if not rows:
        return {
            **base,
            "avg_dd": None,
            "avg_hold_sec": None,
        }
    dds: list[float] = []
    holds: list[float] = []
    for r in rows:
        mae = _safe_float(r.get("mae_pct"))
        if mae is None:
            mae = _safe_float(r.get("max_drawdown_pct"))
        if mae is not None:
            dds.append(abs(float(mae)))
        elif (_safe_float(r.get("pnl_usd")) or 0) < 0:
            # fallback proxy: loss magnitude as rough DD
            dds.append(abs(float(r.get("pnl_pct") or r.get("pnl_usd") or 0)))
        hold = _safe_float(r.get("duration_sec"))
        if hold is None:
            hold = _safe_float(r.get("holding_seconds"))
        if hold is not None and hold >= 0:
            holds.append(float(hold))
    return {
        **base,
        "avg_dd": round(sum(dds) / len(dds), 4) if dds else None,
        "avg_hold_sec": round(sum(holds) / len(holds), 1) if holds else None,
    }


# ---------------------------------------------------------------------------
# Pattern miner atoms (Block 1)
# ---------------------------------------------------------------------------


def _atoms(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def add(name: str, label: str, fn: Predicate, *, family: str) -> None:
        out.append({"name": name, "label": label, "fn": fn, "family": family})

    add("funding_lt_0", "Funding<0", lambda r: (lambda f: None if f is None else f < 0)(_safe_float(r.get("funding"))), family="funding")
    add("funding_gt_0", "Funding>0", lambda r: (lambda f: None if f is None else f > 0)(_safe_float(r.get("funding"))), family="funding")

    add("fear_lt_25", "FearGreed<25", lambda r: (lambda f: None if f is None else f < 25)(_safe_float(r.get("fear_greed"))), family="fear")
    add("fear_lt_30", "FearGreed<30", lambda r: (lambda f: None if f is None else f < 30)(_safe_float(r.get("fear_greed"))), family="fear")
    add("fear_gt_70", "FearGreed>70", lambda r: (lambda f: None if f is None else f > 70)(_safe_float(r.get("fear_greed"))), family="fear")

    def ema200_up(r: dict[str, Any]) -> bool | None:
        ema = _safe_float(r.get("ema200"))
        entry = _safe_float(r.get("entry"))
        if ema is not None and entry is not None:
            return entry > ema
        trend = _safe_float(r.get("trend"))
        return None if trend is None else trend > 0

    add("btc_gt_ema200", "BTC>EMA200", ema200_up, family="ema")
    add(
        "btc_lt_ema200",
        "BTC<EMA200",
        lambda r: (lambda v: None if v is None else (not v))(ema200_up(r)),
        family="ema",
    )

    add(
        "rsi_lt_30",
        "RSI<30",
        lambda r: (lambda x: None if x is None else x < 30)(_safe_float(r.get("rsi"))),
        family="rsi",
    )
    add(
        "rsi_gt_70",
        "RSI>70",
        lambda r: (lambda x: None if x is None else x > 70)(_safe_float(r.get("rsi"))),
        family="rsi",
    )

    add(
        "hour_14_18",
        "Hour 14-18 UTC",
        lambda r: (lambda h: None if h is None else 14 <= h <= 18)(_hour(r)),
        family="session",
    )
    add(
        "hour_11_15",
        "Hour 11-15 UTC",
        lambda r: (lambda h: None if h is None else 11 <= h <= 15)(_hour(r)),
        family="session",
    )
    add(
        "asian_session",
        "Asian Session (0-6 UTC)",
        lambda r: (lambda h: None if h is None else 0 <= h <= 6)(_hour(r)),
        family="session",
    )

    add("dir_long", "Direction=LONG", lambda r: _dir(r) == "LONG" if _dir(r) else None, family="direction")
    add("dir_short", "Direction=SHORT", lambda r: _dir(r) == "SHORT" if _dir(r) else None, family="direction")

    regimes = sorted({
        str(r.get("market_regime") or "").upper()
        for r in rows
        if str(r.get("market_regime") or "").strip()
    })
    for reg in regimes[:8]:
        add(
            f"regime_{reg.lower()}",
            f"Regime={reg}",
            lambda r, _reg=reg: (
                None
                if not str(r.get("market_regime") or "").strip()
                else str(r.get("market_regime") or "").upper() == _reg
            ),
            family="regime",
        )

    counts: dict[str, int] = {}
    for r in rows:
        s = _sym(r)
        if s:
            counts[s] = counts.get(s, 0) + 1
    for sym, _n in sorted(counts.items(), key=lambda kv: -kv[1])[:12]:
        add(
            f"coin_{sym.lower()}",
            f"Coin={sym}",
            lambda r, _s=sym: _sym(r) == _s if _sym(r) else None,
            family="coin",
        )
    return out


def generate_patterns(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compose ALLOW / BLOCK pattern hypotheses (Block 1)."""
    atoms = _atoms(rows)
    by_f: dict[str, list[dict[str, Any]]] = {}
    for a in atoms:
        by_f.setdefault(a["family"], []).append(a)

    patterns: list[dict[str, Any]] = []
    seen: set[str] = set()

    def push(mode: str, parts: list[dict[str, Any]], *, title: str | None = None) -> None:
        if not parts:
            return
        labels = [p["label"] for p in parts]
        if mode == "BLOCK":
            rule = f"Disable / skip when {' + '.join(labels)}"
        else:
            rule = " + ".join(labels)
        if title:
            rule = title
        fp = _fingerprint(rule, mode)
        if fp in seen:
            return
        seen.add(fp)
        fns = [p["fn"] for p in parts]

        def pred(r: dict[str, Any], _fns=fns) -> bool | None:
            vals = [f(r) for f in _fns]
            if any(v is None for v in vals):
                return None
            return all(bool(v) for v in vals)

        patterns.append({
            "rule_text": rule,
            "mode": mode,
            "atoms": [p["name"] for p in parts],
            "fingerprint": fp,
            "fn": pred,
        })

    # Classic: Regime + EMA + Funding + Session + Direction
    for reg, ema, fund, sess, d in itertools.product(
        (by_f.get("regime") or [])[:5],
        by_f.get("ema") or [],
        [a for a in (by_f.get("funding") or []) if a["name"] == "funding_lt_0"],
        [a for a in (by_f.get("session") or []) if a["name"] == "hour_14_18"],
        by_f.get("direction") or [],
    ):
        push("ALLOW", [reg, ema, fund, sess, d])

    # Coin + RSI + Fear
    for coin, rsi, fear in itertools.product(
        (by_f.get("coin") or [])[:8],
        [a for a in (by_f.get("rsi") or []) if a["name"] == "rsi_lt_30"],
        [a for a in (by_f.get("fear") or []) if a["name"] == "fear_lt_25"],
    ):
        push("ALLOW", [coin, rsi, fear])

    # Coin + Direction + Asian (ALLOW and BLOCK)
    for coin, d, sess in itertools.product(
        (by_f.get("coin") or [])[:10],
        by_f.get("direction") or [],
        [a for a in (by_f.get("session") or []) if a["name"] == "asian_session"],
    ):
        push("ALLOW", [coin, d, sess])
        push(
            "BLOCK",
            [coin, d, sess],
            title=f"Disable {d['label'].replace('Direction=', '')} Coin={coin['label'].replace('Coin=', '')} Asian Session",
        )

    # Shorter 2–3 way combos
    for family_a, family_b in (
        ("funding", "direction"),
        ("fear", "direction"),
        ("session", "direction"),
        ("regime", "direction"),
        ("ema", "direction"),
        ("coin", "session"),
    ):
        for a, b in itertools.product(by_f.get(family_a) or [], by_f.get(family_b) or []):
            push("ALLOW", [a, b])

    for coin in (by_f.get("coin") or [])[:10]:
        push("BLOCK", [coin], title=f"Disable trading Coin={coin['label'].replace('Coin=', '')}")

    if len(patterns) > S62_MAX_PATTERNS:
        patterns = patterns[:S62_MAX_PATTERNS]
    return patterns


# ---------------------------------------------------------------------------
# Delta + apply pattern (Block 2)
# ---------------------------------------------------------------------------


def _split_by_pred(
    rows: list[dict[str, Any]],
    fn: Predicate,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    skipped = 0
    for r in rows:
        v = fn(r)
        if v is None:
            skipped += 1
            continue
        (matched if v else unmatched).append(r)
    return matched, unmatched, skipped


def apply_pattern_book(
    rows: list[dict[str, Any]],
    pattern: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Return (strategy_rows, matched, skipped)."""
    matched, unmatched, skipped = _split_by_pred(rows, pattern["fn"])
    if pattern["mode"] == "BLOCK":
        return unmatched, matched, skipped
    return matched, matched, skipped


def delta_analyze(
    base_rows: list[dict[str, Any]],
    strategy_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Base → Pattern Enabled → Difference (Block 2)."""
    base_m = pattern_metrics(base_rows)
    strat_m = pattern_metrics(strategy_rows)
    return {
        "base": base_m,
        "enabled": strat_m,
        "delta_expectancy": _delta(strat_m.get("expectancy"), base_m.get("expectancy")),
        "delta_pf": _delta(_pf_value(strat_m), _pf_value(base_m)),
        "delta_sharpe": _delta(strat_m.get("sharpe"), base_m.get("sharpe")),
        "delta_wr": _delta(strat_m.get("winrate"), base_m.get("winrate")),
        "delta_avg_dd": _delta(strat_m.get("avg_dd"), base_m.get("avg_dd")),
        "delta_avg_hold_sec": _delta(strat_m.get("avg_hold_sec"), base_m.get("avg_hold_sec")),
    }


# ---------------------------------------------------------------------------
# Stability (Block 3)
# ---------------------------------------------------------------------------


def stability_check(
    rows: list[dict[str, Any]],
    pattern: dict[str, Any],
    *,
    now: int,
    min_n: int | None = None,
) -> dict[str, Any]:
    """Require non-degrading improvement across 30/60/90d + full history."""
    need = int(min_n if min_n is not None else S62_MIN_N)
    windows: dict[str, Any] = {}
    improving = 0
    tested = 0

    def _window(label: str, subset: list[dict[str, Any]]) -> None:
        nonlocal improving, tested
        if len(subset) < max(10, need // 3):
            windows[label] = {"n": len(subset), "ok": None, "reason": "insufficient"}
            return
        strat, _m, _s = apply_pattern_book(subset, pattern)
        if len(strat) < max(8, need // 4):
            windows[label] = {"n": len(subset), "n_strategy": len(strat), "ok": None, "reason": "thin"}
            return
        d = delta_analyze(subset, strat)
        tested += 1
        ok = (d.get("delta_expectancy") or 0) >= 0 and (
            (d.get("delta_pf") or 0) >= -0.02 or (d.get("delta_expectancy") or 0) > 0
        )
        if ok and ((d.get("delta_expectancy") or 0) > 0 or (d.get("delta_pf") or 0) > 0):
            improving += 1
        windows[label] = {
            "n": len(subset),
            "n_strategy": len(strat),
            "delta_expectancy": d.get("delta_expectancy"),
            "delta_pf": d.get("delta_pf"),
            "ok": ok,
        }

    for days in S62_WINDOWS_DAYS:
        cutoff = now - days * DAY
        subset = [r for r in rows if _trade_ts(r) >= cutoff]
        _window(f"last_{days}d", subset)
    _window("full", rows)

    degrade = any(w.get("ok") is False for w in windows.values())
    stable = improving >= 2 and not degrade and tested >= 2
    return {
        "windows": windows,
        "improving_windows": improving,
        "tested_windows": tested,
        "stable": stable,
    }


# ---------------------------------------------------------------------------
# Walk-forward (Block 4)
# ---------------------------------------------------------------------------


def walk_forward_split(
    rows: list[dict[str, Any]],
    *,
    min_n: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    need = int(min_n if min_n is not None else S62_MIN_N)
    ordered = sorted(rows, key=_trade_ts)
    n = len(ordered)
    if n < need:
        return {"train": ordered, "validation": [], "test": []}
    i_train = max(1, int(n * S62_TRAIN_FRAC))
    i_val = max(i_train + 1, int(n * (S62_TRAIN_FRAC + S62_VAL_FRAC)))
    i_val = min(i_val, n - 1) if n > i_train + 1 else i_train
    return {
        "train": ordered[:i_train],
        "validation": ordered[i_train:i_val],
        "test": ordered[i_val:],
    }


def walk_forward_validate(
    rows: list[dict[str, Any]],
    pattern: dict[str, Any],
    *,
    min_n: int | None = None,
) -> dict[str, Any]:
    need = int(min_n if min_n is not None else S62_MIN_N)
    parts = walk_forward_split(rows, min_n=need)
    out: dict[str, Any] = {"splits": {}}
    for name, subset in parts.items():
        if len(subset) < max(8, need // 4):
            out["splits"][name] = {"n": len(subset), "ok": None, "reason": "thin"}
            continue
        strat, _m, _s = apply_pattern_book(subset, pattern)
        if len(strat) < max(5, need // 5):
            out["splits"][name] = {"n": len(subset), "n_strategy": len(strat), "ok": None, "reason": "thin_strategy"}
            continue
        d = delta_analyze(subset, strat)
        ok = (d.get("delta_expectancy") or 0) > 0 or (d.get("delta_pf") or 0) > 0.05
        out["splits"][name] = {
            "n": len(subset),
            "n_strategy": len(strat),
            "delta_expectancy": d.get("delta_expectancy"),
            "delta_pf": d.get("delta_pf"),
            "ok": ok,
        }

    train_ok = bool((out["splits"].get("train") or {}).get("ok"))
    val = out["splits"].get("validation") or {}
    test = out["splits"].get("test") or {}
    val_ok = val.get("ok")
    test_ok = test.get("ok")
    test_degrade = test_ok is False
    passed = train_ok and (val_ok is True or test_ok is True) and not test_degrade
    if val_ok is None and test_ok is None:
        passed = train_ok
    out["passed"] = passed
    return out


# ---------------------------------------------------------------------------
# Ranking (Block 5)
# ---------------------------------------------------------------------------


def assign_confidence(
    *,
    n_strategy: int,
    delta: dict[str, Any],
    stability: dict[str, Any],
    walk: dict[str, Any],
    min_n: int | None = None,
) -> str:
    """A / B / C from sample size, stability, improvement, OOS."""
    need = int(min_n if min_n is not None else S62_MIN_N)
    d_exp = delta.get("delta_expectancy") or 0
    d_pf = delta.get("delta_pf") or 0
    stable = bool(stability.get("stable"))
    wf = bool(walk.get("passed"))
    score = 0
    if n_strategy >= need * 2:
        score += 2
    elif n_strategy >= need:
        score += 1
    if d_exp > 0.5 or d_pf > 0.2:
        score += 2
    elif d_exp > 0 or d_pf > 0.05:
        score += 1
    if stable:
        score += 2
    if wf:
        score += 2
    if score >= 7:
        return "A"
    if score >= 4:
        return "B"
    return "C"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def evaluate_pattern(
    rows: list[dict[str, Any]],
    pattern: dict[str, Any],
    *,
    now: int,
    min_n: int | None = None,
) -> dict[str, Any] | None:
    refresh_s62_config_from_env()
    need = int(min_n if min_n is not None else S62_MIN_N)
    strat, matched, skipped = apply_pattern_book(rows, pattern)
    if pattern["mode"] == "ALLOW" and len(matched) < need:
        return None
    if pattern["mode"] == "BLOCK" and len(matched) < max(15, need // 2):
        return None
    if len(strat) < need:
        return None

    delta = delta_analyze(rows, strat)
    if (delta.get("delta_expectancy") or 0) <= 0 and (delta.get("delta_pf") or 0) <= 0:
        return None

    # Temporarily use need for stability thin checks via env-less path
    stability = stability_check(rows, pattern, now=now, min_n=need)
    walk = walk_forward_validate(rows, pattern, min_n=need)

    if not walk.get("passed") and not stability.get("stable"):
        return None

    conf = assign_confidence(
        n_strategy=len(strat),
        delta=delta,
        stability=stability,
        walk=walk,
        min_n=need,
    )
    if conf == "C" and not (walk.get("passed") and stability.get("stable")):
        return None

    enabled = delta["enabled"]
    base = delta["base"]
    return {
        "rule_text": pattern["rule_text"],
        "mode": pattern["mode"],
        "atoms": pattern.get("atoms") or [],
        "fingerprint": pattern["fingerprint"],
        "n_matched": len(matched),
        "n_strategy": len(strat),
        "n_baseline": len(rows),
        "skipped": skipped,
        "metrics": enabled,
        "baseline": base,
        "delta_expectancy": delta.get("delta_expectancy"),
        "delta_pf": delta.get("delta_pf"),
        "delta_sharpe": delta.get("delta_sharpe"),
        "delta_wr": delta.get("delta_wr"),
        "delta_avg_dd": delta.get("delta_avg_dd"),
        "avg_dd": enabled.get("avg_dd"),
        "avg_hold_sec": enabled.get("avg_hold_sec"),
        "stability": stability,
        "walk_forward": walk,
        "confidence": conf,
        "score": round(
            (delta.get("delta_expectancy") or 0) * 2
            + (delta.get("delta_pf") or 0) * 1.5
            + (delta.get("delta_sharpe") or 0)
            + ({"A": 3, "B": 1.5, "C": 0}.get(conf, 0))
            + math.log1p(len(strat)) * 0.1,
            4,
        ),
    }


def run_alpha_discovery(
    conn: Any,
    *,
    now: int | None = None,
    top_n: int | None = None,
    write_suggestions: bool | None = None,
    min_n: int | None = None,
) -> dict[str, Any]:
    """Mine → delta → stability → walk-forward → rank → persist (Blocks 1–6)."""
    refresh_s62_config_from_env()
    now = int(now if now is not None else time.time())
    top_n = int(top_n if top_n is not None else S62_TOP_N)
    do_suggest = S62_WRITE_SUGGESTIONS if write_suggestions is None else bool(write_suggestions)
    need = int(min_n if min_n is not None else S62_MIN_N)

    rows = load_lab_trades(conn)

    patterns = generate_patterns(rows)
    evaluated: list[dict[str, Any]] = []
    for p in patterns:
        try:
            out = evaluate_pattern(rows, p, now=now, min_n=need)
        except Exception as exc:
            logger.debug("s62 evaluate failed %s: %s", p.get("rule_text"), exc)
            continue
        if out is not None:
            evaluated.append(out)

    ranked = sorted(
        evaluated,
        key=lambda x: (
            {"A": 0, "B": 1, "C": 2}.get(str(x.get("confidence")), 9),
            -(x.get("score") or -999),
            -(x.get("delta_pf") or -999),
        ),
    )
    top = ranked[:top_n]
    baseline = pattern_metrics(rows)

    cur = execute_with_retry(
        conn,
        f"""
        INSERT INTO {_RUNS} (
          n_trades, n_patterns, n_evaluated, top_n, min_n,
          baseline_json, results_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            len(rows),
            len(patterns),
            len(evaluated),
            top_n,
            need,
            json.dumps(baseline, default=str),
            json.dumps(top, default=str),
            now,
        ),
    )
    run_id = None
    try:
        run_id = int(cur.lastrowid)
    except Exception:
        try:
            run_id = int(conn.execute(f"SELECT MAX(id) AS i FROM {_RUNS}").fetchone()["i"])
        except Exception:
            run_id = None

    if run_id is not None:
        for rank, item in enumerate(top, start=1):
            m, b = item["metrics"], item["baseline"]
            execute_with_retry(
                conn,
                f"""
                INSERT INTO {_RESULTS} (
                  run_id, hypothesis_no, rank, fingerprint, rule_text, mode,
                  n_matched, n_strategy, n_baseline,
                  expectancy, pf, sharpe, winrate, avg_dd, avg_hold_sec,
                  base_expectancy, base_pf, base_sharpe, base_winrate,
                  delta_expectancy, delta_pf, delta_sharpe, delta_wr,
                  confidence, score, stability_json, walk_forward_json,
                  atoms_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    rank,
                    rank,
                    item["fingerprint"],
                    item["rule_text"],
                    item["mode"],
                    item["n_matched"],
                    item["n_strategy"],
                    item["n_baseline"],
                    m.get("expectancy"),
                    m.get("pf"),
                    m.get("sharpe"),
                    m.get("winrate"),
                    m.get("avg_dd"),
                    m.get("avg_hold_sec"),
                    b.get("expectancy"),
                    b.get("pf"),
                    b.get("sharpe"),
                    b.get("winrate"),
                    item.get("delta_expectancy"),
                    item.get("delta_pf"),
                    item.get("delta_sharpe"),
                    item.get("delta_wr"),
                    item.get("confidence"),
                    item.get("score"),
                    json.dumps(item.get("stability") or {}, default=str),
                    json.dumps(item.get("walk_forward") or {}, default=str),
                    json.dumps(item.get("atoms") or []),
                    now,
                ),
            )

    suggestions_created = 0
    if do_suggest and run_id is not None:
        suggestions_created = _write_human_approvals(conn, run_id=run_id, top=top, now=now)

    return {
        "run_id": run_id,
        "n_trades": len(rows),
        "n_patterns": len(patterns),
        "n_evaluated": len(evaluated),
        "top_n": top_n,
        "min_n": need,
        "baseline": baseline,
        "top": top,
        "suggestions_created": suggestions_created,
        "suggestions_enabled": do_suggest,
    }


def _write_human_approvals(
    conn: Any,
    *,
    run_id: int,
    top: list[dict[str, Any]],
    now: int,
) -> int:
    """Block 6 — WAITING_APPROVAL only; never auto-apply."""
    created = 0
    for item in top:
        if item.get("confidence") not in ("A", "B"):
            continue
        evidence = {
            "hypothesis_no": None,
            "confidence": item.get("confidence"),
            "delta_pf": item.get("delta_pf"),
            "delta_expectancy": item.get("delta_expectancy"),
            "stability": item.get("stability"),
            "walk_forward": {
                "passed": (item.get("walk_forward") or {}).get("passed"),
                "splits": {
                    k: {kk: vv for kk, vv in (v or {}).items() if kk != "reason"}
                    for k, v in ((item.get("walk_forward") or {}).get("splits") or {}).items()
                },
            },
            "fingerprint": item.get("fingerprint"),
            "mode": item.get("mode"),
        }
        try:
            execute_with_retry(
                conn,
                """
                INSERT INTO market_events_rule_suggestions_s56 (
                  run_id, rule_text, evidence_json, evidence_trades,
                  expected_improvement_pct, confidence_pct, status, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'WAITING_APPROVAL', 'alpha_discovery_s62', ?)
                """,
                (
                    run_id,
                    item["rule_text"],
                    json.dumps(evidence, default=str),
                    int(item.get("n_strategy") or 0),
                    float(item.get("delta_expectancy") or 0),
                    90.0 if item.get("confidence") == "A" else 70.0,
                    now,
                ),
            )
            created += 1
        except Exception as exc:
            logger.warning("s62 suggestion insert failed: %s", exc)
    return created


def latest_alpha_rows(conn: Any, *, run_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    run = None
    if run_id is not None:
        run = conn.execute(f"SELECT id FROM {_RUNS} WHERE id = ?", (int(run_id),)).fetchone()
    if run is None:
        run = conn.execute(f"SELECT id FROM {_RUNS} ORDER BY id DESC LIMIT 1").fetchone()
    if not run:
        return []
    rows = conn.execute(
        f"SELECT * FROM {_RESULTS} WHERE run_id = ? ORDER BY rank ASC LIMIT ?",
        (int(run["id"]), int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def format_alpha_discovery_report(conn: Any, *, run_id: int | None = None) -> str:
    refresh_s62_config_from_env()
    run = None
    if run_id is not None:
        run = conn.execute(f"SELECT * FROM {_RUNS} WHERE id = ?", (int(run_id),)).fetchone()
    if run is None:
        run = conn.execute(f"SELECT * FROM {_RUNS} ORDER BY id DESC LIMIT 1").fetchone()
    if not run:
        return (
            "S62 Alpha Discovery Engine\n"
            "  (no runs yet — python -m bot.research.market_events alpha-discovery --force)\n"
            "  Observe-only. Human approval required. Never auto-applies."
        )
    try:
        top = json.loads(run["results_json"] or "[]")
    except Exception:
        top = []
    try:
        baseline = json.loads(run["baseline_json"] or "{}")
    except Exception:
        baseline = {}
    return _format_results(
        top,
        n_trades=int(run["n_trades"] or 0),
        n_patterns=int(run["n_patterns"] or 0),
        n_evaluated=int(run["n_evaluated"] or 0),
        min_n=int(run["min_n"] or S62_MIN_N),
        baseline=baseline,
        run_id=int(run["id"]),
    )


def _fmt(v: Any, *, plus: bool = False, digits: int = 2) -> str:
    if v is None:
        return "—"
    x = float(v)
    return f"{x:+.{digits}f}" if plus else f"{x:.{digits}f}"


def _format_results(
    top: list[dict[str, Any]],
    *,
    n_trades: int,
    n_patterns: int,
    n_evaluated: int,
    min_n: int,
    baseline: dict[str, Any],
    run_id: int | None,
) -> str:
    lines = [
        "S62 Alpha Discovery Engine",
        f"  run=#{run_id or '—'}  trades={n_trades}  patterns={n_patterns}  "
        f"passed_gates={n_evaluated}  shown={len(top)}  min_n={min_n}",
        f"  base: E={_fmt(baseline.get('expectancy'))}$  "
        f"PF={_fmt(baseline.get('pf'))}  Sharpe={_fmt(baseline.get('sharpe'), digits=3)}  "
        f"WR={_fmt(baseline.get('winrate'))}%",
        "",
        "  #   Conf  ΔPF     ΔE($)   n     Stable  WF    Rule",
        "  --  ----  ------  ------  ----  ------  ----  ----",
    ]
    if not top:
        lines.append("  (no hypotheses passed stability + walk-forward gates)")
    for i, item in enumerate(top, start=1):
        st = "Y" if (item.get("stability") or {}).get("stable") else "N"
        wf = "Y" if (item.get("walk_forward") or {}).get("passed") else "N"
        lines.append(
            f"  {i:<2}  {str(item.get('confidence') or '—'):<4}  "
            f"{_fmt(item.get('delta_pf'), plus=True):>6}  "
            f"{_fmt(item.get('delta_expectancy'), plus=True):>6}  "
            f"{int(item.get('n_strategy') or 0):<4}  "
            f"{st:<6}  {wf:<4}  {item.get('rule_text')}"
        )
        # Human-readable proposal block for top A/B
        if i <= 5 and item.get("confidence") in ("A", "B"):
            lines.extend([
                f"      Hypothesis #{i}",
                f"      {item.get('rule_text')}",
                f"      Confidence {item.get('confidence')}  "
                f"Expected PF {_fmt(item.get('delta_pf'), plus=True)}  "
                f"Expected E {_fmt(item.get('delta_expectancy'), plus=True)}$",
                "      → Human approval required (no auto-apply)",
            ])
    lines.extend([
        "",
        "  Gates: min_n · 30/60/90d stability · train/val/test walk-forward · rank A/B/C",
        "  Observe-only — never changes strategy. Approve via trade-suggestions if enabled.",
    ])
    return "\n".join(lines)


def format_s62_report_block(conn: Any) -> list[str]:
    try:
        return format_alpha_discovery_report(conn).splitlines()[:12]
    except Exception as exc:
        return [f"S62 Alpha Discovery: unavailable ({exc})"]


__all__ = [
    "assign_confidence",
    "delta_analyze",
    "evaluate_pattern",
    "format_alpha_discovery_report",
    "format_s62_report_block",
    "generate_patterns",
    "latest_alpha_rows",
    "pattern_metrics",
    "refresh_s62_config_from_env",
    "run_alpha_discovery",
    "stability_check",
    "walk_forward_validate",
]
