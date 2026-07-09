"""Read-only audit for likely unit-test artifacts in production research tables."""

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

TEST_THESIS_TEXTS = frozenset({
    "test",
    "smoke",
    "btc tech",
    "ordi",
})


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

    posts = conn.execute(
        f"""
        SELECT p.id, p.source_message_id, p.content_hash, p.raw_text, p.content_type
        FROM futures_agent_trader_posts p
        WHERE 1=1{ch_clause}
        ORDER BY p.id
        """,
        params,
    ).fetchall()

    suspicious_post_ids: set[int] = set()
    for row in posts:
        reasons: list[str] = []
        if _is_test_source_message_id(row["source_message_id"]):
            reasons.append("test_source_message_id")
        if _is_test_content_hash(row["content_hash"]):
            reasons.append("test_content_hash")
        raw = (row["raw_text"] or "")[:120]
        if raw.startswith("BTC LONG\nEntry: 100\nSL: 95\nTP: 110") and row["content_type"] == "EXPLICIT_SIGNAL":
            reasons.append("canonical_d1_fixture_text")
        if reasons:
            suspicious_post_ids.add(int(row["id"]))
            report.posts.append(TestArtifactRow(
                table="futures_agent_trader_posts",
                row_id=int(row["id"]),
                reason=",".join(reasons),
                detail=f"source_message_id={row['source_message_id']} hash={row['content_hash']}",
            ))

    if suspicious_post_ids:
        ph = ",".join("?" for _ in suspicious_post_ids)
        for row in conn.execute(
            f"""
            SELECT t.id, t.post_id, t.thesis_text, t.symbol
            FROM futures_agent_trader_theses t
            WHERE t.post_id IN ({ph})
            """,
            list(suspicious_post_ids),
        ).fetchall():
            report.theses.append(TestArtifactRow(
                table="futures_agent_trader_theses",
                row_id=int(row["id"]),
                reason="linked_test_post",
                detail=f"post_id={row['post_id']} thesis_text={row['thesis_text']!r}",
            ))

        for row in conn.execute(
            f"""
            SELECT l.id, l.thesis_id, l.level_type, l.price
            FROM futures_agent_trader_levels l
            JOIN futures_agent_trader_theses t ON t.id = l.thesis_id
            WHERE t.post_id IN ({ph})
            """,
            list(suspicious_post_ids),
        ).fetchall():
            report.levels.append(TestArtifactRow(
                table="futures_agent_trader_levels",
                row_id=int(row["id"]),
                reason="linked_test_thesis",
                detail=f"thesis_id={row['thesis_id']} {row['level_type']}={row['price']}",
            ))

        for row in conn.execute(
            f"""
            SELECT o.id, o.thesis_id, o.post_id, o.engine_version, o.entry_status
            FROM futures_agent_research_signal_outcomes o
            WHERE o.post_id IN ({ph})
            """,
            list(suspicious_post_ids),
        ).fetchall():
            report.outcomes.append(TestArtifactRow(
                table="futures_agent_research_signal_outcomes",
                row_id=int(row["id"]),
                reason="linked_test_post",
                detail=f"thesis_id={row['thesis_id']} engine={row['engine_version']}",
            ))
            report.suspicious_outcome_count += 1

        for row in conn.execute(
            f"""
            SELECT e.id, e.outcome_id, e.event_type, e.event_ts
            FROM futures_agent_research_signal_events e
            JOIN futures_agent_research_signal_outcomes o ON o.id = e.outcome_id
            WHERE o.post_id IN ({ph})
            """,
            list(suspicious_post_ids),
        ).fetchall():
            report.events.append(TestArtifactRow(
                table="futures_agent_research_signal_events",
                row_id=int(row["id"]),
                reason="linked_test_outcome",
                detail=f"outcome_id={row['outcome_id']} event={row['event_type']}",
            ))

        for row in conn.execute(
            f"""
            SELECT m.id, m.outcome_id, m.horizon
            FROM futures_agent_research_signal_markouts m
            JOIN futures_agent_research_signal_outcomes o ON o.id = m.outcome_id
            WHERE o.post_id IN ({ph})
            """,
            list(suspicious_post_ids),
        ).fetchall():
            report.markouts.append(TestArtifactRow(
                table="futures_agent_research_signal_markouts",
                row_id=int(row["id"]),
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

  # Orphan test-like theses without matching post heuristics
    for row in conn.execute(
        f"""
        SELECT t.id, t.post_id, t.thesis_text
        FROM futures_agent_trader_theses t
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        WHERE t.thesis_text IN ('test', 'smoke'){ch_clause}
        """,
        params,
    ).fetchall():
        if int(row["id"]) in {r.row_id for r in report.theses}:
            continue
        report.theses.append(TestArtifactRow(
            table="futures_agent_trader_theses",
            row_id=int(row["id"]),
            reason="test_thesis_text",
            detail=f"post_id={row['post_id']} thesis_text={row['thesis_text']!r}",
        ))

    return report


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
        ("CACHE", report.cache_rows),
    ):
        if not rows:
            continue
        lines.append(f"{label} ({len(rows)}):")
        for r in rows[:50]:
            lines.append(f"  id={r.row_id} reason={r.reason} {r.detail}")
        lines.append("")

    lines.append("READ-ONLY: no rows deleted. Review manually before any cleanup.")
    return "\n".join(lines)
