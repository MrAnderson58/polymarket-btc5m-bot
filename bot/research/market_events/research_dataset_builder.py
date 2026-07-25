"""Phase 5B — Unified Research Dataset Builder (export layer only).

Builds one canonical open-time feature row per closed trade by joining
S40 / S42 / S55 / S56 / S58. Leakage columns are rejected automatically.

Does NOT modify trading, paper, strategy, or existing report modules.
Future S59–S65 consumers should prefer this export over ad-hoc loaders;
current reports keep using their existing paths (backward compatible).
"""

from __future__ import annotations

import csv
import json
import logging
import math
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_S40 = "market_events_signal_learning_s40_signals"
_S42 = "market_events_paper_trades_s42"
_S55 = "market_events_trade_features_s55"
_S56 = "market_events_trade_snapshots_s56"
_S58 = "market_events_trade_decisions_s58"

# Exact names (any source) that must never appear as features.
_LEAKAGE_EXACT = frozenset(
    {
        "exit_price",
        "exit_reason",
        "exit_ts",
        "closed_at",
        "pnl",
        "pnl_usd",
        "pnl_pct",
        "pnl_usdc",
        "mfe",
        "mae",
        "mfe_pct",
        "mae_pct",
        "max_profit_pct",
        "max_drawdown_pct",
        "final_pnl_usd",
        "final_pnl_pct",
        "duration_sec",
        "holding_seconds",
        "holding_time",
        "seconds_open",
        "result",
        "rr_achieved",
        "is_win",
        "is_loss",
        "is_stop",
        "is_time_stop",
        "is_trailing",
        "reached_tp1",
        "reached_tp2",
        "stopped",
        "trailing",
        "trailing_active",
        "trailing_stop",
        "trailing_exit_reason",
        "highest_price_after_tp1",
        "lowest_price_after_tp1",
        "status",  # CLOSED is post-facto for closed-universe rows
        "updated_at",
    }
)

# Substring / regex patterns for future path leakage.
_LEAKAGE_REGEX = (
    re.compile(r"^btc_move_"),
    re.compile(r"_after_"),
    re.compile(r"^future_"),
    re.compile(r"post[_-]?entry"),
)

# Identity / join keys retained in every row (not model features).
_ID_COLUMNS = (
    "row_id",
    "paper_trade_id",
    "s40_signal_type",
    "s40_signal_id",
    "opened_at",
)

# Open-time column allow-lists per source (prefix applied when merged).
_S40_OPEN = (
    "symbol",
    "direction",
    "entry",
    "stop",
    "tp1",
    "tp2",
    "timestamp",
    "snapshot_funding",
    "snapshot_open_interest",
    "snapshot_volume",
    "snapshot_atr",
    "snapshot_fear_greed",
    "snapshot_trend",
    "snapshot_news_score",
    "snapshot_news_impact",
    "snapshot_pattern_json",
    "snapshot_decision_confidence",
    "created_at",
)

_S42_OPEN = (
    "symbol",
    "direction",
    "entry",
    "stop",
    "tp1",
    "tp2",
    "created_at",
    "decision_confidence",
    "pattern_json",
    "news_category",
    "capital_usd",
    "leverage",
)

_S55_OPEN = (
    "symbol",
    "direction",
    "hour",
    "weekday",
    "volatility",
    "atr",
    "rsi",
    "funding",
    "oi_delta",
    "etf_flow",
    "macro_score",
    "news_score",
    "ai_score",
    "trend",
    "volume",
    "fear_greed",
    "btc_dominance",
    "spread",
    "funding_sign",
    "market_regime",
    "shock_score",
    "gate_decision",
    "gate_expected_pnl_pct",
    "similar_count",
    "created_at",
)

