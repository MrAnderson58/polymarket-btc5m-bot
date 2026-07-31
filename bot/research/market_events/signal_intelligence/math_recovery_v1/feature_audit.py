"""Part 1 — Full Feature Pipeline audit (read-only)."""

from __future__ import annotations

import json
import math
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.feature_store import (
    FEATURE_SPEC,
    PREDICTION_FEATURES,
    extract_sample,
    load_closed_trade_rows,
)
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)
from bot.research.market_events.signal_intelligence.math_recovery_v1.formula_sources import (
    audit_formula_sources,
)

REPORT_DIR = BASE_DIR / "reports" / "research"
FEATURE_AUDIT_MD = REPORT_DIR / "FEATURE_AUDIT.md"
FEATURE_AUDIT_JSON = REPORT_DIR / "feature_audit.json"

STALE_AGE_SEC = 7 * 86400
LOW_VARIANCE_UNIQUE_MAX = 3
CONSTANT_UNIQUE = 1
EMPTY_FILL_MAX = 0.01
GOOD_FILL_MIN = 0.80


FEATURE_META: dict[str, dict[str, str]] = {
    # column features from S55 / S42 join
    "rsi": {"source_table": "market_events_trade_features_s55", "source_module": "trade_intelligence_s55.build_entry_features"},
    "atr": {"source_table": "market_events_trade_features_s55 / market_snapshots_g3", "source_module": "trade_intelligence_s55.build_entry_features"},
    "volatility": {"source_table": "market_events_trade_features_s55", "source_module": "trade_intelligence_s55 (alias of atr)"},
    "funding": {"source_table": "market_events_trade_features_s55 / market_snapshots_g3", "source_module": "trade_intelligence_s55.build_entry_features"},
    "oi_delta": {"source_table": "market_events_trade_features_s55", "source_module": "trade_intelligence_s55.build_entry_features"},
    "fear_greed": {"source_table": "market_events_trade_features_s55 / market_snapshots_g3", "source_module": "trade_intelligence_s55.build_entry_features"},
    "trend": {"source_table": "market_events_trade_features_s55", "source_module": "trade_intelligence_s55.build_entry_features"},
    "volume": {"source_table": "market_events_trade_features_s55", "source_module": "trade_intelligence_s55.build_entry_features"},
    "ai_score": {"source_table": "market_events_trade_features_s55", "source_module": "trade_intelligence_s55 (confidence alias)"},
    "macro_score": {"source_table": "market_events_trade_features_s55", "source_module": "trade_intelligence_s55"},
    "news_score": {"source_table": "market_events_trade_features_s55", "source_module": "trade_intelligence_s55"},
    "spread": {"source_table": "market_events_trade_features_s55", "source_module": "trade_intelligence_s55"},
    "confidence": {"source_table": "S42.decision_confidence / S40.snapshot_decision_confidence", "source_module": "candidate / decision"},
    "vwap_distance": {"source_table": "derived in feature_store.extract_sample", "source_module": "feature_store"},
    "ema20_distance": {"source_table": "derived in feature_store.extract_sample", "source_module": "feature_store"},
    "ema50_distance": {"source_table": "derived in feature_store.extract_sample", "source_module": "feature_store"},
    "ema200_distance": {"source_table": "derived in feature_store.extract_sample", "source_module": "feature_store"},
    "macd": {"source_table": "features_json", "source_module": "feature_store.extract_sample"},
    "liquidation_metric": {"source_table": "features_json", "source_module": "feature_store"},
    "book_imbalance": {"source_table": "features_json", "source_module": "feature_store"},
    "time_to_expiry": {"source_table": "features_json", "source_module": "feature_store"},
    "btc_move": {"source_table": "features_json", "source_module": "feature_store"},
    "hour": {"source_table": "market_events_trade_features_s55", "source_module": "trade_intelligence_s55"},
    "weekday": {"source_table": "market_events_trade_features_s55", "source_module": "trade_intelligence_s55"},
    "symbol": {"source_table": "market_events_paper_trades_s42", "source_module": "paper / S40"},
    "direction": {"source_table": "market_events_paper_trades_s42", "source_module": "paper / S40"},
    "pattern": {"source_table": "S42.pattern_json", "source_module": "strategy_optimizer._pattern_label"},
    "gate_decision": {"source_table": "market_events_trade_features_s55", "source_module": "trade_intelligence_s55 gate"},
    "news_category": {"source_table": "market_events_paper_trades_s42", "source_module": "news"},
    "market_regime": {"source_table": "market_events_trade_features_s55", "source_module": "market_regime_s57"},
}


