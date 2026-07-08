"""Deterministic thesis extraction quality audit."""

from __future__ import annotations

import random
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from bot.research.futures_agent.research_taxonomy import ResearchContentType

_STRATIFIED_TYPES = (
    ResearchContentType.EXPLICIT_SIGNAL.value,
    ResearchContentType.TRADER_THESIS.value,
    ResearchContentType.TECHNICAL_LEVELS.value,
)

_RE_DEFERRED_STOP = re.compile(
    r"(?i)(?:стоп\s*[:：]?\s*(?:пока\s+не\s+ставлю|не\s+ставлю)|"
    r"stop\s*[:：]?\s*(?:later|not\s+set|pending))",
)


@dataclass
class ThesisQualityRow:
    post_id: int
    thesis_id: int
    content_type: str
    channel_name: str
    raw_preview: str
    symbol: str | None
    direction: str | None
    entry_low: float | None
    entry_high: float | None
    stop: float | None
    stop_status: str
    targets: list[float]
    support: list[float]
    resistance: list[float]
    horizon: str | None
    confidence: float | None
    suspicious: list[str] = field(default_factory=list)


@dataclass
class ThesisQualityAuditReport:
    sample_size: int = 0
    rows: list[ThesisQualityRow] = field(default_factory=list)
    suspicious_rows: list[ThesisQualityRow] = field(default_factory=list)
    sampled_by_type: dict[str, int] = field(default_factory=dict)
    incomplete: bool = False
    eligible_posts: int = 0
    total_theses: int = 0


def _levels_by_type(levels: list[dict]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for lv in levels:
        out[lv["level_type"]].append(float(lv["price"]))
    return out


def _stop_status(raw_text: str, stop: float | None) -> str:
    if stop is not None:
        return "numeric"
    if _RE_DEFERRED_STOP.search(raw_text):
        return "deferred"
    return "missing"


def _audit_row_suspicious(row: ThesisQualityRow) -> list[str]:
    flags: list[str] = []
    if not row.symbol:
        flags.append("thesis_without_symbol")
    if not row.direction or row.direction == "NEUTRAL":
        flags.append("thesis_without_direction")

    entry = row.entry_low or row.entry_high
    if row.direction == "LONG" and entry is not None and row.stop is not None:
        if row.stop >= entry:
            flags.append("long_stop_gte_entry")
    if row.direction == "SHORT" and entry is not None and row.stop is not None:
        if row.stop <= entry:
            flags.append("short_stop_lte_entry")

    if row.direction == "LONG" and entry is not None:
        for tp in row.targets:
            if tp <= entry:
                flags.append("long_target_lte_entry")
                break
    if row.direction == "SHORT" and entry is not None:
        for tp in row.targets:
            if tp >= entry:
                flags.append("short_target_gte_entry")
                break

    if entry is not None:
        for tp in row.targets:
            if entry > 0 and abs(tp - entry) / entry > 5.0:
                flags.append("extreme_target_distance")
                break

    if len(row.targets) != len(set(row.targets)):
        flags.append("duplicate_targets")

    for val in [row.entry_low, row.entry_high, row.stop, *row.targets]:
        if val is not None and (val <= 0 or val > 1e12):
            flags.append("malformed_decimal")
            break

    return flags


def _build_row(conn: Any, post: dict, thesis: dict) -> ThesisQualityRow:
    levels = conn.execute(
        """
        SELECT level_type, price, ordinal
        FROM futures_agent_trader_levels
        WHERE thesis_id = ?
        ORDER BY ordinal ASC
        """,
        (thesis["id"],),
    ).fetchall()
    by_type = _levels_by_type(levels)
    entry_lows = by_type.get("ENTRY_LOW", [])
    entry_highs = by_type.get("ENTRY_HIGH", [])
    stop_vals = by_type.get("STOP", [])

    row = ThesisQualityRow(
        post_id=post["id"],
        thesis_id=thesis["id"],
        content_type=post["content_type"],
        channel_name=post["channel_name"],
        raw_preview=post["raw_text"][:300].replace("\n", " "),
        symbol=thesis["symbol"],
        direction=thesis["direction"],
        entry_low=entry_lows[0] if entry_lows else None,
        entry_high=entry_highs[0] if entry_highs else None,
        stop=stop_vals[0] if stop_vals else None,
        stop_status=_stop_status(post["raw_text"], stop_vals[0] if stop_vals else None),
        targets=by_type.get("TARGET", []),
        support=by_type.get("SUPPORT", []),
        resistance=by_type.get("RESISTANCE", []),
        horizon=thesis["horizon"],
        confidence=thesis["confidence"],
    )
    row.suspicious = _audit_row_suspicious(row)
    return row


def run_thesis_quality_audit(
    conn: Any,
    *,
    channel: str | None = "signalyp",
    sample_size: int = 100,
    seed: int = 42,
) -> ThesisQualityAuditReport:
    """Stratified sample of theses across key content types."""
    report = ThesisQualityAuditReport()
    ch_clause = ""
    params: list[Any] = []
    if channel:
        ch_clause = " AND p.channel_name = ?"
        params.append(channel)

    eligible_placeholders = ",".join("?" for _ in _STRATIFIED_TYPES)
    report.eligible_posts = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_posts p
        WHERE p.content_type IN ({eligible_placeholders}){ch_clause}
        """,
        [*_STRATIFIED_TYPES, *params],
    ).fetchone()["n"]
    report.total_theses = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_theses t
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        WHERE p.content_type IN ({eligible_placeholders}){ch_clause}
        """,
        [*_STRATIFIED_TYPES, *params],
    ).fetchone()["n"]

    if report.eligible_posts > 0 and report.total_theses == 0:
        report.incomplete = True
        return report

    rng = random.Random(seed)

    per_type: dict[str, list[dict]] = defaultdict(list)

    for ctype in _STRATIFIED_TYPES:
        rows = conn.execute(
            f"""
            SELECT p.id AS post_id, p.channel_name, p.content_type, p.raw_text,
                   t.id AS thesis_id, t.symbol, t.direction, t.horizon, t.confidence
            FROM futures_agent_trader_posts p
            JOIN futures_agent_trader_theses t ON t.post_id = p.id
            WHERE p.content_type = ?{ch_clause}
            ORDER BY p.message_ts ASC
            """,
            [ctype, *params],
        ).fetchall()
        per_type[ctype] = [dict(r) for r in rows]

    n_types = len(_STRATIFIED_TYPES)
    if sample_size <= n_types:
        sizes = {ctype: (1 if i < sample_size else 0) for i, ctype in enumerate(_STRATIFIED_TYPES)}
    else:
        base = sample_size // n_types
        rem = sample_size - base * n_types
        sizes = {
            ctype: base + (1 if i < rem else 0)
            for i, ctype in enumerate(_STRATIFIED_TYPES)
        }

    for ctype in _STRATIFIED_TYPES:
        pool = per_type[ctype]
        k = min(sizes[ctype], len(pool))
        report.sampled_by_type[ctype] = k
        if k == 0:
            continue
        if len(pool) <= k:
            chosen = pool
        else:
            chosen = rng.sample(pool, k)
        for item in chosen:
            post = {
                "id": item["post_id"],
                "channel_name": item["channel_name"],
                "content_type": item["content_type"],
                "raw_text": item["raw_text"],
            }
            thesis = {
                "id": item["thesis_id"],
                "symbol": item["symbol"],
                "direction": item["direction"],
                "horizon": item["horizon"],
                "confidence": item["confidence"],
            }
            row = _build_row(conn, post, thesis)
            report.rows.append(row)
            report.sample_size += 1
            if row.suspicious:
                report.suspicious_rows.append(row)

    return report