_S56_OPEN = (
    "symbol",
    "direction",
    "entry",
    "tp1",
    "tp2",
    "ai_score",
    "expected_pnl_pct",
    "funding",
    "oi_delta",
    "etf_flow",
    "fear_greed",
    "macro_score",
    "news_score",
    "market_score",
    "volatility",
    "atr",
    "volume",
    "trend",
    "vwap",
    "spread",
    "timestamp",
    "hour",
    "weekday",
    "market_regime",
)

_S58_OPEN = (
    "opened_at",
    "symbol",
    "direction",
    "entry_price",
    "market_regime",
    "btc_return",
    "ema20",
    "ema50",
    "ema200",
    "atr",
    "rsi",
    "volume",
    "funding",
    "fear_greed",
    "macro_score",
    "news_score",
    "ai_score",
    "expected_pnl_pct",
    "expected_pnl_usd",
    "candidate_rank",
    "gate_result",
    "gate_reason",
    "portfolio_state",
    "why_opened_json",
    "rejected_alternatives_json",
    "inputs_json",
)

# Nested hist / ER feature pack keys that are safe at open (when present).
_NESTED_OPEN_SAFE = frozenset(
    {
        "ask",
        "bid",
        "spread",
        "distance_to_strike",
        "volatility_15s",
        "volatility_30s",
        "volatility_60s",
        "stop_loss_pct",
        "trailing_activation",
        "trailing_distance",
        "market_slug",
        "strategy_name",
        "side",
        "source_table",
        "entry_price",
        "entry_ts",
        "regime_label",
    }
)

_NESTED_LEAKAGE = frozenset(
    {
        "exit_price",
        "exit_reason",
        "pnl",
        "pnl_usdc",
        "mfe",
        "mae",
        "is_win",
        "is_loss",
        "is_stop",
        "is_time_stop",
        "is_trailing",
        "holding_time",
        "seconds_open",
        "trade_id",
        "built_at",
    }
) | {f"btc_move_{s}s" for s in (5, 10, 15, 20, 30, 45, 60, 90)}


@dataclass
class ColumnMeta:
    name: str
    dtype: str
    fill_rate: float
    source_table: str
    available_at_open: bool
    role: str = "feature"  # feature | id | label
    rejected_reason: str | None = None


@dataclass
class DatasetBuildResult:
    n_rows: int
    n_feature_columns: int
    n_rejected_leakage: int
    out_dir: str
    files: dict[str, str] = field(default_factory=dict)
    columns: list[ColumnMeta] = field(default_factory=list)
    rejected_leakage: list[str] = field(default_factory=list)
    label_join: dict[str, Any] = field(default_factory=dict)


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "bot" / "research" / "market_events" / "__main__.py").exists():
            return p
    return Path.cwd()


def default_dataset_dir(root: Path | None = None) -> Path:
    return (root or _repo_root()) / "research" / "datasets"


def is_leakage_name(name: str) -> bool:
    """Return True if a column name is forbidden as an open-time feature."""
    base = name.split(".")[-1]
    # strip source prefixes: s40_, s42_, s55_, s56_, s58_, feat_, json_, s56j_, s55fj_
    bare = re.sub(
        r"^(s40_|s42_|s55_|s56_|s58_|feat_|json_|s56j_|s55fj_|derived_)",
        "",
        name,
    )
    bare = bare.split(".")[-1]
    low = bare.lower()
    # Open-time gate / model estimates — not realized outcomes.
    if low.startswith("expected_pnl") or low.startswith("gate_expected_pnl"):
        return False
    candidates = {name.lower(), base.lower(), low}
    if candidates & {x.lower() for x in _LEAKAGE_EXACT}:
        return True
    if low in {x.lower() for x in _NESTED_LEAKAGE}:
        return True
    # Realized pnl tokens only (avoid matching expected_pnl*)
    if re.search(r"(^|_)pnl(_|$)", low) and "expected_pnl" not in low:
        return True
    for rx in _LEAKAGE_REGEX:
        if rx.search(low) or rx.search(name.lower()):
            return True
    return False


