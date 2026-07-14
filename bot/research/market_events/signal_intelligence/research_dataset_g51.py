"""Phase G.5.1 — SQL research dataset builder, audit, and missing-info resolver."""

from __future__ import annotations

import json
import time
from typing import Any

from bot.research.market_events.db import insert_returning_id
from bot.research.market_events.signal_intelligence.candle_pattern_g51 import build_candle_patterns_g51
from bot.research.market_events.signal_intelligence.liquidity_history_g51 import build_liquidity_history_g51
from bot.research.market_events.signal_intelligence.replay_timeline_g51 import (
    build_replay_timeline_g51,
    format_replay_evolution,
)
from bot.research.market_events.signal_intelligence.research_lake_types_g51 import (
    G51_COMPLETENESS_KEYS,
    G51_WINDOWS,
    MISSING_INFO_ALIASES,
)
from bot.research.market_events.signal_intelligence.snapshot_history_g51 import build_snapshot_history_g51

_TABLE = "market_research_dataset_g51"
_BUILD_TABLE = "market_research_lake_builds_g51"


def _row_completeness(
    *,
    snapshot_rows: list[dict],
    candle_rows: list[dict],
    liquidity_rows: list[dict],
    replay_rows: list[dict],
) -> float:
    checks = [
        bool(snapshot_rows),
        any(r.get("metric") == "funding" for r in liquidity_rows),
        any(r.get("metric") == "oi" for r in liquidity_rows),
        bool(candle_rows),
        len(replay_rows) >= 4,
    ]
    return round(sum(1 for c in checks if c) / len(checks) * 100.0, 1)


def _market_evolution_text(snapshot_rows: list[dict[str, Any]]) -> str:
    if not snapshot_rows:
        return ""
    parts = []
    for row in sorted(snapshot_rows, key=lambda r: G51_WINDOWS.get(str(r.get("window_key")), 0)):
        ret = row.get("return_pct")
        if ret is not None:
            parts.append(f"{row['window_key']} ret={ret:+.2f}%")
    return " | ".join(parts)


def _metric_evolution(liquidity_rows: list[dict[str, Any]], metric: str) -> str:
    for row in liquidity_rows:
        if row.get("metric") == metric:
            return str(row.get("evolution_text") or "")
    return ""


