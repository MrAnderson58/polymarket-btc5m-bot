"""S59 — Feature Laboratory.

For each feature, compare Filter ON vs Filter OFF on closed trades:
trades / winrate / expectancy / PF / Sharpe, then Δ metrics.

Observe-only. LLM may only read the finished table. Never auto-applies strategy.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from typing import Any, Callable

from bot.research.market_events.db import execute_with_retry
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)

logger = logging.getLogger(__name__)

_RESULTS = "market_events_feature_lab_s59"
_RUNS = "market_events_feature_lab_runs_s59"
_SNAP = "market_events_trade_snapshots_s56"
_S58 = "market_events_trade_decisions_s58"
_FEATURES = "market_events_trade_features_s55"

S59_MIN_SIDE = 15
S59_LLM_ENABLED = False

FilterFn = Callable[[dict[str, Any]], bool | None]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def refresh_s59_config_from_env() -> None:
    global S59_MIN_SIDE, S59_LLM_ENABLED
    if "S59_MIN_SIDE" in os.environ:
        try:
            S59_MIN_SIDE = max(5, int(os.environ["S59_MIN_SIDE"]))
        except (TypeError, ValueError):
            pass
    if "S59_LLM_ENABLED" in os.environ:
        S59_LLM_ENABLED = _env_bool("S59_LLM_ENABLED", False)


def _apply_defaults() -> None:
    global S59_MIN_SIDE, S59_LLM_ENABLED
    try:
        S59_MIN_SIDE = max(5, int(os.environ.get("S59_MIN_SIDE", "15")))
    except (TypeError, ValueError):
        S59_MIN_SIDE = 15
    S59_LLM_ENABLED = _env_bool("S59_LLM_ENABLED", False)


_apply_defaults()


def _row(r: Any) -> dict[str, Any]:
    if r is None:
        return {}
    try:
        return dict(r)
    except Exception:
        return {}


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    m = len(s) // 2
    if len(s) % 2:
        return s[m]
    return (s[m - 1] + s[m]) / 2.0


def _sharpe(pnls: list[float]) -> float | None:
    """Per-trade Sharpe proxy: mean / std (population)."""
    if len(pnls) < 2:
        return None
    mean = sum(pnls) / len(pnls)
    var = sum((x - mean) ** 2 for x in pnls) / len(pnls)
    if var <= 1e-18:
        return 0.0 if abs(mean) < 1e-12 else None
    return round(mean / math.sqrt(var), 4)


def lab_bucket_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {
            "n": 0,
            "winrate": None,
            "expectancy": None,
            "pf": None,
            "pf_inf": False,
            "sharpe": None,
            "pnl": 0.0,
        }
    pnls = [float(r.get("pnl_usd") or 0.0) for r in rows]
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
    return {
        "n": n,
        "winrate": round(100.0 * len(wins) / n, 2),
        "expectancy": round(sum(pnls) / n, 4),
        "pf": None if pf_inf else (round(pf, 4) if pf is not None else None),
        "pf_inf": pf_inf,
        "sharpe": _sharpe(pnls),
        "pnl": round(sum(pnls), 4),
    }


def load_lab_trades(conn: Any) -> list[dict[str, Any]]:
    """Closed trades with features; enrich from S58 decision EMAs when present."""
    rows: list[dict[str, Any]] = []
    try:
        snap = conn.execute(
            f"""
            SELECT s.*, d.ema20 AS s58_ema20, d.ema50 AS s58_ema50, d.ema200 AS s58_ema200,
                   d.rsi AS s58_rsi, d.btc_return AS s58_btc_return
            FROM {_SNAP} s
            LEFT JOIN {_S58} d ON d.paper_trade_id = s.paper_trade_id
            WHERE s.pnl_usd IS NOT NULL
            """,
        ).fetchall()
        rows = [_row(r) for r in snap]
    except Exception:
        try:
            rows = [_row(r) for r in conn.execute(
                f"SELECT * FROM {_SNAP} WHERE pnl_usd IS NOT NULL",
            ).fetchall()]
        except Exception:
            rows = []

    if not rows:
        try:
            feat = conn.execute(
                f"""
                SELECT * FROM {_FEATURES}
                WHERE pnl_usd IS NOT NULL AND result IS NOT NULL
                """,
            ).fetchall()
            rows = [_row(r) for r in feat]
        except Exception:
            return []

    for r in rows:
        if r.get("ema20") is None and r.get("s58_ema20") is not None:
            r["ema20"] = r.get("s58_ema20")
        if r.get("ema50") is None and r.get("s58_ema50") is not None:
            r["ema50"] = r.get("s58_ema50")
        if r.get("ema200") is None and r.get("s58_ema200") is not None:
            r["ema200"] = r.get("s58_ema200")
        if r.get("rsi") is None and r.get("s58_rsi") is not None:
            r["rsi"] = r.get("s58_rsi")
        if r.get("btc_return") is None and r.get("s58_btc_return") is not None:
            r["btc_return"] = r.get("s58_btc_return")
    return rows


def _dir(r: dict[str, Any]) -> str:
    return str(r.get("direction") or "").upper()


def _build_filters(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Define binary filters. Predicate returns True=ON, False=OFF, None=skip."""
    atrs = [v for v in (_safe_float(r.get("atr")) for r in rows) if v is not None]
    vols = [v for v in (_safe_float(r.get("volume")) for r in rows) if v is not None]
    atr_med = _median(atrs)
    vol_med = _median(vols)

    def funding(r: dict[str, Any]) -> bool | None:
        f = _safe_float(r.get("funding"))
        if f is None:
            return None
        d = _dir(r)
        if d == "LONG":
            return f < 0
        if d == "SHORT":
            return f > 0
        return None

    def fear_greed(r: dict[str, Any]) -> bool | None:
        fg = _safe_float(r.get("fear_greed"))
        if fg is None:
            return None
        d = _dir(r)
        if d == "LONG":
            return fg >= 50
        if d == "SHORT":
            return fg <= 50
        return None

    def ema(r: dict[str, Any]) -> bool | None:
        e20 = _safe_float(r.get("ema20"))
        e50 = _safe_float(r.get("ema50"))
        trend = _safe_float(r.get("trend"))
        d = _dir(r)
        if e20 is not None and e50 is not None:
            if d == "LONG":
                return e20 > e50
            if d == "SHORT":
                return e20 < e50
        if trend is not None:
            if d == "LONG":
                return trend > 0
            if d == "SHORT":
                return trend < 0
        return None

    def atr(r: dict[str, Any]) -> bool | None:
        v = _safe_float(r.get("atr"))
        if v is None or atr_med is None:
            return None
        # ON = enough volatility (ATR >= median)
        return v >= atr_med

    def rsi(r: dict[str, Any]) -> bool | None:
        v = _safe_float(r.get("rsi"))
        if v is None:
            return None
        d = _dir(r)
        if d == "LONG":
            return v <= 45
        if d == "SHORT":
            return v >= 55
        return None

    def volume(r: dict[str, Any]) -> bool | None:
        v = _safe_float(r.get("volume"))
        if v is None or vol_med is None:
            return None
        return v >= vol_med

    def news(r: dict[str, Any]) -> bool | None:
        v = _safe_float(r.get("news_score"))
        if v is None:
            return None
        return v >= 0.5

    def macro(r: dict[str, Any]) -> bool | None:
        v = _safe_float(r.get("macro_score"))
        if v is None:
            return None
        return v >= 0.5

    def ai(r: dict[str, Any]) -> bool | None:
        v = _safe_float(r.get("ai_score"))
        if v is None:
            return None
        return v >= 0.5

    def regime(r: dict[str, Any]) -> bool | None:
        reg = str(r.get("market_regime") or "").upper().replace(" ", "_").replace("-", "_")
        if not reg:
            return None
        d = _dir(r)
        bull = reg in ("STRONG_BULL", "WEAK_BULL", "RISK_ON", "BULL")
        bear = reg in ("STRONG_BEAR", "WEAK_BEAR", "RISK_OFF", "BEAR")
        if d == "LONG":
            return bull or reg == "RANGE"
        if d == "SHORT":
            return bear or reg == "RANGE"
        return None

    def hour(r: dict[str, Any]) -> bool | None:
        h = r.get("hour")
        if h is None:
            return None
        try:
            hi = int(h)
        except (TypeError, ValueError):
            return None
        # ON = London/NY active window (UTC)
        return 7 <= hi <= 20

    def weekday(r: dict[str, Any]) -> bool | None:
        w = r.get("weekday")
        if w is None:
            return None
        try:
            wi = int(w)
        except (TypeError, ValueError):
            return None
        # ON = Mon–Thu (0–3)
        return 0 <= wi <= 3

    return [
        {"feature": "Funding", "rule": "LONG funding<0 / SHORT funding>0", "fn": funding},
        {"feature": "Fear & Greed", "rule": "LONG FG≥50 / SHORT FG≤50", "fn": fear_greed},
        {"feature": "EMA", "rule": "LONG EMA20>EMA50 (or trend>0) / SHORT inverse", "fn": ema},
        {
            "feature": "ATR",
            "rule": f"ATR ≥ median ({atr_med:.4g})" if atr_med is not None else "ATR ≥ median",
            "fn": atr,
        },
        {"feature": "RSI", "rule": "LONG RSI≤45 / SHORT RSI≥55", "fn": rsi},
        {
            "feature": "Volume",
            "rule": f"Volume ≥ median ({vol_med:.4g})" if vol_med is not None else "Volume ≥ median",
            "fn": volume,
        },
        {"feature": "News", "rule": "news_score ≥ 0.5", "fn": news},
        {"feature": "Macro", "rule": "macro_score ≥ 0.5", "fn": macro},
        {"feature": "AI", "rule": "ai_score ≥ 0.5", "fn": ai},
        {"feature": "Regime", "rule": "direction aligned with bull/bear (RANGE ok)", "fn": regime},
        {"feature": "Hour", "rule": "hour UTC in [7, 20]", "fn": hour},
        {"feature": "Weekday", "rule": "weekday Mon–Thu (0–3)", "fn": weekday},
    ]


