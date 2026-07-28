"""Feature influence report — correlations without ML libraries."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.expectancy_intelligence.row_features import (
    row_to_features,
)
from bot.research.market_events.expectancy_intelligence.stats import pearson, safe_float

_TABLE = "market_events_trade_features_s55"

_COMPUTE_KEYS: tuple[tuple[str, str], ...] = (
    ("fear_greed", "FearGreed"),
    ("funding", "Funding"),
    ("trend", "BTC Trend"),
    ("volatility", "BTC Volatility"),
    ("atr", "ATR"),
    ("oi_delta", "OI"),
    ("volume", "Volume spike"),
    ("shock_score", "Shock score"),
    ("hour", "Time of day"),
    ("weekday", "Day of week"),
    ("decision_confidence", "Confidence"),
    ("macro_score", "Macro score"),
    ("news_score", "News score"),
    ("ai_score", "AI score"),
)


def _regime_numeric(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().upper()
    mapping = {
        "STRONG_BULL": 1.0,
        "WEAK_BULL": 0.5,
        "RANGE": 0.0,
        "WEAK_BEAR": -0.5,
        "STRONG_BEAR": -1.0,
        "HIGH_VOL": 0.25,
        "LOW_VOL": -0.25,
    }
    return mapping.get(s, 0.0)


def load_closed_rows(conn: Any, *, limit: int = 2500) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            f"""
            SELECT * FROM {_TABLE}
            WHERE closed_at IS NOT NULL AND pnl_pct IS NOT NULL
            ORDER BY closed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        feats = row_to_features(r)
        pnl = safe_float(r["pnl_pct"])
        if pnl is None:
            continue
        win = 1.0 if str(r["result"] or "").upper() == "WIN" else 0.0
        ev = safe_float(r["gate_expected_pnl_pct"])
        if ev is None:
            ev = pnl
        out.append({"feats": feats, "pnl": pnl, "win": win, "expectancy": ev})
    return out


def build_feature_importance(conn: Any) -> dict[str, Any]:
    rows = load_closed_rows(conn)
    results: list[dict[str, Any]] = []
    for key, label in _COMPUTE_KEYS:
        xs: list[float] = []
        ys_pnl: list[float] = []
        ys_win: list[float] = []
        ys_ev: list[float] = []
        for row in rows:
            feats = row["feats"]
            if key == "market_regime":
                x = _regime_numeric(feats.get("market_regime"))
            elif key == "decision_confidence":
                x = safe_float(feats.get("decision_confidence") or feats.get("confidence"))
            else:
                x = safe_float(feats.get(key))
            if x is None:
                continue
            xs.append(x)
            ys_pnl.append(row["pnl"])
            ys_win.append(row["win"])
            ys_ev.append(row["expectancy"])
        corr_pnl = pearson(xs, ys_pnl)
        corr_win = pearson(xs, ys_win)
        corr_ev = pearson(xs, ys_ev)
        influence = 0.0
        for c in (corr_pnl, corr_win, corr_ev):
            if c is not None:
                influence = max(influence, abs(c))
        results.append(
            {
                "feature": label,
                "key": key,
                "corr_pnl": corr_pnl,
                "corr_win": corr_win,
                "corr_expectancy": corr_ev,
                "influence": round(influence, 4),
            }
        )
    # Regime as separate computed column
    xs_r: list[float] = []
    ys_pnl_r: list[float] = []
    ys_win_r: list[float] = []
    ys_ev_r: list[float] = []
    for row in rows:
        x = _regime_numeric(row["feats"].get("market_regime"))
        if x is None:
            continue
        xs_r.append(x)
        ys_pnl_r.append(row["pnl"])
        ys_win_r.append(row["win"])
        ys_ev_r.append(row["expectancy"])
    c_rp = pearson(xs_r, ys_pnl_r)
    c_rw = pearson(xs_r, ys_win_r)
    c_re = pearson(xs_r, ys_ev_r)
    infl_r = 0.0
    for c in (c_rp, c_rw, c_re):
        if c is not None:
            infl_r = max(infl_r, abs(c))
    results.append(
        {
            "feature": "Regime score",
            "key": "market_regime",
            "corr_pnl": c_rp,
            "corr_win": c_rw,
            "corr_expectancy": c_re,
            "influence": round(infl_r, 4),
        }
    )
    results.sort(key=lambda r: -r["influence"])
    return {"n_rows": len(rows), "features": results}


def format_feature_importance(conn: Any) -> str:
    data = build_feature_importance(conn)
    lines = [
        "FEATURE IMPORTANCE (closed S55 trades, Pearson correlation)",
        f"  n_closed={data['n_rows']}",
        "",
        f"{'Feature':<22} {'Influence':>10}  corr_pnl  corr_win  corr_EV",
    ]
    for row in data["features"]:
        def _c(v: float | None) -> str:
            return f"{v:+.2f}" if v is not None else "n/a"

        lines.append(
            f"{row['feature']:<22} {row['influence']:>10.2f}  "
            f"{_c(row['corr_pnl'])}  {_c(row['corr_win'])}  {_c(row['corr_expectancy'])}"
        )
    return "\n".join(lines)