def _entropy(values: list[Any]) -> float | None:
    if not values:
        return None
    counts = Counter(values)
    n = len(values)
    h = 0.0
    for c in counts.values():
        p = c / n
        if p > 0:
            h -= p * math.log(p, 2)
    return round(h, 4)


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx < 1e-12 or dy < 1e-12:
        return 0.0
    return round(num / (dx * dy), 4)


def _mutual_info_binned(xs: list[float], ys: list[float], bins: int = 8) -> float | None:
    """Simple histogram MI (bits)."""
    n = len(xs)
    if n < 8 or n != len(ys):
        return None
    # discretize x into quantile bins; y into win/loss or quantile
    xs_s = sorted(xs)
    edges = [xs_s[min(n - 1, int(i * (n - 1) / bins))] for i in range(bins + 1)]

    def xbin(v: float) -> int:
        for i in range(bins):
            if v <= edges[i + 1]:
                return i
        return bins - 1

    # binary label if y mostly pos/neg else quantile
    y_pos = sum(1 for y in ys if y > 0)
    if min(y_pos, n - y_pos) >= 3:
        ybins = [1 if y > 0 else 0 for y in ys]
        y_n = 2
    else:
        ys_s = sorted(ys)
        y_edges = [ys_s[min(n - 1, int(i * (n - 1) / 4))] for i in range(5)]

        def ybin(v: float) -> int:
            for i in range(4):
                if v <= y_edges[i + 1]:
                    return i
            return 3

        ybins = [ybin(y) for y in ys]
        y_n = 4

    xbins = [xbin(x) for x in xs]
    joint: Counter[tuple[int, int]] = Counter(zip(xbins, ybins))
    cx = Counter(xbins)
    cy = Counter(ybins)
    mi = 0.0
    for (xi, yi), c in joint.items():
        pxy = c / n
        px = cx[xi] / n
        py = cy[yi] / n
        if pxy > 0 and px > 0 and py > 0:
            mi += pxy * math.log(pxy / (px * py), 2)
    return round(mi, 4)


def classify_status(
    *,
    fill_pct: float,
    n_unique: int,
    first_seen: int | None,
    last_seen: int | None,
    now: int,
    broken_source: bool = False,
) -> str:
    if broken_source and fill_pct < EMPTY_FILL_MAX * 100:
        return "BROKEN"
    if fill_pct <= EMPTY_FILL_MAX * 100:
        return "EMPTY"
    if n_unique <= CONSTANT_UNIQUE:
        return "CONSTANT"
    if last_seen is not None and (now - int(last_seen)) > STALE_AGE_SEC:
        return "STALE"
    if n_unique <= LOW_VARIANCE_UNIQUE_MAX:
        return "LOW_VARIANCE"
    if fill_pct < GOOD_FILL_MIN * 100:
        return "EMPTY" if fill_pct < 20 else "LOW_VARIANCE"
    return "GOOD"