def _pf_num(m: dict[str, Any]) -> float | None:
    if m.get("pf_inf"):
        return float("inf")
    return m.get("pf")


def _delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    if a == float("inf") and b == float("inf"):
        return 0.0
    if a == float("inf"):
        return None
    if b == float("inf"):
        return None
    return round(float(a) - float(b), 4)


def _confidence(on_n: int, off_n: int, delta_pf: float | None, delta_e: float | None) -> str:
    if on_n < S59_MIN_SIDE or off_n < S59_MIN_SIDE:
        return "LOW"
    strength = 0.0
    if delta_pf is not None and delta_pf != float("inf"):
        strength += abs(float(delta_pf))
    if delta_e is not None:
        strength += abs(float(delta_e)) / 10.0
    if on_n >= 50 and off_n >= 50 and strength >= 0.25:
        return "HIGH"
    if on_n >= S59_MIN_SIDE and off_n >= S59_MIN_SIDE and strength >= 0.1:
        return "MEDIUM"
    return "LOW"


def evaluate_feature(
    rows: list[dict[str, Any]],
    *,
    feature: str,
    rule: str,
    fn: FilterFn,
) -> dict[str, Any]:
    on_rows: list[dict[str, Any]] = []
    off_rows: list[dict[str, Any]] = []
    skipped = 0
    for r in rows:
        flag = fn(r)
        if flag is None:
            skipped += 1
            continue
        if flag:
            on_rows.append(r)
        else:
            off_rows.append(r)
    on_m = lab_bucket_metrics(on_rows)
    off_m = lab_bucket_metrics(off_rows)
    d_e = _delta(on_m.get("expectancy"), off_m.get("expectancy"))
    d_pf = _delta(_pf_num(on_m), _pf_num(off_m))
    d_wr = _delta(on_m.get("winrate"), off_m.get("winrate"))
    conf = _confidence(on_m["n"], off_m["n"], d_pf, d_e)
    useful = None
    if d_pf is not None and d_e is not None:
        useful = bool(d_pf > 0 and d_e > 0)
    elif d_pf is not None:
        useful = d_pf > 0
    elif d_e is not None:
        useful = d_e > 0
    return {
        "feature": feature,
        "filter_rule": rule,
        "skipped": skipped,
        "on": on_m,
        "off": off_m,
        "delta_expectancy": d_e,
        "delta_pf": None if d_pf == float("inf") else d_pf,
        "delta_wr": d_wr,
        "contribution_pf": None if d_pf == float("inf") else d_pf,
        "confidence": conf,
        "useful": useful,
    }


