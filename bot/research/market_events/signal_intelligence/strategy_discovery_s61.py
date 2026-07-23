"""S61 — Strategy Discovery Engine.

Automatically generate filter hypotheses on closed paper-trade snapshots,
evaluate Expectancy / PF / Sharpe vs baseline, rank, and surface only the best.

Observe-only. Never auto-applies strategy changes.
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

_RUNS = "market_events_strategy_discovery_runs_s61"
_RESULTS = "market_events_strategy_discovery_s61"

S61_MIN_N = 20
S61_TOP_N = 25
S61_MAX_HYPOTHESES = 400
S61_MIN_DELTA_EXPECTANCY = 0.0
S61_WRITE_SUGGESTIONS = False
S61_LLM_ENABLED = False

Predicate = Callable[[dict[str, Any]], bool | None]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def refresh_s61_config_from_env() -> None:
    global S61_MIN_N, S61_TOP_N, S61_MAX_HYPOTHESES, S61_MIN_DELTA_EXPECTANCY
    global S61_WRITE_SUGGESTIONS, S61_LLM_ENABLED
    if "S61_MIN_N" in os.environ:
        try:
            S61_MIN_N = max(5, int(os.environ["S61_MIN_N"]))
        except (TypeError, ValueError):
            pass
    if "S61_TOP_N" in os.environ:
        try:
            S61_TOP_N = max(5, int(os.environ["S61_TOP_N"]))
        except (TypeError, ValueError):
            pass
    if "S61_MAX_HYPOTHESES" in os.environ:
        try:
            S61_MAX_HYPOTHESES = max(50, int(os.environ["S61_MAX_HYPOTHESES"]))
        except (TypeError, ValueError):
            pass
    if "S61_MIN_DELTA_EXPECTANCY" in os.environ:
        try:
            S61_MIN_DELTA_EXPECTANCY = float(os.environ["S61_MIN_DELTA_EXPECTANCY"])
        except (TypeError, ValueError):
            pass
    if "S61_WRITE_SUGGESTIONS" in os.environ:
        S61_WRITE_SUGGESTIONS = _env_bool("S61_WRITE_SUGGESTIONS", False)
    if "S61_LLM_ENABLED" in os.environ:
        S61_LLM_ENABLED = _env_bool("S61_LLM_ENABLED", False)


def _apply_defaults() -> None:
    global S61_MIN_N, S61_TOP_N, S61_MAX_HYPOTHESES, S61_MIN_DELTA_EXPECTANCY
    global S61_WRITE_SUGGESTIONS, S61_LLM_ENABLED
    try:
        S61_MIN_N = max(5, int(os.environ.get("S61_MIN_N", "20")))
    except (TypeError, ValueError):
        S61_MIN_N = 20
    try:
        S61_TOP_N = max(5, int(os.environ.get("S61_TOP_N", "25")))
    except (TypeError, ValueError):
        S61_TOP_N = 25
    try:
        S61_MAX_HYPOTHESES = max(50, int(os.environ.get("S61_MAX_HYPOTHESES", "400")))
    except (TypeError, ValueError):
        S61_MAX_HYPOTHESES = 400
    try:
        S61_MIN_DELTA_EXPECTANCY = float(os.environ.get("S61_MIN_DELTA_EXPECTANCY", "0"))
    except (TypeError, ValueError):
        S61_MIN_DELTA_EXPECTANCY = 0.0
    S61_WRITE_SUGGESTIONS = _env_bool("S61_WRITE_SUGGESTIONS", False)
    S61_LLM_ENABLED = _env_bool("S61_LLM_ENABLED", False)


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


def _fingerprint(rule_text: str, mode: str, direction: str | None) -> str:
    raw = json.dumps(
        {"rule": rule_text, "mode": mode, "direction": direction or ""},
        sort_keys=True,
    )
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
# Atomic predicates
# ---------------------------------------------------------------------------


def _atomic_predicates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Named atomic filters used to compose hypotheses."""
    atoms: list[dict[str, Any]] = []

    def add(name: str, label: str, fn: Predicate, *, family: str) -> None:
        atoms.append({"name": name, "label": label, "fn": fn, "family": family})

    add("funding_lt_0", "Funding<0", lambda r: (lambda f: None if f is None else f < 0)(_safe_float(r.get("funding"))), family="funding")
    add("funding_gt_0", "Funding>0", lambda r: (lambda f: None if f is None else f > 0)(_safe_float(r.get("funding"))), family="funding")
    add(
        "funding_favor",
        "Funding favorable",
        lambda r: (
            None
            if _safe_float(r.get("funding")) is None
            else (
                (_dir(r) == "LONG" and float(r["funding"]) < 0)
                or (_dir(r) == "SHORT" and float(r["funding"]) > 0)
            )
        ),
        family="funding",
    )

    add("fear_lt_30", "Fear<30", lambda r: (lambda f: None if f is None else f < 30)(_safe_float(r.get("fear_greed"))), family="fear")
    add("fear_lt_40", "Fear<40", lambda r: (lambda f: None if f is None else f < 40)(_safe_float(r.get("fear_greed"))), family="fear")
    add("fear_gt_70", "Fear>70", lambda r: (lambda f: None if f is None else f > 70)(_safe_float(r.get("fear_greed"))), family="fear")

    def ema200_up(r: dict[str, Any]) -> bool | None:
        ema = _safe_float(r.get("ema200"))
        entry = _safe_float(r.get("entry"))
        if ema is not None and entry is not None:
            return entry > ema
        trend = _safe_float(r.get("trend"))
        if trend is None:
            return None
        return trend > 0

    def ema200_down(r: dict[str, Any]) -> bool | None:
        v = ema200_up(r)
        return None if v is None else (not v)

    add("ema200_up", "BTC EMA200↑", ema200_up, family="ema")
    add("ema200_down", "BTC EMA200↓", ema200_down, family="ema")

    add(
        "hour_11_15",
        "Hour 11-15 UTC",
        lambda r: (lambda h: None if h is None else 11 <= h <= 15)(_hour(r)),
        family="hour",
    )
    add(
        "hour_7_20",
        "Hour 7-20 UTC",
        lambda r: (lambda h: None if h is None else 7 <= h <= 20)(_hour(r)),
        family="hour",
    )
    add(
        "hour_asian",
        "Hour 0-6 UTC",
        lambda r: (lambda h: None if h is None else 0 <= h <= 6)(_hour(r)),
        family="hour",
    )

    add(
        "weekday_mon_thu",
        "Weekday Mon-Thu",
        lambda r: (
            None
            if r.get("weekday") is None
            else int(r["weekday"]) in (0, 1, 2, 3)
        ),
        family="weekday",
    )

    add(
        "ai_ge_06",
        "AI≥0.6",
        lambda r: (lambda a: None if a is None else a >= 0.6)(_safe_float(r.get("ai_score"))),
        family="ai",
    )
    add(
        "ai_lt_04",
        "AI<0.4",
        lambda r: (lambda a: None if a is None else a < 0.4)(_safe_float(r.get("ai_score"))),
        family="ai",
    )

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

    # Top symbols by trade count
    counts: dict[str, int] = {}
    for r in rows:
        s = _sym(r)
        if s:
            counts[s] = counts.get(s, 0) + 1
    top_syms = [s for s, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:12]]
    for sym in top_syms:
        add(
            f"symbol_{sym.lower()}",
            f"Symbol={sym}",
            lambda r, _s=sym: _sym(r) == _s if _sym(r) else None,
            family="symbol",
        )

    return atoms


