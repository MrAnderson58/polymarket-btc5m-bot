"""Push successful experiment results into Knowledge Engine tables."""

from __future__ import annotations

import time
from typing import Any

from bot.research.market_events.experiment_engine.schema import STATUS_VALIDATED
from bot.research.market_events.knowledge_engine.schema import ensure_knowledge_engine_schema
from bot.research.market_events.knowledge_engine.store import (
    STATUS_CANDIDATE,
    STATUS_VALIDATED as RULE_VALIDATED,
    STATUS_REJECTED as RULE_REJECTED,
    _record_history,
)


def _now() -> int:
    return int(time.time())


def update_knowledge_from_experiment(
    conn: Any,
    *,
    experiment: dict[str, Any],
    hypothesis: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, int]:
    """
    After a successful experiment, update knowledge_* as a trust source.
    Does not touch Gate / Trading Core.
    """
    ensure_knowledge_engine_schema(conn)
    now = _now()
    counts = {"features": 0, "rules": 0, "interactions": 0, "history": 0}
    status = str(experiment.get("status") or "")
    if status not in (STATUS_VALIDATED, "REJECTED"):
        return counts

    note = f"experiment#{experiment.get('id')}:{experiment.get('experiment_type')}"
    delta_ev = experiment.get("delta_ev")
    delta_pf = experiment.get("delta_pf")
    delta_wr = experiment.get("delta_wr")
    n = int(experiment.get("dataset_size") or result.get("after_n") or 0)
    conf = 80.0 if status == STATUS_VALIDATED else 20.0
    verdict = "KEEP" if status == STATUS_VALIDATED else "REMOVE"
    rule_status = RULE_VALIDATED if status == STATUS_VALIDATED else RULE_REJECTED

    exp_type = str(experiment.get("experiment_type") or "")
    key = str(hypothesis.get("hypothesis_key") or "")
    title = str(hypothesis.get("title") or "")

    # Feature / rule updates for single filter & conditional
    feature_name = result.get("feature_name")
    feature_key = result.get("feature_key")
    rule_text = result.get("rule") or title

    if feature_name and exp_type in ("Single Filter",):
        _upsert_feature(
            conn,
            name=str(feature_name),
            feature_key=str(feature_key or feature_name),
            ev_delta=delta_ev,
            pf_delta=delta_pf,
            wr_delta=delta_wr,
            sample_size=n,
            confidence=conf / 100.0,
            verdict=verdict,
            rule_text=str(rule_text),
            note=note,
            now=now,
            counts=counts,
        )
        _upsert_rule(
            conn,
            rule_key=f"experiment::{feature_key or feature_name}::{rule_text}",
            feature_name=str(feature_name),
            rule_text=str(rule_text),
            ev_delta=delta_ev,
            pf_delta=delta_pf,
            wr_delta=delta_wr,
            sample_size=n,
            confidence=conf,
            status=rule_status,
            note=note,
            now=now,
            counts=counts,
        )

    # Interactions / dependency
    if exp_type in ("Interaction", "Dependency") and "×" in str(result.get("notes") or ""):
        # parse names from notes or hypothesis key
        names = []
        if key.startswith("strong_ix:"):
            names = key.split(":", 1)[1].split("|")
        elif key.startswith("dependency:"):
            body = key.split(":", 1)[1]
            names = body.split("->") if "->" in body else body.split("|")
        if len(names) >= 2:
            a, b = names[0].strip(), names[1].strip()
            pair_key = f"exp|{a}|{b}"
            existing = conn.execute(
                "SELECT synergy FROM knowledge_interactions WHERE pair_key = ?",
                (pair_key,),
            ).fetchone()
            synergy = float(delta_ev or 0.0)
            if existing:
                _record_history(
                    conn,
                    entity_type="interaction",
                    entity_key=pair_key,
                    field_name="synergy",
                    old_value=existing["synergy"],
                    new_value=synergy,
                    note=note,
                    now=now,
                )
                counts["history"] += 1
                conn.execute(
                    """
                    UPDATE knowledge_interactions SET
                      feature_a=?, feature_b=?, synergy=?, ev_joint=?,
                      sample_size=?, stronger_together=?, last_updated=?
                    WHERE pair_key=?
                    """,
                    (
                        a, b, synergy, experiment.get("ev_after"),
                        n, 1 if status == STATUS_VALIDATED else 0, now, pair_key,
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
                        pair_key, a, b, synergy, experiment.get("ev_after"),
                        None, None, n, 1 if status == STATUS_VALIDATED else 0, now,
                    ),
                )
            counts["interactions"] += 1
            _record_history(
                conn,
                entity_type="interaction",
                entity_key=pair_key,
                field_name="experiment",
                old_value=None,
                new_value=status,
                note=note,
                now=now,
            )
            counts["history"] += 1

    # Replacement: annotate both features
    if exp_type == "Replacement" and key.startswith("replace:"):
        parts = key.split(":", 1)[1].split("|")
        for name in parts[:2]:
            name = name.strip()
            if not name:
                continue
            _upsert_feature(
                conn,
                name=name,
                feature_key=name.lower().replace(" ", "_"),
                ev_delta=delta_ev,
                pf_delta=delta_pf,
                wr_delta=delta_wr,
                sample_size=n,
                confidence=conf / 100.0,
                verdict="WATCH" if status == STATUS_VALIDATED else verdict,
                rule_text=f"replacement candidate vs peer ({title})",
                note=note,
                now=now,
                counts=counts,
            )

    # Pattern: store as rule under Pattern cluster
    if exp_type == "Pattern":
        cluster = key.replace("pattern:", "") if key.startswith("pattern:") else title
        _upsert_rule(
            conn,
            rule_key=f"experiment::pattern::{cluster}",
            feature_name=f"Pattern:{cluster}",
            rule_text=str(result.get("notes") or title),
            ev_delta=delta_ev,
            pf_delta=delta_pf,
            wr_delta=delta_wr,
            sample_size=int(result.get("after_n") or n),
            confidence=conf,
            status=rule_status if status == STATUS_VALIDATED else STATUS_CANDIDATE,
            note=note,
            now=now,
            counts=counts,
        )

    # Always log experiment outcome on hypothesis entity in knowledge_history
    _record_history(
        conn,
        entity_type="experiment",
        entity_key=str(experiment.get("id")),
        field_name="status",
        old_value=None,
        new_value=status,
        note=note,
        now=now,
    )
    counts["history"] += 1
    return counts