def run_feature_lab(conn: Any, *, now: int | None = None, with_llm: bool = False) -> dict[str, Any]:
    """Compute ON/OFF table for all features, persist, optional LLM Q&A."""
    refresh_s59_config_from_env()
    now = int(now if now is not None else time.time())
    rows = load_lab_trades(conn)
    filters = _build_filters(rows)
    results = [
        evaluate_feature(rows, feature=f["feature"], rule=f["rule"], fn=f["fn"])
        for f in filters
    ]
    results_sorted = sorted(
        results,
        key=lambda x: (
            -(x.get("contribution_pf") or -999),
            -(x.get("delta_expectancy") or -999),
        ),
    )

    cur = execute_with_retry(
        conn,
        f"""
        INSERT INTO {_RUNS} (n_trades, results_json, llm_text, llm_method, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (len(rows), json.dumps(results_sorted, default=str), None, "pending", now),
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
        for item in results_sorted:
            on_m, off_m = item["on"], item["off"]
            execute_with_retry(
                conn,
                f"""
                INSERT INTO {_RESULTS} (
                  run_id, feature, filter_rule,
                  on_n, on_winrate, on_expectancy, on_pf, on_sharpe,
                  off_n, off_winrate, off_expectancy, off_pf, off_sharpe,
                  delta_expectancy, delta_pf, delta_wr, contribution_pf,
                  confidence, skipped_n, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    item["feature"],
                    item["filter_rule"],
                    on_m["n"], on_m.get("winrate"), on_m.get("expectancy"),
                    on_m.get("pf"), on_m.get("sharpe"),
                    off_m["n"], off_m.get("winrate"), off_m.get("expectancy"),
                    off_m.get("pf"), off_m.get("sharpe"),
                    item.get("delta_expectancy"), item.get("delta_pf"), item.get("delta_wr"),
                    item.get("contribution_pf"),
                    item.get("confidence"),
                    int(item.get("skipped") or 0),
                    now,
                ),
            )

    llm_text, method = _analyst_answers(results_sorted)
    if with_llm or S59_LLM_ENABLED:
        llm_text, method = _call_llm_feature_lab(results_sorted)

    if run_id is not None:
        execute_with_retry(
            conn,
            f"UPDATE {_RUNS} SET llm_text = ?, llm_method = ? WHERE id = ?",
            (llm_text, method, run_id),
        )

    return {
        "run_id": run_id,
        "n_trades": len(rows),
        "results": results_sorted,
        "llm_text": llm_text,
        "llm_method": method,
    }