def generate_hypotheses(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compose ALLOW / BLOCK hypotheses from atomic predicates.

    Examples encoded:
      Funding<0 + EMA200↑ + Fear<30 → LONG ALLOW
      Symbol=NEAR + SHORT + Hour 11-15 → ALLOW
      Symbol=ARB → BLOCK (do not trade)
    """
    atoms = _atomic_predicates(rows)
    by_family: dict[str, list[dict[str, Any]]] = {}
    for a in atoms:
        by_family.setdefault(a["family"], []).append(a)

    hyps: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _push(
        *,
        mode: str,
        direction: str | None,
        parts: list[dict[str, Any]],
        title: str | None = None,
    ) -> None:
        if not parts:
            return
        labels = [p["label"] for p in parts]
        rule = " + ".join(labels)
        if direction:
            rule = f"{rule} → {direction}"
        if mode == "BLOCK":
            rule = f"BLOCK if { ' + '.join(labels) }"
        fp = _fingerprint(rule, mode, direction)
        if fp in seen:
            return
        seen.add(fp)

        fns = [p["fn"] for p in parts]

        def pred(r: dict[str, Any], _fns=fns, _wanted=direction) -> bool | None:
            if _wanted and _dir(r) != _wanted:
                return False
            vals = [f(r) for f in _fns]
            if any(v is None for v in vals):
                return None
            return all(bool(v) for v in vals)

        hyps.append({
            "rule_text": title or rule,
            "mode": mode,
            "direction": direction,
            "atoms": [p["name"] for p in parts],
            "fingerprint": fp,
            "fn": pred,
        })

    # 1) Single-atom ALLOW for each direction
    for a in atoms:
        if a["family"] == "symbol":
            continue
        for d in ("LONG", "SHORT"):
            _push(mode="ALLOW", direction=d, parts=[a])

    # 2) Classic combo: Funding + EMA + Fear → LONG / SHORT
    fund = by_family.get("funding") or []
    ema = by_family.get("ema") or []
    fear = by_family.get("fear") or []
    for f, e, fe in itertools.product(
        [a for a in fund if a["name"] in ("funding_lt_0", "funding_gt_0", "funding_favor")],
        ema,
        [a for a in fear if a["name"] in ("fear_lt_30", "fear_lt_40", "fear_gt_70")],
    ):
        _push(mode="ALLOW", direction="LONG", parts=[f, e, fe])
        _push(mode="ALLOW", direction="SHORT", parts=[f, e, fe])

    # 3) Symbol + direction + hour
    hour = by_family.get("hour") or []
    symbols = by_family.get("symbol") or []
    for s, h in itertools.product(symbols[:8], hour):
        for d in ("LONG", "SHORT"):
            _push(mode="ALLOW", direction=d, parts=[s, h])

    # 4) Symbol BLOCK (do not trade this symbol at all)
    for s in symbols[:10]:
        _push(
            mode="BLOCK",
            direction=None,
            parts=[s],
            title=f"Do not trade {s['label'].replace('Symbol=', '')}",
        )

    # 5) Regime + direction
    for reg in by_family.get("regime") or []:
        for d in ("LONG", "SHORT"):
            _push(mode="ALLOW", direction=d, parts=[reg])

    # 6) Funding favorable + hour
    fav = next((a for a in fund if a["name"] == "funding_favor"), None)
    if fav:
        for h in hour:
            for d in ("LONG", "SHORT"):
                _push(mode="ALLOW", direction=d, parts=[fav, h])

    # Cap
    if len(hyps) > S61_MAX_HYPOTHESES:
        hyps = hyps[:S61_MAX_HYPOTHESES]
    return hyps


def evaluate_hypothesis(
    rows: list[dict[str, Any]],
    hyp: dict[str, Any],
    *,
    baseline_all: dict[str, Any],
) -> dict[str, Any] | None:
    """Score one hypothesis. Returns None if insufficient evidence."""
    mode = hyp["mode"]
    direction = hyp.get("direction")
    fn: Predicate = hyp["fn"]

    # Scope baseline to direction when rule is direction-specific
    scoped = rows
    if direction:
        scoped = [r for r in rows if _dir(r) == direction]
    base_m = lab_bucket_metrics(scoped) if direction else baseline_all

    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    skipped = 0
    for r in scoped:
        v = fn(r)
        if v is None:
            skipped += 1
            continue
        if v:
            matched.append(r)
        else:
            unmatched.append(r)

    if mode == "ALLOW":
        # Taking only matched trades vs taking all scoped
        strategy_rows = matched
        compare_m = base_m
    else:
        # BLOCK: remove matched trades from book
        strategy_rows = unmatched
        compare_m = base_m

    if len(strategy_rows) < S61_MIN_N:
        return None
    # For ALLOW also require matched itself has enough; for BLOCK strategy is unmatched
    if mode == "ALLOW" and len(matched) < S61_MIN_N:
        return None
    if mode == "BLOCK" and len(matched) < max(8, S61_MIN_N // 2):
        return None

    strat_m = lab_bucket_metrics(strategy_rows)
    d_exp = _delta(strat_m.get("expectancy"), compare_m.get("expectancy"))
    d_pf = _delta(_pf_value(strat_m), _pf_value(compare_m))
    d_sh = _delta(strat_m.get("sharpe"), compare_m.get("sharpe"))
    d_wr = _delta(strat_m.get("winrate"), compare_m.get("winrate"))

    if d_exp is None or d_exp < S61_MIN_DELTA_EXPECTANCY:
        # Still keep strong PF/Sharpe improvements with flat expectancy
        if not (
            (d_pf is not None and d_pf > 0.05)
            or (d_sh is not None and d_sh > 0.05)
        ):
            return None

    # Rank score: prioritize expectancy, then PF, then Sharpe, then sample size
    score = 0.0
    if d_exp is not None:
        score += d_exp * 2.0
    if d_pf is not None:
        score += d_pf * 1.5
    if d_sh is not None:
        score += d_sh * 1.0
    score += math.log1p(len(strategy_rows)) * 0.15

    conf = "LOW"
    n = len(strategy_rows)
    if n >= S61_MIN_N * 3 and (d_exp or 0) > 0 and (d_pf or 0) > 0:
        conf = "HIGH"
    elif n >= S61_MIN_N * 1.5 and ((d_exp or 0) > 0 or (d_pf or 0) > 0):
        conf = "MEDIUM"

    return {
        "rule_text": hyp["rule_text"],
        "mode": mode,
        "direction": direction,
        "atoms": hyp.get("atoms") or [],
        "fingerprint": hyp["fingerprint"],
        "n_matched": len(matched),
        "n_strategy": len(strategy_rows),
        "n_baseline": int(compare_m.get("n") or 0),
        "skipped": skipped,
        "strategy": strat_m,
        "baseline": compare_m,
        "delta_expectancy": d_exp,
        "delta_pf": d_pf,
        "delta_sharpe": d_sh,
        "delta_wr": d_wr,
        "score": round(score, 4),
        "confidence": conf,
    }


def run_strategy_discovery(
    conn: Any,
    *,
    now: int | None = None,
    top_n: int | None = None,
    write_suggestions: bool | None = None,
) -> dict[str, Any]:
    """Generate → evaluate → rank → persist top hypotheses."""
    refresh_s61_config_from_env()
    now = int(now if now is not None else time.time())
    top_n = int(top_n if top_n is not None else S61_TOP_N)
    do_suggest = S61_WRITE_SUGGESTIONS if write_suggestions is None else bool(write_suggestions)

    rows = load_lab_trades(conn)
    baseline_all = lab_bucket_metrics(rows)
    hyps = generate_hypotheses(rows)

    evaluated: list[dict[str, Any]] = []
    for h in hyps:
        try:
            out = evaluate_hypothesis(rows, h, baseline_all=baseline_all)
        except Exception as exc:
            logger.debug("s61 evaluate failed %s: %s", h.get("rule_text"), exc)
            continue
        if out is not None:
            evaluated.append(out)

    ranked = sorted(
        evaluated,
        key=lambda x: (
            -(x.get("score") or -999),
            -(x.get("delta_expectancy") or -999),
            -(x.get("delta_pf") or -999),
        ),
    )
    top = ranked[:top_n]

    cur = execute_with_retry(
        conn,
        f"""
        INSERT INTO {_RUNS} (
          n_trades, n_hypotheses, n_evaluated, top_n,
          baseline_json, results_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            len(rows),
            len(hyps),
            len(evaluated),
            top_n,
            json.dumps(baseline_all, default=str),
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
            sm, bm = item["strategy"], item["baseline"]
            execute_with_retry(
                conn,
                f"""
                INSERT INTO {_RESULTS} (
                  run_id, rank, fingerprint, rule_text, mode, direction,
                  n_matched, n_strategy, n_baseline,
                  strategy_expectancy, strategy_pf, strategy_sharpe, strategy_winrate,
                  baseline_expectancy, baseline_pf, baseline_sharpe, baseline_winrate,
                  delta_expectancy, delta_pf, delta_sharpe, delta_wr,
                  score, confidence, atoms_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    rank,
                    item["fingerprint"],
                    item["rule_text"],
                    item["mode"],
                    item.get("direction"),
                    item["n_matched"],
                    item["n_strategy"],
                    item["n_baseline"],
                    sm.get("expectancy"),
                    sm.get("pf"),
                    sm.get("sharpe"),
                    sm.get("winrate"),
                    bm.get("expectancy"),
                    bm.get("pf"),
                    bm.get("sharpe"),
                    bm.get("winrate"),
                    item.get("delta_expectancy"),
                    item.get("delta_pf"),
                    item.get("delta_sharpe"),
                    item.get("delta_wr"),
                    item.get("score"),
                    item.get("confidence"),
                    json.dumps(item.get("atoms") or []),
                    now,
                ),
            )

    suggestions_created = 0
    if do_suggest and run_id is not None and top:
        suggestions_created = _write_waiting_suggestions(conn, run_id=run_id, top=top, now=now)

    return {
        "run_id": run_id,
        "n_trades": len(rows),
        "n_hypotheses": len(hyps),
        "n_evaluated": len(evaluated),
        "top_n": top_n,
        "baseline": baseline_all,
        "top": top,
        "suggestions_created": suggestions_created,
        "suggestions_enabled": do_suggest,
    }


def _write_waiting_suggestions(
    conn: Any,
    *,
    run_id: int,
    top: list[dict[str, Any]],
    now: int,
) -> int:
    """Optional: park top discoveries as WAITING_APPROVAL (never auto-apply)."""
    created = 0
    for item in top[:10]:
        if (item.get("delta_expectancy") or 0) <= 0 and (item.get("delta_pf") or 0) <= 0:
            continue
        evidence = {
            "mode": item.get("mode"),
            "direction": item.get("direction"),
            "n_strategy": item.get("n_strategy"),
            "delta_expectancy": item.get("delta_expectancy"),
            "delta_pf": item.get("delta_pf"),
            "delta_sharpe": item.get("delta_sharpe"),
            "score": item.get("score"),
            "fingerprint": item.get("fingerprint"),
        }
        try:
            execute_with_retry(
                conn,
                """
                INSERT INTO market_events_rule_suggestions_s56 (
                  run_id, rule_text, evidence_json, evidence_trades,
                  expected_improvement_pct, confidence_pct, status, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'WAITING_APPROVAL', 'strategy_discovery_s61', ?)
                """,
                (
                    run_id,
                    item["rule_text"],
                    json.dumps(evidence, default=str),
                    int(item.get("n_strategy") or 0),
                    float(item.get("delta_expectancy") or 0),
                    70.0 if item.get("confidence") == "HIGH" else (
                        55.0 if item.get("confidence") == "MEDIUM" else 40.0
                    ),
                    now,
                ),
            )
            created += 1
        except Exception as exc:
            logger.warning("s61 suggestion insert failed: %s", exc)
    return created


def latest_discovery_rows(conn: Any, *, run_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    run = None
    if run_id is not None:
        run = conn.execute(f"SELECT id FROM {_RUNS} WHERE id = ?", (int(run_id),)).fetchone()
    if run is None:
        run = conn.execute(f"SELECT id FROM {_RUNS} ORDER BY id DESC LIMIT 1").fetchone()
    if not run:
        return []
    rid = int(run["id"])
    rows = conn.execute(
        f"""
        SELECT * FROM {_RESULTS}
        WHERE run_id = ?
        ORDER BY rank ASC
        LIMIT ?
        """,
        (rid, int(limit)),
    ).fetchall()
    return [dict(r) for r in rows]


def format_strategy_discovery_report(
    conn: Any,
    *,
    run_id: int | None = None,
    top_n: int | None = None,
) -> str:
    refresh_s61_config_from_env()
    run = None
    if run_id is not None:
        run = conn.execute(f"SELECT * FROM {_RUNS} WHERE id = ?", (int(run_id),)).fetchone()
    if run is None:
        run = conn.execute(f"SELECT * FROM {_RUNS} ORDER BY id DESC LIMIT 1").fetchone()

    if not run:
        # Live compute without persist
        rows = load_lab_trades(conn)
        baseline = lab_bucket_metrics(rows)
        hyps = generate_hypotheses(rows)
        evaluated = []
        for h in hyps:
            out = evaluate_hypothesis(rows, h, baseline_all=baseline)
            if out is not None:
                evaluated.append(out)
        top = sorted(evaluated, key=lambda x: -(x.get("score") or 0))[: (top_n or S61_TOP_N)]
        return _format_results(
            top,
            n_trades=len(rows),
            n_hypotheses=len(hyps),
            n_evaluated=len(evaluated),
            baseline=baseline,
            run_id=None,
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
        n_hypotheses=int(run["n_hypotheses"] or 0),
        n_evaluated=int(run["n_evaluated"] or 0),
        baseline=baseline,
        run_id=int(run["id"]),
    )


def _fmt(v: Any, *, plus: bool = False, digits: int = 2) -> str:
    if v is None:
        return "—"
    x = float(v)
    if plus:
        return f"{x:+.{digits}f}"
    return f"{x:.{digits}f}"


def _format_results(
    top: list[dict[str, Any]],
    *,
    n_trades: int,
    n_hypotheses: int,
    n_evaluated: int,
    baseline: dict[str, Any],
    run_id: int | None,
) -> str:
    lines = [
        "S61 Strategy Discovery Engine",
        f"  run=#{run_id or 'live'}  trades={n_trades}  "
        f"hypotheses={n_hypotheses}  evaluated={n_evaluated}  shown={len(top)}",
        f"  baseline: n={baseline.get('n')}  "
        f"E={_fmt(baseline.get('expectancy'))}  "
        f"PF={_fmt(baseline.get('pf'))}  "
        f"Sharpe={_fmt(baseline.get('sharpe'), digits=3)}  "
        f"WR={_fmt(baseline.get('winrate'))}%",
        "",
        "  Rank  Score   ΔE      ΔPF     ΔSharpe  n     Conf   Rule",
        "  ----  ------  ------  ------  -------  ----  -----  ----",
    ]
    if not top:
        lines.append("  (no improving hypotheses — need more closed trades / evidence)")
    for i, item in enumerate(top, start=1):
        lines.append(
            f"  {i:<4}  {_fmt(item.get('score'), digits=2):>6}  "
            f"{_fmt(item.get('delta_expectancy'), plus=True):>6}  "
            f"{_fmt(item.get('delta_pf'), plus=True):>6}  "
            f"{_fmt(item.get('delta_sharpe'), plus=True, digits=3):>7}  "
            f"{int(item.get('n_strategy') or 0):<4}  "
            f"{str(item.get('confidence') or '—'):<5}  "
            f"{item.get('rule_text')}"
        )
    lines.extend([
        "",
        "  Modes: ALLOW = take only matching trades; BLOCK = exclude matching trades.",
        "  Observe-only — never auto-applies. Approve via trade-suggestions if enabled.",
    ])
    return "\n".join(lines)


def format_s61_report_block(conn: Any) -> list[str]:
    try:
        text = format_strategy_discovery_report(conn)
    except Exception as exc:
        return [f"S61 Strategy Discovery: unavailable ({exc})"]
    return text.splitlines()


__all__ = [
    "evaluate_hypothesis",
    "format_s61_report_block",
    "format_strategy_discovery_report",
    "generate_hypotheses",
    "latest_discovery_rows",
    "refresh_s61_config_from_env",
    "run_strategy_discovery",
]
