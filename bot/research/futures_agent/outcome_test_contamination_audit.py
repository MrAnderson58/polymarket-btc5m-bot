"""Audit and scoped cleanup for unit-test artifacts in research tables."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Known deterministic IDs/hashes used by futures_agent unit tests.
TEST_SOURCE_MESSAGE_IDS = frozenset({
    "d1",
    "fk1",
    "wf1",
    "pg_seed_smoke",
    "m1",
    "c1",
    "g1",
    "ng1",
    "r1",
})

TEST_CONTENT_HASH_PREFIXES = (
    "hd1",
    "hfk1",
    "hwf1",
    "h_pg_seed",
    "hm1",
    "hc1",
    "hg1",
    "hng1",
    "hr1",
)

CANONICAL_D1_FIXTURE_TEXT = "BTC LONG\nEntry: 100\nSL: 95\nTP: 110"

# Mac Mini production fixture cleanup (2026-07-09 validation).
KNOWN_FIXTURE_SOURCE_IDS = frozenset({"d1", "fk1", "wf1"})
KNOWN_FIXTURE_DELETE_COUNTS = {
    "posts": 3,
    "theses": 3,
    "levels": 12,
    "outcomes": 3,
    "events": 6,
    "markouts": 18,
}


@dataclass
class TestArtifactRow:
    table: str
    row_id: int
    reason: str
    detail: str


@dataclass
class TestContaminationReport:
    channel: str | None = None
    posts: list[TestArtifactRow] = field(default_factory=list)
    theses: list[TestArtifactRow] = field(default_factory=list)
    levels: list[TestArtifactRow] = field(default_factory=list)
    outcomes: list[TestArtifactRow] = field(default_factory=list)
    events: list[TestArtifactRow] = field(default_factory=list)
    markouts: list[TestArtifactRow] = field(default_factory=list)
    cache_rows: list[TestArtifactRow] = field(default_factory=list)
    production_outcome_count: int = 0
    suspicious_outcome_count: int = 0

    @property
    def total_suspicious(self) -> int:
        return (
            len(self.posts) + len(self.theses) + len(self.levels)
            + len(self.outcomes) + len(self.events) + len(self.markouts)
            + len(self.cache_rows)
        )


@dataclass
class CleanupPlan:
    channel: str | None
    post_ids: list[int] = field(default_factory=list)
    thesis_ids: list[int] = field(default_factory=list)
    level_ids: list[int] = field(default_factory=list)
    outcome_ids: list[int] = field(default_factory=list)
    event_ids: list[int] = field(default_factory=list)
    markout_ids: list[int] = field(default_factory=list)
    source_message_ids: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "posts": len(self.post_ids),
            "theses": len(self.thesis_ids),
            "levels": len(self.level_ids),
            "outcomes": len(self.outcome_ids),
            "events": len(self.event_ids),
            "markouts": len(self.markout_ids),
        }


@dataclass
class CleanupResult:
    dry_run: bool
    applied: bool
    plan: CleanupPlan
    deleted: dict[str, int] = field(default_factory=dict)
    post_cleanup_report: TestContaminationReport | None = None
    known_fixture_assertion_ok: bool | None = None
    errors: list[str] = field(default_factory=list)


def _is_test_source_message_id(value: str | None) -> bool:
    if not value:
        return False
    if value in TEST_SOURCE_MESSAGE_IDS:
        return True
    if value.startswith("m") and len(value) <= 4 and value[1:].isdigit():
        return True
    return False


def _is_test_content_hash(value: str | None) -> bool:
    if not value:
        return False
    return any(value == p or value.startswith(p) for p in TEST_CONTENT_HASH_PREFIXES)


def _is_test_fixture_post(row: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if _is_test_source_message_id(row["source_message_id"]):
        reasons.append("test_source_message_id")
    if _is_test_content_hash(row["content_hash"]):
        reasons.append("test_content_hash")
    raw = (row["raw_text"] or "")[:120]
    if raw.startswith(CANONICAL_D1_FIXTURE_TEXT) and row["content_type"] == "EXPLICIT_SIGNAL":
        reasons.append("canonical_d1_fixture_text")
    return bool(reasons), reasons


def detect_test_fixture_post_ids(
    conn: Any,
    *,
    channel: str | None = "signalyp",
) -> tuple[set[int], list[dict[str, Any]]]:
    """Return post IDs positively identified as unit-test fixtures."""
    ch_clause = ""
    params: list[Any] = []
    if channel:
        ch_clause = " AND p.channel_name = ?"
        params.append(channel)

    posts = conn.execute(
        f"""
        SELECT p.id, p.source_message_id, p.content_hash, p.raw_text, p.content_type
        FROM futures_agent_trader_posts p
        WHERE 1=1{ch_clause}
        ORDER BY p.id
        """,
        params,
    ).fetchall()

    post_ids: set[int] = set()
    matched: list[dict[str, Any]] = []
    for row in posts:
        is_fixture, reasons = _is_test_fixture_post(row)
        if is_fixture:
            post_ids.add(int(row["id"]))
            matched.append({
                "id": int(row["id"]),
                "source_message_id": row["source_message_id"],
                "content_hash": row["content_hash"],
                "reasons": reasons,
            })
    return post_ids, matched


def build_cleanup_plan(
    conn: Any,
    *,
    channel: str | None = "signalyp",
) -> CleanupPlan:
    post_ids, matched = detect_test_fixture_post_ids(conn, channel=channel)
    plan = CleanupPlan(
        channel=channel,
        post_ids=sorted(post_ids),
        source_message_ids=sorted({m["source_message_id"] for m in matched}),
    )
    if not post_ids:
        return plan

    ph = ",".join("?" for _ in post_ids)
    pid_list = list(post_ids)

    plan.thesis_ids = [
        int(r["id"]) for r in conn.execute(
            f"SELECT id FROM futures_agent_trader_theses WHERE post_id IN ({ph})",
            pid_list,
        ).fetchall()
    ]
    if plan.thesis_ids:
        th_ph = ",".join("?" for _ in plan.thesis_ids)
        plan.level_ids = [
            int(r["id"]) for r in conn.execute(
                f"SELECT id FROM futures_agent_trader_levels WHERE thesis_id IN ({th_ph})",
                plan.thesis_ids,
            ).fetchall()
        ]

    plan.outcome_ids = [
        int(r["id"]) for r in conn.execute(
            f"SELECT id FROM futures_agent_research_signal_outcomes WHERE post_id IN ({ph})",
            pid_list,
        ).fetchall()
    ]
    if plan.outcome_ids:
        oc_ph = ",".join("?" for _ in plan.outcome_ids)
        plan.event_ids = [
            int(r["id"]) for r in conn.execute(
                f"SELECT id FROM futures_agent_research_signal_events WHERE outcome_id IN ({oc_ph})",
                plan.outcome_ids,
            ).fetchall()
        ]
        plan.markout_ids = [
            int(r["id"]) for r in conn.execute(
                f"SELECT id FROM futures_agent_research_signal_markouts WHERE outcome_id IN ({oc_ph})",
                plan.outcome_ids,
            ).fetchall()
        ]
    return plan


def run_test_contamination_audit(
    conn: Any,
    *,
    channel: str | None = "signalyp",
) -> TestContaminationReport:
    report = TestContaminationReport(channel=channel)
    ch_clause = ""
    params: list[Any] = []
    if channel:
        ch_clause = " AND p.channel_name = ?"
        params.append(channel)

    report.production_outcome_count = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM futures_agent_research_signal_outcomes o
        JOIN futures_agent_trader_posts p ON p.id = o.post_id
        WHERE 1=1{ch_clause}
        """,
        params,
    ).fetchone()["n"]

    post_ids, matched = detect_test_fixture_post_ids(conn, channel=channel)
    for m in matched:
        report.posts.append(TestArtifactRow(
            table="futures_agent_trader_posts",
            row_id=m["id"],
            reason=",".join(m["reasons"]),
            detail=f"source_message_id={m['source_message_id']} hash={m['content_hash']}",
        ))

    if post_ids:
        plan = build_cleanup_plan(conn, channel=channel)
        for tid in plan.thesis_ids:
            row = conn.execute(
                "SELECT post_id, thesis_text FROM futures_agent_trader_theses WHERE id = ?",
                (tid,),
            ).fetchone()
            report.theses.append(TestArtifactRow(
                table="futures_agent_trader_theses",
                row_id=tid,
                reason="linked_test_post",
                detail=f"post_id={row['post_id']} thesis_text={row['thesis_text']!r}",
            ))
        for lid in plan.level_ids:
            row = conn.execute(
                "SELECT thesis_id, level_type, price FROM futures_agent_trader_levels WHERE id = ?",
                (lid,),
            ).fetchone()
            report.levels.append(TestArtifactRow(
                table="futures_agent_trader_levels",
                row_id=lid,
                reason="linked_test_thesis",
                detail=f"thesis_id={row['thesis_id']} {row['level_type']}={row['price']}",
            ))
        for oid in plan.outcome_ids:
            row = conn.execute(
                "SELECT thesis_id, engine_version FROM futures_agent_research_signal_outcomes WHERE id = ?",
                (oid,),
            ).fetchone()
            report.outcomes.append(TestArtifactRow(
                table="futures_agent_research_signal_outcomes",
                row_id=oid,
                reason="linked_test_post",
                detail=f"thesis_id={row['thesis_id']} engine={row['engine_version']}",
            ))
            report.suspicious_outcome_count += 1
        for eid in plan.event_ids:
            row = conn.execute(
                "SELECT outcome_id, event_type FROM futures_agent_research_signal_events WHERE id = ?",
                (eid,),
            ).fetchone()
            report.events.append(TestArtifactRow(
                table="futures_agent_research_signal_events",
                row_id=eid,
                reason="linked_test_outcome",
                detail=f"outcome_id={row['outcome_id']} event={row['event_type']}",
            ))
        for mid in plan.markout_ids:
            row = conn.execute(
                "SELECT outcome_id, horizon FROM futures_agent_research_signal_markouts WHERE id = ?",
                (mid,),
            ).fetchone()
            report.markouts.append(TestArtifactRow(
                table="futures_agent_research_signal_markouts",
                row_id=mid,
                reason="linked_test_outcome",
                detail=f"outcome_id={row['outcome_id']} horizon={row['horizon']}",
            ))

    for row in conn.execute(
        """
        SELECT id, exchange_symbol, interval, open_ts, data_source
        FROM futures_agent_research_market_data_cache
        WHERE data_source = 'mock'
        ORDER BY id
        LIMIT 200
        """,
    ).fetchall():
        report.cache_rows.append(TestArtifactRow(
            table="futures_agent_research_market_data_cache",
            row_id=int(row["id"]),
            reason="mock_data_source",
            detail=f"{row['exchange_symbol']} ts={row['open_ts']}",
        ))

    return report


