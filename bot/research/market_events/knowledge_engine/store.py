"""Persist Feature Validation results into Knowledge Engine tables."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.knowledge_engine.schema import ensure_knowledge_engine_schema

STATUS_CANDIDATE = "candidate"
STATUS_VALIDATED = "validated"
STATUS_REJECTED = "rejected"


def _now() -> int:
    return int(time.time())


def _s(v: Any) -> str | None:
    if v is None:
        return None
    return str(v)


def _record_history(
    conn: Any,
    *,
    entity_type: str,
    entity_key: str,
    field_name: str,
    old_value: Any,
    new_value: Any,
    note: str | None = None,
    now: int | None = None,
) -> None:
    if old_value is None and new_value is None:
        return
    if _s(old_value) == _s(new_value):
        return
    conn.execute(
        """
        INSERT INTO knowledge_history (
          entity_type, entity_key, field_name, old_value, new_value, note, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_type,
            entity_key,
            field_name,
            _s(old_value),
            _s(new_value),
            note,
            int(now if now is not None else _now()),
        ),
    )


def _verdict_to_status(verdict: str) -> str:
    v = str(verdict or "").upper()
    if v == "KEEP":
        return STATUS_VALIDATED
    if v == "REMOVE":
        return STATUS_REJECTED
    return STATUS_CANDIDATE


def _stability_score(stable: bool | None, reason: str | None) -> float:
    if stable is True:
        return 1.0
    if stable is False:
        return 0.0
    return 0.5


