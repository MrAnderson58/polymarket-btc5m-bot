"""Feature Store V1 — one ML sample per completed paper trade (shadow / offline).

Does NOT place trades. Persists versioned feature rows for offline training.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.lib.feature_utils import (
    safe_float as _safe_float,
)
from bot.research.market_events.signal_intelligence.strategy_optimizer import (
    _confidence,
    _pattern_label,
    _row,
)

logger = logging.getLogger(__name__)

FEATURE_VERSION = "v1"
FEATURE_STORE_DIR = BASE_DIR / "research" / "ml" / "feature_store"
MANIFEST_NAME = "manifest.json"

# Explicit schema: every feature carries version metadata in the sample.
FEATURE_SPEC: dict[str, dict[str, str]] = {
    # categoricals / ids
    "symbol": {"type": "categorical", "version": FEATURE_VERSION},
    "direction": {"type": "categorical", "version": FEATURE_VERSION},
    "pattern": {"type": "categorical", "version": FEATURE_VERSION},
    "gate_decision": {"type": "categorical", "version": FEATURE_VERSION},
    "news_category": {"type": "categorical", "version": FEATURE_VERSION},
    "market_regime": {"type": "categorical", "version": FEATURE_VERSION},
    # confidence / scores
    "confidence": {"type": "numeric", "version": FEATURE_VERSION},
    "ai_score": {"type": "numeric", "version": FEATURE_VERSION},
    "macro_score": {"type": "numeric", "version": FEATURE_VERSION},
    "news_score": {"type": "numeric", "version": FEATURE_VERSION},
    # market microstructure / technicals
    "volatility": {"type": "numeric", "version": FEATURE_VERSION},
    "atr": {"type": "numeric", "version": FEATURE_VERSION},
    "vwap_distance": {"type": "numeric", "version": FEATURE_VERSION},
    "ema20_distance": {"type": "numeric", "version": FEATURE_VERSION},
    "ema50_distance": {"type": "numeric", "version": FEATURE_VERSION},
    "ema200_distance": {"type": "numeric", "version": FEATURE_VERSION},
    "rsi": {"type": "numeric", "version": FEATURE_VERSION},
    "macd": {"type": "numeric", "version": FEATURE_VERSION},
    "funding": {"type": "numeric", "version": FEATURE_VERSION},
    "oi_delta": {"type": "numeric", "version": FEATURE_VERSION},
    "liquidation_metric": {"type": "numeric", "version": FEATURE_VERSION},
    "spread": {"type": "numeric", "version": FEATURE_VERSION},
    "book_imbalance": {"type": "numeric", "version": FEATURE_VERSION},
    "time_to_expiry": {"type": "numeric", "version": FEATURE_VERSION},
    "btc_move": {"type": "numeric", "version": FEATURE_VERSION},
    "volume": {"type": "numeric", "version": FEATURE_VERSION},
    "fear_greed": {"type": "numeric", "version": FEATURE_VERSION},
    "trend": {"type": "numeric", "version": FEATURE_VERSION},
    "hour": {"type": "numeric", "version": FEATURE_VERSION},
    "weekday": {"type": "numeric", "version": FEATURE_VERSION},
    # outcomes (labels / post-trade — available for training only)
    "holding_time": {"type": "label_aux", "version": FEATURE_VERSION},
    "mfe": {"type": "label_aux", "version": FEATURE_VERSION},
    "mae": {"type": "label_aux", "version": FEATURE_VERSION},
    "pnl": {"type": "label", "version": FEATURE_VERSION},
    "pnl_pct": {"type": "label", "version": FEATURE_VERSION},
    "result": {"type": "label", "version": FEATURE_VERSION},
    "profitable": {"type": "label", "version": FEATURE_VERSION},
}

PREDICTION_FEATURES = [
    k for k, v in FEATURE_SPEC.items()
    if v["type"] in ("categorical", "numeric")
]
LABEL_COL = "profitable"


def feature_store_path(*, version: str | None = None) -> Path:
    ver = version or FEATURE_VERSION
    root = Path(os.environ.get("ML_FEATURE_STORE_DIR", str(FEATURE_STORE_DIR)))
    return root / ver


def _parse_features_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _dist(entry: float | None, level: float | None) -> float | None:
    if entry is None or level is None or abs(float(entry)) < 1e-12:
        return None
    return (float(level) - float(entry)) / float(entry) * 100.0


def extract_sample(row: dict[str, Any], *, feature_version: str = FEATURE_VERSION) -> dict[str, Any]:
    """Build one versioned ML sample from a closed trade (+ features) row."""
    blob = _parse_features_json(row.get("features_json") or row.get("snapshot_json"))
    entry = _safe_float(row.get("entry"))
    vwap = _safe_float(row.get("vwap") if row.get("vwap") is not None else blob.get("vwap"))
    ema20 = _safe_float(row.get("ema20") if row.get("ema20") is not None else blob.get("ema20"))
    ema50 = _safe_float(row.get("ema50") if row.get("ema50") is not None else blob.get("ema50"))
    ema200 = _safe_float(row.get("ema200") if row.get("ema200") is not None else blob.get("ema200"))
    macd = _safe_float(row.get("macd") if row.get("macd") is not None else blob.get("macd"))
    if macd is None:
        macd = _safe_float(blob.get("macd_hist") or blob.get("macd_histogram"))

    liq = _safe_float(
        row.get("liquidation_metric")
        if row.get("liquidation_metric") is not None
        else blob.get("liquidation_metric") or blob.get("liq_score") or blob.get("liquidations"),
    )
    imbalance = _safe_float(
        row.get("book_imbalance")
        if row.get("book_imbalance") is not None
        else blob.get("book_imbalance") or blob.get("imbalance"),
    )
    tte = _safe_float(
        row.get("time_to_expiry")
        if row.get("time_to_expiry") is not None
        else blob.get("time_to_expiry") or blob.get("seconds_to_expiry"),
    )
    btc_move = _safe_float(
        row.get("btc_move")
        if row.get("btc_move") is not None
        else row.get("btc_return") or blob.get("btc_move") or blob.get("btc_return"),
    )

    pnl_usd = _safe_float(row.get("pnl_usd"))
    pnl_pct = _safe_float(row.get("pnl_pct"))
    if pnl_usd is None and pnl_pct is not None:
        pnl_usd = pnl_pct
    result = str(row.get("result") or "").upper() or None
    profitable = 0
    if pnl_usd is not None:
        profitable = 1 if float(pnl_usd) > 0 else 0
    elif result == "WIN":
        profitable = 1

    conf = _confidence(row)
    sample = {
        "sample_id": (
            f"{row.get('s40_signal_type') or 'paper'}:"
            f"{row.get('s40_signal_id') or row.get('id') or row.get('paper_trade_id')}"
        ),
        "paper_trade_id": row.get("paper_trade_id") or row.get("id"),
        "feature_version": feature_version,
        "created_at": int(row.get("closed_at") or row.get("updated_at") or time.time()),
        "symbol": str(row.get("symbol") or "NULL").upper(),
        "direction": str(row.get("direction") or "NULL").upper(),
        "pattern": str(row.get("pattern") or _pattern_label(row) or "NULL"),
        "gate_decision": str(row.get("gate_decision") or "NULL"),
        "news_category": str(row.get("news_category") or "NULL"),
        "market_regime": str(row.get("market_regime") or "NULL"),
        "confidence": conf,
        "ai_score": _safe_float(row.get("ai_score")),
        "macro_score": _safe_float(row.get("macro_score")),
        "news_score": _safe_float(row.get("news_score")),
        "volatility": _safe_float(row.get("volatility")),
        "atr": _safe_float(row.get("atr")),
        "vwap_distance": _dist(entry, vwap),
        "ema20_distance": _dist(entry, ema20),
        "ema50_distance": _dist(entry, ema50),
        "ema200_distance": _dist(entry, ema200),
        "rsi": _safe_float(row.get("rsi")),
        "macd": macd,
        "funding": _safe_float(row.get("funding")),
        "oi_delta": _safe_float(row.get("oi_delta")),
        "liquidation_metric": liq,
        "spread": _safe_float(row.get("spread")),
        "book_imbalance": imbalance,
        "time_to_expiry": tte,
        "btc_move": btc_move,
        "volume": _safe_float(row.get("volume")),
        "fear_greed": _safe_float(row.get("fear_greed")),
        "trend": _safe_float(row.get("trend")),
        "hour": _safe_float(row.get("hour")),
        "weekday": _safe_float(row.get("weekday")),
        "holding_time": _safe_float(row.get("holding_seconds") or row.get("duration_sec") or row.get("holding_time")),
        "mfe": _safe_float(row.get("mfe_pct") if row.get("mfe_pct") is not None else row.get("mfe")),
        "mae": _safe_float(row.get("mae_pct") if row.get("mae_pct") is not None else row.get("mae")),
        "pnl": pnl_usd,
        "pnl_pct": pnl_pct,
        "result": result,
        "profitable": profitable,
        "shadow_mode": True,
        "ml_may_execute": False,
    }
    sample["feature_spec_version"] = feature_version
    return sample


def load_closed_trade_rows(conn: Any, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Load CLOSED paper trades joined with S55 feature columns when present."""
    limit = int(limit or os.environ.get("ML_STORE_LIMIT", "5000"))
    sql = """
        SELECT p.*,
               f.gate_decision, f.market_regime, f.ai_score, f.macro_score, f.news_score,
               f.volatility, f.atr, f.rsi, f.funding, f.oi_delta, f.spread, f.volume,
               f.fear_greed, f.trend, f.hour, f.weekday, f.features_json,
               f.mfe_pct AS f_mfe, f.mae_pct AS f_mae, f.duration_sec,
               f.pnl_usd AS f_pnl_usd, f.pnl_pct AS f_pnl_pct
        FROM market_events_paper_trades_s42 p
        LEFT JOIN market_events_trade_features_s55 f ON f.paper_trade_id = p.id
        WHERE p.status = 'CLOSED' AND p.pnl_pct IS NOT NULL
        ORDER BY COALESCE(p.closed_at, p.updated_at, p.id) DESC
        LIMIT ?
        """
    try:
        rows = [_row(r) for r in conn.execute(sql, (limit,)).fetchall()]
    except Exception as exc:
        logger.warning("feature_store: S42/S55 load failed: %s", exc)
        rows = []

    if not rows:
        try:
            from bot.research.market_events.signal_intelligence.feature_lab_s59 import (
                load_lab_trades,
            )
            rows = load_lab_trades(conn)
            if limit and len(rows) > limit:
                rows = rows[:limit]
        except Exception as exc:
            logger.warning("feature_store: lab fallback failed: %s", exc)
            rows = []

    for r in rows:
        if r.get("mfe_pct") is None and r.get("f_mfe") is not None:
            r["mfe_pct"] = r["f_mfe"]
        if r.get("mae_pct") is None and r.get("f_mae") is not None:
            r["mae_pct"] = r["f_mae"]
        if r.get("holding_seconds") is None and r.get("duration_sec") is not None:
            r["holding_seconds"] = r["duration_sec"]
        if r.get("pnl_usd") is None and r.get("f_pnl_usd") is not None:
            r["pnl_usd"] = r["f_pnl_usd"]
        r["pattern"] = _pattern_label(r)
    return rows