def _safe_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if isinstance(f, float) and (math.isnan(f) or math.isinf(f)):
        return None
    return f


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        return dict(row)
    except Exception:
        return {}


def _parse_json(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        d = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def _table_exists(conn: Any, table: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:
        return False


def _fetch_index(
    conn: Any,
    table: str,
    *,
    key_cols: tuple[str, ...],
) -> dict[tuple[Any, ...], dict[str, Any]]:
    if not _table_exists(conn, table):
        return {}
    try:
        rows = [_row_dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
    except Exception as exc:
        logger.warning("dataset builder: failed reading %s: %s", table, exc)
        return {}
    out: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in rows:
        try:
            key = tuple(r.get(k) for k in key_cols)
        except Exception:
            continue
        out[key] = r
    return out


def _put(
    dest: dict[str, Any],
    *,
    prefix: str,
    col: str,
    value: Any,
    source_map: dict[str, str],
    source_table: str,
) -> None:
    if is_leakage_name(col) or is_leakage_name(f"{prefix}{col}"):
        return
    name = f"{prefix}{col}"
    # Prefer first non-null; do not overwrite with None
    if name in dest and dest[name] is not None:
        return
    if value is None:
        if name not in dest:
            dest[name] = None
            source_map[name] = source_table
        return
    dest[name] = value
    source_map[name] = source_table


def _extract_prefixed(
    dest: dict[str, Any],
    source_row: dict[str, Any] | None,
    *,
    prefix: str,
    allow: Iterable[str],
    source_map: dict[str, str],
    source_table: str,
) -> None:
    if not source_row:
        return
    for col in allow:
        if col not in source_row:
            continue
        _put(
            dest,
            prefix=prefix,
            col=col,
            value=source_row.get(col),
            source_map=source_map,
            source_table=source_table,
        )


def _extract_features_json(
    dest: dict[str, Any],
    features_json: Any,
    *,
    source_map: dict[str, str],
    source_table: str,
) -> list[str]:
    """Parse S55 features_json; return rejected leakage names."""
    rejected: list[str] = []
    blob = _parse_json(features_json)
    for k, v in blob.items():
        if k in ("features_json",):
            continue
        if is_leakage_name(k) or k in _LEAKAGE_EXACT:
            rejected.append(f"s55.features_json.{k}")
            continue
        _put(
            dest,
            prefix="s55fj_",
            col=k,
            value=v,
            source_map=source_map,
            source_table=source_table,
        )
    return rejected


def _extract_snapshot_json(
    dest: dict[str, Any],
    snapshot_json: Any,
    *,
    source_map: dict[str, str],
    source_table: str,
) -> list[str]:
    rejected: list[str] = []
    blob = _parse_json(snapshot_json)
    # Top-level json open fields (skip ids / leakage)
    for k, v in blob.items():
        if k in ("features", "paper_trade_id", "s40_signal_type", "s40_signal_id", "snapshot_json"):
            continue
        if is_leakage_name(k):
            rejected.append(f"s56.snapshot_json.{k}")
            continue
        if k in _S56_OPEN or k in (
            "market_slug",
            "strategy",
            "source_table",
            "source_db",
            "entry_ts",
        ):
            _put(
                dest,
                prefix="s56j_",
                col=k,
                value=v,
                source_map=source_map,
                source_table=source_table,
            )
        elif k not in _LEAKAGE_EXACT:
            # unknown keys: keep only if not leakage-patterned
            if is_leakage_name(k):
                rejected.append(f"s56.snapshot_json.{k}")
            else:
                _put(
                    dest,
                    prefix="s56j_",
                    col=k,
                    value=v,
                    source_map=source_map,
                    source_table=source_table,
                )

    feat = blob.get("features") if isinstance(blob.get("features"), dict) else {}
    for k, v in feat.items():
        if k in _NESTED_LEAKAGE or is_leakage_name(k):
            rejected.append(f"s56.features.{k}")
            continue
        if k not in _NESTED_OPEN_SAFE and is_leakage_name(k):
            rejected.append(f"s56.features.{k}")
            continue
        if k not in _NESTED_OPEN_SAFE:
            # conservative: reject unknown nested keys (may be path-dependent)
            rejected.append(f"s56.features.{k}:not_in_open_allowlist")
            continue
        _put(
            dest,
            prefix="feat_",
            col=k,
            value=v,
            source_map=source_map,
            source_table=source_table,
        )
    return rejected


def _derived_open_fields(row: dict[str, Any], source_map: dict[str, str]) -> None:
    try:
        from bot.research.market_events.signal_intelligence.lib.feature_utils import (
            normalize_coin,
            normalize_score_0_100,
            session_from_hour,
            safe_float,
        )
    except Exception:
        return

    sym = row.get("s55_symbol") or row.get("s56_symbol") or row.get("s42_symbol") or row.get("s40_symbol")
    coin = normalize_coin(sym) if sym is not None else None
    if coin:
        row["derived_coin"] = coin
        source_map["derived_coin"] = "derived"

    hour = row.get("s55_hour")
    if hour is None:
        hour = row.get("s56_hour")
    try:
        hour_i = int(hour) if hour is not None else None
    except (TypeError, ValueError):
        hour_i = None
    sess = session_from_hour(hour_i)
    if sess is not None:
        row["derived_session"] = sess
        source_map["derived_session"] = "derived"

    st = str(row.get("s40_signal_type") or "")
    row["derived_is_hist"] = st.startswith("hist:")
    source_map["derived_is_hist"] = "derived"

    ai = safe_float(row.get("s55_ai_score") if row.get("s55_ai_score") is not None else row.get("s56_ai_score"))
    if ai is not None:
        row["derived_ai_score_0_100"] = normalize_score_0_100(ai)
        source_map["derived_ai_score_0_100"] = "derived"

    e20 = safe_float(row.get("s58_ema20"))
    e50 = safe_float(row.get("s58_ema50"))
    entry = safe_float(row.get("s55_entry") or row.get("s56_entry") or row.get("s42_entry") or row.get("s58_entry_price"))
    if e20 is not None and e50 is not None:
        row["derived_ema20_minus_ema50"] = e20 - e50
        source_map["derived_ema20_minus_ema50"] = "derived"
    if entry is not None and e20 is not None and e20 != 0:
        row["derived_distance_from_ema20_pct"] = (entry - e20) / e20 * 100.0
        source_map["derived_distance_from_ema20_pct"] = "derived"


def build_canonical_rows(
    *,
    research_conn: Any,
    live_conn: Any | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str], list[str], list[dict[str, Any]]]:
    """Join sources into open-time feature rows + parallel label records.

    Returns:
      rows: feature+id dicts
      source_map: column -> source_table
      rejected: leakage names encountered
      labels: {paper_trade_id, s40_*, label_pnl_usd, label_win, ...}
    """
    if not _table_exists(research_conn, _S56):
        return [], {}, [], []

    snaps = [
        _row_dict(r)
        for r in research_conn.execute(
            f"SELECT * FROM {_S56} WHERE pnl_usd IS NOT NULL"
        ).fetchall()
    ]

    s58_by_pid = _fetch_index(research_conn, _S58, key_cols=("paper_trade_id",))
    # Also index S58 by signal pair when paper_trade_id missing/0
    s58_by_sig: dict[tuple[Any, Any], dict[str, Any]] = {}
    if _table_exists(research_conn, _S58):
        for r in s58_by_pid.values():
            s58_by_sig[(r.get("s40_signal_type"), r.get("s40_signal_id"))] = r

    live = live_conn
    s42_by_id: dict[tuple[Any, ...], dict[str, Any]] = {}
    s42_by_sig: dict[tuple[Any, ...], dict[str, Any]] = {}
    s55_by_sig: dict[tuple[Any, ...], dict[str, Any]] = {}
    s40_by_sig: dict[tuple[Any, ...], dict[str, Any]] = {}
    if live is not None:
        s42_by_id = _fetch_index(live, _S42, key_cols=("id",))
        if s42_by_id:
            for r in s42_by_id.values():
                s42_by_sig[(r.get("s40_signal_type"), r.get("s40_signal_id"))] = r
        else:
            s42_by_sig = _fetch_index(live, _S42, key_cols=("s40_signal_type", "s40_signal_id"))
        s55_by_sig = _fetch_index(live, _S55, key_cols=("s40_signal_type", "s40_signal_id"))
        s40_by_sig = _fetch_index(live, _S40, key_cols=("signal_type", "signal_id"))

    rows: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    source_map: dict[str, str] = {}
    rejected: list[str] = []
    seen_reject: set[str] = set()

    def _rej(name: str) -> None:
        if name not in seen_reject:
            seen_reject.add(name)
            rejected.append(name)

    for i, snap in enumerate(snaps):
        s_type = snap.get("s40_signal_type")
        s_id = snap.get("s40_signal_id")
        pid = snap.get("paper_trade_id")
        sig = (s_type, s_id)

        row: dict[str, Any] = {
            "row_id": i + 1,
            "paper_trade_id": pid,
            "s40_signal_type": s_type,
            "s40_signal_id": s_id,
            "opened_at": snap.get("timestamp") or snap.get("created_at"),
        }
        for k in _ID_COLUMNS:
            source_map.setdefault(k, "identity")

        # S56 open columns
        _extract_prefixed(
            row, snap, prefix="s56_", allow=_S56_OPEN, source_map=source_map, source_table=_S56
        )
        for name in _extract_snapshot_json(
            row, snap.get("snapshot_json"), source_map=source_map, source_table=_S56
        ):
            _rej(name)

        # S58
        d58 = None
        if pid is not None and (pid,) in s58_by_pid:
            d58 = s58_by_pid[(pid,)]
        elif sig in s58_by_sig:
            d58 = s58_by_sig[sig]
        _extract_prefixed(
            row, d58, prefix="s58_", allow=_S58_OPEN, source_map=source_map, source_table=_S58
        )
        if d58 and d58.get("opened_at") is not None:
            row["opened_at"] = d58.get("opened_at")

        # Live joins
        t42 = None
        if pid is not None and (pid,) in s42_by_id:
            t42 = s42_by_id[(pid,)]
        elif sig in s42_by_sig:
            t42 = s42_by_sig[sig]
        _extract_prefixed(
            row, t42, prefix="s42_", allow=_S42_OPEN, source_map=source_map, source_table=_S42
        )

        f55 = s55_by_sig.get(sig)
        _extract_prefixed(
            row, f55, prefix="s55_", allow=_S55_OPEN, source_map=source_map, source_table=_S55
        )
        if f55:
            for name in _extract_features_json(
                row, f55.get("features_json"), source_map=source_map, source_table=_S55
            ):
                _rej(name)

        s40 = s40_by_sig.get(sig)
        _extract_prefixed(
            row, s40, prefix="s40_", allow=_S40_OPEN, source_map=source_map, source_table=_S40
        )

        _derived_open_fields(row, source_map)

        # Final leakage sweep
        for key in list(row.keys()):
            if key in _ID_COLUMNS:
                continue
            if is_leakage_name(key):
                _rej(key)
                row.pop(key, None)
                source_map.pop(key, None)

        rows.append(row)

        # Labels stored separately (not in feature matrix)
        pnl = _safe_float(snap.get("pnl_usd"))
        labels.append(
            {
                "paper_trade_id": pid,
                "s40_signal_type": s_type,
                "s40_signal_id": s_id,
                "label_pnl_usd": pnl,
                "label_pnl_pct": _safe_float(snap.get("pnl_pct")),
                "label_win": (1 if pnl is not None and pnl > 0 else (0 if pnl is not None else None)),
                "label_exit_reason": snap.get("exit_reason"),
                "label_duration_sec": snap.get("duration_sec"),
                "label_source_table": _S56,
            }
        )

    return rows, source_map, rejected, labels


def _infer_dtype(values: list[Any]) -> str:
    filled = [v for v in values if v is not None and v != ""]
    if not filled:
        return "null"
    if all(isinstance(v, bool) for v in filled):
        return "bool"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in filled):
        return "int"
    nums = [_safe_float(v) for v in filled]
    if nums and all(x is not None for x in nums):
        if all(float(x).is_integer() for x in nums if x is not None):
            return "int"
        return "float"
    return "str"


def _fill_rate(values: list[Any], n: int) -> float:
    if n <= 0:
        return 0.0
    filled = sum(1 for v in values if v is not None and v != "")
    return round(100.0 * filled / n, 4)


def _build_column_meta(
    rows: list[dict[str, Any]],
    source_map: dict[str, str],
) -> list[ColumnMeta]:
    if not rows:
        return []
    keys = sorted(rows[0].keys())
    # union all keys
    all_keys: set[str] = set()
    for r in rows:
        all_keys.update(r.keys())
    metas: list[ColumnMeta] = []
    n = len(rows)
    for name in sorted(all_keys):
        vals = [r.get(name) for r in rows]
        role = "id" if name in _ID_COLUMNS else "feature"
        metas.append(
            ColumnMeta(
                name=name,
                dtype=_infer_dtype(vals),
                fill_rate=_fill_rate(vals, n),
                source_table=source_map.get(name, "unknown"),
                available_at_open=True if role == "feature" else True,
                role=role,
            )
        )
    # ids are available at open by construction
    return metas


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            out = {}
            for c in columns:
                v = r.get(c)
                if isinstance(v, (dict, list)):
                    out[c] = json.dumps(v, default=str)
                else:
                    out[c] = v
            w.writerow(out)


def _write_parquet(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(
            "pyarrow is required to write lab_dataset.parquet; pip install pyarrow"
        ) from exc

    arrays = {}
    for c in columns:
        col_vals = []
        for r in rows:
            v = r.get(c)
            if isinstance(v, (dict, list)):
                v = json.dumps(v, default=str)
            col_vals.append(v)
        arrays[c] = col_vals
    table = pa.table(arrays)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def _categorical_continuous(metas: list[ColumnMeta]) -> tuple[list[str], list[str]]:
    cat: list[str] = []
    cont: list[str] = []
    for m in metas:
        if m.role != "feature":
            continue
        if m.dtype in ("str", "bool") or m.name in {
            "derived_session",
            "derived_coin",
            "derived_is_hist",
            "s56_direction",
            "s55_direction",
            "s42_direction",
            "s40_direction",
            "s56_symbol",
            "s55_symbol",
            "s56_market_regime",
            "s55_market_regime",
            "s55_gate_decision",
            "s58_gate_result",
            "s58_gate_reason",
        }:
            cat.append(m.name)
        elif m.dtype in ("int", "float"):
            # hour/weekday treated categorical for ML cardinally but listed continuous+note
            if m.name.endswith("_hour") or m.name.endswith("_weekday") or m.name in (
                "s55_hour",
                "s56_hour",
                "s55_weekday",
                "s56_weekday",
            ):
                cat.append(m.name)
            else:
                cont.append(m.name)
        else:
            cat.append(m.name)
    return cat, cont


def write_dataset_schema_md(
    path: Path,
    *,
    metas: list[ColumnMeta],
    rejected: list[str],
    n_rows: int,
    label_fields: list[dict[str, Any]],
) -> None:
    cat, cont = _categorical_continuous(metas)
    ids = [m.name for m in metas if m.role == "id"]
    feats = [m for m in metas if m.role == "feature"]
    lines = [
        "# Canonical Research Dataset Schema (Phase 5B)",
        "",
        "Export layer only. Open-time features per closed trade.",
        "Leakage columns are rejected at build time.",
        "",
        f"- Rows: **{n_rows}**",
        f"- Feature columns: **{len(feats)}**",
        f"- Identity columns: **{len(ids)}**",
        f"- Rejected leakage names (unique): **{len(rejected)}**",
        "",
        "## Identity keys",
        "",
        "Used to join labels and upstream tables:",
        "",
    ]
    for name in ids:
        lines.append(f"- `{name}`")
    lines += [
        "",
        "## Target variables (labels — NOT in feature matrix)",
        "",
        "Labels are written to `metadata.json` → `labels` (and summarized here).",
        "Join back on `(paper_trade_id)` or `(s40_signal_type, s40_signal_id)`.",
        "",
        "| Label | Meaning | Source |",
        "|-------|---------|--------|",
        "| `label_pnl_usd` | Closed PnL USD | S56 |",
        "| `label_pnl_pct` | Closed PnL % | S56 |",
        "| `label_win` | 1 if pnl_usd > 0 else 0 | derived |",
        "| `label_exit_reason` | Exit reason (forensics) | S56 |",
        "| `label_duration_sec` | Hold duration | S56 |",
        "",
        f"_Label rows attached in metadata: {len(label_fields)}_",
        "",
        "## Categorical features",
        "",
    ]
    for n in cat:
        m = next(x for x in metas if x.name == n)
        lines.append(
            f"- `{n}` — fill={m.fill_rate}% source=`{m.source_table}` dtype={m.dtype}"
        )
    lines += ["", "## Continuous features", ""]
    for n in cont:
        m = next(x for x in metas if x.name == n)
        lines.append(
            f"- `{n}` — fill={m.fill_rate}% source=`{m.source_table}` dtype={m.dtype}"
        )
    lines += [
        "",
        "## Full feature list",
        "",
        "| name | dtype | fill_rate | source_table | available_at_open |",
        "|------|-------|----------:|--------------|-------------------|",
    ]
    for m in feats:
        lines.append(
            f"| `{m.name}` | {m.dtype} | {m.fill_rate} | `{m.source_table}` | {str(m.available_at_open).lower()} |"
        )
    lines += [
        "",
        "## Rejected leakage (examples)",
        "",
    ]
    for name in rejected[:80]:
        lines.append(f"- `{name}`")
    if len(rejected) > 80:
        lines.append(f"- … +{len(rejected) - 80} more")
    lines += [
        "",
        "## Future consumers",
        "",
        "This dataset is the **intended sole feature input** for S59 / S61 / S62 / S64 / S65.",
        "Existing reports remain on `load_lab_trades()` until explicitly migrated.",
        "",
        "## Build",
        "",
        "```bash",
        "python -m bot.research.market_events.research_dataset_builder",
        "```",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_lab_dataset(
    out_dir: Path | None = None,
    *,
    research_conn: Any | None = None,
    live_conn: Any | None = None,
    write_docs_schema: bool = True,
) -> DatasetBuildResult:
    """Build and write lab_dataset.parquet/csv + metadata.json."""
    from bot.research.market_events.db import market_events_readonly_connection
    from bot.research.market_events.signal_intelligence.research_repository_s60 import (
        research_connection,
    )

    out = default_dataset_dir() if out_dir is None else Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()

    owns_research = research_conn is None
    owns_live = live_conn is None

    def _run(rconn: Any, lconn: Any | None) -> DatasetBuildResult:
        rows, source_map, rejected, labels = build_canonical_rows(
            research_conn=rconn, live_conn=lconn
        )
        metas = _build_column_meta(rows, source_map)
        columns = [m.name for m in metas]

        csv_path = out / "lab_dataset.csv"
        pq_path = out / "lab_dataset.parquet"
        meta_path = out / "metadata.json"
        schema_path = out / "DATASET_SCHEMA.md"

        _write_csv(csv_path, rows, columns)
        _write_parquet(pq_path, rows, columns)

        cat, cont = _categorical_continuous(metas)
        meta = {
            "ok": True,
            "phase": "5B",
            "generated_at_iso": datetime.now(timezone.utc).isoformat(),
            "elapsed_sec": round(time.perf_counter() - t0, 3),
            "n_rows": len(rows),
            "n_feature_columns": sum(1 for m in metas if m.role == "feature"),
            "n_id_columns": sum(1 for m in metas if m.role == "id"),
            "output_dir": str(out),
            "files": {
                "lab_dataset.csv": str(csv_path),
                "lab_dataset.parquet": str(pq_path),
                "metadata.json": str(meta_path),
                "DATASET_SCHEMA.md": str(schema_path),
            },
            "join_keys": list(_ID_COLUMNS),
            "sources": {
                "S40": _S40,
                "S42": _S42,
                "S55": _S55,
                "S56": _S56,
                "S58": _S58,
            },
            "columns": [asdict(m) for m in metas],
            "categorical_features": cat,
            "continuous_features": cont,
            "rejected_leakage": rejected,
            "n_rejected_leakage": len(rejected),
            "labels": labels,
            "label_schema": [
                {"name": "label_pnl_usd", "dtype": "float", "available_at_open": False, "source_table": _S56},
                {"name": "label_pnl_pct", "dtype": "float", "available_at_open": False, "source_table": _S56},
                {"name": "label_win", "dtype": "int", "available_at_open": False, "source_table": "derived"},
                {"name": "label_exit_reason", "dtype": "str", "available_at_open": False, "source_table": _S56},
                {"name": "label_duration_sec", "dtype": "float", "available_at_open": False, "source_table": _S56},
            ],
            "future_consumers": ["S59", "S61", "S62", "S64", "S65"],
            "backward_compatibility": (
                "Existing reports continue on load_lab_trades(); this export is additive."
            ),
        }
        meta_path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
        write_dataset_schema_md(
            schema_path,
            metas=metas,
            rejected=rejected,
            n_rows=len(rows),
            label_fields=labels,
        )
        if write_docs_schema:
            docs_schema = _repo_root() / "docs" / "DATASET_SCHEMA.md"
            write_dataset_schema_md(
                docs_schema,
                metas=metas,
                rejected=rejected,
                n_rows=len(rows),
                label_fields=labels,
            )
            meta["files"]["docs_DATASET_SCHEMA.md"] = str(docs_schema)

        return DatasetBuildResult(
            n_rows=len(rows),
            n_feature_columns=sum(1 for m in metas if m.role == "feature"),
            n_rejected_leakage=len(rejected),
            out_dir=str(out),
            files=meta["files"],
            columns=metas,
            rejected_leakage=rejected,
            label_join={"n_labels": len(labels), "keys": ["paper_trade_id", "s40_signal_type", "s40_signal_id"]},
        )

    if owns_research:
        with research_connection(readonly=True) as rconn:
            if owns_live:
                with market_events_readonly_connection() as lconn:
                    return _run(rconn, lconn)
            return _run(rconn, live_conn)
    return _run(research_conn, live_conn)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Phase 5B — export canonical open-time lab dataset")
    parser.add_argument(
        "--out-dir",
        type=str,
        default=None,
        help="Output directory (default: research/datasets)",
    )
    args = parser.parse_args(argv)
    result = export_lab_dataset(Path(args.out_dir) if args.out_dir else None)
    print(
        json.dumps(
            {
                "ok": True,
                "n_rows": result.n_rows,
                "n_feature_columns": result.n_feature_columns,
                "n_rejected_leakage": result.n_rejected_leakage,
                "out_dir": result.out_dir,
                "files": result.files,
            },
            indent=2,
        )
    )
    return 0 if result.n_rows >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
