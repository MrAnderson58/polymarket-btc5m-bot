"""Feature Recovery V2 — freshness / completeness / validate / health CLI."""

from __future__ import annotations

import json
import math
import os
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.feature_recovery_v2 import (
    compute_live_feature_vector,
)
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)

REPORT_PATH = BASE_DIR / "FEATURE_RECOVERY_REPORT.md"
REPORT_DIR = BASE_DIR / "reports" / "research"

# TTL seconds by feature family
FEATURE_TTL: dict[str, int] = {
    "rsi": 900,
    "ema20_distance": 900,
    "ema50_distance": 900,
    "ema200_distance": 900,
    "vwap_distance": 900,
    "atr": 900,
    "atr_pct": 900,
    "macd": 900,
    "macd_hist": 900,
    "bb_pct_b": 900,
    "adx": 900,
    "stoch_k": 900,
    "trend": 900,
    "slope": 900,
    "funding": 300,
    "funding_delta": 300,
    "open_interest": 300,
    "oi_delta": 300,
    "fear_greed": 3600,
    "volume": 900,
    "volatility": 900,
    "confidence": 3600,
    "ai_score": 3600,
}

CORE_FEATURES = list(FEATURE_TTL.keys()) + [
    "ema20", "ema50", "ema200", "vwap", "bb_upper", "bb_lower", "stoch_d", "macd_signal",
]

S55_FRESH_MAX_AGE = int(os.environ.get("FEATURE_AUDIT_FRESH_MAX_AGE_SEC", str(7 * 86400)))


def feature_source_registry() -> dict[str, dict[str, Any]]:
    candle = {
        "collector": "historical_candles → feature_recovery_v2.indicators",
        "sql": "SELECT open_ts,open,high,low,close,volume FROM market_events_historical_candles WHERE symbol=? …",
        "function": "indicators_from_bars",
        "table": "market_events_historical_candles",
    }
    snap = {
        "collector": "g3 snapshot collector",
        "sql": "SELECT … FROM market_snapshots_g3 ORDER BY snapshot_ts DESC",
        "function": "enrich_entry_features",
        "table": "market_snapshots_g3",
    }
    return {
        "rsi": {**candle, "function": "compute_rsi"},
        "ema20_distance": {**candle, "function": "compute_ema + pct_distance"},
        "ema50_distance": {**candle, "function": "compute_ema + pct_distance"},
        "ema200_distance": {**candle, "function": "compute_ema + pct_distance"},
        "vwap_distance": {**candle, "function": "compute_vwap"},
        "atr": {**candle, "function": "compute_atr (rejects stub 50)"},
        "atr_pct": {**candle, "function": "atr_pct"},
        "macd": {**candle, "function": "compute_macd"},
        "macd_hist": {**candle, "function": "compute_macd"},
        "bb_pct_b": {**candle, "function": "compute_bollinger"},
        "adx": {**candle, "function": "compute_adx"},
        "stoch_k": {**candle, "function": "compute_stochastic"},
        "trend": {**candle, "function": "compute_trend_score"},
        "slope": {**candle, "function": "compute_slope"},
        "volume": {**candle, "function": "volume_ma20 / snapshot volume"},
        "volatility": {**candle, "function": "atr_pct (not atr alias)"},
        "funding": {**snap, "collector": "g3 snapshot / Bybit funding"},
        "funding_delta": {**snap, "collector": "g3 snapshot funding delta"},
        "open_interest": {**snap, "collector": "g3 snapshot OI"},
        "oi_delta": {**snap, "collector": "g3 snapshot OI delta"},
        "fear_greed": {**snap, "collector": "macro fear&greed → g3 snapshot"},
        "confidence": {
            "collector": "S40 snapshot_decision_confidence",
            "sql": "market_events_signal_learning_s40_signals.snapshot_decision_confidence",
            "function": "build_entry_features",
            "table": "market_events_signal_learning_s40_signals",
        },
        "ai_score": {
            "collector": "NONE (no longer aliases confidence)",
            "sql": None,
            "function": None,
            "table": None,
            "status": "BROKEN_SOURCE_UNTIL_AI_PRODUCER",
        },
    }


