"""Phase 5C — Canonical research dataset validation (analysis only).

Reads the Phase 5B export (`research/datasets/lab_dataset.*`) and writes
quality / anomaly reports. Does not modify trading, S40–S56, or existing reports.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_ID_COLS = frozenset(
    {"row_id", "paper_trade_id", "s40_signal_type", "s40_signal_id", "opened_at"}
)

# Features that must not be negative when filled (signed spreads / stop % allowed).
_NON_NEGATIVE = (
    "atr",
    "volume",
    "volatility",
    "ask",
    "bid",
    "fear_greed",
    "rsi",
    "distance_to_strike",
    "trailing_activation",
    "trailing_distance",
    "capital_usd",
    "leverage",
    "similar_count",
    "hour",
    "weekday",
)

# Bounded ranges when the name matches.
_BOUNDED: dict[str, tuple[float, float]] = {
    "hour": (0.0, 23.0),
    "weekday": (0.0, 6.0),
    "rsi": (0.0, 100.0),
    "fear_greed": (0.0, 100.0),
    "ai_score_0_100": (0.0, 100.0),
}

_NEAR_CONSTANT_MODE_SHARE = 0.95
_SPARSE_FILL_LT = 30.0
_UNUSED_FILL_LT = 1.0
_HIGH_CORR = 0.98
_PERFECT_CORR = 1.0 - 1e-12


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "bot" / "research" / "market_events" / "__main__.py").exists():
            return p
    return Path.cwd()


def default_dataset_path(root: Path | None = None) -> Path:
    base = (root or _repo_root()) / "research" / "datasets"
    pq = base / "lab_dataset.parquet"
    if pq.exists():
        return pq
    return base / "lab_dataset.csv"


def default_report_dir(root: Path | None = None) -> Path:
    return (root or _repo_root()) / "research" / "reports" / "dataset_validation"


def _safe_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return float(v)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _is_filled(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str) and v.strip() in ("", "None", "null", "nan"):
        return False
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return False
    return True


def _bare(name: str) -> str:
    for p in (
        "derived_",
        "feat_",
        "s40_",
        "s42_",
        "s55_",
        "s56_",
        "s58_",
        "s56j_",
        "s55fj_",
    ):
        if name.startswith(p):
            return name[len(p) :]
    return name


def load_lab_dataset(path: Path | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Load parquet (preferred) or CSV into list-of-dicts + column order."""
    path = path or default_dataset_path()
    if not path.exists():
        raise FileNotFoundError(f"lab dataset not found: {path}")

    if path.suffix == ".parquet":
        import pyarrow.parquet as pq

        table = pq.read_table(path)
        cols = list(table.column_names)
        n = table.num_rows
        col_data = {c: table.column(c).to_pylist() for c in cols}
        rows = [{c: col_data[c][i] for c in cols} for i in range(n)]
        return rows, cols

    import csv

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cols = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]
    return rows, cols


def _feature_columns(columns: list[str]) -> list[str]:
    return [c for c in columns if c not in _ID_COLS]


