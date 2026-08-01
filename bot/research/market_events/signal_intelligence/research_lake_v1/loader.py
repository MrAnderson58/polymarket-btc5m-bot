"""Load flattened research rows from the canonical Research Lake."""

from __future__ import annotations

import json
import logging
from typing import Any

from bot.research.market_events.signal_intelligence.research_lake_v1.schema import (
    LAKE_TABLE,
    ensure_research_lake_schema,
)

logger = logging.getLogger(__name__)


def _parse(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def research_lake_row_count(conn: Any) -> int:
    ensure_research_lake_schema(conn)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {LAKE_TABLE}").fetchone()[0])
    except Exception:
        return 0


def load_research_lake_rows(
    conn: Any,
    *,
    limit: int | None = None,
    require_pnl: bool = True,
) -> list[dict[str, Any]]:
    """Canonical loader used by Alpha / Math / Edge / Feature research."""
    ensure_research_lake_schema(conn)
    sql = f"SELECT * FROM {LAKE_TABLE}"
    if require_pnl:
        sql += " WHERE pnl IS NOT NULL"
    sql += " ORDER BY COALESCE(closed_at, trade_id) ASC"
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (int(limit),)
    try:
        raw = conn.execute(sql, params).fetchall()
    except Exception as exc:
        logger.warning("research_lake load failed: %s", exc)
        return []

    out: list[dict[str, Any]] = []
    for r in raw:
        row = {k: r[k] for k in r.keys()} if hasattr(r, "keys") else dict(r)
        feats = _parse(row.get("features_json"))
        macro = _parse(row.get("macro_json"))
        news = _parse(row.get("news_json"))
        patterns = _parse(row.get("patterns_json"))
        flat: dict[str, Any] = {
            "trade_id": row.get("trade_id"),
            "id": row.get("trade_id"),
            "symbol": row.get("symbol"),
            "direction": row.get("direction"),
            "entry": row.get("entry"),
            "exit": row.get("exit"),
            "result": row.get("result"),
            "pnl": row.get("pnl"),
            "pnl_pct": row.get("pnl_pct"),
            "pnl_usd": row.get("pnl"),
            "gate": row.get("gate"),
            "gate_decision": row.get("gate"),
            "confidence": row.get("confidence"),
            "regime": row.get("regime"),
            "market_regime": row.get("regime"),
            "feature_version": row.get("feature_version"),
            "dataset_version": row.get("dataset_version"),
            "schema_version": row.get("schema_version"),
            "closed_at": row.get("closed_at"),
            "opened_at": row.get("opened_at"),
            "status": row.get("status") or "CLOSED",
            "alpha_labels": _parse(row.get("alpha_labels_json")),
            "optimizer_state": _parse(row.get("optimizer_state_json")),
            "experiment_state": _parse(row.get("experiment_state_json")),
            "patterns": patterns,
            "macro": macro,
            "news": news,
            "features_json": row.get("features_json"),
            "_source": "research_lake_v1",
        }
        for k, v in feats.items():
            if flat.get(k) is None:
                flat[k] = v
        for k, v in macro.items():
            if flat.get(k) is None:
                flat[k] = v
        for k, v in news.items():
            if flat.get(k) is None:
                flat[k] = v
        if flat.get("pattern") is None and patterns.get("pattern") is not None:
            flat["pattern"] = patterns.get("pattern")
        out.append(flat)
    return out


__all__ = ["load_research_lake_rows", "research_lake_row_count"]