def render_thesis_quality_audit(report: ThesisQualityAuditReport) -> str:
    if report.incomplete:
        return "\n".join([
            "THESIS QUALITY AUDIT: INCOMPLETE",
            "Eligible posts exist but no theses were extracted.",
            f"eligible_posts: {report.eligible_posts:,}",
            f"total_theses: {report.total_theses:,}",
        ])

    lines = [
        "THESIS QUALITY AUDIT (stratified sample)",
        f"sample_size: {report.sample_size}",
        "",
        "Sampled by content_type:",
    ]
    for ctype in _STRATIFIED_TYPES:
        lines.append(f"  {ctype}: {report.sampled_by_type.get(ctype, 0)}")

    lines.append("")
    lines.append("Samples:")
    for row in report.rows:
        lines.append(f"  --- [{row.content_type}] post={row.post_id} thesis={row.thesis_id} ---")
        lines.append(f"  preview: {row.raw_preview}")
        lines.append(f"  symbol: {row.symbol}")
        lines.append(f"  direction: {row.direction}")
        lines.append(f"  entry_low: {row.entry_low}")
        lines.append(f"  entry_high: {row.entry_high}")
        lines.append(f"  stop: {row.stop}")
        lines.append(f"  stop_status: {row.stop_status}")
        lines.append(f"  targets: {row.targets}")
        lines.append(f"  support: {row.support}")
        lines.append(f"  resistance: {row.resistance}")
        lines.append(f"  horizon: {row.horizon}")
        lines.append(f"  confidence: {row.confidence}")
        if row.suspicious:
            lines.append(f"  SUSPICIOUS: {', '.join(row.suspicious)}")

    if report.suspicious_rows:
        lines.append("")
        lines.append(f"Suspicious rows: {len(report.suspicious_rows)}")
        for row in report.suspicious_rows:
            lines.append(
                f"  thesis={row.thesis_id} [{row.content_type}]: {', '.join(row.suspicious)}",
            )

    return "\n".join(lines)