def _delete_by_ids(conn: Any, table: str, ids: list[int]) -> int:
    if not ids:
        return 0
    ph = ",".join("?" for _ in ids)
    conn.execute(f"DELETE FROM {table} WHERE id IN ({ph})", ids)
    return len(ids)


def _validate_known_fixture_counts(
    source_ids: list[str],
    deleted: dict[str, int],
) -> bool:
    if set(source_ids) != KNOWN_FIXTURE_SOURCE_IDS:
        return False
    return all(deleted.get(k) == v for k, v in KNOWN_FIXTURE_DELETE_COUNTS.items())


def run_test_contamination_cleanup(
    conn: Any,
    *,
    channel: str | None = "signalyp",
    apply: bool = False,
) -> CleanupResult:
    plan = build_cleanup_plan(conn, channel=channel)
    result = CleanupResult(dry_run=not apply, applied=False, plan=plan)

    if not plan.post_ids:
        result.post_cleanup_report = run_test_contamination_audit(conn, channel=channel)
        return result

    if not apply:
        return result

    try:
        conn.execute("BEGIN")
        deleted = {
            "markouts": _delete_by_ids(conn, "futures_agent_research_signal_markouts", plan.markout_ids),
            "events": _delete_by_ids(conn, "futures_agent_research_signal_events", plan.event_ids),
            "outcomes": _delete_by_ids(conn, "futures_agent_research_signal_outcomes", plan.outcome_ids),
            "levels": _delete_by_ids(conn, "futures_agent_trader_levels", plan.level_ids),
            "theses": _delete_by_ids(conn, "futures_agent_trader_theses", plan.thesis_ids),
            "posts": _delete_by_ids(conn, "futures_agent_trader_posts", plan.post_ids),
        }
        result.deleted = deleted
        result.applied = True
        result.known_fixture_assertion_ok = _validate_known_fixture_counts(
            plan.source_message_ids, deleted,
        )
        if set(plan.source_message_ids) == KNOWN_FIXTURE_SOURCE_IDS:
            if not result.known_fixture_assertion_ok:
                result.errors.append(
                    f"Known fixture cleanup count mismatch: deleted={deleted} "
                    f"expected={KNOWN_FIXTURE_DELETE_COUNTS}",
                )
                conn.rollback()
                result.applied = False
                return result
        conn.commit()
    except Exception as exc:
        conn.rollback()
        result.errors.append(str(exc))
        result.applied = False
        return result

    result.post_cleanup_report = run_test_contamination_audit(conn, channel=channel)
    return result