def build_candidate_dataset_g51(conn: Any, *, candidate: dict[str, Any]) -> dict[str, Any] | None:
    candidate_id = int(candidate["id"])
    symbol = str(candidate["symbol"])
    anchor_ts = int(candidate.get("created_at") or candidate.get("candidate_ts") or 0)
    if not anchor_ts:
        return None

    snapshot_rows = build_snapshot_history_g51(
        conn, candidate_id=candidate_id, symbol=symbol, anchor_ts=anchor_ts,
    )
    candle_rows = build_candle_patterns_g51(
        conn, candidate_id=candidate_id, symbol=symbol, anchor_ts=anchor_ts,
    )
    liquidity_rows = build_liquidity_history_g51(
        conn, candidate_id=candidate_id, symbol=symbol, anchor_ts=anchor_ts,
    )
    replay_rows = build_replay_timeline_g51(
        conn,
        candidate_id=candidate_id,
        symbol=symbol,
        anchor_ts=anchor_ts,
        direction=candidate.get("direction"),
        entry_price=candidate.get("price_entry"),
    )

    market_evolution = _market_evolution_text(snapshot_rows)
    funding_evolution = _metric_evolution(liquidity_rows, "funding")
    oi_evolution = _metric_evolution(liquidity_rows, "oi")
    replay_evolution = format_replay_evolution(replay_rows)
    completeness = _row_completeness(
        snapshot_rows=snapshot_rows,
        candle_rows=candle_rows,
        liquidity_rows=liquidity_rows,
        replay_rows=replay_rows,
    )

    row_json = {
        "candidate_id": candidate_id,
        "symbol": symbol,
        "anchor_ts": anchor_ts,
        "direction": candidate.get("direction"),
        "market_score": candidate.get("market_score"),
        "confidence": candidate.get("confidence"),
        "funding": candidate.get("funding_score"),
        "oi": candidate.get("oi_score"),
        "volume": candidate.get("volume_score"),
        "atr": candidate.get("atr_score"),
        "final_pnl": candidate.get("max_profit_pct"),
        "snapshot_history": snapshot_rows,
        "candle_patterns": candle_rows,
        "liquidity_history": liquidity_rows,
        "replay_timeline": replay_rows,
        "market_evolution": market_evolution,
        "funding_evolution": funding_evolution,
        "oi_evolution": oi_evolution,
        "replay_evolution": replay_evolution,
        "dataset_completeness": completeness,
    }

    now = int(time.time())
    conn.execute(
        f"""
        INSERT OR REPLACE INTO {_TABLE} (
          candidate_id, symbol, anchor_ts, direction, market_score, confidence,
          funding, oi, volume, atr, final_pnl,
          snapshot_history_json, candle_patterns_json, liquidity_history_json, replay_timeline_json,
          market_evolution, funding_evolution, oi_evolution, replay_evolution,
          dataset_completeness, row_json, created_at, updated_at
        ) VALUES (
          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            candidate_id, symbol, anchor_ts, candidate.get("direction"),
            candidate.get("market_score"), candidate.get("confidence"),
            candidate.get("funding_score"), candidate.get("oi_score"),
            candidate.get("volume_score"), candidate.get("atr_score"),
            candidate.get("max_profit_pct"),
            json.dumps(snapshot_rows, default=str),
            json.dumps(candle_rows, default=str),
            json.dumps(liquidity_rows, default=str),
            json.dumps(replay_rows, default=str),
            market_evolution, funding_evolution, oi_evolution, replay_evolution,
            completeness, json.dumps(row_json, default=str), now, now,
        ),
    )
    return row_json


def build_research_lake_g51(
    conn: Any,
    *,
    days: int = 30,
    limit: int = 500,
) -> dict[str, Any]:
    since = int(time.time()) - days * 86400
    candidates = conn.execute(
        """
        SELECT c.id, c.symbol, c.direction, c.market_score, c.confidence,
               c.funding_score, c.oi_score, c.volume_score, c.atr_score,
               c.created_at, c.candidate_ts, o.price_entry, o.max_profit_pct
        FROM market_candidate_g31 c
        LEFT JOIN market_candidate_outcomes_g32 o ON o.candidate_id = c.id
        WHERE c.created_at >= ?
        ORDER BY c.created_at DESC
        LIMIT ?
        """,
        (since, limit),
    ).fetchall()

    written = 0
    for cand in candidates:
        if build_candidate_dataset_g51(conn, candidate=dict(cand)):
            written += 1

    audit = audit_research_dataset_g51(conn)
    now = int(time.time())
    insert_returning_id(
        conn,
        f"""
        INSERT INTO {_BUILD_TABLE} (
          build_ts, candidates_processed, rows_written, completeness_json, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (now, len(candidates), written, json.dumps(audit), now),
    )
    from bot.research.market_events.signal_intelligence.health_g3 import set_g3_ops_state
    set_g3_ops_state(conn, "last_g51_lake_ts", str(now))
    set_g3_ops_state(conn, "g51_completeness_json", json.dumps(audit))

    return {
        "candidates_processed": len(candidates),
        "rows_written": written,
        "completeness": audit,
    }


def query_research_dataset_g51(
    conn: Any,
    *,
    symbol: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """SQL-like dataset query: SELECT * FROM research_dataset WHERE symbol=..."""
    if symbol:
        rows = conn.execute(
            """
            SELECT * FROM research_dataset
            WHERE symbol = ?
            ORDER BY anchor_ts DESC LIMIT ?
            """,
            (symbol.upper(), limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM research_dataset ORDER BY anchor_ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        if d.get("row_json"):
            try:
                d["row"] = json.loads(d["row_json"])
            except (json.JSONDecodeError, TypeError):
                d["row"] = {}
        out.append(d)
    return out


def audit_research_dataset_g51(conn: Any) -> dict[str, Any]:
    total = conn.execute(f"SELECT COUNT(*) AS n FROM {_TABLE}").fetchone()
    n = int(total["n"] or 0) if total else 0
    if n == 0:
        return {
            "overall_pct": 0.0,
            "total_rows": 0,
            "funding_pct": 0.0,
            "oi_pct": 0.0,
            "replay_pct": 0.0,
            "candles_pct": 0.0,
            "false_rejects_pct": 0.0,
            "replay_pending": 0,
        }

    funding_n = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_TABLE} WHERE funding_evolution IS NOT NULL AND funding_evolution != ''",
    ).fetchone()
    oi_n = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_TABLE} WHERE oi_evolution IS NOT NULL AND oi_evolution != ''",
    ).fetchone()
    replay_n = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_TABLE} WHERE replay_evolution IS NOT NULL AND replay_evolution != ''",
    ).fetchone()
    candles_n = conn.execute(
        f"SELECT COUNT(*) AS n FROM {_TABLE} WHERE candle_patterns_json IS NOT NULL AND candle_patterns_json != '[]'",
    ).fetchone()

    replay_pending = conn.execute(
        """
        SELECT COUNT(*) AS n FROM market_candidate_outcomes_g32 o
        LEFT JOIN market_research_dataset_g51 d ON d.candidate_id = o.candidate_id
        WHERE o.replay_status = 'OPEN' OR d.replay_evolution IS NULL OR d.replay_evolution = ''
        """,
    ).fetchone()

    false_reject_n = 0
    try:
        fr_total = conn.execute(
            "SELECT COUNT(*) AS n FROM market_validation_analysis_g4 WHERE analysis_type = 'false_reject'",
        ).fetchone()
        false_reject_n = int(fr_total["n"] or 0) if fr_total else 0
    except Exception:
        pass
    false_rejects_pct = min(100.0, round(false_reject_n / max(1, n) * 100.0, 1)) if false_reject_n else 0.0

    def _pct(row: Any) -> float:
        return round(int(row["n"] or 0) / n * 100.0, 1) if row else 0.0

    funding_pct = _pct(funding_n)
    oi_pct = _pct(oi_n)
    replay_pct = _pct(replay_n)
    candles_pct = _pct(candles_n)
    overall = round((funding_pct + oi_pct + replay_pct + candles_pct + false_rejects_pct) / 5.0, 1)

    return {
        "overall_pct": overall,
        "total_rows": n,
        "funding_pct": funding_pct,
        "oi_pct": oi_pct,
        "replay_pct": replay_pct,
        "candles_pct": candles_pct,
        "false_rejects_pct": false_rejects_pct,
        "replay_pending": int(replay_pending["n"] or 0) if replay_pending else 0,
    }


