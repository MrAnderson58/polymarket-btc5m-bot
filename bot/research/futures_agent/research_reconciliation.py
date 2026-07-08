"""Stage 3 pipeline reconciliation and integrity checks."""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from bot.research.futures_agent.research_ingest import IngestResearchStats
from bot.research.futures_agent.thesis_extract import _extract_levels, _infer_direction
from bot.research.futures_agent.research_taxonomy import ResearchContentType


CONTENT_TYPES = (
    "EXPLICIT_SIGNAL",
    "TRADER_THESIS",
    "TECHNICAL_LEVELS",
    "MARKET_COMMENTARY",
    "NEWS_EVENT",
    "ONCHAIN_EVENT",
    "WHALE_FLOW",
    "TRADE_UPDATE",
    "RESULT_UPDATE",
    "PROMO",
    "OTHER",
)

THESIS_ELIGIBLE_TYPES = (
    "EXPLICIT_SIGNAL",
    "TRADER_THESIS",
    "TECHNICAL_LEVELS",
)


@dataclass
class ReconciliationReport:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    passed: bool = True
    extraction_status: str = "UNKNOWN"
    eligible_posts: int = 0
    posts_with_theses: int = 0
    eligible_posts_without_theses: int = 0
    thesis_coverage_pct: float = 0.0

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append((name, ok, detail))
        if not ok:
            self.passed = False


def render_ingest_reconciliation(
    stats: IngestResearchStats,
    *,
    channel: str | None = None,
    conn: Any | None = None,
) -> str:
    """Render final ingest reconciliation block."""
    lines = [
        "",
        "INGEST RECONCILIATION",
        f"SOURCE ROWS SCANNED: {stats.scanned:,}",
        f"POSTS INSERTED: {stats.inserted:,}",
        f"DUPLICATES SKIPPED: {stats.skipped_duplicate:,}",
        f"CONTENT HASH DEDUPED: {stats.skipped_hash_duplicate:,}",
    ]
    if stats.skipped_empty:
        lines.append(f"EMPTY SKIPPED: {stats.skipped_empty:,}")
    if stats.skipped_source_cap:
        lines.append(f"SOURCE CAP SKIPPED: {stats.skipped_source_cap:,}")
    if channel:
        lines.append(f"CHANNEL: {channel}")

    lines.append("")
    lines.append("CONTENT TYPES:")
    counts = Counter(stats.content_type_counts)
    if conn is not None and channel:
        for row in conn.execute(
            """
            SELECT content_type, COUNT(*) AS n
            FROM futures_agent_trader_posts
            WHERE channel_name = ?
            GROUP BY content_type
            """,
            (channel,),
        ).fetchall():
            counts[row["content_type"]] = row["n"]
    elif conn is not None:
        for row in conn.execute(
            """
            SELECT content_type, COUNT(*) AS n
            FROM futures_agent_trader_posts
            GROUP BY content_type
            """,
        ).fetchall():
            counts[row["content_type"]] = row["n"]

    for ctype in CONTENT_TYPES:
        lines.append(f"  {ctype}: {counts.get(ctype, 0):,}")

    scanned = max(stats.scanned, 1)
    lines.extend([
        "",
        f"SYMBOL EXTRACTION COVERAGE: {stats.symbols_extracted / scanned:.1%}",
        f"DIRECTION EXTRACTION COVERAGE: {stats.directions_extracted / scanned:.1%}",
        f"LEVEL EXTRACTION COVERAGE: {stats.levels_extracted / scanned:.1%}",
    ])
    if stats.current_run_inserted_by_channel or stats.total_stored_by_channel:
        lines.append("")
        lines.append("CURRENT RUN INSERTED BY CHANNEL:")
        for ch, n in sorted(stats.current_run_inserted_by_channel.items()):
            lines.append(f"  {ch}: {n:,}")
        lines.append("")
        lines.append("TOTAL STORED BY CHANNEL:")
        for ch, n in sorted(stats.total_stored_by_channel.items()):
            lines.append(f"  {ch}: {n:,}")
    return "\n".join(lines)