def run_feature_freshness(conn: Any) -> dict[str, Any]:
    now = int(time.time())
    live = compute_live_feature_vector(conn, symbol="BTC")
    candle_ts = live.get("_candle_last_ts")
    snap_ts = live.get("_snapshot_ts")
    rows = []
    for feat, ttl in FEATURE_TTL.items():
        src = feature_source_registry().get(feat, {})
        table = src.get("table")
        if table == "market_events_historical_candles":
            age = None if candle_ts is None else now - int(candle_ts)
            last = candle_ts
        elif table == "market_snapshots_g3":
            age = None if snap_ts is None else now - int(snap_ts)
            last = snap_ts
        else:
            age = None
            last = None
        if age is None and live.get(feat) is None and src.get("status"):
            status = "BROKEN_SOURCE"
        elif age is None:
            status = "UNKNOWN"
        elif age <= ttl:
            status = "fresh"
        else:
            status = "STALE"
        rows.append({
            "feature": feat,
            "age_sec": age,
            "ttl_sec": ttl,
            "status": status,
            "collector": src.get("collector"),
            "last_update": last,
            "live_value": live.get(feat),
        })
    md = ["# Feature Freshness V2", ""]
    md.append("| feature | age | ttl | status | live | collector |")
    md.append("|---|---:|---:|---|---|---|")
    for r in rows:
        md.append(
            f"| {r['feature']} | {r['age_sec']} | {r['ttl_sec']} | **{r['status']}** | "
            f"{r['live_value']} | `{r['collector']}` |"
        )
    text = "\n".join(md) + "\n"
    out = {"ok": True, "rows": rows, "report_markdown": text, "live_symbol": "BTC"}
    path = REPORT_DIR / "FEATURE_FRESHNESS.md"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    out["report_path"] = str(path)
    return out


def _extract_from_s55_row(r: dict[str, Any]) -> dict[str, Any]:
    blob = {}
    raw = r.get("features_json")
    if isinstance(raw, str) and raw.strip():
        try:
            blob = json.loads(raw) or {}
        except Exception:
            blob = {}
    out = dict(blob)
    for k in (
        "rsi", "atr", "funding", "oi_delta", "fear_greed", "trend", "volume",
        "volatility", "ai_score", "macro_score", "news_score", "spread",
    ):
        if out.get(k) is None and r.get(k) is not None:
            out[k] = r.get(k)
    out["_created_at"] = r.get("created_at")
    out["_closed_at"] = r.get("closed_at")
    out["pnl"] = _safe_float(r.get("pnl_usd")) or _safe_float(r.get("pnl_pct"))
    return out


