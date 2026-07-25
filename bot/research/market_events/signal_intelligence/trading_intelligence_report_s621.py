"""S62.1 — Trading Intelligence Report (research-only).

Read-only quantitative layer over existing S56–S59 (+ S61/S62 pattern tables).
Never writes production/live tables. Never reruns Feature Lab / Alpha Discovery.
Never changes strategy.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bot.research.market_events.signal_intelligence.feature_lab_s59 import (
    lab_bucket_metrics,
    latest_lab_rows,
    load_lab_trades,
)
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    normalize_score_0_100,
    safe_float as _safe_float,
)

logger = logging.getLogger(__name__)

PERIODS = ("lifetime", "24h", "3h", "1h")
PERIOD_SECONDS = {
    "lifetime": None,
    "24h": 24 * 3600,
    "3h": 3 * 3600,
    "1h": 3600,
}

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

FUNDING_BUCKETS = (
    ("Funding < -0.05%", lambda f: f < -0.0005),
    ("Funding -0.05%..0", lambda f: -0.0005 <= f < 0),
    ("Funding 0..0.05%", lambda f: 0 <= f <= 0.0005),
    ("Funding > 0.05%", lambda f: f > 0.0005),
)

RSI_BUCKETS = (
    ("RSI <20", lambda x: x < 20),
    ("RSI 20-30", lambda x: 20 <= x < 30),
    ("RSI 30-40", lambda x: 30 <= x < 40),
    ("RSI 40-60", lambda x: 40 <= x < 60),
    ("RSI 60-70", lambda x: 60 <= x < 70),
    ("RSI 70-80", lambda x: 70 <= x < 80),
    ("RSI >80", lambda x: x >= 80),
)

AI_BUCKETS = (
    ("AI 0-20", lambda x: 0 <= x < 20),
    ("AI 20-40", lambda x: 20 <= x < 40),
    ("AI 40-60", lambda x: 40 <= x < 60),
    ("AI 60-80", lambda x: 60 <= x < 80),
    ("AI 80-100", lambda x: 80 <= x <= 100),
)

# Rough regime family map for section 6
_REGIME_FAMILY = {
    "STRONG_BULL": "Trend",
    "WEAK_BULL": "Trend",
    "STRONG_BEAR": "Trend",
    "WEAK_BEAR": "Trend",
    "TREND": "Trend",
    "RANGE": "Range",
    "RISK_ON": "Trend",
    "RISK_OFF": "Shock",
    "SHOCK": "Shock",
}


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "bot" / "research" / "market_events" / "__main__.py").exists():
            return p
    return Path.cwd()


def default_report_dir(root: Path | None = None) -> Path:
    return (root or _repo_root()) / "research" / "reports" / "intelligence"


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


def _row(r: Any) -> dict[str, Any]:
    if r is None:
        return {}
    try:
        return dict(r)
    except Exception:
        return {}


def filter_by_period(
    rows: list[dict[str, Any]],
    period: str,
    *,
    now: int,
) -> list[dict[str, Any]]:
    secs = PERIOD_SECONDS.get(period)
    if secs is None:
        return list(rows)
    cutoff = now - int(secs)
    return [r for r in rows if _trade_ts(r) >= cutoff]


def overall_performance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Trades / PnL / WR / PF / Expectancy / Sharpe / MaxDD / Avg Hold."""
    base = lab_bucket_metrics(rows)
    holds: list[float] = []
    for r in rows:
        h = _safe_float(r.get("duration_sec"))
        if h is None:
            h = _safe_float(r.get("holding_seconds"))
        if h is not None and h >= 0:
            holds.append(float(h))

    # Max drawdown on equity curve (time-ordered cumulative PnL)
    ordered = sorted(rows, key=_trade_ts)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in ordered:
        equity += float(r.get("pnl_usd") or 0.0)
        peak = max(peak, equity)
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    return {
        "trades": base["n"],
        "pnl": base["pnl"],
        "winrate": base["winrate"],
        "profit_factor": base["pf"],
        "pf_inf": base.get("pf_inf"),
        "expectancy": base["expectancy"],
        "sharpe": base["sharpe"],
        "max_drawdown": round(max_dd, 4),
        "avg_hold_sec": round(sum(holds) / len(holds), 1) if holds else None,
    }