def _as_scalar(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return float(v)
    s = str(v).strip()
    if not s or s.upper() in ("NULL", "NONE", "NAN"):
        return None
    return s


def audit_feature_values(
    name: str,
    pairs: list[tuple[Any, float | None, int | None]],
    *,
    now: int,
    meta: dict[str, str] | None = None,
    broken_source: bool = False,
) -> dict[str, Any]:
    """pairs: (value, pnl, ts)."""
    n = len(pairs)
    filled_vals: list[Any] = []
    filled_pnl: list[float] = []
    filled_num: list[float] = []
    filled_pnl_num: list[float] = []
    times: list[int] = []
    for v, pnl, ts in pairs:
        sv = _as_scalar(v)
        if sv is None:
            continue
        filled_vals.append(sv)
        if ts is not None:
            times.append(int(ts))
        if pnl is not None:
            filled_pnl.append(float(pnl))
        num = _safe_float(sv) if not isinstance(sv, str) else _safe_float(sv)
        if num is not None and pnl is not None:
            filled_num.append(float(num))
            filled_pnl_num.append(float(pnl))

    null_count = n - len(filled_vals)
    fill_pct = round(100.0 * len(filled_vals) / n, 2) if n else 0.0
    uniq = sorted({str(v) for v in filled_vals}, key=lambda x: x)
    n_unique = len(uniq)
    # constant % = share of mode
    const_pct = 0.0
    mode_val = None
    if filled_vals:
        mode_val, mode_n = Counter(map(str, filled_vals)).most_common(1)[0]
        const_pct = round(100.0 * mode_n / len(filled_vals), 2)

    nums = [float(x) for x in filled_num] if filled_num else []
    # also try coerce all filled
    if not nums:
        nums = [float(x) for x in (_safe_float(v) for v in filled_vals) if x is not None]

    def _stat(fn):
        if not nums:
            return None
        try:
            return round(float(fn(nums)), 6)
        except Exception:
            return None

    median = None
    if nums:
        s = sorted(nums)
        mid = len(s) // 2
        median = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2
        median = round(float(median), 6)
    std = None
    if len(nums) >= 2:
        m = sum(nums) / len(nums)
        std = round(math.sqrt(sum((x - m) ** 2 for x in nums) / len(nums)), 6)

    corr = _pearson(filled_num, filled_pnl_num) if len(filled_num) >= 3 else None
    mi = _mutual_info_binned(filled_num, filled_pnl_num) if len(filled_num) >= 8 else None
    # categorical entropy on stringified values
    ent = _entropy([str(v) for v in filled_vals])

    first_seen = min(times) if times else None
    last_seen = max(times) if times else None
    status = classify_status(
        fill_pct=fill_pct,
        n_unique=n_unique,
        first_seen=first_seen,
        last_seen=last_seen,
        now=now,
        broken_source=broken_source,
    )
    meta = meta or FEATURE_META.get(name, {})
    return {
        "feature": name,
        "source_table": meta.get("source_table", "unknown"),
        "source_module": meta.get("source_module", "unknown"),
        "percent_filled": fill_pct,
        "null_count": null_count,
        "n": n,
        "constant_pct": const_pct,
        "mode": mode_val,
        "unique_values": n_unique,
        "unique_sample": uniq[:20],
        "min": _stat(min),
        "max": _stat(max),
        "median": median,
        "std": std,
        "entropy": ent,
        "mutual_information_vs_pnl": mi,
        "correlation_vs_pnl": corr,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "status": status,
    }


def _load_s55_rows(conn: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        cur = conn.execute(
            """
            SELECT * FROM market_events_trade_features_s55
            ORDER BY COALESCE(closed_at, created_at, id) DESC
            LIMIT 5000
            """
        )
        cols = [d[0] for d in cur.description]
        for r in cur.fetchall():
            d = {cols[i]: r[i] for i in range(len(cols))}
            blob = {}
            raw = d.get("features_json")
            if isinstance(raw, str) and raw.strip():
                try:
                    blob = json.loads(raw) or {}
                except Exception:
                    blob = {}
            d["_blob"] = blob
            rows.append(d)
    except Exception:
        pass
    return rows


def run_feature_audit(
    conn: Any,
    *,
    write_reports: bool = True,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    """Audit every Feature Store prediction feature + key S55 columns."""
    now = int(time.time())
    closed = load_closed_trade_rows(conn)
    samples = [extract_sample(r) for r in closed]
    s55 = _load_s55_rows(conn)
    formulas = audit_formula_sources(conn)

    broken_keys = set()
    for f in formulas.get("formulas") or []:
        if f.get("status") == "BROKEN SOURCE":
            for k in f.get("feature_keys") or []:
                broken_keys.add(k)

    audits: list[dict[str, Any]] = []

    # 1) Feature-store samples (aligned with ML)
    for name in PREDICTION_FEATURES:
        pairs = []
        for s in samples:
            pairs.append((s.get(name), _safe_float(s.get("pnl")), s.get("created_at")))
        audits.append(
            audit_feature_values(
                name,
                pairs,
                now=now,
                broken_source=name in broken_keys
                or name in ("rsi", "vwap_distance", "ema20_distance", "ema50_distance", "ema200_distance"),
            )
        )

    # 2) Extra S55 JSON / column keys not already covered
    extra_keys = [
        "open_interest", "etf_flow", "btc_dominance", "shock_score", "ema_trend",
        "decision_confidence", "regime_score", "regime_confidence", "similar_count",
        "gate_expected_pnl_pct",
    ]
    seen = {a["feature"] for a in audits}
    for name in extra_keys:
        if name in seen:
            continue
        pairs = []
        for r in s55:
            blob = r.get("_blob") or {}
            v = r.get(name)
            if v is None:
                v = blob.get(name)
            pnl = _safe_float(r.get("pnl_usd"))
            if pnl is None:
                pnl = _safe_float(r.get("pnl_pct"))
            pairs.append((v, pnl, r.get("created_at") or r.get("closed_at")))
        meta = FEATURE_META.get(name, {
            "source_table": "market_events_trade_features_s55",
            "source_module": "trade_intelligence_s55 / features_json",
        })
        audits.append(
            audit_feature_values(
                name, pairs, now=now, meta=meta, broken_source=name in broken_keys,
            )
        )

    # sort: broken/empty first then by fill
    order = {"BROKEN": 0, "EMPTY": 1, "CONSTANT": 2, "STALE": 3, "LOW_VARIANCE": 4, "GOOD": 5}
    audits.sort(key=lambda a: (order.get(a["status"], 9), a["feature"]))

    by_status: dict[str, list[str]] = {}
    for a in audits:
        by_status.setdefault(a["status"], []).append(a["feature"])

    result = {
        "ok": True,
        "generated_at": now,
        "n_closed_samples": len(samples),
        "n_s55_rows": len(s55),
        "features": audits,
        "by_status": {k: sorted(v) for k, v in by_status.items()},
        "formula_sources": formulas,
        "read_only": True,
        "gate_trading_unchanged": True,
    }

    md = format_feature_audit_md(result)
    result["report_markdown"] = md
    if write_reports:
        out = Path(os.environ.get("FEATURE_AUDIT_DIR", str(report_dir or REPORT_DIR)))
        out.mkdir(parents=True, exist_ok=True)
        md_path = out / "FEATURE_AUDIT.md"
        json_path = out / "feature_audit.json"
        md_path.write_text(md, encoding="utf-8")
        json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        result["report_paths"] = {"md": str(md_path), "json": str(json_path)}
    return result


def format_feature_audit_md(result: dict[str, Any]) -> str:
    lines = [
        "# Feature Audit V1 — Signal Mathematics Recovery",
        "",
        f"_Generated ts={result.get('generated_at')} | closed samples={result.get('n_closed_samples')} | S55 rows={result.get('n_s55_rows')}_",
        "",
        "Read-only. No Gate / Trading / Paper / Execution changes.",
        "",
        "## Status summary",
        "",
    ]
    for st, feats in sorted((result.get("by_status") or {}).items()):
        lines.append(f"- **{st}**: {len(feats)} — `{', '.join(feats[:30])}`" + ("…" if len(feats) > 30 else ""))
    lines.extend(["", "## Features", ""])
    lines.append(
        "| feature | filled% | nulls | const% | unique | min | max | median | std | entropy | MI|pnl | corr|pnl | status |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for a in result.get("features") or []:
        lines.append(
            "| {feature} | {pct} | {null} | {cp} | {u} | {mn} | {mx} | {med} | {std} | {ent} | {mi} | {corr} | **{st}** |".format(
                feature=a["feature"],
                pct=a["percent_filled"],
                null=a["null_count"],
                cp=a["constant_pct"],
                u=a["unique_values"],
                mn=a.get("min"),
                mx=a.get("max"),
                med=a.get("median"),
                std=a.get("std"),
                ent=a.get("entropy"),
                mi=a.get("mutual_information_vs_pnl"),
                corr=a.get("correlation_vs_pnl"),
                st=a["status"],
            )
        )
    lines.extend(["", "## Formula sources", ""])
    for f in (result.get("formula_sources") or {}).get("formulas") or []:
        lines.append(
            f"### {f['formula']} — `{f['status']}`\n"
            f"- module: `{f.get('compute_module')}`\n"
            f"- function: `{f.get('compute_function')}`\n"
            f"- table: `{f.get('source_table')}`\n"
            f"- collector: `{f.get('collector')}`\n"
            f"- notes: {f.get('notes')}\n"
        )
    lines.extend(["", "## Live table freshness", ""])
    for name, info in ((result.get("formula_sources") or {}).get("live_tables") or {}).items():
        if name == "checked_at":
            continue
        lines.append(f"- `{name}`: {info}")
    lines.append("")
    return "\n".join(lines)


__all__ = ["run_feature_audit", "format_feature_audit_md", "classify_status"]
