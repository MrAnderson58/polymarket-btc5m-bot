"""Individual integrity fixes (research freeze — observe + reconcile only)."""

from __future__ import annotations

from typing import Any

from bot.research.market_events.signal_intelligence.elite_candidate_v1.store import (
    CANDIDATES_TABLE,
)
from bot.research.market_events.signal_intelligence.research_integrity_v1.canonical import (
    CANONICAL_ELITE_TABLE,
    feature_store_status,
    get_canonical_dataset_meta,
    load_canonical_elite,
    persist_reality_dataset_binding,
    read_persisted_reality_meta,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.s55_join import (
    UNEXPECTED_REASONS,
    diagnose_missing_s55_joins,
    reconcile_timestamp_mismatches,
)
from bot.research.market_events.signal_intelligence.research_lake_v1.loader import (
    research_lake_row_count,
)

_S55 = "market_events_trade_features_s55"


def fix_reality_dataset_parity(conn: Any) -> dict[str, Any]:
    """
    FIX 1: Reality Validation must bind to ONE canonical dataset.
    Same score on Cursor / Mini / CI when DB + hash match.
    """
    current = get_canonical_dataset_meta(conn)
    stored = read_persisted_reality_meta(conn)

    mismatches: list[str] = []
    if not stored.get("hash"):
        mismatches.append("reality_dataset_not_persisted")
    else:
        if stored.get("hash") != current.get("hash"):
            mismatches.append("dataset_hash_mismatch")
        if stored.get("dataset_version") and stored.get("dataset_version") != current.get("dataset_version"):
            mismatches.append("dataset_version_mismatch")
        if stored.get("lake_rows") is not None and int(stored["lake_rows"]) != int(current.get("lake_rows") or 0):
            mismatches.append("lake_rows_mismatch")
        if stored.get("build_ts") is not None and current.get("build_ts") is not None:
            if int(stored["build_ts"]) != int(current["build_ts"]):
                mismatches.append("build_ts_mismatch")

    # Deterministic recompute check (same DB → same score)
    recompute_score = None
    recompute_ok = True
    try:
        from bot.research.market_events.signal_intelligence.reality_validation_v1.engine import (
            run_reality_validation_v1,
        )
        out = run_reality_validation_v1(conn, write_reports=False, persist=False, mc_sims=100)
        recompute_score = out.get("reality_score")
        stored_score = stored.get("reality_score")
        if stored_score is not None and recompute_score is not None:
            if abs(float(stored_score) - float(recompute_score)) > 1e-6:
                mismatches.append("reality_score_recompute_mismatch")
                recompute_ok = False
    except Exception as exc:
        mismatches.append(f"reality_recompute_error:{exc}")
        recompute_ok = False

    ok = len(mismatches) == 0
    fixed = ok
    if (
        not ok
        and "reality_dataset_not_persisted" in mismatches
        and recompute_ok
        and recompute_score is not None
    ):
        persist_reality_dataset_binding(conn, current, reality_score=recompute_score)
        stored = read_persisted_reality_meta(conn)
        mismatches = [m for m in mismatches if m != "reality_dataset_not_persisted"]
        ok = len(mismatches) == 0
        fixed = ok

    return {
        "ok": ok,
        "fixed": fixed,
        "current": current,
        "stored": stored,
        "recompute_score": recompute_score,
        "recompute_ok": recompute_ok,
        "mismatches": mismatches,
    }


def fix_elite_canonical(conn: Any) -> dict[str, Any]:
    """
    FIX 2: Elite Audit / Profile / Morning / Book D use ONE canonical table.
    """
    elite = load_canonical_elite(conn)
    n_elite = len(elite)
    n_table = 0
    try:
        n_table = int(conn.execute(f"SELECT COUNT(*) FROM {CANDIDATES_TABLE}").fetchone()[0])
    except Exception:
        pass

    # Consumers must not apply extra local filters — verify table is source
    issues: list[str] = []
    if n_elite == 0:
        issues.append("empty_elite_corpus")
    if n_table > 0 and abs(n_elite - n_table) > max(5, int(0.05 * n_table)):
        # categories filter may shrink vs full table — only fail if way off
        if n_elite < n_table * 0.5:
            issues.append("elite_category_filter_too_narrow")

    return {
        "ok": len(issues) == 0,
        "fixed": len(issues) == 0,
        "canonical_table": CANONICAL_ELITE_TABLE,
        "n_elite": n_elite,
        "n_table_total": n_table,
        "issues": issues,
    }


def fix_s55_audit(conn: Any) -> dict[str, Any]:
    """
    FIX 3: Investigate NO_S55_RECORD — why Feature Store was never built.
    """
    audit = diagnose_missing_s55_joins(conn)
    fs = feature_store_status()
    n_s55 = 0
    try:
        n_s55 = int(conn.execute(f"SELECT COUNT(*) FROM {_S55}").fetchone()[0])
    except Exception:
        pass
    n_lake = research_lake_row_count(conn)
    no_s55 = int((audit.get("reasons") or {}).get("NO_S55_RECORD") or 0)

    root_causes: list[str] = []
    if fs.get("missing"):
        root_causes.append("feature_store_never_built")
    if n_s55 == 0:
        root_causes.append("s55_table_empty_collector_never_wrote")
    elif no_s55 > 0 and n_s55 > 0:
        root_causes.append("lake_s55_join_gap_not_collector")
    if no_s55 > n_lake * 0.5:
        root_causes.append("majority_lake_missing_s55")

    return {
        "ok": no_s55 == 0 or no_s55 in (audit.get("missing_expected") or 0),
        "fixed": False,  # investigation only unless repair run separately
        "n_lake": n_lake,
        "n_s55": n_s55,
        "NO_S55_RECORD": no_s55,
        "TIMESTAMP_MISMATCH": int((audit.get("reasons") or {}).get("TIMESTAMP_MISMATCH") or 0),
        "unexpected_s55": sum(
            int((audit.get("reasons") or {}).get(r) or 0) for r in UNEXPECTED_REASONS
        ),
        "feature_store": fs,
        "pipeline": {
            "generation_stage": "S42 paper trade close → S55 feature row (collector)",
            "lake_build": "research_lake_build joins S55 via paper_trade_id / s40 keys",
            "feature_store_build": "sync_feature_store extracts closed trades → parquet",
            "join_key_primary": "paper_trade_id",
            "join_key_fallback": "(s40_signal_type, s40_signal_id)",
            "timestamp_key": "closed_at (±300s fuzzy audit, ±60s auto-reconcile)",
        },
        "root_causes": root_causes,
        "audit": audit,
    }


def fix_timestamp_reconcile(conn: Any, *, apply: bool = True) -> dict[str, Any]:
    """FIX 4: Auto-reconcile TIMESTAMP_MISMATCH if delta < 60 sec."""
    before = int(
        (diagnose_missing_s55_joins(conn).get("reasons") or {}).get("TIMESTAMP_MISMATCH") or 0
    )
    if apply:
        recon = reconcile_timestamp_mismatches(conn, max_delta_sec=60)
        after = int(
            (diagnose_missing_s55_joins(conn).get("reasons") or {}).get("TIMESTAMP_MISMATCH") or 0
        )
    else:
        recon = {
            "reconciled": 0,
            "remaining_timestamp_mismatch": before,
            "max_delta_sec": 60,
        }
        after = before
    return {
        "ok": True,
        "fixed": apply and int(recon.get("reconciled") or 0) > 0,
        "before": before,
        "after": after,
        "reconciled": int(recon.get("reconciled") or 0) if apply else 0,
        "remaining": int(recon.get("remaining_timestamp_mismatch") or 0) if apply else before,
        "samples_remaining": recon.get("samples_remaining") if apply else [],
        "report_only": not apply or after > 0,
    }


def fix_book_d_feature_store(conn: Any) -> dict[str, Any]:
    """FIX 5: Book D must refuse if Feature Store missing."""
    fs = feature_store_status()
    return {
        "ok": bool(fs.get("ok")),
        "fixed": bool(fs.get("ok")),
        "feature_store": fs,
        "book_d_will_refuse": bool(fs.get("missing")),
        "reason": None if fs.get("ok") else "feature_store_missing",
    }


__all__ = [
    "fix_book_d_feature_store",
    "fix_elite_canonical",
    "fix_reality_dataset_parity",
    "fix_s55_audit",
    "fix_timestamp_reconcile",
]