def run_pipeline_reconciliation(
    conn: Any,
    *,
    channel: str | None = None,
) -> ReconciliationReport:
    """Deterministic integrity checks on ingested posts/theses/levels."""
    report = ReconciliationReport()
    ch_clause = ""
    params: list[Any] = []
    if channel:
        ch_clause = " AND p.channel_name = ?"
        params = [channel]

    orphan_theses = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_theses t
        LEFT JOIN futures_agent_trader_posts p ON p.id = t.post_id
        WHERE p.id IS NULL
        """,
    ).fetchone()["n"]
    report.add(
        "thesis_post_fk",
        orphan_theses == 0,
        f"orphan theses: {orphan_theses}",
    )

    orphan_levels = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_levels l
        LEFT JOIN futures_agent_trader_theses t ON t.id = l.thesis_id
        WHERE t.id IS NULL
        """,
    ).fetchone()["n"]
    report.add(
        "level_thesis_fk",
        orphan_levels == 0,
        f"orphan levels: {orphan_levels}",
    )

    dup_posts = conn.execute(
        f"""
        SELECT channel_name, source_message_id, COUNT(*) AS n
        FROM futures_agent_trader_posts p
        WHERE 1=1{ch_clause}
        GROUP BY channel_name, source_message_id
        HAVING COUNT(*) > 1
        LIMIT 5
        """,
        params,
    ).fetchall()
    report.add(
        "unique_channel_source_message",
        len(dup_posts) == 0,
        f"duplicate post keys: {len(dup_posts)}",
    )

    now_ts = int(time.time()) + 300
    future_posts = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_posts p
        WHERE message_ts > ?{ch_clause}
        """,
        [now_ts, *params],
    ).fetchone()["n"]
    report.add(
        "no_future_timestamps",
        future_posts == 0,
        f"future-dated posts: {future_posts}",
    )

    null_ts = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_posts p
        WHERE message_ts IS NULL OR message_ts <= 0{ch_clause}
        """,
        params,
    ).fetchone()["n"]
    report.add(
        "message_ts_preserved",
        null_ts == 0,
        f"invalid message_ts: {null_ts}",
    )

    dup_theses = conn.execute(
        f"""
        SELECT COUNT(*) AS n FROM (
            SELECT t.post_id, t.symbol, t.direction, t.thesis_text, COUNT(*) AS c
            FROM futures_agent_trader_theses t
            JOIN futures_agent_trader_posts p ON p.id = t.post_id
            WHERE 1=1{ch_clause}
            GROUP BY t.post_id, t.symbol, t.direction, t.thesis_text
            HAVING COUNT(*) > 1
        ) x
        """,
        params,
    ).fetchone()["n"]
    report.add(
        "no_duplicate_thesis_fingerprints",
        dup_theses == 0,
        f"duplicate thesis fingerprints: {dup_theses}",
    )

    eligible_placeholders = ",".join("?" for _ in THESIS_ELIGIBLE_TYPES)
    eligible_posts = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_posts p
        WHERE p.content_type IN ({eligible_placeholders}){ch_clause}
        """,
        [*THESIS_ELIGIBLE_TYPES, *params],
    ).fetchone()["n"]
    posts_with_theses = conn.execute(
        f"""
        SELECT COUNT(DISTINCT p.id) AS n
        FROM futures_agent_trader_posts p
        JOIN futures_agent_trader_theses t ON t.post_id = p.id
        WHERE p.content_type IN ({eligible_placeholders}){ch_clause}
        """,
        [*THESIS_ELIGIBLE_TYPES, *params],
    ).fetchone()["n"]
    total_theses = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_theses t
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        WHERE p.content_type IN ({eligible_placeholders}){ch_clause}
        """,
        [*THESIS_ELIGIBLE_TYPES, *params],
    ).fetchone()["n"]

    report.eligible_posts = eligible_posts
    report.posts_with_theses = posts_with_theses
    report.eligible_posts_without_theses = max(eligible_posts - posts_with_theses, 0)
    report.thesis_coverage_pct = (
        posts_with_theses / eligible_posts if eligible_posts else 0.0
    )

    if eligible_posts > 0 and total_theses == 0:
        report.extraction_status = "INCOMPLETE"
        report.add(
            "thesis_extraction_status",
            False,
            (
                f"eligible_posts={eligible_posts} posts_with_theses=0 "
                f"thesis_coverage={report.thesis_coverage_pct:.1%} status=INCOMPLETE"
            ),
        )
    elif eligible_posts > 0 and posts_with_theses < eligible_posts:
        report.extraction_status = "PARTIAL"
        report.add(
            "thesis_extraction_status",
            True,
            (
                f"eligible_posts={eligible_posts} posts_with_theses={posts_with_theses} "
                f"eligible_without_theses={report.eligible_posts_without_theses} "
                f"thesis_coverage={report.thesis_coverage_pct:.1%} status=PARTIAL"
            ),
        )
    elif eligible_posts > 0:
        report.extraction_status = "COMPLETE"
        report.add(
            "thesis_extraction_status",
            True,
            (
                f"eligible_posts={eligible_posts} posts_with_theses={posts_with_theses} "
                f"thesis_coverage={report.thesis_coverage_pct:.1%} status=COMPLETE"
            ),
        )
    else:
        report.extraction_status = "NO_ELIGIBLE_POSTS"
        report.add(
            "thesis_extraction_status",
            True,
            "eligible_posts=0 status=NO_ELIGIBLE_POSTS",
        )

    return report


