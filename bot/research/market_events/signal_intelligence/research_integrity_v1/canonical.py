"""ONE canonical dataset + elite corpus for all research reports."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from bot.research.market_events.signal_intelligence.elite_candidate_v1.score import (
    STORE_CATEGORIES,
)
from bot.research.market_events.signal_intelligence.elite_candidate_v1.store import (
    load_stored_candidates,
)
from bot.research.market_events.signal_intelligence.feature_store import (
    FEATURE_VERSION,
    MANIFEST_NAME,
    feature_store_path,
    load_samples,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.schema import (
    BUILD_TABLE,
    DATASET_VERSION,
    LAKE_TABLE,
    META_TABLE,
    ensure_research_lake_schema,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.loader import (
    research_lake_row_count,
)

CANONICAL_ELITE_TABLE = "elite_candidates_v1"


def load_canonical_elite(conn: Any) -> list[dict[str, Any]]:
    """
    ONE canonical elite corpus — no local filtering, no resampling, no LIMIT.
    All Elite / Audit / Profile / Morning / Book D must use this.
    """
    return load_stored_candidates(conn, categories=list(STORE_CATEGORIES))


def _lake_meta(conn: Any) -> dict[str, str]:
    ensure_research_lake_schema(conn)
    out: dict[str, str] = {}
    try:
        for k, v in conn.execute(f"SELECT key, value FROM {META_TABLE}").fetchall():
            out[str(k)] = str(v)
    except Exception:
        pass
    return out


def _latest_build_ts(conn: Any) -> int | None:
    ensure_research_lake_schema(conn)
    try:
        row = conn.execute(
            f"SELECT build_ts FROM {BUILD_TABLE} ORDER BY build_ts DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return int(row[0] if not hasattr(row, "keys") else row["build_ts"])
    except Exception:
        return None


def canonical_dataset_hash(conn: Any) -> str:
    """Stable SHA256 over canonical lake rows (trade_id|opened_at|closed_at|pnl)."""
    ensure_research_lake_schema(conn)
    parts: list[str] = []
    try:
        rows = conn.execute(
            f"""
            SELECT trade_id, opened_at, closed_at, pnl
            FROM {LAKE_TABLE}
            ORDER BY trade_id ASC
            """
        ).fetchall()
        for r in rows:
            tid = int(r[0] if not hasattr(r, "keys") else r["trade_id"])
            oa = int((r[1] if not hasattr(r, "keys") else r["opened_at"]) or 0)
            ca = int((r[2] if not hasattr(r, "keys") else r["closed_at"]) or 0)
            pnl = (r[3] if not hasattr(r, "keys") else r["pnl"])
            parts.append(f"{tid}|{oa}|{ca}|{pnl}")
    except Exception:
        pass
    raw = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def get_canonical_dataset_meta(conn: Any) -> dict[str, Any]:
    """Canonical dataset descriptor — must match across Cursor / Mini / CI."""
    meta = _lake_meta(conn)
    lake_rows = research_lake_row_count(conn)
    build_ts = _latest_build_ts(conn)
    if build_ts is None:
        try:
            build_ts = int(meta.get("last_build_ts") or 0) or None
        except Exception:
            build_ts = None
    dataset_version = meta.get("dataset_version") or DATASET_VERSION
    return {
        "dataset_version": dataset_version,
        "lake_rows": lake_rows,
        "build_ts": build_ts,
        "hash": canonical_dataset_hash(conn),
        "feature_version": meta.get("feature_version"),
        "schema_version": meta.get("schema_version"),
        "updated_at": int(time.time()),
    }


def feature_store_status(*, version: str = FEATURE_VERSION) -> dict[str, Any]:
    """Whether offline Feature Store was built (parquet/jsonl/manifest)."""
    directory = feature_store_path(version=version)
    manifest_path = directory / MANIFEST_NAME
    parquet_path = directory / "samples.parquet"
    jsonl_path = directory / "samples.jsonl"
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    n_samples = int(manifest.get("n_samples") or 0)
    if n_samples <= 0:
        try:
            n_samples = len(load_samples(feature_version=version, store_dir=directory))
        except Exception:
            n_samples = 0
    built = bool(
        manifest_path.exists()
        or parquet_path.exists()
        or jsonl_path.exists()
    ) and n_samples > 0
    return {
        "ok": built,
        "missing": not built,
        "feature_version": version,
        "directory": str(directory),
        "n_samples": n_samples,
        "manifest_updated_at": manifest.get("updated_at"),
        "parquet_exists": parquet_path.exists(),
        "jsonl_exists": jsonl_path.exists(),
        "manifest_exists": manifest_path.exists(),
    }


def read_persisted_reality_meta(conn: Any) -> dict[str, Any]:
    """Load dataset binding stored by Reality Validation."""
    out: dict[str, Any] = {}
    try:
        rows = conn.execute(
            """
            SELECT key, value_real, value_text, meta_json
            FROM reality_validation_v1
            WHERE section = 'dataset'
            """
        ).fetchall()
        for r in rows:
            key = str(r["key"] if hasattr(r, "keys") else r[0])
            if hasattr(r, "keys"):
                vr, vt, mj = r["value_real"], r["value_text"], r["meta_json"]
            else:
                vr, vt, mj = r[1], r[2], r[3]
            if key == "hash":
                out["hash"] = vt or (str(vr) if vr is not None else None)
            elif key in ("lake_rows", "build_ts"):
                out[key] = int(vr) if vr is not None else None
            elif key == "reality_score":
                out[key] = float(vr) if vr is not None else None
            elif key == "dataset_version":
                out["dataset_version"] = vt
            elif mj:
                try:
                    out[key] = json.loads(mj)
                except Exception:
                    out[key] = mj
    except Exception:
        pass
    # legacy summary score
    try:
        row = conn.execute(
            """
            SELECT value_real FROM reality_validation_v1
            WHERE section='summary' AND key='reality_score'
            """
        ).fetchone()
        if row and out.get("reality_score") is None:
            v = row["value_real"] if hasattr(row, "keys") else row[0]
            out["reality_score"] = float(v) if v is not None else None
    except Exception:
        pass
    return out


def persist_reality_dataset_binding(
    conn: Any,
    meta: dict[str, Any],
    *,
    reality_score: float | None,
) -> None:
    """Bind Reality Validation to the canonical dataset (parity fix)."""
    from bot.research.market_events.signal_intelligence.reality_validation_v1.schema import (
        ensure_reality_validation_schema,
    )

    ensure_reality_validation_schema(conn)
    now = int(time.time())
    rows = [
        ("dataset_version", None, meta.get("dataset_version")),
        ("lake_rows", float(meta.get("lake_rows") or 0), None),
        ("build_ts", float(meta.get("build_ts") or 0) if meta.get("build_ts") else None, None),
        ("hash", None, meta.get("hash")),
        ("reality_score", reality_score, None),
    ]
    for key, vr, vt in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO reality_validation_v1(section, key, value_real, value_text, updated_at)
            VALUES ('dataset', ?, ?, ?, ?)
            """,
            (key, vr, vt, now),
        )
    conn.commit()


__all__ = [
    "CANONICAL_ELITE_TABLE",
    "canonical_dataset_hash",
    "feature_store_status",
    "get_canonical_dataset_meta",
    "load_canonical_elite",
    "persist_reality_dataset_binding",
    "read_persisted_reality_meta",
]
