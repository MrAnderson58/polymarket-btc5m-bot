"""S55 historical trade feature completeness audit (research only)."""

from __future__ import annotations

from typing import Any, Literal

from bot.research.market_events.expectancy_intelligence.row_features import (
    row_to_features,
)
from bot.research.market_events.expectancy_intelligence.stats import (
    mean,
    pearson,
    safe_float,
    stdev,
)
from bot.research.market_events.signal_intelligence.trade_intelligence_s55 import (
    _NUMERIC_DIMS,
)

_TABLE = "market_events_trade_features_s55"

Kind = Literal["numeric", "categorical"]


def _display_name(key: str) -> str:
    labels = {
        "fear_greed": "FearGreed",
        "macro_score": "Macro score",
        "news_score": "News score",
        "ai_score": "AI score",
        "oi_delta": "OI",
        "etf_flow": "ETF flow",
        "btc_dominance": "BTC dominance",
        "funding_sign": "Funding sign",
        "shock_score": "Shock score",
        "market_regime": "Market regime",
        "hour": "Time of day",
        "weekday": "Day of week",
        "volatility": "BTC volatility",
        "trend": "BTC trend",
        "volume": "Volume",
        "funding": "Funding",
        "atr": "ATR",
        "rsi": "RSI",
    }
    return labels.get(key, key.replace("_", " ").title())


# S55 similarity dims + regime (gate / context).
_SPECS: tuple[tuple[str, str, Kind], ...] = tuple(
    (key, _display_name(key), "numeric") for key, _ in _NUMERIC_DIMS
) + (("market_regime", "Market regime", "categorical"),)


def _is_missing(val: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, str) and not val.strip():
        return True
    return False


def _numeric_values(rows: list[dict[str, Any]], key: str) -> list[float]:
    out: list[float] = []
    for feats in rows:
        v = feats.get(key)
        if key == "funding_sign" and v is not None:
            try:
                out.append(float(int(v)))
                continue
            except (TypeError, ValueError):
                pass
        f = safe_float(v)
        if f is not None:
            out.append(f)
    return out


def _categorical_values(rows: list[dict[str, Any]], key: str) -> list[str]:
    out: list[str] = []
    for feats in rows:
        v = feats.get(key)
        if _is_missing(v):
            continue
        out.append(str(v).strip())
    return out


def _correlation_assessment(
    rows: list[dict[str, Any]],
    key: str,
    *,
    kind: Kind,
) -> tuple[bool, str | None]:
    if kind == "categorical":
        return False, "categorical feature"
    pairs: list[tuple[float, float]] = []
    for feats in rows:
        pnl = safe_float(feats.get("_pnl_pct"))
        v = feats.get(key)
        if key == "funding_sign" and v is not None:
            try:
                f = float(int(v))
            except (TypeError, ValueError):
                f = None
        else:
            f = safe_float(v)
        if pnl is not None and f is not None:
            pairs.append((f, pnl))
    if len(pairs) < 3:
        return False, "fewer than 3 paired observations"
    xs_p = [p[0] for p in pairs]
    ys_p = [p[1] for p in pairs]
    if len(set(xs_p)) <= 1:
        return False, "constant value"
    if stdev(xs_p) <= 0 or stdev(ys_p) <= 0:
        return False, "zero variance in feature or PnL"
    if pearson(xs_p, ys_p) is None:
        return False, "correlation undefined"
    return True, None


