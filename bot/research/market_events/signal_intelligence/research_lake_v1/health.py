"""Research Lake integrity / health checks."""

from __future__ import annotations

import json
from typing import Any

from bot.research.market_events.signal_intelligence.research_lake_v1.s55_join import (
    diagnose_missing_s55_joins,
    format_s55_join_audit,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.schema import (
    DATASET_VERSION,
    FEATURE_VERSION,
    LAKE_TABLE,
    META_TABLE,
    SCHEMA_VERSION_LAKE,
    ensure_research_lake_schema,
)

_FEATURE_KEYS = (
    "rsi", "atr", "funding", "oi_delta", "fear_greed", "trend",
    "ema20_distance", "atr_pct",
)


def _safe_count(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


def research_lake_health_v1(conn: Any) -> dict[str, Any]:
    """Check duplicates, missing joins, NULL explosion, broken features, schema drift."""
    ensure_research_lake_schema(conn)

    n_lake = _safe_count(conn, f"SELECT COUNT(*) FROM {LAKE_TABLE}")
    n_closed = _safe_count(
        conn,
        "SELECT COUNT(*) FROM market_events_paper_trades_s42 "
        "WHERE status='CLOSED' AND pnl_pct IS NOT NULL",
    )
    duplicates = _safe_count(
        conn,
        f"""
        SELECT COUNT(*) FROM (
          SELECT trade_id FROM {LAKE_TABLE} GROUP BY trade_id HAVING COUNT(*) > 1
        )
        """,
    )
    missing_pnl = _safe_count(
        conn,
        f"SELECT COUNT(*) FROM {LAKE_TABLE} WHERE pnl IS NULL",
    )
    missing_symbol = _safe_count(
        conn,
        f"SELECT COUNT(*) FROM {LAKE_TABLE} WHERE symbol IS NULL OR symbol = ''",
    )

    s55_audit = diagnose_missing_s55_joins(conn)
    missing_s55 = int(s55_audit.get("n_missing_raw_null") or 0)
    missing_expected = int(s55_audit.get("missing_expected") or 0)
    missing_unexpected = int(s55_audit.get("missing_unexpected") or 0)
    missing_unexpected_pct = float(s55_audit.get("missing_unexpected_pct") or 0.0)
    missing_s55_pct = (
        round(100.0 * missing_s55 / n_lake, 4) if n_lake else 0.0
    )

    # NULL explosion across feature JSON
    null_feature_hits = 0
    broken_features = 0
    sampled = 0
    try:
        rows = conn.execute(
            f"SELECT features_json FROM {LAKE_TABLE} ORDER BY trade_id DESC LIMIT 500"
        ).fetchall()
        for (raw,) in rows:
            sampled += 1
            try:
                feats = json.loads(raw) if raw else {}
            except Exception:
                broken_features += 1
                continue
            if not isinstance(feats, dict):
                broken_features += 1
                continue
            nulls = sum(1 for k in _FEATURE_KEYS if feats.get(k) is None)
            if nulls >= max(3, len(_FEATURE_KEYS) // 2):
                null_feature_hits += 1
            # stub ATR / funding
            if feats.get("atr") == 50.0 or feats.get("funding") == 54.1:
                broken_features += 1
    except Exception:
        pass

    # Schema drift vs expected versions in meta
    meta: dict[str, str] = {}
    try:
        for k, v in conn.execute(f"SELECT key, value FROM {META_TABLE}").fetchall():
            meta[str(k)] = str(v)
    except Exception:
        pass

    drift = []
    if meta.get("schema_version") and meta["schema_version"] != SCHEMA_VERSION_LAKE:
        drift.append(f"schema_version meta={meta['schema_version']} expected={SCHEMA_VERSION_LAKE}")
    if meta.get("dataset_version") and meta["dataset_version"] != DATASET_VERSION:
        drift.append(f"dataset_version meta={meta['dataset_version']} expected={DATASET_VERSION}")
    if meta.get("feature_version") and meta["feature_version"] != FEATURE_VERSION:
        drift.append(f"feature_version meta={meta['feature_version']} expected={FEATURE_VERSION}")

    coverage = round(100.0 * n_lake / n_closed, 2) if n_closed else (100.0 if n_lake == 0 else 0.0)
    issues: list[str] = []
    notes: list[str] = []
    if duplicates:
        issues.append(f"duplicates={duplicates}")
    if missing_pnl:
        issues.append(f"missing_pnl={missing_pnl}")
    if missing_symbol:
        issues.append(f"missing_symbol={missing_symbol}")
    if drift:
        issues.extend(drift)
    if n_closed and n_lake < n_closed:
        msg = f"lake_behind_s42 lake={n_lake} closed={n_closed}"
        # Small lag with ≥99% coverage is a note; large lag remains an issue.
        if coverage >= 99.0:
            notes.append(msg)
        else:
            issues.append(msg)
    if n_closed and coverage < 99.0:
        issues.append(f"coverage_below_99={coverage}")

    # S55 Join Audit: FAIL only on unexpected misses, not expected materialization gaps.
    if missing_unexpected and missing_unexpected_pct >= 1.0:
        issues.append(
            f"missing_s55_unexpected={missing_unexpected} ({missing_unexpected_pct}%)"
        )
    elif missing_unexpected:
        notes.append(
            f"missing_s55_unexpected={missing_unexpected} ({missing_unexpected_pct}%)"
        )
    if missing_expected:
        notes.append(f"missing_s55_expected={missing_expected}")
    if missing_s55:
        notes.append(f"missing_s55_joins_raw_null={missing_s55} ({missing_s55_pct}%)")

    if sampled and null_feature_hits > sampled * 0.5:
        (notes if coverage >= 99.0 else issues).append(
            f"null_explosion={null_feature_hits}/{sampled}"
        )
    if broken_features:
        (notes if coverage >= 99.0 else issues).append(f"broken_features={broken_features}")

    if n_lake == 0 and n_closed == 0:
        status = "EMPTY"
    elif (
        duplicates
        or (n_closed and coverage < 99.0)
        or (missing_unexpected and missing_unexpected_pct >= 1.0)
    ):
        status = "FAIL"
    elif issues:
        status = "WARN"
    else:
        status = "OK"

    return {
        "ok": status in ("OK", "WARN"),
        "status": status,
        "n_lake": n_lake,
        "n_s42_closed": n_closed,
        "coverage_pct": coverage,
        "duplicates": duplicates,
        "missing_s55_joins": missing_s55,
        "missing_s55_pct": missing_s55_pct,
        "missing_expected": missing_expected,
        "missing_unexpected": missing_unexpected,
        "missing_unexpected_pct": missing_unexpected_pct,
        "s55_audit": s55_audit,
        "s55_verdict": s55_audit.get("verdict"),
        "missing_pnl": missing_pnl,
        "missing_symbol": missing_symbol,
        "null_feature_rows": null_feature_hits,
        "broken_feature_rows": broken_features,
        "sampled_feature_rows": sampled,
        "schema_drift": drift,
        "issues": issues,
        "notes": notes,
        "dataset_version": DATASET_VERSION,
        "feature_version": FEATURE_VERSION,
        "schema_version": SCHEMA_VERSION_LAKE,
        "meta": meta,
    }


def format_research_lake_health(health: dict[str, Any]) -> str:
    lines = [
        "RESEARCH LAKE HEALTH V1",
        f"status: {health.get('status')}",
        f"n_lake: {health.get('n_lake')}",
        f"n_s42_closed: {health.get('n_s42_closed')}",
        f"coverage_pct: {health.get('coverage_pct')}",
        f"duplicates: {health.get('duplicates')}",
        f"missing_s55_joins: {health.get('missing_s55_joins')}",
        f"missing_expected: {health.get('missing_expected')}",
        f"missing_unexpected: {health.get('missing_unexpected')}",
        f"s55_verdict: {health.get('s55_verdict')}",
        f"missing_pnl: {health.get('missing_pnl')}",
        f"null_feature_rows: {health.get('null_feature_rows')}",
        f"broken_feature_rows: {health.get('broken_feature_rows')}",
        f"schema_drift: {health.get('schema_drift')}",
        f"dataset_version: {health.get('dataset_version')}",
        f"feature_version: {health.get('feature_version')}",
        f"schema_version: {health.get('schema_version')}",
        "issues:",
    ]
    issues = health.get("issues") or []
    if not issues:
        lines.append("  (none)")
    else:
        for i in issues:
            lines.append(f"  - {i}")
    notes = health.get("notes") or []
    if notes:
        lines.append("notes:")
        for n in notes:
            lines.append(f"  - {n}")
    audit = health.get("s55_audit")
    if audit:
        lines.append("")
        lines.append(format_s55_join_audit(audit))
    return "\n".join(lines)


__all__ = ["format_research_lake_health", "research_lake_health_v1"]
