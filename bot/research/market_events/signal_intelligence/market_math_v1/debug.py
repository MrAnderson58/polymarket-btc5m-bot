"""Market-math data source diagnostics (paths, counts, SQL)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from bot.research.market_events.config import BASE_DIR
from bot.research.market_events.signal_intelligence.feature_store import (
    FEATURE_STORE_DIR,
    feature_store_path,
)
from bot.research.market_events.signal_intelligence.market_math_v1.dataset import (
    CLOSED_S42_SQL,
    JOIN_SQL,
    S55_COUNT_SQL,
    count_corpus,
    load_market_math_dataset,
)

# Optional lake / parquet corpora (not used by the research JOIN, but audited).
_RESEARCH_DATASET_SQL = (
    "SELECT COUNT(*) FROM research_dataset"
)
_G51_SQL = "SELECT COUNT(*) FROM market_research_dataset_g51"


def _count(conn: Any, sql: str) -> int | None:
    try:
        row = conn.execute(sql).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return None


def _parquet_rows(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        import pyarrow.parquet as pq

        return int(pq.ParquetFile(path).metadata.num_rows)
    except Exception:
        try:
            import pandas as pd

            return int(len(pd.read_parquet(path)))
        except Exception:
            return None


def resolve_feature_store_parquet() -> Path:
    root = Path(os.environ.get("ML_FEATURE_STORE_DIR", str(FEATURE_STORE_DIR)))
    return (root / "v1" / "samples.parquet").resolve()


def resolve_training_dataset_parquet() -> Path:
    return (BASE_DIR / "research" / "ml" / "datasets" / "v1" / "training_dataset.parquet").resolve()


def audit_market_math_sources(conn: Any, *, db_path: str | Path | None = None) -> dict[str, Any]:
    """Collect absolute paths, row counts, and SQL for every audited source."""
    path = Path(db_path).resolve() if db_path else None
    if path is None:
        try:
            path = Path(str(conn.execute("PRAGMA database_list").fetchone()[2])).resolve()
        except Exception:
            path = Path("unknown")

    corpus = count_corpus(conn)
    fs_path = resolve_feature_store_parquet()
    td_path = resolve_training_dataset_parquet()
    # Prefer canonical feature_store_path helper when versioned dir exists.
    try:
        alt = feature_store_path(version="v1") / "samples.parquet"
        if alt.is_file():
            fs_path = alt.resolve()
    except Exception:
        pass

    research_n = _count(conn, _RESEARCH_DATASET_SQL)
    g51_n = _count(conn, _G51_SQL)

    # Final research corpus = rows that survive JOIN + feature extraction.
    research_rows = load_market_math_dataset(conn, limit=None, print_stats=False)
    hidden = {
        "MARKET_MATH_LIMIT": os.environ.get("MARKET_MATH_LIMIT"),
        "ML_STORE_LIMIT": os.environ.get("ML_STORE_LIMIT"),
        "loader_default_limit": "none (full history; LIMIT only if MARKET_MATH_LIMIT set)",
        "join_type": "INNER JOIN s42.id = s55.paper_trade_id",
        "note": (
            "If CLOSED/matched counts are small, the analytics DB itself is sparse — "
            "not an artificial LIMIT 50 in market-math. "
            "research_dataset / feature_store / training_dataset are audited separately "
            "and are NOT the primary market-math corpus."
        ),
    }

    sources = {
        "market_events_paper_trades_s42": {
            "path": str(path),
            "rows": corpus.get("closed_s42"),
            "rows_total": _count(conn, "SELECT COUNT(*) FROM market_events_paper_trades_s42"),
            "sql": CLOSED_S42_SQL.strip(),
        },
        "market_events_trade_features_s55": {
            "path": str(path),
            "rows": corpus.get("s55"),
            "sql": S55_COUNT_SQL.strip(),
        },
        "s42_join_s55": {
            "path": str(path),
            "rows": corpus.get("matched"),
            "sql": JOIN_SQL.strip(),
        },
        "research_dataset": {
            "path": str(path),
            "rows": research_n,
            "sql": _RESEARCH_DATASET_SQL,
            "used_by_market_math": False,
        },
        "market_research_dataset_g51": {
            "path": str(path),
            "rows": g51_n,
            "sql": _G51_SQL,
            "used_by_market_math": False,
        },
        "feature_store": {
            "path": str(fs_path),
            "rows": _parquet_rows(fs_path),
            "sql": "(parquet file — built from S42/S55 via feature_store.load_closed_trade_rows)",
            "used_by_market_math": False,
        },
        "training_dataset": {
            "path": str(td_path),
            "rows": _parquet_rows(td_path),
            "sql": "(parquet file — exported ML training set)",
            "used_by_market_math": False,
        },
    }

    return {
        "ok": True,
        "db_path": str(path),
        "sources": sources,
        "corpus": corpus,
        "research_rows": len(research_rows),
        "hidden_limits": hidden,
    }


def format_market_math_debug(conn: Any, *, db_path: str | Path | None = None) -> str:
    audit = audit_market_math_sources(conn, db_path=db_path)
    lines = [
        "Market Mathematics Data Loader Debug",
        "",
        f"DB path: {audit['db_path']}",
        "",
    ]
    for name, src in (audit.get("sources") or {}).items():
        lines.append(f"=== {name} ===")
        lines.append(f"path: {src.get('path')}")
        if src.get("rows_total") is not None:
            lines.append(f"rows_total: {src.get('rows_total')}")
        lines.append(f"rows: {src.get('rows')}")
        lines.append(f"sql:\n{src.get('sql')}")
        if "used_by_market_math" in src:
            lines.append(f"used_by_market_math: {src.get('used_by_market_math')}")
        lines.append("")

    corpus = audit.get("corpus") or {}
    lines.extend([
        "=== load summary (research JOIN) ===",
        "Loaded CLOSED trades:",
        str(corpus.get("closed_s42")),
        "",
        "Loaded S55 rows:",
        str(corpus.get("s55")),
        "",
        "Matched rows:",
        str(corpus.get("matched")),
        "",
        "Rows in market-math research:",
        str(audit.get("research_rows")),
        "",
        "=== hidden limit audit ===",
    ])
    for k, v in (audit.get("hidden_limits") or {}).items():
        lines.append(f"{k}: {v}")
    return "\n".join(lines)


__all__ = [
    "audit_market_math_sources",
    "format_market_math_debug",
]