def upsert_from_feature_validation(conn: Any, data: dict[str, Any]) -> dict[str, int]:
    """Write Feature Validation V1 payload into knowledge_* tables."""
    ensure_knowledge_engine_schema(conn)
    now = _now()
    n_feat = n_rules = n_ix = n_hist_before = 0
    try:
        n_hist_before = int(
            conn.execute("SELECT COUNT(*) AS n FROM knowledge_history").fetchone()["n"]
        )
    except Exception:
        n_hist_before = 0

    ranking = {r["key"]: r for r in data.get("ranking") or []}
    singles = {s["key"]: s for s in data.get("singles") or []}
    stability = {s["key"]: s for s in data.get("stability") or []}

    for key, rank in ranking.items():
        single = singles.get(key) or {}
        stab = stability.get(key) or {}
        name = str(rank.get("feature") or key)
        ev_d = rank.get("delta_ev")
        pf_d = rank.get("delta_pf")
        wr_d = rank.get("delta_wr")
        sample = int(rank.get("n") or 0)
        conf = float(rank.get("trust") or 0.0)
        verdict = str(rank.get("verdict") or "WATCH")
        rule = str(rank.get("rule") or single.get("rule") or "")
        stab_score = _stability_score(rank.get("stable"), stab.get("reason"))

        existing = conn.execute(
            "SELECT * FROM knowledge_features WHERE feature_name = ?",
            (name,),
        ).fetchone()
        if existing:
            for field, new_v in (
                ("ev_delta", ev_d),
                ("pf_delta", pf_d),
                ("wr_delta", wr_d),
                ("stability_score", stab_score),
                ("sample_size", sample),
                ("confidence", conf),
                ("verdict", verdict),
            ):
                old_v = existing[field] if field in existing.keys() else None
                _record_history(
                    conn,
                    entity_type="feature",
                    entity_key=name,
                    field_name=field,
                    old_value=old_v,
                    new_value=new_v,
                    note="feature-validation",
                    now=now,
                )
            conn.execute(
                """
                UPDATE knowledge_features SET
                  feature_key=?, ev_delta=?, pf_delta=?, wr_delta=?,
                  stability_score=?, sample_size=?, confidence=?, verdict=?,
                  rule_text=?, last_updated=?
                WHERE feature_name=?
                """,
                (
                    key, ev_d, pf_d, wr_d, stab_score, sample, conf, verdict,
                    rule, now, name,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO knowledge_features (
                  feature_name, feature_key, ev_delta, pf_delta, wr_delta,
                  stability_score, sample_size, confidence, verdict, rule_text, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name, key, ev_d, pf_d, wr_d, stab_score, sample, conf,
                    verdict, rule, now,
                ),
            )
            _record_history(
                conn,
                entity_type="feature",
                entity_key=name,
                field_name="created",
                old_value=None,
                new_value=verdict,
                note="feature-validation first seen",
                now=now,
            )
        n_feat += 1

        status = _verdict_to_status(verdict)
        rule_key = f"{key}::{rule}" if rule else key
        prev_rule = conn.execute(
            "SELECT status, confidence, ev_delta FROM knowledge_rules WHERE rule_key = ?",
            (rule_key,),
        ).fetchone()
        if prev_rule:
            _record_history(
                conn,
                entity_type="rule",
                entity_key=rule_key,
                field_name="status",
                old_value=prev_rule["status"],
                new_value=status,
                note="feature-validation",
                now=now,
            )
            conn.execute(
                """
                UPDATE knowledge_rules SET
                  feature_name=?, rule_text=?, ev_delta=?, pf_delta=?, wr_delta=?,
                  sample_size=?, confidence=?, status=?, last_updated=?
                WHERE rule_key=?
                """,
                (
                    name, rule or name, ev_d, pf_d, wr_d, sample, conf * 100.0,
                    status, now, rule_key,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO knowledge_rules (
                  rule_key, feature_name, rule_text, ev_delta, pf_delta, wr_delta,
                  sample_size, confidence, status, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule_key, name, rule or name, ev_d, pf_d, wr_d,
                    sample, conf * 100.0, status, now,
                ),
            )
            _record_history(
                conn,
                entity_type="rule",
                entity_key=rule_key,
                field_name="created",
                old_value=None,
                new_value=status,
                note="feature-validation",
                now=now,
            )
        n_rules += 1

    for ix in data.get("interactions") or []:
        a = str(ix.get("a") or "")
        b = str(ix.get("b") or "")
        pair_key = f"{ix.get('key_a')}|{ix.get('key_b')}"
        synergy = ix.get("synergy")
        existing = conn.execute(
            "SELECT synergy FROM knowledge_interactions WHERE pair_key = ?",
            (pair_key,),
        ).fetchone()
        if existing:
            _record_history(
                conn,
                entity_type="interaction",
                entity_key=pair_key,
                field_name="synergy",
                old_value=existing["synergy"],
                new_value=synergy,
                note="feature-validation",
                now=now,
            )
            conn.execute(
                """
                UPDATE knowledge_interactions SET
                  feature_a=?, feature_b=?, synergy=?, ev_joint=?, ev_a=?, ev_b=?,
                  sample_size=?, stronger_together=?, last_updated=?
                WHERE pair_key=?
                """,
                (
                    a, b, synergy, ix.get("ev"), ix.get("ev_a"), ix.get("ev_b"),
                    int(ix.get("n") or 0), 1 if ix.get("stronger_together") else 0,
                    now, pair_key,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO knowledge_interactions (
                  pair_key, feature_a, feature_b, synergy, ev_joint, ev_a, ev_b,
                  sample_size, stronger_together, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pair_key, a, b, synergy, ix.get("ev"), ix.get("ev_a"), ix.get("ev_b"),
                    int(ix.get("n") or 0), 1 if ix.get("stronger_together") else 0, now,
                ),
            )
        n_ix += 1

    try:
        conn.commit()
    except Exception:
        pass
    n_hist_after = n_hist_before
    try:
        n_hist_after = int(
            conn.execute("SELECT COUNT(*) AS n FROM knowledge_history").fetchone()["n"]
        )
    except Exception:
        pass
    return {
        "features": n_feat,
        "rules": n_rules,
        "interactions": n_ix,
        "history_events": max(0, n_hist_after - n_hist_before),
    }