def _upsert_feature(
    conn: Any,
    *,
    name: str,
    feature_key: str,
    ev_delta: Any,
    pf_delta: Any,
    wr_delta: Any,
    sample_size: int,
    confidence: float,
    verdict: str,
    rule_text: str,
    note: str,
    now: int,
    counts: dict[str, int],
) -> None:
    existing = conn.execute(
        "SELECT * FROM knowledge_features WHERE feature_name = ?",
        (name,),
    ).fetchone()
    if existing:
        for field, new_v in (
            ("ev_delta", ev_delta),
            ("verdict", verdict),
            ("confidence", confidence),
            ("sample_size", sample_size),
        ):
            old_v = existing[field] if field in existing.keys() else None
            _record_history(
                conn,
                entity_type="feature",
                entity_key=name,
                field_name=field,
                old_value=old_v,
                new_value=new_v,
                note=note,
                now=now,
            )
            counts["history"] += 1
        conn.execute(
            """
            UPDATE knowledge_features SET
              feature_key=?, ev_delta=?, pf_delta=?, wr_delta=?,
              sample_size=?, confidence=?, verdict=?, rule_text=?, last_updated=?
            WHERE feature_name=?
            """,
            (
                feature_key, ev_delta, pf_delta, wr_delta,
                sample_size, confidence, verdict, rule_text, now, name,
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
                name, feature_key, ev_delta, pf_delta, wr_delta,
                1.0 if verdict == "KEEP" else 0.5, sample_size, confidence,
                verdict, rule_text, now,
            ),
        )
        _record_history(
            conn,
            entity_type="feature",
            entity_key=name,
            field_name="created",
            old_value=None,
            new_value=verdict,
            note=note,
            now=now,
        )
        counts["history"] += 1
    counts["features"] += 1


def _upsert_rule(
    conn: Any,
    *,
    rule_key: str,
    feature_name: str,
    rule_text: str,
    ev_delta: Any,
    pf_delta: Any,
    wr_delta: Any,
    sample_size: int,
    confidence: float,
    status: str,
    note: str,
    now: int,
    counts: dict[str, int],
) -> None:
    prev = conn.execute(
        "SELECT status, confidence, ev_delta FROM knowledge_rules WHERE rule_key = ?",
        (rule_key,),
    ).fetchone()
    if prev:
        _record_history(
            conn,
            entity_type="rule",
            entity_key=rule_key,
            field_name="status",
            old_value=prev["status"],
            new_value=status,
            note=note,
            now=now,
        )
        counts["history"] += 1
        conn.execute(
            """
            UPDATE knowledge_rules SET
              feature_name=?, rule_text=?, ev_delta=?, pf_delta=?, wr_delta=?,
              sample_size=?, confidence=?, status=?, last_updated=?
            WHERE rule_key=?
            """,
            (
                feature_name, rule_text, ev_delta, pf_delta, wr_delta,
                sample_size, confidence, status, now, rule_key,
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
                rule_key, feature_name, rule_text, ev_delta, pf_delta, wr_delta,
                sample_size, confidence, status, now,
            ),
        )
        _record_history(
            conn,
            entity_type="rule",
            entity_key=rule_key,
            field_name="created",
            old_value=None,
            new_value=status,
            note=note,
            now=now,
        )
        counts["history"] += 1
    counts["rules"] += 1