def _analyze_column(name: str, values: list[Any], n: int) -> dict[str, Any]:
    filled_vals = [v for v in values if _is_filled(v)]
    filled_n = len(filled_vals)
    null_n = n - filled_n
    fill_rate = round(100.0 * filled_n / n, 4) if n else 0.0

    nums = [_safe_float(v) for v in filled_vals]
    nums_ok = [x for x in nums if x is not None]
    numeric_ratio = (len(nums_ok) / filled_n) if filled_n else 0.0
    is_numeric = filled_n > 0 and numeric_ratio >= 0.85

    unique: list[Any]
    unique_n: int
    mode_share = 0.0
    dist: dict[str, Any] | None = None

    if is_numeric and nums_ok:
        arr = np.asarray(nums_ok, dtype=float)
        unique_n = int(len(set(np.round(arr, 10).tolist())))
        unique = sorted(set(np.round(arr, 8).tolist()))[:30]
        counts = Counter(np.round(arr, 8).tolist())
        mode_share = counts.most_common(1)[0][1] / len(nums_ok)
        dist = {
            "min": float(np.min(arr)),
            "max": float(np.max(arr)),
            "mean": float(np.mean(arr)),
            "median": float(np.median(arr)),
            "std": float(np.std(arr)),
        }
    else:
        str_vals = [str(v) for v in filled_vals]
        counts = Counter(str_vals)
        unique_n = len(counts)
        unique = [k for k, _ in counts.most_common(30)]
        mode_share = (counts.most_common(1)[0][1] / filled_n) if filled_n else 0.0
        dist = None

    constant = filled_n > 0 and unique_n <= 1
    near_constant = filled_n > 0 and mode_share >= _NEAR_CONSTANT_MODE_SHARE
    unused = fill_rate < _UNUSED_FILL_LT
    sparse = (not unused) and fill_rate < _SPARSE_FILL_LT

    return {
        "feature": name,
        "fill_rate_pct": fill_rate,
        "null_count": null_n,
        "filled_n": filled_n,
        "unique_n": unique_n,
        "unique_values_sample": unique[:20],
        "min": None if dist is None else dist["min"],
        "max": None if dist is None else dist["max"],
        "mean": None if dist is None else dist["mean"],
        "median": None if dist is None else dist["median"],
        "std": None if dist is None else dist["std"],
        "is_numeric": is_numeric,
        "constant": constant,
        "near_constant": near_constant and not constant,
        "unused": unused,
        "sparse": sparse,
        "mode_share": round(mode_share, 6),
    }


def _encode_numeric(values: list[Any]) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    for i, v in enumerate(values):
        if not _is_filled(v):
            continue
        x = _safe_float(v)
        if x is not None:
            out[i] = x
    return out


def _find_duplicate_columns(
    rows: list[dict[str, Any]], features: list[str]
) -> list[dict[str, Any]]:
    n = len(rows)
    cols = {f: [rows[i].get(f) for i in range(n)] for f in features}
    dups: list[dict[str, Any]] = []
    for i, a in enumerate(features):
        va = cols[a]
        for b in features[i + 1 :]:
            vb = cols[b]
            both = 0
            equal = True
            same_mask = True
            for xa, xb in zip(va, vb):
                fa, fb = _is_filled(xa), _is_filled(xb)
                if fa != fb:
                    same_mask = False
                    break
                if fa and fb:
                    both += 1
                    na, nb = _safe_float(xa), _safe_float(xb)
                    if na is not None and nb is not None:
                        if abs(na - nb) > 1e-9:
                            equal = False
                            break
                    elif str(xa) != str(xb):
                        equal = False
                        break
            if same_mask and equal and both >= max(10, int(0.05 * n)):
                dups.append({"a": a, "b": b, "both_filled": both})
    return dups