def format_research_data_audit_g51(conn: Any) -> str:
    audit = audit_research_dataset_g51(conn)
    lines = [
        "G5.1 Research Data Lake Audit",
        "",
        "Dataset completeness",
        f"{audit['overall_pct']}%",
        "",
        f"Total rows: {audit['total_rows']}",
        "",
        "Funding",
        f"{audit['funding_pct']}%",
        "",
        "OI",
        f"{audit['oi_pct']}%",
        "",
        "Replay",
        f"{audit['replay_pct']}%",
        "",
        "Candles",
        f"{audit['candles_pct']}%",
        "",
        "False Rejects",
        f"{audit['false_rejects_pct']}%",
        "",
        "Replay pending",
        str(audit.get("replay_pending", 0)),
    ]
    return "\n".join(lines)


def format_dataset_telegram_g51(conn: Any) -> str:
    audit = audit_research_dataset_g51(conn)
    status = lambda pct: "OK" if pct >= 80 else ("Partial" if pct >= 40 else "Missing")

    lines = [
        "Research Dataset",
        "",
        "Completeness",
        f"{audit['overall_pct']}%",
        "",
        "Funding",
        status(audit["funding_pct"]),
        "",
        "OI",
        status(audit["oi_pct"]),
        "",
        "Replay",
        f"Pending {audit.get('replay_pending', 0)}" if audit["replay_pct"] < 100 else "OK",
        "",
        "Candles",
        status(audit["candles_pct"]),
    ]
    return "\n".join(lines)


def _missing_matches_key(text: str, key: str) -> bool:
    lower = text.lower()
    for alias in MISSING_INFO_ALIASES.get(key, ()):
        if alias in lower:
            return True
    return False


def dataset_has_field(row: dict[str, Any], key: str) -> bool:
    if key == "funding":
        return bool(row.get("funding_evolution"))
    if key == "oi":
        return bool(row.get("oi_evolution"))
    if key == "candles":
        patterns = row.get("candle_patterns_json") or row.get("candle_patterns")
        if isinstance(patterns, str):
            return patterns not in ("", "[]", "null")
        return bool(patterns)
    if key == "replay":
        return bool(row.get("replay_evolution"))
    if key == "liquidations":
        liq = row.get("liquidity_history_json") or row.get("liquidity_history")
        if isinstance(liq, str):
            return "liquidations" in liq
        return any(r.get("metric") == "liquidations" for r in (liq or []))
    if key == "false_rejects":
        return True
    return False


def filter_missing_info_g51(
    missing_info: list[Any],
    *,
    dataset_rows: list[dict[str, Any]] | None = None,
    conn: Any | None = None,
) -> list[Any]:
    """Remove missing_info items already present in the data lake."""
    if conn is not None and not dataset_rows:
        dataset_rows = query_research_dataset_g51(conn, limit=50)

    if not dataset_rows:
        return missing_info

    sample = dataset_rows[0]
    filtered: list[Any] = []
    for item in missing_info:
        label = item if isinstance(item, str) else str(item.get("label") or item)
        suppressed = False
        for key in MISSING_INFO_ALIASES:
            if _missing_matches_key(label, key):
                coverage = sum(1 for r in dataset_rows if dataset_has_field(r, key))
                if coverage >= max(1, len(dataset_rows) // 2):
                    suppressed = True
                    break
        if not suppressed:
            filtered.append(item)
    return filtered