def _decision_reason(r: dict[str, Any]) -> str:
    why = r.get("why_opened")
    if isinstance(why, str):
        try:
            why = json.loads(why)
        except Exception:
            why = None
    if isinstance(why, list) and why:
        tags = []
        for item in why[:4]:
            if isinstance(item, dict):
                tag = item.get("tag") or item.get("reason")
                ok = item.get("ok")
                if tag:
                    tags.append(f"{'✓' if ok else '·'}{tag}")
        if tags:
            return " ".join(tags)
    gate = r.get("gate_reason") or r.get("gate_result") or r.get("exit_reason")
    return str(gate or "—")


def _enrich_decisions(conn: Any, rows: list[dict[str, Any]]) -> None:
    """Attach S58 why_opened / gate fields when present (one query)."""
    try:
        decs = conn.execute(
            """
            SELECT paper_trade_id, gate_reason, gate_result, why_opened_json,
                   market_regime, rsi, ema200
            FROM market_events_trade_decisions_s58
            """,
        ).fetchall()
    except Exception:
        return
    by_id: dict[int, dict[str, Any]] = {}
    for d in decs:
        dd = _row(d)
        try:
            pid = int(dd.get("paper_trade_id") or 0)
        except (TypeError, ValueError):
            continue
        if pid:
            by_id[pid] = dd
    for r in rows:
        try:
            pid = int(r.get("paper_trade_id") or 0)
        except (TypeError, ValueError):
            pid = 0
        d = by_id.get(pid)
        if not d:
            continue
        if not r.get("gate_reason"):
            r["gate_reason"] = d.get("gate_reason")
        if not r.get("gate_result"):
            r["gate_result"] = d.get("gate_result")
        if not r.get("why_opened") and d.get("why_opened_json"):
            try:
                r["why_opened"] = json.loads(d["why_opened_json"])
            except Exception:
                r["why_opened"] = d.get("why_opened_json")
        if r.get("rsi") is None and d.get("rsi") is not None:
            r["rsi"] = d.get("rsi")
        if not r.get("market_regime") and d.get("market_regime"):
            r["market_regime"] = d.get("market_regime")


def trade_card(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "coin": str(r.get("symbol") or "?").upper().replace("USDT", ""),
        "direction": str(r.get("direction") or "?").upper(),
        "pnl": _safe_float(r.get("pnl_usd")),
        "strategy": str(r.get("s40_signal_type") or r.get("exit_reason") or "—"),
        "market_regime": str(r.get("market_regime") or "—"),
        "hour": r.get("hour"),
        "funding": _safe_float(r.get("funding")),
        "ai_score": _safe_float(r.get("ai_score")),
        "decision_reason": _decision_reason(r),
    }


def best_worst_trades(rows: list[dict[str, Any]], *, n: int = 20) -> dict[str, list[dict[str, Any]]]:
    scored = [r for r in rows if r.get("pnl_usd") is not None]
    winners = sorted(scored, key=lambda r: float(r.get("pnl_usd") or 0), reverse=True)[:n]
    losers = sorted(scored, key=lambda r: float(r.get("pnl_usd") or 0))[:n]
    return {
        "best": [trade_card(r) for r in winners],
        "worst": [trade_card(r) for r in losers],
    }