def load_fresh_s55(conn: Any, *, max_age_sec: int | None = None, limit: int = 50_000) -> list[dict[str, Any]]:
    max_age = S55_FRESH_MAX_AGE if max_age_sec is None else int(max_age_sec)
    cutoff = int(time.time()) - max_age
    rows = []
    try:
        cur = conn.execute(
            """
            SELECT * FROM market_events_trade_features_s55
            WHERE created_at >= ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (cutoff, limit),
        )
        cols = [d[0] for d in cur.description]
        for r in cur.fetchall():
            d = {cols[i]: r[i] for i in range(len(cols))}
            rows.append(_extract_from_s55_row(d))
    except Exception:
        pass
    return rows


def run_feature_completeness(conn: Any) -> dict[str, Any]:
    # Prefer live-computed vectors across symbols for "recovered" completeness
    symbols = []
    try:
        symbols = [
            str(r[0]) for r in conn.execute(
                """
                SELECT DISTINCT symbol FROM market_events_historical_candles
                WHERE timeframe='5m' ORDER BY symbol LIMIT 20
                """
            ).fetchall()
        ]
    except Exception:
        symbols = ["BTC", "ETH", "SOL"]
    vectors = []
    for sym in symbols:
        try:
            vectors.append(compute_live_feature_vector(conn, symbol=sym))
        except Exception:
            continue
    features = CORE_FEATURES
    rows = []
    n = len(vectors) or 1
    for f in features:
        vals = []
        for v in vectors:
            x = v.get(f)
            if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
                continue
            vals.append(x)
        filled = len(vals)
        null_pct = round(100.0 * (n - filled) / n, 2)
        uniq = len({round(float(x), 8) if isinstance(x, (int, float)) else str(x) for x in vals})
        const_pct = 0.0
        if vals:
            mode, mc = Counter(
                round(float(x), 8) if isinstance(x, (int, float)) else str(x) for x in vals
            ).most_common(1)[0]
            const_pct = round(100.0 * mc / len(vals), 2)
        variance = None
        nums = [float(x) for x in vals if isinstance(x, (int, float))]
        if len(nums) >= 2:
            variance = round(statistics.pvariance(nums), 8)
        rows.append({
            "feature": f,
            "filled_pct": round(100.0 * filled / n, 2),
            "null_pct": null_pct,
            "constant_pct": const_pct,
            "unique": uniq,
            "variance": variance,
            "n_symbols": n,
        })
    md = ["# Feature Completeness V2 (live candle enrichment)", ""]
    md.append(f"_symbols={n}_")
    md.append("")
    md.append("| feature | filled% | null% | const% | unique | variance |")
    md.append("|---|---:|---:|---:|---:|---:|")
    for r in rows:
        md.append(
            f"| {r['feature']} | {r['filled_pct']} | {r['null_pct']} | {r['constant_pct']} | "
            f"{r['unique']} | {r['variance']} |"
        )
    text = "\n".join(md) + "\n"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "FEATURE_COMPLETENESS.md"
    path.write_text(text, encoding="utf-8")
    return {"ok": True, "rows": rows, "report_markdown": text, "report_path": str(path)}


def validate_feature_vector(vec: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    now = int(time.time())

    def bad(feat: str, rule: str, value: Any = None) -> None:
        issues.append({"feature": feat, "rule": rule, "value": value})

    for k, v in list(vec.items()):
        if isinstance(v, float) and math.isnan(v):
            bad(k, "NaN", v)
        if isinstance(v, float) and math.isinf(v):
            bad(k, "Inf", v)
    rsi = _safe_float(vec.get("rsi"))
    if rsi is not None and (rsi < 0 or rsi > 100):
        bad("rsi", "RSI_OUT_OF_RANGE", rsi)
    atr = _safe_float(vec.get("atr"))
    if atr is not None and atr < 0:
        bad("atr", "NEGATIVE_ATR", atr)
    if atr == 50.0:
        bad("atr", "STUB_ATR_50", atr)
    for ek in ("ema20", "ema50", "ema200", "vwap"):
        v = _safe_float(vec.get(ek))
        if v is not None and v <= 0:
            bad(ek, "EMA_OR_VWAP_INVALID", v)
    ts = vec.get("_candle_last_ts") or vec.get("_snapshot_ts")
    if ts is not None and int(ts) > now + 3600:
        bad("_timestamp", "FUTURE_TIMESTAMP", ts)
    # constant detection across selected keys when values equal stubs
    if _safe_float(vec.get("volatility")) is not None and atr is not None:
        if abs(float(vec["volatility"]) - float(atr)) < 1e-12:
            bad("volatility", "DUPLICATE_OF_ATR", vec.get("volatility"))
    conf = _safe_float(vec.get("confidence"))
    ai = _safe_float(vec.get("ai_score"))
    if conf is not None and ai is not None and abs(conf - ai) < 1e-12:
        bad("ai_score", "DUPLICATE_OF_CONFIDENCE", ai)
    if vec.get("_candle_source") == "BROKEN_SOURCE":
        bad("_candle_source", "BROKEN_SOURCE", None)
    return issues


def run_feature_validate(conn: Any) -> dict[str, Any]:
    symbols = ["BTC", "ETH", "SOL", "XRP", "BNB"]
    all_issues = []
    vectors = []
    for sym in symbols:
        vec = compute_live_feature_vector(conn, symbol=sym)
        vectors.append(vec)
        for iss in validate_feature_vector(vec):
            iss["symbol"] = sym
            all_issues.append(iss)
    md = ["# Feature Validate V2", "", f"issues={len(all_issues)}", ""]
    for i in all_issues[:50]:
        md.append(f"- `{i.get('symbol')}` **{i['feature']}**: {i['rule']} value={i.get('value')}")
    if not all_issues:
        md.append("_No validation issues on live vectors._")
    text = "\n".join(md) + "\n"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / "FEATURE_VALIDATE.md"
    path.write_text(text, encoding="utf-8")
    return {
        "ok": len(all_issues) == 0,
        "issues": all_issues,
        "n_vectors": len(vectors),
        "report_markdown": text,
        "report_path": str(path),
        "sample_btc": {k: vectors[0].get(k) for k in CORE_FEATURES if vectors},
    }


def classify_health_row(comp: dict[str, Any], fresh: dict[str, Any], issues: list[dict[str, Any]]) -> str:
    feat = comp["feature"]
    if any(i["feature"] == feat and i["rule"] in ("BROKEN_SOURCE", "STUB_ATR_50") for i in issues):
        return "BROKEN"
    if fresh.get("status") == "BROKEN_SOURCE":
        return "BROKEN"
    if feat == "confidence" and comp.get("filled_pct", 0) < 50:
        return "WARNING"
    if feat == "ai_score":
        return "BROKEN"
    if comp.get("filled_pct", 0) < 50:
        return "BROKEN"
    # Macro/snapshot features may be identical across symbols at one timestamp
    macro = {
        "funding", "funding_delta", "open_interest", "oi_delta", "fear_greed",
    }
    if feat not in macro:
        if comp.get("constant_pct", 0) >= 99 and comp.get("unique", 0) <= 1:
            return "BROKEN"
    if fresh.get("status") == "STALE":
        return "WARNING"
    if comp.get("filled_pct", 0) < 90:
        return "WARNING"
    if fresh.get("live_value") is None and feat in ("confidence", "ai_score"):
        return "WARNING" if feat == "confidence" else "BROKEN"
    return "GOOD"


def run_feature_health(conn: Any, *, write_recovery_report: bool = True) -> dict[str, Any]:
    fresh = run_feature_freshness(conn)
    comp = run_feature_completeness(conn)
    val = run_feature_validate(conn)
    fresh_map = {r["feature"]: r for r in fresh["rows"]}
    comp_map = {r["feature"]: r for r in comp["rows"]}
    health_rows = []
    for f in FEATURE_TTL:
        status = classify_health_row(
            comp_map.get(f, {"feature": f, "filled_pct": 0, "constant_pct": 100, "unique": 0}),
            fresh_map.get(f, {}),
            val.get("issues") or [],
        )
        health_rows.append({
            "feature": f,
            "status": status,
            "filled_pct": (comp_map.get(f) or {}).get("filled_pct"),
            "fresh": (fresh_map.get(f) or {}).get("status"),
            "live_value": (fresh_map.get(f) or {}).get("live_value"),
        })
    counts = Counter(r["status"] for r in health_rows)
    recovered = [r for r in health_rows if r["status"] == "GOOD" and r.get("live_value") is not None]
    broken = [r for r in health_rows if r["status"] == "BROKEN"]
    constantish = [
        r for r in comp["rows"]
        if (r.get("constant_pct") or 0) >= 99 and (r.get("unique") or 0) <= 1 and (r.get("filled_pct") or 0) > 0
    ]

    md = [
        "# Feature Health V2",
        "",
        f"- GOOD: **{counts.get('GOOD', 0)}**",
        f"- WARNING: **{counts.get('WARNING', 0)}**",
        f"- BROKEN: **{counts.get('BROKEN', 0)}**",
        "",
        "| feature | status | filled% | freshness | live |",
        "|---|---|---:|---|---|",
    ]
    for r in health_rows:
        md.append(
            f"| {r['feature']} | **{r['status']}** | {r.get('filled_pct')} | {r.get('fresh')} | {r.get('live_value')} |"
        )
    text = "\n".join(md) + "\n"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "FEATURE_HEALTH.md").write_text(text, encoding="utf-8")

    result = {
        "ok": True,
        "counts": dict(counts),
        "rows": health_rows,
        "recovered": [r["feature"] for r in recovered],
        "broken": [r["feature"] for r in broken],
        "constant": [r["feature"] for r in constantish],
        "report_markdown": text,
        "validate": val,
        "freshness": fresh,
        "completeness": comp,
    }
    if write_recovery_report:
        result["recovery_report"] = write_feature_recovery_report(conn, result)
    return result


def write_feature_recovery_report(conn: Any, health: dict[str, Any]) -> dict[str, Any]:
    info: dict[str, Any] = {}
    prev = os.environ.get("ML_STORE_LIMIT")
    os.environ["ML_STORE_LIMIT"] = os.environ.get("FEATURE_INFO_LIMIT", "100000")
    try:
        from bot.research.market_events.signal_intelligence.math_recovery_v1.feature_information import (
            run_feature_information,
        )
        info = run_feature_information(conn, write_reports=True)
    except Exception as exc:
        info = {"ok": False, "error": str(exc), "top20": [], "n_samples": 0}
    finally:
        if prev is None:
            os.environ.pop("ML_STORE_LIMIT", None)
        else:
            os.environ["ML_STORE_LIMIT"] = prev

    reg = feature_source_registry()
    live = compute_live_feature_vector(conn, symbol="BTC")
    n_s42 = 0
    try:
        n_s42 = int(conn.execute(
            "SELECT COUNT(*) FROM market_events_paper_trades_s42 WHERE status='CLOSED'"
        ).fetchone()[0])
    except Exception:
        pass

    recovered = health.get("recovered") or []
    broken = health.get("broken") or []
    constant = health.get("constant") or []
    total = max(1, len(health.get("rows") or []))
    recovery_pct = round(100.0 * len(recovered) / total, 1)

    lines = [
        "# FEATURE_RECOVERY_REPORT",
        "",
        "_Signal Feature Recovery V2 — Gate/Trading/Paper/Execution unchanged._",
        "",
        f"- S42 closed corpus available locally: **{n_s42}**",
        f"- Info matrix samples used: **{info.get('n_samples')}**",
        f"- Recovery % (GOOD with live value): **{recovery_pct}%**",
        "",
        "## Recovered Features",
        "",
    ]
    for f in recovered:
        lines.append(f"- `{f}` live={next((r['live_value'] for r in health['rows'] if r['feature']==f), None)}")
    lines.extend(["", "## Still Broken", ""])
    for f in broken:
        lines.append(f"- `{f}`")
    lines.extend(["", "## Fresh Collectors", ""])
    for r in (health.get("freshness") or {}).get("rows") or []:
        if r.get("status") == "fresh":
            lines.append(f"- `{r['feature']}` age={r.get('age_sec')}s")
    lines.extend(["", "## Dead Collectors", ""])
    for name, meta in reg.items():
        if meta.get("status") or meta.get("table") is None:
            lines.append(f"- `{name}`: {meta.get('status') or 'no table'} — {meta.get('collector')}")
    lines.extend(["", "## Constant Features (live completeness)", ""])
    for f in constant:
        lines.append(f"- `{f}`")
    lines.extend(["", "## Ranking (predictive power on available S42)", ""])
    for i, r in enumerate((info.get("top20") or [])[:20], 1):
        lines.append(
            f"{i}. `{r['feature']}` score={r.get('rank_score')} IG={r.get('information_gain')} "
            f"MI={r.get('mutual_information')} AUC={r.get('auc')} EV={r.get('single_feature_ev')} "
            f"PF={r.get('single_feature_pf')} bucket={r.get('bucket')}"
        )
    lines.extend(["", "## Live BTC proof (not stubs)", ""])
    for k in (
        "rsi", "ema20_distance", "ema50_distance", "ema200_distance", "vwap_distance",
        "atr", "atr_pct", "macd", "bb_pct_b", "adx", "stoch_k", "trend", "funding",
        "oi_delta", "fear_greed", "volatility", "ai_score",
    ):
        lines.append(f"- `{k}` = `{live.get(k)}`")
    lines.append(f"- candle_source=`{live.get('_candle_source')}` n=`{live.get('_candle_n')}`")
    lines.extend([
        "",
        "## Recommendations",
        "",
        "1. Ensure paper opens call `build_entry_features` so V2 enrichment persists into S55.",
        "2. Backfill RSI/EMA/VWAP onto historical S55 rows for research on full corpus.",
        "3. Wire a real AI score producer (ai_score is intentionally not aliased to confidence).",
        "4. When S42 grows past 30k on the research host, re-run `feature-information` with ML_STORE_LIMIT=100000.",
        "",
    ])
    text = "\n".join(lines)
    REPORT_PATH.write_text(text, encoding="utf-8")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        (REPORT_DIR / "FEATURE_RECOVERY_REPORT.md").write_text(text, encoding="utf-8")
    except Exception:
        pass
    return {"path": str(REPORT_PATH), "markdown": text, "info": info, "live_btc": live}


__all__ = [
    "run_feature_completeness",
    "run_feature_freshness",
    "run_feature_health",
    "run_feature_validate",
    "validate_feature_vector",
    "load_fresh_s55",
]