def _fmt_pf(v: float | None, *, inf: bool = False) -> str:
    if inf:
        return "inf"
    if v is None:
        return "—"
    return f"{float(v):.2f}"


def _fmt_num(v: Any, *, plus: bool = False, digits: int = 2) -> str:
    if v is None:
        return "—"
    x = float(v)
    if plus:
        return f"{x:+.{digits}f}"
    return f"{x:.{digits}f}"


def format_feature_lab_report(conn: Any, *, run_id: int | None = None) -> str:
    refresh_s59_config_from_env()
    run = None
    if run_id is not None:
        run = conn.execute(f"SELECT * FROM {_RUNS} WHERE id = ?", (int(run_id),)).fetchone()
    if run is None:
        run = conn.execute(f"SELECT * FROM {_RUNS} ORDER BY id DESC LIMIT 1").fetchone()
    if not run:
        # compute live without persist
        rows = load_lab_trades(conn)
        filters = _build_filters(rows)
        results = [
            evaluate_feature(rows, feature=f["feature"], rule=f["rule"], fn=f["fn"])
            for f in filters
        ]
        return _format_results(results, n_trades=len(rows), llm_text=None, run_id=None)

    try:
        results = json.loads(run["results_json"] or "[]")
    except Exception:
        results = []
    return _format_results(
        results,
        n_trades=int(run["n_trades"] or 0),
        llm_text=run["llm_text"],
        run_id=int(run["id"]),
        method=run["llm_method"],
    )


