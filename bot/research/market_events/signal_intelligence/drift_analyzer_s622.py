"""S62.2 — Drift Analyzer (research-only, statistics only).

Compare Lifetime vs 24h vs 3h vs 1h across every available research dimension.
Explains PF / PnL deterioration. No recommendations. No AI. No strategy changes.
Does not modify intelligence-report or any other report.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from bot.research.market_events.signal_intelligence.feature_lab_s59 import (
    lab_bucket_metrics,
    latest_lab_rows,
    load_lab_trades,
)
from bot.research.market_events.signal_intelligence.trading_intelligence_report_s621 import (
    AI_BUCKETS,
    FUNDING_BUCKETS,
    PERIODS,
    RSI_BUCKETS,
    WEEKDAYS,
    _enrich_decisions,
    _regime_family,
    _safe_float,
    _trade_ts,
    default_report_dir,
    filter_by_period,
    overall_performance,
)

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "bot" / "research" / "market_events" / "__main__.py").exists():
            return p
    return Path.cwd()


def resolve_as_of(rows: list[dict[str, Any]], *, wall_now: int | None = None) -> dict[str, Any]:
    """Anchor period windows.

    Prefer wall clock. If the research DB has no trades inside the last wall-clock
    24h (common after historical backfill), anchor to the latest trade timestamp
    so Lifetime vs 24h/3h/1h remains statistically meaningful.
    """
    wall = int(wall_now if wall_now is not None else time.time())
    max_ts = max((_trade_ts(r) for r in rows), default=0)
    if not rows:
        return {"as_of": wall, "as_of_mode": "wall_clock", "max_trade_ts": 0}
    recent = filter_by_period(rows, "24h", now=wall)
    if recent:
        return {"as_of": wall, "as_of_mode": "wall_clock", "max_trade_ts": max_ts}
    if max_ts > 0:
        return {"as_of": max_ts, "as_of_mode": "latest_trade", "max_trade_ts": max_ts}
    return {"as_of": wall, "as_of_mode": "wall_clock", "max_trade_ts": max_ts}


def _enrich_from_snapshot_json(rows: list[dict[str, Any]]) -> None:
    """Fill missing fields from snapshot_json without mutating report loaders."""
    for r in rows:
        raw = r.get("snapshot_json")
        if not raw:
            continue
        try:
            d = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        if not r.get("market_regime"):
            reg = d.get("market_regime") or (d.get("features") or {}).get("regime_label")
            if reg:
                r["market_regime"] = reg
        if r.get("funding") is None and d.get("funding") is not None:
            r["funding"] = d.get("funding")
        if r.get("rsi") is None:
            rsi = d.get("rsi") or (d.get("features") or {}).get("rsi")
            if rsi is not None:
                r["rsi"] = rsi
        if r.get("ai_score") is None and d.get("ai_score") is not None:
            r["ai_score"] = d.get("ai_score")
        if not r.get("strategy_label"):
            r["strategy_label"] = d.get("strategy") or d.get("source_table")
        feat = d.get("features") or {}
        if isinstance(feat, dict):
            if r.get("spread") is None and feat.get("spread") is not None:
                r["spread"] = feat.get("spread")
            if r.get("volatility") is None and feat.get("volatility_60s") is not None:
                r["volatility"] = feat.get("volatility_60s")


def _gross_loss(rows: list[dict[str, Any]]) -> float:
    return float(
        sum(abs(float(r.get("pnl_usd") or 0.0)) for r in rows if float(r.get("pnl_usd") or 0.0) < 0)
    )


def category_metrics(
    rows: list[dict[str, Any]],
    *,
    period_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Trades / PnL / PF / Expectancy / Win Rate / contribution to total loss."""
    base = lab_bucket_metrics(rows)
    parent = period_rows if period_rows is not None else rows
    parent_gl = _gross_loss(parent)
    bucket_gl = _gross_loss(rows)
    contrib = (
        round(bucket_gl / parent_gl, 6)
        if parent_gl > 1e-12
        else (0.0 if bucket_gl == 0 else 1.0)
    )
    return {
        "trades": base["n"],
        "pnl": base["pnl"],
        "profit_factor": base["pf"],
        "pf_inf": base.get("pf_inf"),
        "expectancy": base["expectancy"],
        "winrate": base["winrate"],
        "contribution_to_total_loss": contrib,
        "gross_loss": round(bucket_gl, 4),
    }