def render_cleanup_plan(plan: CleanupPlan) -> str:
    lines = [
        "TEST CONTAMINATION CLEANUP PLAN",
        f"channel: {plan.channel or 'all'}",
        f"fixture_posts: {plan.counts['posts']}",
        f"source_message_ids: {', '.join(plan.source_message_ids) or 'none'}",
        "",
        "Counts to delete:",
    ]
    for k, v in plan.counts.items():
        lines.append(f"  {k}: {v}")
    lines.extend([
        "",
        "Post IDs:",
        f"  {plan.post_ids}",
        "Thesis IDs:",
        f"  {plan.thesis_ids}",
        "Outcome IDs:",
        f"  {plan.outcome_ids}",
        "",
        "Cache: NOT deleted (shared historical candles preserved).",
    ])
    return "\n".join(lines)


def render_test_contamination_audit(report: TestContaminationReport) -> str:
    lines = [
        "TEST CONTAMINATION AUDIT (read-only)",
        f"channel: {report.channel or 'all'}",
        f"production_outcomes_total: {report.production_outcome_count:,}",
        f"suspicious_rows_total: {report.total_suspicious:,}",
        f"suspicious_outcomes: {report.suspicious_outcome_count:,}",
        "",
    ]
    if report.total_suspicious == 0:
        lines.append("No likely unit-test artifacts detected.")
        lines.append("")
        lines.append(
            "Note: this audit only matches known test fixture IDs/hashes. "
            "Absence of matches does not prove zero historical test leakage.",
        )
        return "\n".join(lines)

    for label, rows in (
        ("POSTS", report.posts),
        ("THESES", report.theses),
        ("LEVELS", report.levels),
        ("OUTCOMES", report.outcomes),
        ("EVENTS", report.events),
        ("MARKOUTS", report.markouts),
        ("CACHE (report only, not auto-deleted)", report.cache_rows),
    ):
        if not rows:
            continue
        lines.append(f"{label} ({len(rows)}):")
        for r in rows[:50]:
            lines.append(f"  id={r.row_id} reason={r.reason} {r.detail}")
        lines.append("")

    lines.append("READ-ONLY: no rows deleted. Use outcome-test-contamination-cleanup --apply to remove.")
    return "\n".join(lines)


def render_test_contamination_cleanup(result: CleanupResult) -> str:
    lines = [
        "TEST CONTAMINATION CLEANUP",
        f"mode: {'APPLY' if result.applied else 'DRY-RUN'}",
        "",
        render_cleanup_plan(result.plan),
        "",
    ]
    if result.applied:
        lines.append("Deleted:")
        for k, v in result.deleted.items():
            lines.append(f"  {k}: {v}")
        if result.known_fixture_assertion_ok is True:
            lines.append("")
            lines.append("Known Mac Mini fixture counts: PASS")
        elif result.known_fixture_assertion_ok is False:
            lines.append("")
            lines.append("Known Mac Mini fixture counts: FAIL (transaction rolled back)")
    elif not result.plan.post_ids:
        lines.append("Nothing to delete.")
    else:
        lines.append("DRY-RUN: no rows deleted. Re-run with --apply to execute.")

    if result.errors:
        lines.extend(["", "ERRORS:"])
        for e in result.errors:
            lines.append(f"  - {e}")

    if result.post_cleanup_report is not None:
        lines.extend(["", "POST-CLEANUP AUDIT:", ""])
        lines.append(render_test_contamination_audit(result.post_cleanup_report))

    return "\n".join(lines)