def build_samples_from_conn(
    conn: Any,
    *,
    feature_version: str = FEATURE_VERSION,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    rows = load_closed_trade_rows(conn, limit=limit)
    return [extract_sample(r, feature_version=feature_version) for r in rows]


def persist_samples(
    samples: list[dict[str, Any]],
    *,
    feature_version: str = FEATURE_VERSION,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Write samples to versioned feature-store directory (parquet + jsonl + manifest)."""
    directory = out_dir or feature_store_path(version=feature_version)
    directory.mkdir(parents=True, exist_ok=True)
    parquet_path = directory / "samples.parquet"
    jsonl_path = directory / "samples.jsonl"
    manifest_path = directory / MANIFEST_NAME

    # JSONL always (no extra deps)
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for s in samples:
            fh.write(json.dumps(s, default=str) + "\n")

    parquet_ok = False
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        if samples:
            # Normalize keys
            keys = sorted({k for s in samples for k in s.keys()})
            cols = {k: [s.get(k) for s in samples] for k in keys}
            table = pa.Table.from_pydict(cols)
            pq.write_table(table, parquet_path)
            parquet_ok = True
    except Exception as exc:
        logger.info("feature_store: parquet write skipped: %s", exc)

    n_pos = sum(1 for s in samples if int(s.get("profitable") or 0) == 1)
    manifest = {
        "feature_version": feature_version,
        "feature_spec": FEATURE_SPEC,
        "n_samples": len(samples),
        "n_profitable": n_pos,
        "n_unprofitable": len(samples) - n_pos,
        "prediction_features": PREDICTION_FEATURES,
        "label": LABEL_COL,
        "shadow_mode": True,
        "ml_may_execute": False,
        "updated_at": int(time.time()),
        "paths": {
            "jsonl": str(jsonl_path.resolve()),
            "parquet": str(parquet_path.resolve()) if parquet_ok else None,
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "feature_version": feature_version,
        "n_samples": len(samples),
        "directory": str(directory.resolve()),
        "manifest": manifest,
        "parquet_ok": parquet_ok,
    }


def load_samples(
    *,
    feature_version: str = FEATURE_VERSION,
    store_dir: Path | None = None,
) -> list[dict[str, Any]]:
    directory = store_dir or feature_store_path(version=feature_version)
    parquet_path = directory / "samples.parquet"
    jsonl_path = directory / "samples.jsonl"
    if parquet_path.exists():
        try:
            import pyarrow.parquet as pq

            table = pq.read_table(parquet_path)
            data = table.to_pydict()
            n = len(next(iter(data.values()))) if data else 0
            keys = list(data.keys())
            return [{k: data[k][i] for k in keys} for i in range(n)]
        except Exception:
            pass
    if not jsonl_path.exists():
        return []
    out: list[dict[str, Any]] = []
    with jsonl_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def sync_feature_store(
    conn: Any,
    *,
    feature_version: str = FEATURE_VERSION,
    limit: int | None = None,
) -> dict[str, Any]:
    """Extract from DB and persist versioned samples."""
    samples = build_samples_from_conn(conn, feature_version=feature_version, limit=limit)
    return persist_samples(samples, feature_version=feature_version)


__all__ = [
    "FEATURE_SPEC",
    "FEATURE_STORE_DIR",
    "FEATURE_VERSION",
    "LABEL_COL",
    "PREDICTION_FEATURES",
    "build_samples_from_conn",
    "extract_sample",
    "feature_store_path",
    "load_closed_trade_rows",
    "load_samples",
    "persist_samples",
    "sync_feature_store",
]