def _format_results(
    results: list[dict[str, Any]],
    *,
    n_trades: int,
    llm_text: str | None,
    run_id: int | None,
    method: str | None = None,
) -> str:
    lines = [
        "S59 Feature Laboratory",
        f"  trades={n_trades}  run_id={run_id or '—'}  min_side={S59_MIN_SIDE}",
        "",
    ]
    for item in results:
        on_m = item.get("on") or {}
        off_m = item.get("off") or {}
        lines.extend([
            str(item.get("feature")),
            f"  rule: {item.get('filter_rule')}",
            "  ON",
            f"    Trades {_fmt_num(on_m.get('n'), digits=0)}  "
            f"WR {_fmt_num(on_m.get('winrate'))}%  "
            f"E ${_fmt_num(on_m.get('expectancy'), plus=True)}  "
            f"PF {_fmt_pf(on_m.get('pf'), inf=bool(on_m.get('pf_inf')))}  "
            f"Sharpe {_fmt_num(on_m.get('sharpe'), plus=True, digits=3)}",
            "  OFF",
            f"    Trades {_fmt_num(off_m.get('n'), digits=0)}  "
            f"WR {_fmt_num(off_m.get('winrate'))}%  "
            f"E ${_fmt_num(off_m.get('expectancy'), plus=True)}  "
            f"PF {_fmt_pf(off_m.get('pf'), inf=bool(off_m.get('pf_inf')))}  "
            f"Sharpe {_fmt_num(off_m.get('sharpe'), plus=True, digits=3)}",
            f"  Contribution  {_fmt_num(item.get('contribution_pf'), plus=True)} PF   "
            f"ΔE ${_fmt_num(item.get('delta_expectancy'), plus=True)}   "
            f"ΔWR {_fmt_num(item.get('delta_wr'), plus=True)}%",
            f"  Confidence  {item.get('confidence')}",
            "",
        ])
    if llm_text:
        lines.extend(["Analyst (read-only table)", llm_text, ""])
        if method:
            lines.append(f"  method={method}")
    lines.extend([
        "No automatic strategy changes.",
        "Re-run: python -m bot.research.market_events feature-lab --force",
    ])
    return "\n".join(lines)


def _analyst_answers(results: list[dict[str, Any]]) -> tuple[str, str]:
    useful = [
        r for r in results
        if r.get("useful") and r.get("confidence") in ("HIGH", "MEDIUM")
    ]
    harmful = [
        r for r in results
        if r.get("useful") is False and r.get("confidence") in ("HIGH", "MEDIUM")
    ]
    check_next = [
        r for r in results
        if r.get("confidence") == "LOW" or (
            r.get("on", {}).get("n", 0) < S59_MIN_SIDE
            or r.get("off", {}).get("n", 0) < S59_MIN_SIDE
        )
    ]
    lines = [
        "1) Useful features",
    ]
    if useful:
        for r in useful[:6]:
            lines.append(
                f"  - {r['feature']}: +{_fmt_num(r.get('contribution_pf'))} PF "
                f"(ΔE ${_fmt_num(r.get('delta_expectancy'), plus=True)}, "
                f"conf={r.get('confidence')})"
            )
    else:
        lines.append("  - insufficient confident positive contributions")
    lines.append("2) Harmful / weak features")
    if harmful:
        for r in harmful[:6]:
            lines.append(
                f"  - {r['feature']}: {_fmt_num(r.get('contribution_pf'), plus=True)} PF "
                f"(conf={r.get('confidence')})"
            )
    else:
        lines.append("  - none with confident negative contribution")
    lines.append("3) Check next")
    if check_next:
        for r in check_next[:6]:
            lines.append(
                f"  - {r['feature']}: need more data "
                f"(ON={r.get('on', {}).get('n')} OFF={r.get('off', {}).get('n')} "
                f"skip={r.get('skipped')})"
            )
    else:
        lines.append("  - coverage adequate for listed filters")
    return "\n".join(lines), "deterministic"