def _group_by(
    rows: list[dict[str, Any]],
    key_fn: Callable[[dict[str, Any]], str | None],
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        k = key_fn(r)
        if k is None or k == "":
            continue
        out[str(k)].append(r)
    return out


def _bucket_by_specs(
    rows: list[dict[str, Any]],
    *,
    value_fn: Callable[[dict[str, Any]], float | None],
    specs: tuple,
) -> dict[str, list[dict[str, Any]]]:
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
    return buckets


def _ai_value(r: dict[str, Any]) -> float | None:
    v = _safe_float(r.get("ai_score"))
    if v is None:
        return None
    if 0 <= v <= 1.0:
        return v * 100.0
    return v


def _strategy_key(r: dict[str, Any]) -> str:
    return str(
        r.get("s40_signal_type")
        or r.get("strategy_label")
        or r.get("exit_reason")
        or "unknown"
    )


def build_dimension_slices(rows: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """dimension → category → trades."""
    dims: dict[str, dict[str, list[dict[str, Any]]]] = {}
    dims["coin"] = _group_by(
        rows,
        lambda r: str(r.get("symbol") or "?").upper().replace("USDT", "") or None,
    )
    dims["direction"] = _group_by(
        rows,
        lambda r: (lambda d: d if d in ("LONG", "SHORT") else None)(
            str(r.get("direction") or "").upper()
        ),
    )
    dims["market_regime"] = _group_by(rows, lambda r: _regime_family(r.get("market_regime")))
    dims["hour"] = _group_by(
        rows,
        lambda r: (
            None
            if r.get("hour") is None
            else (f"H{int(r['hour']):02d}" if 0 <= int(r["hour"]) <= 23 else None)
        ),
    )
    dims["weekday"] = _group_by(
        rows,
        lambda r: (
            None
            if r.get("weekday") is None
            else (WEEKDAYS[int(r["weekday"])] if 0 <= int(r["weekday"]) <= 6 else None)
        ),
    )
    dims["strategy_version"] = _group_by(rows, _strategy_key)
    dims["funding_bucket"] = _bucket_by_specs(
        rows,
        value_fn=lambda r: _safe_float(r.get("funding")),
        specs=FUNDING_BUCKETS,
    )
    dims["rsi_bucket"] = _bucket_by_specs(
        rows,
        value_fn=lambda r: _safe_float(r.get("rsi")),
        specs=RSI_BUCKETS,
    )
    dims["ai_score_bucket"] = _bucket_by_specs(
        rows,
        value_fn=_ai_value,
        specs=AI_BUCKETS,
    )
    return dims


def _load_pattern_defs(conn: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Existing pattern IDs from S62/S61, with live predicates when fingerprint matches."""
    stored: list[dict[str, Any]] = []
    for table in (
        "market_events_alpha_discovery_s62",
        "market_events_strategy_discovery_s61",
    ):
        try:
            latest = conn.execute(f"SELECT MAX(run_id) AS r FROM {table}").fetchone()
            run_id = int(latest["r"] or 0) if latest else 0
            if not run_id:
                continue
            qrows = conn.execute(
                f"SELECT * FROM {table} WHERE run_id = ? ORDER BY rank ASC LIMIT 80",
                (run_id,),
            ).fetchall()
            for r in qrows:
                d = dict(r)
                stored.append({
                    "pattern_id": str(d.get("fingerprint") or d.get("id")),
                    "rule_text": d.get("rule_text") or "—",
                    "mode": d.get("mode") or "ALLOW",
                    "source": table,
                })
        except Exception:
            continue

    if not stored:
        return []

    fn_by_fp: dict[str, Any] = {}
    try:
        from bot.research.market_events.signal_intelligence.alpha_discovery_s62 import (
            generate_patterns,
        )
        for p in generate_patterns(rows):
            fp = str(p.get("fingerprint") or "")
            if fp:
                fn_by_fp[fp] = p
    except Exception as exc:
        logger.debug("pattern rebuild failed: %s", exc)

    out = []
    for s in stored:
        pid = s["pattern_id"]
        live = fn_by_fp.get(pid)
        if live is None:
            out.append({**s, "fn": None, "live_mode": s["mode"]})
            continue
        out.append({
            **s,
            "fn": live.get("fn"),
            "live_mode": live.get("mode") or s["mode"],
            "rule_text": live.get("rule_text") or s["rule_text"],
        })
    return out


def _pattern_matched(
    rows: list[dict[str, Any]],
    pattern: dict[str, Any],
) -> list[dict[str, Any]]:
    fn = pattern.get("fn")
    if fn is None:
        return []
    mode = str(pattern.get("live_mode") or pattern.get("mode") or "ALLOW").upper()
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for r in rows:
        try:
            v = fn(r)
        except Exception:
            continue
        if v is None:
            continue
        (matched if v else unmatched).append(r)
    if mode == "BLOCK":
        return unmatched
    return matched


def _feature_importance_defs(conn: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Existing S59 feature importance rows + live ON predicates when feature name matches."""
    try:
        lab = latest_lab_rows(conn)
    except Exception:
        lab = []
    if not lab:
        return []

    pred_by_feature: dict[str, Callable[[dict[str, Any]], bool | None]] = {}
    try:
        from bot.research.market_events.signal_intelligence.feature_lab_s59 import (
            _build_filters,
        )
        for f in _build_filters(rows):
            pred_by_feature[str(f.get("feature"))] = f["fn"]
    except Exception as exc:
        logger.debug("feature filter rebuild failed: %s", exc)

    out = []
    for r in lab:
        name = str(r.get("feature") or "")
        if not name:
            continue
        out.append({
            "feature": name,
            "filter_rule": r.get("filter_rule"),
            "contribution_pf": _safe_float(r.get("contribution_pf")),
            "delta_pf": _safe_float(r.get("delta_pf")),
            "fn": pred_by_feature.get(name),
        })
    return out


def _pf_num(m: dict[str, Any] | None) -> float | None:
    if not m:
        return None
    if m.get("pf_inf"):
        return 99.0
    return _safe_float(m.get("profit_factor"))


def _delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return round(float(a) - float(b), 6)


def _row_uid(r: dict[str, Any]) -> tuple:
    return (
        str(r.get("s40_signal_type") or ""),
        int(r.get("s40_signal_id") or 0),
        int(r.get("paper_trade_id") or 0),
        int(_trade_ts(r)),
        float(r.get("pnl_usd") or 0.0),
    )


def _fmt(v: Any, *, digits: int = 4) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f) >= 100:
        return f"{f:.2f}"
    return f"{f:.{digits}g}"


def analyze_category_across_periods(
    *,
    dimension: str,
    category: str,
    period_map: dict[str, list[dict[str, Any]]],
    pred: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    """Compute per-period metrics for one category."""
    periods_out: dict[str, Any] = {}
    for period, prow in period_map.items():
        bucket = [r for r in prow if pred(r)]
        periods_out[period] = category_metrics(bucket, period_rows=prow)

    life = periods_out.get("lifetime") or {}
    h24 = periods_out.get("24h") or {}
    delta_pf = _delta(_pf_num(h24), _pf_num(life))
    delta_exp = _delta(_safe_float(h24.get("expectancy")), _safe_float(life.get("expectancy")))
    delta_pnl = _delta(_safe_float(h24.get("pnl")), _safe_float(life.get("pnl")))
    delta_wr = _delta(_safe_float(h24.get("winrate")), _safe_float(life.get("winrate")))
    n24 = int(h24.get("trades") or 0)
    weight = math.sqrt(max(n24, 0))

    det = 0.0
    if delta_exp is not None and delta_exp < 0:
        det += abs(delta_exp) * max(weight, 1.0)
    if delta_pf is not None and delta_pf < 0:
        det += abs(delta_pf) * max(weight, 1.0) * 0.25
    # Loss mass only counts toward deterioration when the bucket itself is red
    # and expectancy worsened vs lifetime.
    if float(h24.get("pnl") or 0.0) < 0 and (delta_exp is None or delta_exp < 0):
        det += float(h24.get("contribution_to_total_loss") or 0.0) * abs(
            float(h24.get("pnl") or 0.0)
        )

    imp = 0.0
    if delta_exp is not None and delta_exp > 0:
        imp += delta_exp * max(weight, 1.0)
    if delta_pf is not None and delta_pf > 0:
        imp += delta_pf * max(weight, 1.0) * 0.25
    if float(h24.get("pnl") or 0.0) > 0 and (delta_exp is None or delta_exp >= 0):
        imp += float(h24.get("pnl") or 0.0) * 0.1

    return {
        "dimension": dimension,
        "category": category,
        "periods": periods_out,
        "vs_lifetime_24h": {
            "delta_profit_factor": delta_pf,
            "delta_expectancy": delta_exp,
            "delta_pnl": delta_pnl,
            "delta_winrate": delta_wr,
            "deterioration_score": round(det, 6),
            "improvement_score": round(imp, 6),
        },
        "reason": (
            f"{dimension}={category}: "
            f"PF {_fmt(_pf_num(life))}→{_fmt(_pf_num(h24))} "
            f"(Δ{_fmt(delta_pf)}), "
            f"E {_fmt(life.get('expectancy'))}→{_fmt(h24.get('expectancy'))} "
            f"(Δ{_fmt(delta_exp)}), "
            f"24h PnL={_fmt(h24.get('pnl'))} "
            f"loss_share={_fmt(h24.get('contribution_to_total_loss'), digits=3)}"
        ),
    }


def run_drift_analyzer(
    conn: Any,
    *,
    now: int | None = None,
    report_dir: Path | None = None,
    top_n: int = 20,
) -> dict[str, Any]:
    t0 = time.time()
    rows = load_lab_trades(conn)
    _enrich_decisions(conn, rows)
    _enrich_from_snapshot_json(rows)

    as_of_info = resolve_as_of(rows, wall_now=now)
    as_of = int(as_of_info["as_of"])

    period_map: dict[str, list[dict[str, Any]]] = {
        p: filter_by_period(rows, p, now=as_of) for p in PERIODS
    }
    overall = {p: overall_performance(period_map[p]) for p in PERIODS}

    lifetime = period_map["lifetime"]
    slices = build_dimension_slices(lifetime)

    analyses: list[dict[str, Any]] = []
    for dim, cats in slices.items():
        for cat, members in cats.items():
            uids = {_row_uid(r) for r in members}

            def _pred(r: dict[str, Any], _uids=uids) -> bool:
                return _row_uid(r) in _uids

            analyses.append(
                analyze_category_across_periods(
                    dimension=dim,
                    category=cat,
                    period_map=period_map,
                    pred=_pred,
                )
            )

    for pdef in _load_pattern_defs(conn, rows):
        pid = pdef["pattern_id"]
        label = f"{pid[:12]}…({pdef.get('rule_text') or ''})"[:80]
        matched = _pattern_matched(lifetime, pdef)
        uids = {_row_uid(r) for r in matched}

        def _pred(r: dict[str, Any], _uids=uids) -> bool:
            return _row_uid(r) in _uids

        analyses.append(
            analyze_category_across_periods(
                dimension="pattern_id",
                category=label,
                period_map=period_map,
                pred=_pred,
            )
        )

    for fdef in _feature_importance_defs(conn, rows):
        fn = fdef.get("fn")
        name = fdef["feature"]
        uids: set[tuple] = set()
        if fn is not None:
            for r in lifetime:
                try:
                    v = fn(r)
                except Exception:
                    continue
                if v is True:
                    uids.add(_row_uid(r))

        def _pred(r: dict[str, Any], _uids=uids) -> bool:
            return _row_uid(r) in _uids

        analyses.append(
            analyze_category_across_periods(
                dimension="feature_importance",
                category=f"{name} ON",
                period_map=period_map,
                pred=_pred,
            )
        )

    active = [
        a for a in analyses
        if int((a.get("periods") or {}).get("24h", {}).get("trades") or 0) > 0
        or int((a.get("periods") or {}).get("lifetime", {}).get("trades") or 0) >= 5
    ]
    deterioration = sorted(
        [
            a for a in active
            if float((a.get("vs_lifetime_24h") or {}).get("deterioration_score") or 0) > 0
        ],
        key=lambda a: -float(a["vs_lifetime_24h"]["deterioration_score"]),
    )[:top_n]
    improvement = sorted(
        [
            a for a in active
            if float((a.get("vs_lifetime_24h") or {}).get("improvement_score") or 0) > 0
        ],
        key=lambda a: -float(a["vs_lifetime_24h"]["improvement_score"]),
    )[:top_n]

    loss_where = sorted(
        [
            a for a in analyses
            if float((a.get("periods") or {}).get("24h", {}).get("pnl") or 0) < 0
        ],
        key=lambda a: float(a["periods"]["24h"]["pnl"]),
    )[:top_n]

    dim_pf: dict[str, float] = defaultdict(float)
    for a in analyses:
        dlt = (a.get("vs_lifetime_24h") or {}).get("delta_profit_factor")
        n24 = int((a.get("periods") or {}).get("24h", {}).get("trades") or 0)
        if dlt is not None and dlt < 0 and n24 > 0:
            dim_pf[str(a["dimension"])] += abs(float(dlt)) * math.sqrt(n24)
    pf_by_dimension = sorted(
        [
            {"dimension": k, "weighted_pf_deterioration": round(v, 6)}
            for k, v in dim_pf.items()
        ],
        key=lambda x: -x["weighted_pf_deterioration"],
    )

    what_changed = []
    life_o, h24_o = overall.get("lifetime") or {}, overall.get("24h") or {}
    for metric, key in (
        ("profit_factor", "profit_factor"),
        ("expectancy", "expectancy"),
        ("winrate", "winrate"),
        ("pnl", "pnl"),
        ("trades", "trades"),
    ):
        b, c = life_o.get(key), h24_o.get(key)
        if metric == "trades":
            delta: Any = (int(c or 0) - int(b or 0)) if (b is not None or c is not None) else None
        else:
            delta = _delta(_safe_float(c), _safe_float(b))
        what_changed.append({
            "metric": metric,
            "lifetime": b,
            "24h": c,
            "delta": delta,
        })

    # Slim categories for JSON (full metrics kept; drop ultra-empty)
    categories_out = [
        a for a in analyses
        if int((a.get("periods") or {}).get("lifetime", {}).get("trades") or 0) > 0
    ]

    report = {
        "ok": True,
        "stage": "S62.2",
        "as_of": as_of,
        "as_of_mode": as_of_info["as_of_mode"],
        "max_trade_ts": as_of_info["max_trade_ts"],
        "n_trades_loaded": len(rows),
        "periods": list(PERIODS),
        "overall": overall,
        "what_changed": what_changed,
        "dimensions_analyzed": sorted(slices.keys()) + ["pattern_id", "feature_importance"],
        "categories": categories_out,
        "top_deterioration": deterioration,
        "top_improvement": improvement,
        "where_lost_money_24h": loss_where,
        "pf_deterioration_by_dimension": pf_by_dimension,
        "elapsed_sec": round(time.time() - t0, 3),
    }

    out_dir = Path(report_dir) if report_dir else default_report_dir(_repo_root())
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "drift_report.md"
    json_path = out_dir / "drift_report.json"
    md_path.write_text(format_drift_markdown(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["export_paths"] = {
        "markdown": str(md_path.resolve()),
        "json": str(json_path.resolve()),
    }
    return report


def format_drift_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Drift Report (S62.2)",
        "",
        f"_as_of={report.get('as_of')} mode={report.get('as_of_mode')} "
        f"trades_loaded={report.get('n_trades_loaded')} "
        f"elapsed={report.get('elapsed_sec')}s_",
        "",
        "## What changed? (Lifetime → 24h)",
        "",
        "| Metric | Lifetime | 24h | Δ |",
        "|---|---:|---:|---:|",
    ]
    for w in report.get("what_changed") or []:
        lines.append(
            f"| {w.get('metric')} | {_fmt(w.get('lifetime'))} | {_fmt(w.get('24h'))} | {_fmt(w.get('delta'))} |"
        )

    lines.extend([
        "",
        "## Overall by period",
        "",
        "| Period | Trades | PnL | PF | Expectancy | WinRate |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for p in report.get("periods") or PERIODS:
        o = (report.get("overall") or {}).get(p) or {}
        lines.append(
            f"| {p} | {o.get('trades')} | {_fmt(o.get('pnl'))} | {_fmt(o.get('profit_factor'))} | "
            f"{_fmt(o.get('expectancy'))} | {_fmt(o.get('winrate'))} |"
        )

    lines.extend(["", "## Where did we lose money? (24h, worst categories)", ""])
    for i, a in enumerate(report.get("where_lost_money_24h") or [], 1):
        h = (a.get("periods") or {}).get("24h") or {}
        lines.append(
            f"{i}. **{a.get('dimension')}={a.get('category')}** — "
            f"PnL={_fmt(h.get('pnl'))} trades={h.get('trades')} "
            f"loss_share={_fmt(h.get('contribution_to_total_loss'), digits=3)}"
        )
    if not (report.get("where_lost_money_24h") or []):
        lines.append("_No negative-PnL categories in 24h window._")

    lines.extend(["", "## Which dimensions explain most PF deterioration?", ""])
    for row in report.get("pf_deterioration_by_dimension") or []:
        lines.append(
            f"- **{row.get('dimension')}**: weighted_pf_deterioration="
            f"{_fmt(row.get('weighted_pf_deterioration'))}"
        )
    if not (report.get("pf_deterioration_by_dimension") or []):
        lines.append("_No PF deterioration detected vs lifetime._")

    lines.extend(["", "## TOP 20 reasons for deterioration", ""])
    for i, a in enumerate(report.get("top_deterioration") or [], 1):
        vs = a.get("vs_lifetime_24h") or {}
        lines.append(
            f"{i}. {a.get('reason')} "
            f"[score={_fmt(vs.get('deterioration_score'))}]"
        )
    if not (report.get("top_deterioration") or []):
        lines.append("_None._")

    lines.extend(["", "## TOP 20 reasons for improvement", ""])
    for i, a in enumerate(report.get("top_improvement") or [], 1):
        vs = a.get("vs_lifetime_24h") or {}
        lines.append(
            f"{i}. {a.get('reason')} "
            f"[score={_fmt(vs.get('improvement_score'))}]"
        )
    if not (report.get("top_improvement") or []):
        lines.append("_None._")

    lines.extend([
        "",
        "## Category detail (compact)",
        "",
        "| Dimension | Category | Life n | Life PF | Life E | 24h n | 24h PF | 24h E | 24h loss% |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    ranked = sorted(
        report.get("categories") or [],
        key=lambda a: -(
            float((a.get("vs_lifetime_24h") or {}).get("deterioration_score") or 0)
            + float((a.get("vs_lifetime_24h") or {}).get("improvement_score") or 0)
        ),
    )[:60]
    for a in ranked:
        life = (a.get("periods") or {}).get("lifetime") or {}
        h24 = (a.get("periods") or {}).get("24h") or {}
        lines.append(
            f"| {a.get('dimension')} | {a.get('category')} | {life.get('trades')} | "
            f"{_fmt(_pf_num(life))} | {_fmt(life.get('expectancy'))} | {h24.get('trades')} | "
            f"{_fmt(_pf_num(h24))} | {_fmt(h24.get('expectancy'))} | "
            f"{_fmt(h24.get('contribution_to_total_loss'), digits=3)} |"
        )

    lines.extend(["", "---", "_Statistics only. No recommendations. No AI._", ""])
    return "\n".join(lines)


def format_drift_summary(report: dict[str, Any]) -> str:
    lines = [
        "S62.2 Drift Analyzer",
        f"  trades_loaded={report.get('n_trades_loaded')}  "
        f"as_of_mode={report.get('as_of_mode')}  elapsed={report.get('elapsed_sec')}s",
    ]
    o = report.get("overall") or {}
    for p in PERIODS:
        cur = o.get(p) or {}
        lines.append(
            f"  {p}: n={cur.get('trades')} pnl={_fmt(cur.get('pnl'))} "
            f"pf={_fmt(cur.get('profit_factor'))} E={_fmt(cur.get('expectancy'))}"
        )
    det = report.get("top_deterioration") or []
    if det:
        lines.append("  top deterioration:")
        for a in det[:5]:
            lines.append(f"    - {a.get('reason')}")
    paths = report.get("export_paths") or {}
    for k, p in paths.items():
        lines.append(f"  {k}: {p}")
    return "\n".join(lines)


__all__ = [
    "format_drift_markdown",
    "format_drift_summary",
    "run_drift_analyzer",
]