def load_historical_feature_rows(conn: Any, *, limit: int = 50000) -> list[dict[str, Any]]:
    """Closed S55 rows — same pool used for similar-trade neighbors."""
    try:
        raw = conn.execute(
            f"""
            SELECT * FROM {_TABLE}
            WHERE closed_at IS NOT NULL
            ORDER BY closed_at DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for row in raw:
        feats = row_to_features(row)
        feats["_pnl_pct"] = row["pnl_pct"] if "pnl_pct" in row.keys() else None
        feats["_closed_at"] = row["closed_at"] if "closed_at" in row.keys() else None
        out.append(feats)
    return out


def audit_feature(
    rows: list[dict[str, Any]],
    key: str,
    label: str,
    kind: Kind,
) -> dict[str, Any]:
    n = len(rows)
    missing = sum(1 for feats in rows if _is_missing(feats.get(key)))
    missing_pct = round(100.0 * missing / n, 1) if n else 100.0
    present_pct = round(100.0 - missing_pct, 1) if n else 0.0

    reason_lines: list[str] = []
    corr_ok, corr_reason = _correlation_assessment(rows, key, kind=kind)

    if missing_pct >= 100.0:
        reason_lines.append("never stored")
        corr_ok = False
        corr_reason = "never stored"

    if kind == "categorical":
        cats = _categorical_values(rows, key)
        unique = len(set(cats))
        if missing_pct < 100 and unique <= 1 and cats:
            reason_lines.append("single regime label only")
        return {
            "feature": label,
            "key": key,
            "missing_pct": missing_pct,
            "present_pct": present_pct,
            "unique": unique,
            "mean": None,
            "std": None,
            "min": None,
            "max": None,
            "correlation_possible": corr_ok,
            "correlation_reason": corr_reason,
            "reason_lines": reason_lines,
            "ok": missing_pct < 50 and unique > 1,
        }

    nums = _numeric_values(rows, key)
    unique = len(set(nums))
    if missing_pct < 100 and unique <= 1:
        reason_lines.append("constant value")
    stats: dict[str, Any] = {
        "mean": None,
        "std": None,
        "min": None,
        "max": None,
    }
    if nums:
        stats["mean"] = round(mean(nums), 4)
        stats["std"] = round(stdev(nums), 4)
        stats["min"] = round(min(nums), 4)
        stats["max"] = round(max(nums), 4)

    if corr_reason and not corr_ok:
        reason_lines.append(corr_reason)

    return {
        "feature": label,
        "key": key,
        "missing_pct": missing_pct,
        "present_pct": present_pct,
        "unique": unique,
        **stats,
        "correlation_possible": corr_ok,
        "correlation_reason": corr_reason,
        "reason_lines": reason_lines,
        "ok": missing_pct < 5 and unique > 1,
    }


def build_dataset_audit(conn: Any, *, limit: int = 50000) -> dict[str, Any]:
    rows = load_historical_feature_rows(conn, limit=limit)
    features = [audit_feature(rows, key, label, kind) for key, label, kind in _SPECS]
    return {
        "n_historical": len(rows),
        "features": features,
        "completeness": {f["feature"]: f["present_pct"] for f in features},
    }


def _fmt_stats(f: dict[str, Any]) -> str:
    if f.get("mean") is None:
        return "mean/std/min/max: n/a"
    return (
        f"mean={f['mean']} std={f['std']} min={f['min']} max={f['max']}"
    )


def format_dataset_audit(conn: Any, *, limit: int = 50000) -> str:
    data = build_dataset_audit(conn, limit=limit)
    lines = [
        "S55 DATASET AUDIT (closed historical trades)",
        f"  n_historical={data['n_historical']}",
        "",
        f"{'Feature':<18} {'Missing%':>8} {'Unique':>7}  Stats / notes",
    ]
    for f in data["features"]:
        corr = "yes" if f["correlation_possible"] else "no"
        notes = f"corr={corr}"
        if f.get("reason_lines"):
            notes += f"  reason: {'; '.join(f['reason_lines'])}"
        elif f["correlation_possible"]:
            notes += "  OK"
        if f.get("mean") is not None:
            stat_part = _fmt_stats(f)
        else:
            stat_part = f"unique labels={f['unique']}"
        lines.append(
            f"{f['feature']:<18} {f['missing_pct']:>7.1f}% {f['unique']:>7}  {stat_part}  {notes}"
        )
    lines.extend(["", "Dataset completeness (present %)", ""])
    for label, pct in data["completeness"].items():
        short = label.replace(" ", "")
        lines.append(f"{short:<16} {pct:.0f}%")
    return "\n".join(lines)