_LLM_SYSTEM = """You are a trading statistics analyst.
You may ONLY use the provided Feature Lab ON/OFF table.
Answer ONLY:
1) Which features are truly useful?
2) Which features hurt?
3) Which features should be tested next?
Do NOT invent strategy code. Do NOT auto-apply anything. Cite PF/Δ/confidence from the table.
"""


def _call_llm_feature_lab(results: list[dict[str, Any]]) -> tuple[str, str]:
    det, _ = _analyst_answers(results)
    refresh_s59_config_from_env()
    if not S59_LLM_ENABLED:
        return det, "deterministic"
    table = json.dumps([
        {
            "feature": r.get("feature"),
            "rule": r.get("filter_rule"),
            "on": r.get("on"),
            "off": r.get("off"),
            "delta_pf": r.get("delta_pf"),
            "delta_expectancy": r.get("delta_expectancy"),
            "delta_wr": r.get("delta_wr"),
            "confidence": r.get("confidence"),
        }
        for r in results
    ], default=str)
    try:
        from bot.research.market_events.signal_intelligence.claude_channel_s50 import (
            claude_call_allowed,
            telegram_claude_session,
        )
        from bot.research.market_events.signal_intelligence.claude_client_g2 import (
            call_claude_g2,
            is_claude_configured,
        )
        if not is_claude_configured():
            return "Claude not configured.\n\n" + det, "deterministic_fallback"
        ok, reason = claude_call_allowed()
        if not ok:
            return f"Claude blocked ({reason}).\n\n{det}", "deterministic_fallback"
        with telegram_claude_session():
            resp = call_claude_g2(
                system=_LLM_SYSTEM,
                user_content=f"Feature Lab table JSON:\n{table}",
                label="s59_feature_lab",
                max_tokens=2048,
            )
        return resp.text.strip(), "claude"
    except Exception as exc:
        logger.warning("s59 LLM failed: %s", exc)
        return f"Claude error: {exc}\n\n{det}", "deterministic_fallback"


def latest_lab_rows(conn: Any) -> list[dict[str, Any]]:
    run = conn.execute(f"SELECT id FROM {_RUNS} ORDER BY id DESC LIMIT 1").fetchone()
    if not run:
        return []
    rows = conn.execute(
        f"SELECT * FROM {_RESULTS} WHERE run_id = ? ORDER BY contribution_pf DESC",
        (int(run["id"]),),
    ).fetchall()
    return [_row(r) for r in rows]


def doctor_s59_status(conn: Any) -> dict[str, Any]:
    try:
        n_runs = conn.execute(f"SELECT COUNT(*) AS n FROM {_RUNS}").fetchone()["n"]
        last = conn.execute(f"SELECT * FROM {_RUNS} ORDER BY id DESC LIMIT 1").fetchone()
    except Exception as exc:
        return {"runs": 0, "last": "—", "ok": False, "error": str(exc)[:80]}
    last_txt = "—"
    if last:
        last_txt = f"#{last['id']} trades={last['n_trades']} ({last['llm_method']})"
    return {"runs": int(n_runs or 0), "last": last_txt, "ok": True}


__all__ = [
    "doctor_s59_status",
    "evaluate_feature",
    "format_feature_lab_report",
    "lab_bucket_metrics",
    "latest_lab_rows",
    "load_lab_trades",
    "refresh_s59_config_from_env",
    "run_feature_lab",
]