def _correlation_pairs(
    rows: list[dict[str, Any]],
    feature_stats: list[dict[str, Any]],
    *,
    min_fill: float = 20.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    numeric = [
        s["feature"]
        for s in feature_stats
        if s["is_numeric"] and s["fill_rate_pct"] >= min_fill and not s["unused"]
    ]
    mats = {
        f: _encode_numeric([r.get(f) for r in rows]) for f in numeric
    }
    perfect: list[dict[str, Any]] = []
    high: list[dict[str, Any]] = []
    for i, a in enumerate(numeric):
        for b in numeric[i + 1 :]:
            mask = ~np.isnan(mats[a]) & ~np.isnan(mats[b])
            if int(mask.sum()) < 50:
                continue
            xa, xb = mats[a][mask], mats[b][mask]
            if float(np.std(xa)) < 1e-12 or float(np.std(xb)) < 1e-12:
                continue
            corr = float(np.corrcoef(xa, xb)[0, 1])
            if math.isnan(corr):
                continue
            item = {"a": a, "b": b, "corr": round(corr, 8), "n": int(mask.sum())}
            if abs(corr) >= _PERFECT_CORR:
                perfect.append(item)
            elif abs(corr) >= _HIGH_CORR:
                high.append(item)
    perfect.sort(key=lambda x: -abs(x["corr"]))
    high.sort(key=lambda x: -abs(x["corr"]))
    return perfect, high


def _impossible_values(
    rows: list[dict[str, Any]], features: list[str]
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for name in features:
        bare = _bare(name).lower()
        vals = [rows[i].get(name) for i in range(len(rows))]
        nums = [(_safe_float(v), v) for v in vals if _is_filled(v)]
        nums = [(x, raw) for x, raw in nums if x is not None]
        if not nums:
            continue

        # Negative where impossible
        for token in _NON_NEGATIVE:
            if token in bare or bare.endswith(token) or bare == token:
                negs = [x for x, _ in nums if x < 0]
                if negs:
                    issues.append(
                        {
                            "feature": name,
                            "issue": "negative_where_impossible",
                            "count": len(negs),
                            "examples": negs[:5],
                            "rule": f"{token} should be >= 0",
                        }
                    )
                break

        # Bounded ranges
        for token, (lo, hi) in _BOUNDED.items():
            if token in bare or bare.endswith(token):
                bad = [x for x, _ in nums if x < lo or x > hi]
                if bad:
                    issues.append(
                        {
                            "feature": name,
                            "issue": "out_of_bounds",
                            "count": len(bad),
                            "examples": bad[:5],
                            "rule": f"{token} in [{lo}, {hi}]",
                        }
                    )
                break

        # NaN/Inf already stripped by _safe_float; flag non-finite strings
        # Direction must be LONG/SHORT when present
        if bare == "direction":
            bad_d = [
                str(v)
                for v in vals
                if _is_filled(v) and str(v).upper() not in ("LONG", "SHORT")
            ]
            if bad_d:
                issues.append(
                    {
                        "feature": name,
                        "issue": "invalid_direction",
                        "count": len(bad_d),
                        "examples": bad_d[:5],
                        "rule": "direction in {LONG, SHORT}",
                    }
                )
    return issues


def _timestamp_anomalies(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    now = time.time()
    # Collect candidate timestamp fields
    ts_fields = [
        c
        for c in (rows[0].keys() if rows else [])
        if c == "opened_at"
        or c.endswith("_ts")
        or c.endswith("_timestamp")
        or c.endswith("created_at")
        or "entry_ts" in c
        or c.endswith("_opened_at")
    ]
    for field in ts_fields:
        vals = []
        for r in rows:
            x = _safe_float(r.get(field))
            if x is not None:
                vals.append(x)
        if len(vals) < 10:
            continue
        arr = np.asarray(vals, dtype=float)
        # Heuristic: seconds since epoch if median > 1e9
        med = float(np.median(arr))
        unit = "seconds"
        if med > 1e12:  # ms
            arr = arr / 1000.0
            unit = "milliseconds->seconds"
            med = float(np.median(arr))
        if med < 1e9:
            # not epoch-like; skip hard checks
            continue
        future = int(np.sum(arr > now + 86400))
        ancient = int(np.sum(arr < 1_000_000_000))  # before ~2001
        # non-monotonic opened_at vs row order is weak; check duplicates / zeros
        zeros = int(np.sum(arr <= 0))
        if future:
            issues.append(
                {
                    "feature": field,
                    "issue": "timestamp_in_future",
                    "count": future,
                    "unit_assumed": unit,
                }
            )
        if ancient:
            issues.append(
                {
                    "feature": field,
                    "issue": "timestamp_too_old_or_invalid",
                    "count": ancient,
                    "unit_assumed": unit,
                }
            )
        if zeros:
            issues.append(
                {
                    "feature": field,
                    "issue": "timestamp_non_positive",
                    "count": zeros,
                    "unit_assumed": unit,
                }
            )
        # Spread sanity
        span_days = (float(np.max(arr)) - float(np.min(arr))) / 86400.0
        if span_days > 3650:
            issues.append(
                {
                    "feature": field,
                    "issue": "timestamp_span_gt_10y",
                    "span_days": round(span_days, 2),
                    "unit_assumed": unit,
                }
            )
    return issues


def _quality_score(stat: dict[str, Any]) -> float:
    """Higher = better quality for research use."""
    fill = float(stat["fill_rate_pct"]) / 100.0
    uniq = float(stat["unique_n"])
    # Prefer filled, non-constant, some diversity
    diversity = 0.0 if uniq <= 1 else min(1.0, math.log1p(uniq) / math.log1p(50))
    score = 0.55 * fill + 0.35 * diversity
    if stat["constant"] or stat["unused"]:
        score *= 0.05
    elif stat["near_constant"]:
        score *= 0.4
    elif stat["sparse"]:
        score *= 0.7
    return round(score, 6)


def _prefer_drop_name(a: str, b: str) -> str:
    """Prefer dropping mirror/derived duplicates over primary s56_/s55_ columns."""
    def rank(name: str) -> tuple[int, str]:
        if name.startswith("s56j_") or name.startswith("s55fj_"):
            return (3, name)
        if name.startswith("feat_"):
            return (2, name)
        if name.startswith("derived_"):
            return (1, name)
        return (0, name)

    return b if rank(a) <= rank(b) else a


def _recommend(stat: dict[str, Any], *, in_dup: bool, in_corr: bool) -> str:
    if stat.get("has_impossible"):
        return "FIX"
    if stat["unused"] or stat["constant"]:
        return "DROP"
    if in_dup or in_corr:
        return "DROP"
    if stat["sparse"] or stat["near_constant"]:
        return "INVESTIGATE"
    if stat["fill_rate_pct"] >= 80 and stat["unique_n"] > 1:
        return "KEEP"
    if stat["fill_rate_pct"] >= 30:
        return "KEEP"
    return "INVESTIGATE"


def validate_dataset(
    dataset_path: Path | None = None,
) -> dict[str, Any]:
    """Run full validation; return JSON-serializable report dict."""
    t0 = time.perf_counter()
    path = dataset_path or default_dataset_path()
    rows, columns = load_lab_dataset(path)
    n = len(rows)
    features = _feature_columns(columns)

    feature_stats: list[dict[str, Any]] = []
    for name in features:
        vals = [r.get(name) for r in rows]
        feature_stats.append(_analyze_column(name, vals, n))

    dups = _find_duplicate_columns(rows, features)
    perfect, high = _correlation_pairs(rows, feature_stats)
    impossible = _impossible_values(rows, features)
    ts_issues = _timestamp_anomalies(rows)

    impossible_feats = {x["feature"] for x in impossible}
    for s in feature_stats:
        s["has_impossible"] = s["feature"] in impossible_feats
        s["quality_score"] = _quality_score(s)

    dup_feats: set[str] = set()
    for d in dups:
        dup_feats.add(_prefer_drop_name(d["a"], d["b"]))

    corr_drop: set[str] = set()
    for p in perfect + high:
        corr_drop.add(_prefer_drop_name(p["a"], p["b"]))

    recommendations: dict[str, list[str]] = {
        "DROP": [],
        "KEEP": [],
        "FIX": [],
        "INVESTIGATE": [],
    }
    per_feature_rec: list[dict[str, Any]] = []
    for s in feature_stats:
        rec = _recommend(
            s,
            in_dup=s["feature"] in dup_feats,
            in_corr=s["feature"] in corr_drop and s["feature"] not in dup_feats,
        )
        recommendations[rec].append(s["feature"])
        per_feature_rec.append(
            {
                "feature": s["feature"],
                "recommendation": rec,
                "quality_score": s["quality_score"],
                "fill_rate_pct": s["fill_rate_pct"],
                "reasons": [
                    *(["unused"] if s["unused"] else []),
                    *(["constant"] if s["constant"] else []),
                    *(["near_constant"] if s["near_constant"] else []),
                    *(["sparse"] if s["sparse"] else []),
                    *(["duplicate"] if s["feature"] in dup_feats else []),
                    *(["high_or_perfect_corr"] if s["feature"] in corr_drop else []),
                    *(["impossible_values"] if s["has_impossible"] else []),
                ],
            }
        )

    ranked = sorted(feature_stats, key=lambda s: (-s["quality_score"], s["feature"]))
    best = [
        {"feature": s["feature"], "quality_score": s["quality_score"], "fill_rate_pct": s["fill_rate_pct"]}
        for s in ranked
        if not s["unused"] and not s["constant"]
    ][:20]
    worst = [
        {"feature": s["feature"], "quality_score": s["quality_score"], "fill_rate_pct": s["fill_rate_pct"]}
        for s in sorted(feature_stats, key=lambda s: (s["quality_score"], -s["fill_rate_pct"]))
    ][:20]
    unused = [s["feature"] for s in feature_stats if s["unused"]]
    sparse = [s["feature"] for s in feature_stats if s["sparse"]]
    constant = [s["feature"] for s in feature_stats if s["constant"]]
    near_constant = [s["feature"] for s in feature_stats if s["near_constant"]]

    return {
        "ok": True,
        "phase": "5C",
        "generated_at_iso": datetime.now(timezone.utc).isoformat(),
        "elapsed_sec": round(time.perf_counter() - t0, 3),
        "dataset_path": str(path),
        "n_rows": n,
        "n_columns": len(columns),
        "n_feature_columns": len(features),
        "id_columns": sorted(_ID_COLS & set(columns)),
        "features": feature_stats,
        "detections": {
            "duplicated_columns": dups,
            "perfectly_correlated": perfect,
            "highly_correlated_gt_0_98": high,
            "impossible_values": impossible,
            "timestamp_anomalies": ts_issues,
            "constant_columns": constant,
            "near_constant_columns": near_constant,
        },
        "rankings": {
            "best_quality": best,
            "worst_quality": worst,
            "unused_features": unused,
            "sparse_features": sparse,
        },
        "recommendations": recommendations,
        "recommendations_detail": per_feature_rec,
        "summary": {
            "n_drop": len(recommendations["DROP"]),
            "n_keep": len(recommendations["KEEP"]),
            "n_fix": len(recommendations["FIX"]),
            "n_investigate": len(recommendations["INVESTIGATE"]),
            "n_duplicates": len(dups),
            "n_perfect_corr": len(perfect),
            "n_high_corr": len(high),
            "n_impossible": len(impossible),
            "n_timestamp_issues": len(ts_issues),
        },
    }


def _fmt(v: Any, digits: int = 4) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if abs(v) >= 100:
            return f"{v:.2f}"
        return f"{v:.{digits}g}"
    return str(v)


def format_validation_markdown(report: dict[str, Any]) -> str:
    s = report.get("summary") or {}
    lines = [
        "# Dataset Validation (Phase 5C)",
        "",
        f"_dataset=`{report.get('dataset_path')}` n_rows={report.get('n_rows')} "
        f"features={report.get('n_feature_columns')} generated={report.get('generated_at_iso')} "
        f"elapsed={report.get('elapsed_sec')}s_",
        "",
        "## Summary",
        "",
        f"- KEEP: **{s.get('n_keep')}** · DROP: **{s.get('n_drop')}** · "
        f"FIX: **{s.get('n_fix')}** · INVESTIGATE: **{s.get('n_investigate')}**",
        f"- Duplicate column pairs: **{s.get('n_duplicates')}**",
        f"- Perfect corr pairs: **{s.get('n_perfect_corr')}** · "
        f"|r|>0.98: **{s.get('n_high_corr')}**",
        f"- Impossible-value issues: **{s.get('n_impossible')}** · "
        f"Timestamp issues: **{s.get('n_timestamp_issues')}**",
        "",
        "## Best quality features",
        "",
        "| # | Feature | Score | Fill% |",
        "|---:|---|---:|---:|",
    ]
    for i, r in enumerate(report["rankings"]["best_quality"], 1):
        lines.append(
            f"| {i} | `{r['feature']}` | {_fmt(r['quality_score'])} | {_fmt(r['fill_rate_pct'])} |"
        )

    lines += [
        "",
        "## Worst quality features",
        "",
        "| # | Feature | Score | Fill% |",
        "|---:|---|---:|---:|",
    ]
    for i, r in enumerate(report["rankings"]["worst_quality"], 1):
        lines.append(
            f"| {i} | `{r['feature']}` | {_fmt(r['quality_score'])} | {_fmt(r['fill_rate_pct'])} |"
        )

    lines += [
        "",
        "## Unused features (fill < 1%)",
        "",
    ]
    unused = report["rankings"]["unused_features"]
    lines.append(", ".join(f"`{x}`" for x in unused) if unused else "_none_")

    lines += [
        "",
        "## Sparse features (1% ≤ fill < 30%)",
        "",
    ]
    sparse = report["rankings"]["sparse_features"]
    lines.append(", ".join(f"`{x}`" for x in sparse) if sparse else "_none_")

    det = report["detections"]
    lines += [
        "",
        "## Detections",
        "",
        "### Duplicated columns",
        "",
    ]
    if not det["duplicated_columns"]:
        lines.append("_none_")
    else:
        lines += ["| A | B | Both filled |", "|---|---|---:|"]
        for d in det["duplicated_columns"][:40]:
            lines.append(f"| `{d['a']}` | `{d['b']}` | {d['both_filled']} |")

    lines += ["", "### Perfectly correlated", ""]
    if not det["perfectly_correlated"]:
        lines.append("_none_")
    else:
        lines += ["| A | B | corr | n |", "|---|---|---:|---:|"]
        for d in det["perfectly_correlated"][:40]:
            lines.append(f"| `{d['a']}` | `{d['b']}` | {_fmt(d['corr'])} | {d['n']} |")

    lines += ["", "### Highly correlated (|r| > 0.98)", ""]
    if not det["highly_correlated_gt_0_98"]:
        lines.append("_none_")
    else:
        lines += ["| A | B | corr | n |", "|---|---|---:|---:|"]
        for d in det["highly_correlated_gt_0_98"][:40]:
            lines.append(f"| `{d['a']}` | `{d['b']}` | {_fmt(d['corr'])} | {d['n']} |")

    lines += ["", "### Impossible / invalid values", ""]
    if not det["impossible_values"]:
        lines.append("_none_")
    else:
        for d in det["impossible_values"]:
            lines.append(
                f"- `{d['feature']}`: {d['issue']} n={d['count']} rule=`{d.get('rule')}` "
                f"examples={d.get('examples')}"
            )

    lines += ["", "### Timestamp anomalies", ""]
    if not det["timestamp_anomalies"]:
        lines.append("_none_")
    else:
        for d in det["timestamp_anomalies"]:
            lines.append(f"- `{d.get('feature')}`: {d}")

    lines += [
        "",
        "## Recommendations",
        "",
    ]
    for bucket in ("DROP", "KEEP", "FIX", "INVESTIGATE"):
        items = report["recommendations"].get(bucket) or []
        lines.append(f"### {bucket} ({len(items)})")
        lines.append("")
        lines.append(", ".join(f"`{x}`" for x in items) if items else "_none_")
        lines.append("")

    lines += [
        "## Per-feature stats",
        "",
        "| Feature | Fill% | Nulls | Unique | Min | Max | Const | Near | Sparse | Score | Rec |",
        "|---|---:|---:|---:|---:|---:|---|---|---|---:|---|",
    ]
    rec_map = {r["feature"]: r["recommendation"] for r in report["recommendations_detail"]}
    for s in sorted(report["features"], key=lambda x: (-x["quality_score"], x["feature"])):
        lines.append(
            f"| `{s['feature']}` | {_fmt(s['fill_rate_pct'])} | {s['null_count']} | {s['unique_n']} | "
            f"{_fmt(s['min'])} | {_fmt(s['max'])} | {s['constant']} | {s['near_constant']} | "
            f"{s['sparse']} | {_fmt(s['quality_score'])} | {rec_map.get(s['feature'], '—')} |"
        )
    lines.append("")
    return "\n".join(lines)


def run_dataset_validation(
    *,
    dataset_path: Path | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    """Validate dataset and write markdown + JSON reports."""
    report = validate_dataset(dataset_path)
    out = report_dir or default_report_dir()
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / "dataset_validation.md"
    json_path = out / "dataset_validation.json"
    md_path.write_text(format_validation_markdown(report), encoding="utf-8")
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["report_files"] = {"markdown": str(md_path), "json": str(json_path)}
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Phase 5C — validate canonical lab dataset")
    p.add_argument("--dataset", type=str, default=None, help="Path to lab_dataset.parquet/csv")
    p.add_argument("--out-dir", type=str, default=None, help="Report output directory")
    args = p.parse_args(argv)
    report = run_dataset_validation(
        dataset_path=Path(args.dataset) if args.dataset else None,
        report_dir=Path(args.out_dir) if args.out_dir else None,
    )
    print(
        json.dumps(
            {
                "ok": report.get("ok"),
                "n_rows": report.get("n_rows"),
                "n_feature_columns": report.get("n_feature_columns"),
                "summary": report.get("summary"),
                "report_files": report.get("report_files"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