def coin_ranking(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        sym = str(r.get("symbol") or "?").upper().replace("USDT", "")
        by[sym].append(r)
    out = []
    for sym, bucket in by.items():
        m = lab_bucket_metrics(bucket)
        out.append({
            "coin": sym,
            "trades": m["n"],
            "pnl": m["pnl"],
            "pf": m["pf"],
            "pf_inf": m.get("pf_inf"),
            "expectancy": m["expectancy"],
            "winrate": m["winrate"],
        })
    out.sort(key=lambda x: (
        -99.0 if x.get("pf_inf") else -(x.get("pf") if x.get("pf") is not None else -1.0),
        -(x.get("pnl") or 0),
    ))
    return out


def direction_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for d in ("LONG", "SHORT"):
        bucket = [r for r in rows if str(r.get("direction") or "").upper() == d]
        m = lab_bucket_metrics(bucket)
        out[d] = {
            "trades": m["n"],
            "pnl": m["pnl"],
            "pf": m["pf"],
            "pf_inf": m.get("pf_inf"),
            "expectancy": m["expectancy"],
            "winrate": m["winrate"],
        }
    return out


def _regime_family(raw: Any) -> str:
    key = str(raw or "").strip().upper()
    if not key:
        return "Unknown"
    if key in _REGIME_FAMILY:
        return _REGIME_FAMILY[key]
    if "SHOCK" in key or "CRASH" in key:
        return "Shock"
    if "RANGE" in key or "SIDE" in key:
        return "Range"
    if "BULL" in key or "BEAR" in key or "TREND" in key:
        return "Trend"
    return key.title() if len(key) < 20 else "Other"


def regime_analysis(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by[_regime_family(r.get("market_regime"))].append(r)
    # Always show Trend / Range / Shock even if empty
    families = ["Trend", "Range", "Shock"] + sorted(
        k for k in by if k not in ("Trend", "Range", "Shock")
    )
    out = []
    for fam in families:
        m = lab_bucket_metrics(by.get(fam, []))
        out.append({
            "regime": fam,
            "trades": m["n"],
            "pnl": m["pnl"],
            "pf": m["pf"],
            "pf_inf": m.get("pf_inf"),
            "winrate": m["winrate"],
        })
    return out


def hour_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        try:
            h = int(r.get("hour"))
        except (TypeError, ValueError):
            continue
        if 0 <= h <= 23:
            by[h].append(r)
    hours = []
    for h in range(24):
        m = lab_bucket_metrics(by.get(h, []))
        hours.append({
            "hour": h,
            "trades": m["n"],
            "pnl": m["pnl"],
            "pf": m["pf"],
            "pf_inf": m.get("pf_inf"),
            "winrate": m["winrate"],
        })
    ranked = [h for h in hours if h["trades"] > 0]
    by_pf = sorted(
        ranked,
        key=lambda x: (-99 if x.get("pf_inf") else -(x.get("pf") if x.get("pf") is not None else -1)),
    )
    return {
        "hours": hours,
        "top3": by_pf[:3],
        "worst3": list(reversed(by_pf[-3:])) if by_pf else [],
    }


def weekday_analysis(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        try:
            w = int(r.get("weekday"))
        except (TypeError, ValueError):
            continue
        if 0 <= w <= 6:
            by[w].append(r)
    out = []
    for w in range(7):
        m = lab_bucket_metrics(by.get(w, []))
        out.append({
            "weekday": WEEKDAYS[w],
            "weekday_i": w,
            "trades": m["n"],
            "pnl": m["pnl"],
            "pf": m["pf"],
            "pf_inf": m.get("pf_inf"),
            "winrate": m["winrate"],
            "expectancy": m["expectancy"],
        })
    return out


def _bucket_table(
    rows: list[dict[str, Any]],
    *,
    value_fn,
    specs: tuple,
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {name: [] for name, _ in specs}
    for r in rows:
        v = value_fn(r)
        if v is None:
            continue
        for name, pred in specs:
            try:
                if pred(v):
                    buckets[name].append(r)
                    break
            except Exception:
                continue
    out = []
    for name, _ in specs:
        m = lab_bucket_metrics(buckets[name])
        out.append({
            "bucket": name,
            "trades": m["n"],
            "pnl": m["pnl"],
            "pf": m["pf"],
            "pf_inf": m.get("pf_inf"),
            "expectancy": m["expectancy"],
            "winrate": m["winrate"],
        })
    return out


def funding_buckets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _bucket_table(
        rows,
        value_fn=lambda r: _safe_float(r.get("funding")),
        specs=FUNDING_BUCKETS,
    )


def rsi_buckets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _bucket_table(
        rows,
        value_fn=lambda r: _safe_float(r.get("rsi")),
        specs=RSI_BUCKETS,
    )


def ai_score_buckets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _ai(r: dict[str, Any]) -> float | None:
        return normalize_score_0_100(_safe_float(r.get("ai_score")))

    return _bucket_table(rows, value_fn=_ai, specs=AI_BUCKETS)


def feature_importance_summary(conn: Any) -> list[dict[str, Any]]:
    """Reuse latest S59 Feature Lab rows — do NOT rerun."""
    try:
        rows = latest_lab_rows(conn)
    except Exception as exc:
        logger.debug("s62.1 feature lab read failed: %s", exc)
        return []
    out = []
    for r in rows:
        out.append({
            "feature": r.get("feature"),
            "filter_rule": r.get("filter_rule"),
            "contribution_pf": _safe_float(r.get("contribution_pf")),
            "delta_expectancy": _safe_float(r.get("delta_expectancy")),
            "delta_pf": _safe_float(r.get("delta_pf")),
            "confidence": r.get("confidence"),
            "on_n": r.get("on_n"),
            "off_n": r.get("off_n"),
        })
    out.sort(key=lambda x: -(x.get("contribution_pf") if x.get("contribution_pf") is not None else -999))
    return out


def _load_pattern_rows(conn: Any, *, limit: int = 40) -> list[dict[str, Any]]:
    """Prefer S62 alpha discovery; fall back to S61 strategy discovery."""
    try:
        from bot.research.market_events.signal_intelligence.alpha_discovery_s62 import (
            latest_alpha_rows,
        )
        rows = latest_alpha_rows(conn, limit=limit)
        if rows:
            return rows
    except Exception:
        pass
    try:
        from bot.research.market_events.signal_intelligence.strategy_discovery_s61 import (
            latest_discovery_rows,
        )
        rows = latest_discovery_rows(conn, limit=limit)
        if rows:
            return rows
    except Exception:
        pass
    return []


def pattern_rankings(conn: Any, *, n: int = 20) -> dict[str, list[dict[str, Any]]]:
    rows = _load_pattern_rows(conn, limit=max(80, n * 2))
    cards = []
    for r in rows:
        cards.append({
            "pattern": r.get("rule_text") or r.get("filter_rule") or "—",
            "trades": r.get("n_strategy") or r.get("on_n") or r.get("n_matched"),
            "pf": _safe_float(r.get("pf") or r.get("strategy_pf") or r.get("delta_pf")),
            "expectancy": _safe_float(
                r.get("expectancy") or r.get("strategy_expectancy") or r.get("delta_expectancy"),
            ),
            "confidence": r.get("confidence") or "—",
            "delta_pf": _safe_float(r.get("delta_pf")),
            "score": _safe_float(r.get("score")),
        })
    by_score = sorted(
        cards,
        key=lambda x: (
            -(x.get("score") if x.get("score") is not None else -999),
            -(x.get("delta_pf") if x.get("delta_pf") is not None else -999),
            -(x.get("pf") if x.get("pf") is not None else -999),
        ),
    )
    best = by_score[:n]
    # Worst: lowest delta_pf / score among those with trades
    worst = sorted(
        [c for c in cards if c.get("trades")],
        key=lambda x: (
            (x.get("delta_pf") if x.get("delta_pf") is not None else 999),
            (x.get("score") if x.get("score") is not None else 999),
        ),
    )[:n]
    return {"best": best, "worst": worst}


def performance_drift(period_overall: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Compare lifetime → 24h → 3h → 1h; flag deteriorations."""
    order = [p for p in PERIODS if p in period_overall]
    chain = []
    for p in order:
        o = period_overall[p]
        chain.append({
            "period": p,
            "trades": o.get("trades"),
            "pnl": o.get("pnl"),
            "pf": o.get("profit_factor"),
            "winrate": o.get("winrate"),
            "expectancy": o.get("expectancy"),
            "sharpe": o.get("sharpe"),
        })
    flags: list[str] = []
    # Compare consecutive shrinking windows to lifetime
    base = period_overall.get("lifetime") or {}
    for p in ("24h", "3h", "1h"):
        cur = period_overall.get(p) or {}
        if not cur.get("trades"):
            continue
        for metric, label in (
            ("profit_factor", "PF"),
            ("winrate", "WinRate"),
            ("expectancy", "Expectancy"),
        ):
            b = base.get(metric) if metric != "profit_factor" else base.get("profit_factor")
            c = cur.get(metric) if metric != "profit_factor" else cur.get("profit_factor")
            if b is None or c is None:
                continue
            if float(c) < float(b) * 0.85 and float(b) > 0:
                flags.append(f"{label} deterioration: lifetime {_fmt(b)} → {p} {_fmt(c)}")
            elif float(c) < float(b) - (5.0 if metric == "winrate" else 0.5):
                flags.append(f"{label} deterioration: lifetime {_fmt(b)} → {p} {_fmt(c)}")
    return {"chain": chain, "flags": flags}


def _current_regime_hint(rows: list[dict[str, Any]]) -> str:
    recent = sorted(rows, key=_trade_ts, reverse=True)[:30]
    counts: dict[str, int] = defaultdict(int)
    for r in recent:
        counts[_regime_family(r.get("market_regime"))] += 1
    if not counts:
        return "Unknown"
    return max(counts.items(), key=lambda kv: kv[1])[0]


def build_period_report(
    rows: list[dict[str, Any]],
    *,
    period: str,
    conn: Any,
    feature_importance: list[dict[str, Any]],
    patterns: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    bw = best_worst_trades(rows)
    hours = hour_analysis(rows)
    return {
        "period": period,
        "overall": overall_performance(rows),
        "best_trades": bw["best"],
        "worst_trades": bw["worst"],
        "coin_ranking": coin_ranking(rows),
        "direction": direction_analysis(rows),
        "market_regime": regime_analysis(rows),
        "hour_analysis": hours,
        "weekday_analysis": weekday_analysis(rows),
        "funding_buckets": funding_buckets(rows),
        "rsi_buckets": rsi_buckets(rows),
        "ai_score_buckets": ai_score_buckets(rows),
        "feature_importance": feature_importance,
        "best_patterns": patterns.get("best") or [],
        "worst_patterns": patterns.get("worst") or [],
    }


def build_intelligence_report(
    conn: Any,
    *,
    periods: list[str] | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Assemble full multi-period intelligence payload (read-only)."""
    now = int(now if now is not None else time.time())
    periods = [p for p in (periods or list(PERIODS)) if p in PERIOD_SECONDS]
    if not periods:
        periods = list(PERIODS)

    t0 = time.time()
    all_rows = load_lab_trades(conn)
    _enrich_decisions(conn, all_rows)

    # Shared expensive-ish reads once
    feat_imp = feature_importance_summary(conn)
    patterns = pattern_rankings(conn, n=20)

    by_period: dict[str, dict[str, Any]] = {}
    overall_by: dict[str, dict[str, Any]] = {}
    for p in periods:
        subset = filter_by_period(all_rows, p, now=now)
        rep = build_period_report(
            subset,
            period=p,
            conn=conn,
            feature_importance=feat_imp,
            patterns=patterns,
        )
        by_period[p] = rep
        overall_by[p] = rep["overall"]

    # Always compute drift across available periods (fill missing from full load)
    for p in PERIODS:
        if p not in overall_by:
            subset = filter_by_period(all_rows, p, now=now)
            overall_by[p] = overall_performance(subset)

    drift = performance_drift(overall_by)
    elapsed = round(time.time() - t0, 3)

    return {
        "generated_at": now,
        "generated_at_iso": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "source": "research_db",
        "n_trades_loaded": len(all_rows),
        "periods": periods,
        "elapsed_sec": elapsed,
        "by_period": by_period,
        "performance_drift": drift,
        "current_market_regime": _current_regime_hint(all_rows),
        "feature_importance": feat_imp,
        "best_patterns": patterns.get("best") or [],
        "worst_patterns": patterns.get("worst") or [],
        "observe_only": True,
        "strategy_unchanged": True,
    }


def _fmt(v: Any, *, digits: int = 2) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return str(v)
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def format_intelligence_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Trading Intelligence Report (S62.1)",
        "",
        f"Generated: `{report.get('generated_at_iso')}`  ",
        f"Source: research DB only · trades loaded: {report.get('n_trades_loaded')} · "
        f"elapsed: {report.get('elapsed_sec')}s  ",
        "Observe-only — no strategy changes.",
        "",
    ]

    # Drift first for visibility
    drift = report.get("performance_drift") or {}
    lines.extend(["## 15. Performance Drift", ""])
    lines.append("| Period | Trades | PnL | PF | WR% | Expectancy |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for c in drift.get("chain") or []:
        lines.append(
            f"| {c.get('period')} | {c.get('trades')} | {_fmt(c.get('pnl'))} | "
            f"{_fmt(c.get('pf'))} | {_fmt(c.get('winrate'))} | {_fmt(c.get('expectancy'))} |"
        )
    flags = drift.get("flags") or []
    if flags:
        lines.append("")
        lines.append("**Flags:**")
        for f in flags:
            lines.append(f"- {f}")
    else:
        lines.append("")
        lines.append("_No major deterioration flags._")
    lines.append("")

    for period, rep in (report.get("by_period") or {}).items():
        lines.append(f"## Period: `{period}`")
        lines.append("")
        o = rep.get("overall") or {}
        lines.append("### 1. Overall Performance")
        lines.append("")
        lines.append(
            f"Trades **{o.get('trades')}** · PnL **{_fmt(o.get('pnl'))}** · "
            f"WR **{_fmt(o.get('winrate'))}%** · PF **{_fmt(o.get('profit_factor'))}** · "
            f"E **{_fmt(o.get('expectancy'))}** · Sharpe **{_fmt(o.get('sharpe'), digits=3)}** · "
            f"MaxDD **{_fmt(o.get('max_drawdown'))}** · "
            f"AvgHold **{_fmt(o.get('avg_hold_sec'), digits=0)}s**"
        )
        lines.append("")

        lines.append("### 2. Best Trades (Top 20)")
        lines.append("")
        lines.append("| Coin | Dir | PnL | Strategy | Regime | Hour | Funding | AI | Reason |")
        lines.append("|---|---|---:|---|---|---:|---:|---:|---|")
        for t in (rep.get("best_trades") or [])[:20]:
            lines.append(
                f"| {t.get('coin')} | {t.get('direction')} | {_fmt(t.get('pnl'))} | "
                f"{t.get('strategy')} | {t.get('market_regime')} | {t.get('hour')} | "
                f"{_fmt(t.get('funding'), digits=4)} | {_fmt(t.get('ai_score'), digits=3)} | "
                f"{t.get('decision_reason')} |"
            )
        lines.append("")

        lines.append("### 3. Worst Trades (Top 20)")
        lines.append("")
        lines.append("| Coin | Dir | PnL | Strategy | Regime | Hour | Funding | AI | Reason |")
        lines.append("|---|---|---:|---|---|---:|---:|---:|---|")
        for t in (rep.get("worst_trades") or [])[:20]:
            lines.append(
                f"| {t.get('coin')} | {t.get('direction')} | {_fmt(t.get('pnl'))} | "
                f"{t.get('strategy')} | {t.get('market_regime')} | {t.get('hour')} | "
                f"{_fmt(t.get('funding'), digits=4)} | {_fmt(t.get('ai_score'), digits=3)} | "
                f"{t.get('decision_reason')} |"
            )
        lines.append("")

        lines.append("### 4. Coin Ranking (by PF)")
        lines.append("")
        lines.append("| Coin | Trades | PnL | PF | E | WR% |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for c in (rep.get("coin_ranking") or []):
            pf = "inf" if c.get("pf_inf") else _fmt(c.get("pf"))
            lines.append(
                f"| {c.get('coin')} | {c.get('trades')} | {_fmt(c.get('pnl'))} | "
                f"{pf} | {_fmt(c.get('expectancy'))} | {_fmt(c.get('winrate'))} |"
            )
        lines.append("")

        lines.append("### 5. Direction Analysis")
        lines.append("")
        for d, m in (rep.get("direction") or {}).items():
            lines.append(
                f"- **{d}**: trades={m.get('trades')} pnl={_fmt(m.get('pnl'))} "
                f"PF={_fmt(m.get('pf'))} E={_fmt(m.get('expectancy'))}"
            )
        lines.append("")

        lines.append("### 6. Market Regime")
        lines.append("")
        lines.append("| Regime | Trades | PnL | PF | WR% |")
        lines.append("|---|---:|---:|---:|---:|")
        for m in (rep.get("market_regime") or []):
            lines.append(
                f"| {m.get('regime')} | {m.get('trades')} | {_fmt(m.get('pnl'))} | "
                f"{_fmt(m.get('pf'))} | {_fmt(m.get('winrate'))} |"
            )
        lines.append("")

        ha = rep.get("hour_analysis") or {}
        lines.append("### 7. Hour Analysis")
        lines.append("")
        lines.append("| Hour | Trades | PnL | PF | WR% |")
        lines.append("|---:|---:|---:|---:|---:|")
        for h in (ha.get("hours") or []):
            if not h.get("trades"):
                continue
            lines.append(
                f"| {h.get('hour')} | {h.get('trades')} | {_fmt(h.get('pnl'))} | "
                f"{_fmt(h.get('pf'))} | {_fmt(h.get('winrate'))} |"
            )
        lines.append("")
        lines.append(
            "**Top 3 hours:** "
            + ", ".join(
                f"{x.get('hour')} (PF={_fmt(x.get('pf'))})"
                for x in (ha.get("top3") or [])
            )
            or "—"
        )
        lines.append(
            "**Worst 3 hours:** "
            + ", ".join(
                f"{x.get('hour')} (PF={_fmt(x.get('pf'))})"
                for x in (ha.get("worst3") or [])
            )
            or "—"
        )
        lines.append("")

        lines.append("### 8. Weekday Analysis")
        lines.append("")
        lines.append("| Day | Trades | PnL | PF | WR% |")
        lines.append("|---|---:|---:|---:|---:|")
        for w in (rep.get("weekday_analysis") or []):
            lines.append(
                f"| {w.get('weekday')} | {w.get('trades')} | {_fmt(w.get('pnl'))} | "
                f"{_fmt(w.get('pf'))} | {_fmt(w.get('winrate'))} |"
            )
        lines.append("")

        for title, key in (
            ("9. Funding Buckets", "funding_buckets"),
            ("10. RSI Buckets", "rsi_buckets"),
            ("11. AI Score Buckets", "ai_score_buckets"),
        ):
            lines.append(f"### {title}")
            lines.append("")
            lines.append("| Bucket | Trades | PnL | PF |")
            lines.append("|---|---:|---:|---:|")
            for b in (rep.get(key) or []):
                lines.append(
                    f"| {b.get('bucket')} | {b.get('trades')} | {_fmt(b.get('pnl'))} | "
                    f"{_fmt(b.get('pf'))} |"
                )
            lines.append("")

        lines.append("### 12. Feature Importance (S59 cached)")
        lines.append("")
        lines.append("| Feature | ΔPF | ΔE | Conf |")
        lines.append("|---|---:|---:|---|")
        for f in (rep.get("feature_importance") or [])[:20]:
            lines.append(
                f"| {f.get('feature')} | {_fmt(f.get('contribution_pf'))} | "
                f"{_fmt(f.get('delta_expectancy'))} | {f.get('confidence')} |"
            )
        if not (rep.get("feature_importance") or []):
            lines.append("| — | — | — | no S59 run yet |")
        lines.append("")

        lines.append("### 13. Best Patterns")
        lines.append("")
        lines.append("| Pattern | Trades | PF | E | Conf |")
        lines.append("|---|---:|---:|---:|---|")
        for p in (rep.get("best_patterns") or [])[:20]:
            lines.append(
                f"| {p.get('pattern')} | {p.get('trades')} | {_fmt(p.get('pf'))} | "
                f"{_fmt(p.get('expectancy'))} | {p.get('confidence')} |"
            )
        if not (rep.get("best_patterns") or []):
            lines.append("| — | — | — | — | run alpha-discovery --force |")
        lines.append("")

        lines.append("### 14. Worst Patterns")
        lines.append("")
        lines.append("| Pattern | Trades | PF | E | Conf |")
        lines.append("|---|---:|---:|---:|---|")
        for p in (rep.get("worst_patterns") or [])[:20]:
            lines.append(
                f"| {p.get('pattern')} | {p.get('trades')} | {_fmt(p.get('pf'))} | "
                f"{_fmt(p.get('expectancy'))} | {p.get('confidence')} |"
            )
        lines.append("")

    lines.append("---")
    lines.append(f"Current market regime (recent): **{report.get('current_market_regime')}**")
    return "\n".join(lines)


def build_ai_context(report: dict[str, Any], *, max_chars: int = 10000) -> str:
    """Compact context for GPT/Claude (~2500 tokens ≈ 8–10k chars)."""
    lifetime = ((report.get("by_period") or {}).get("lifetime") or {})
    metrics = lifetime.get("overall") or {}
    if not metrics:
        chain = (report.get("performance_drift") or {}).get("chain") or []
        metrics = next((c for c in chain if c.get("period") == "lifetime"), {})

    drift = report.get("performance_drift") or {}
    lines = [
        "# AI Context — Trading Intelligence (S62.1)",
        f"generated={report.get('generated_at_iso')} regime={report.get('current_market_regime')}",
        "observe_only=true strategy_unchanged=true",
        "",
        "## Strategy metrics (lifetime)",
        json.dumps(metrics, default=str),
        "",
        "## Performance drift",
        json.dumps(drift, default=str)[:2000],
        "",
        "## Top winners",
        json.dumps((lifetime.get("best_trades") or [])[:10], default=str),
        "",
        "## Top losers",
        json.dumps((lifetime.get("worst_trades") or [])[:10], default=str),
        "",
        "## Best patterns",
        json.dumps((report.get("best_patterns") or [])[:10], default=str),
        "",
        "## Worst patterns",
        json.dumps((report.get("worst_patterns") or [])[:10], default=str),
        "",
        "## Coin ranking",
        json.dumps((lifetime.get("coin_ranking") or [])[:15], default=str),
        "",
        "## Feature importance (S59)",
        json.dumps((report.get("feature_importance") or [])[:12], default=str),
        "",
        "## Current market regime",
        str(report.get("current_market_regime")),
        "",
        "## Top 10 candidate observations",
    ]
    obs: list[str] = []
    for f in (drift.get("flags") or [])[:5]:
        obs.append(f"drift: {f}")
    for c in (lifetime.get("coin_ranking") or [])[:3]:
        obs.append(
            f"coin {c.get('coin')}: PF={c.get('pf')} E={c.get('expectancy')} n={c.get('trades')}"
        )
    for p in (report.get("best_patterns") or [])[:3]:
        obs.append(f"pattern+: {p.get('pattern')} conf={p.get('confidence')}")
    for p in (report.get("worst_patterns") or [])[:2]:
        obs.append(f"pattern-: {p.get('pattern')} conf={p.get('confidence')}")
    ha = (lifetime.get("hour_analysis") or {})
    for h in (ha.get("worst3") or [])[:2]:
        obs.append(f"weak hour {h.get('hour')}: PF={h.get('pf')}")
    for i, oline in enumerate(obs[:10], start=1):
        lines.append(f"{i}. {oline}")
    if not obs:
        lines.append("1. insufficient research snapshots — run trade-postmortem --backfill")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n…[truncated]"
    return text


def export_intelligence_report(
    report: dict[str, Any],
    *,
    out_dir: Path | None = None,
) -> dict[str, str]:
    """Write latest.md, latest.json, ai_context.md under research/reports/intelligence/."""
    out_dir = out_dir or default_report_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "latest.md"
    json_path = out_dir / "latest.json"
    ai_path = out_dir / "ai_context.md"

    md = format_intelligence_markdown(report)
    ai = build_ai_context(report)
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    ai_path.write_text(ai, encoding="utf-8")
    return {
        "markdown": str(md_path),
        "json": str(json_path),
        "ai_context": str(ai_path),
    }


def run_intelligence_report(
    conn: Any,
    *,
    periods: list[str] | None = None,
    out_dir: Path | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Build + export. Read-only on DB; filesystem write for reports only."""
    report = build_intelligence_report(conn, periods=periods, now=now)
    paths = export_intelligence_report(report, out_dir=out_dir)
    report["export_paths"] = paths
    return report


__all__ = [
    "PERIODS",
    "build_ai_context",
    "build_intelligence_report",
    "default_report_dir",
    "export_intelligence_report",
    "format_intelligence_markdown",
    "run_intelligence_report",
]