def render_pipeline_reconciliation(report: ReconciliationReport) -> str:
    lines = ["PIPELINE RECONCILIATION CHECKS", ""]
    lines.extend([
        f"extraction_status: {report.extraction_status}",
        f"eligible_posts: {report.eligible_posts:,}",
        f"posts_with_theses: {report.posts_with_theses:,}",
        f"eligible_posts_without_theses: {report.eligible_posts_without_theses:,}",
        f"thesis_coverage_pct: {report.thesis_coverage_pct:.1%}",
        "",
    ])
    for name, ok, detail in report.checks:
        status = "PASS" if ok else "FAIL"
        lines.append(f"  [{status}] {name}: {detail}")
    lines.append("")
    overall = "PASS" if report.passed else ("INCOMPLETE" if report.extraction_status == "INCOMPLETE" else "FAIL")
    lines.append(f"Overall: {overall}")
    return "\n".join(lines)


@dataclass
class ThesisExtractStats:
    posts_scanned: int = 0
    posts_total: int = 0
    theses_inserted: int = 0
    levels_inserted: int = 0
    unresolved_skipped: int = 0
    posts_without_thesis: int = 0


def render_thesis_extract_report(
    conn: Any,
    stats: ThesisExtractStats,
    *,
    channel: str | None = None,
) -> str:
    """Render final thesis-extract reconciliation from DB state."""
    ch_clause = ""
    params: list[Any] = []
    if channel:
        ch_clause = " WHERE p.channel_name = ?"
        params = [channel]

    posts_analyzed = conn.execute(
        f"SELECT COUNT(*) AS n FROM futures_agent_trader_posts p{ch_clause}",
        params,
    ).fetchone()["n"]
    theses = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_theses t
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        {ch_clause}
        """,
        params,
    ).fetchone()["n"]
    coverage = theses / posts_analyzed if posts_analyzed else 0.0

    lines = [
        "",
        "THESIS EXTRACT RECONCILIATION",
        f"posts analyzed: {posts_analyzed:,}",
        f"theses created (this run): {stats.theses_inserted:,}",
        f"total theses in DB: {theses:,}",
        f"thesis coverage: {coverage:.1%}",
        f"posts without thesis: {stats.posts_without_thesis:,}",
        "",
        "theses by content_type:",
    ]
    for row in conn.execute(
        f"""
        SELECT p.content_type, COUNT(*) AS n
        FROM futures_agent_trader_theses t
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        {ch_clause}
        GROUP BY p.content_type
        ORDER BY n DESC
        """,
        params,
    ).fetchall():
        lines.append(f"  {row['content_type']}: {row['n']:,}")

    lines.append("")
    lines.append("theses by LONG / SHORT:")
    for row in conn.execute(
        f"""
        SELECT t.direction, COUNT(*) AS n
        FROM futures_agent_trader_theses t
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        {ch_clause}
        GROUP BY t.direction
        ORDER BY n DESC
        """,
        params,
    ).fetchall():
        lines.append(f"  {row['direction']}: {row['n']:,}")

    unique_symbols = conn.execute(
        f"""
        SELECT COUNT(DISTINCT t.symbol) AS n
        FROM futures_agent_trader_theses t
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        {ch_clause}
        """,
        params,
    ).fetchone()["n"]
    lines.append("")
    lines.append(f"unique symbols: {unique_symbols:,}")

    lines.append("")
    lines.append("levels:")
    level_q = """
        SELECT l.level_type, COUNT(*) AS n
        FROM futures_agent_trader_levels l
        JOIN futures_agent_trader_theses t ON t.id = l.thesis_id
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
    """
    if channel:
        level_q += " WHERE p.channel_name = ? GROUP BY l.level_type"
        level_params: list[Any] = [channel]
    else:
        level_q += " GROUP BY l.level_type"
        level_params = []
    level_counts = {
        r["level_type"]: r["n"]
        for r in conn.execute(level_q, level_params).fetchall()
    }
    for ltype in ("ENTRY_LOW", "ENTRY_HIGH", "STOP", "TARGET", "SUPPORT", "RESISTANCE"):
        lines.append(f"  {ltype}: {level_counts.get(ltype, 0):,}")

    # Structure coverage
    struct = _thesis_structure_counts(conn, channel=channel)
    lines.extend([
        "",
        f"theses with entry: {struct['with_entry']:,}",
        f"theses with numeric stop: {struct['with_stop']:,}",
        f"theses with deferred stop: {struct['with_deferred_stop']:,}",
        f"theses with >=1 target: {struct['with_target']:,}",
        f"theses with complete entry+stop+target: {struct['complete_structure']:,}",
    ])
    return "\n".join(lines)


def _thesis_structure_counts(conn: Any, *, channel: str | None = None) -> dict[str, int]:
    ch_clause = ""
    params: list[Any] = []
    if channel:
        ch_clause = " AND p.channel_name = ?"
        params = [channel]

    rows = conn.execute(
        f"""
        SELECT t.id, p.raw_text,
               SUM(CASE WHEN l.level_type IN ('ENTRY_LOW','ENTRY_HIGH') THEN 1 ELSE 0 END) AS has_entry,
               SUM(CASE WHEN l.level_type = 'STOP' THEN 1 ELSE 0 END) AS has_stop,
               SUM(CASE WHEN l.level_type = 'TARGET' THEN 1 ELSE 0 END) AS has_target
        FROM futures_agent_trader_theses t
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        LEFT JOIN futures_agent_trader_levels l ON l.thesis_id = t.id
        WHERE 1=1{ch_clause}
        GROUP BY t.id, p.raw_text
        """,
        params,
    ).fetchall()

    deferred_re = re.compile(
        r"(?i)(?:стоп\s*[:：]?\s*(?:пока\s+не\s+ставлю|не\s+ставлю)|"
        r"stop\s*[:：]?\s*(?:later|not\s+set|pending))",
    )
    out = {
        "with_entry": 0,
        "with_stop": 0,
        "with_deferred_stop": 0,
        "with_target": 0,
        "complete_structure": 0,
    }
    for row in rows:
        has_entry = row["has_entry"] > 0
        has_stop = row["has_stop"] > 0
        has_target = row["has_target"] > 0
        deferred = bool(deferred_re.search(row["raw_text"] or ""))
        if has_entry:
            out["with_entry"] += 1
        if has_stop:
            out["with_stop"] += 1
        if deferred and not has_stop:
            out["with_deferred_stop"] += 1
        if has_target:
            out["with_target"] += 1
        if has_entry and has_stop and has_target:
            out["complete_structure"] += 1
    return out


def count_extraction_coverage(text: str, content_type: str) -> tuple[bool, bool, bool]:
    """Return (has_symbol, has_direction, has_levels) for ingest coverage stats."""
    from bot.research.futures_agent.research_utils import extract_symbols
    from bot.research.futures_agent.research_taxonomy import _signal_symbols

    syms = _signal_symbols(text) or extract_symbols(text)
    has_sym = bool(syms)
    try:
        ctype = ResearchContentType(content_type)
    except ValueError:
        ctype = ResearchContentType.OTHER
    direction = _infer_direction(text, ctype)
    has_dir = direction != "NEUTRAL"
    has_levels = bool(_extract_levels(text))
    return has_sym, has_dir, has_levels
