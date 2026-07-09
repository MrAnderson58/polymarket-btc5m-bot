"""Focused audit for TECHNICAL_LEVELS posts and extracted support/resistance."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bot.research.futures_agent.research_utils import extract_research_symbols

_FIB_RATIOS = frozenset({
    0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.272, 1.618, 2.0, 2.618,
})

_RE_SUPPORT_CTX = re.compile(
    r"(?i)(?:support|поддержк)",
)
_RE_RESISTANCE_CTX = re.compile(
    r"(?i)(?:resistance|сопротивлен)",
)
_RE_RECOGNIZABLE_ASSET = re.compile(
    r"(?i)(?:\bbitcoin\b|\bбиткоин\b|\bбиток\b|\bethereum\b|\bэфир|\bBTC\b|\bETH\b|#TON|\$TON|\bTON\b)",
)


@dataclass
class SuspiciousTechnicalLevel:
    post_id: int
    thesis_id: int
    symbol: str | None
    level_kind: str
    value: float
    reasons: list[str]
    preview: str


@dataclass
class TechnicalLevelsAuditReport:
    channel: str | None = None
    total_posts: int = 0
    theses_created: int = 0
    missing_theses: int = 0
    posts_with_symbol: int = 0
    posts_without_symbol: int = 0
    support_count: int = 0
    resistance_count: int = 0
    suspicious_levels: list[SuspiciousTechnicalLevel] = field(default_factory=list)


def _is_fib_like(value: float) -> bool:
    return any(abs(value - r) < 0.001 for r in _FIB_RATIOS)


def _level_has_lexical_context(raw_text: str, level_type: str, value: float) -> bool:
    text = raw_text.lower()
    val_s = f"{value:g}"
    pat = _RE_SUPPORT_CTX if level_type == "SUPPORT" else _RE_RESISTANCE_CTX
    for m in pat.finditer(text):
        start = max(0, m.start() - 40)
        end = min(len(text), m.end() + 60)
        window = text[start:end]
        if val_s in window or f"{value:.3f}" in window:
            return True
    return False


def _audit_level_suspicious(
    *,
    post_id: int,
    thesis_id: int,
    symbol: str | None,
    raw_text: str,
    level_type: str,
    value: float,
) -> list[str]:
    reasons: list[str] = []
    if not _level_has_lexical_context(raw_text, level_type, value):
        reasons.append("missing_support_resistance_lexical_context")
    if value < 50 and _is_fib_like(value):
        reasons.append("likely_fibonacci_ratio_not_price")
    if value <= 0 or value > 1e9:
        reasons.append("malformed_or_impossible_price")
    if not symbol and _RE_RECOGNIZABLE_ASSET.search(raw_text):
        reasons.append("symbol_missing_despite_recognizable_asset_alias")
    if re.search(r"\d+(?:\.\d+)?\s*%", raw_text) and value <= 100:
        if _is_fib_like(value) or value in (22.0, 8.0, 25.0, 50.0, 58.0):
            reasons.append("likely_from_percentage_prose")
    if re.search(r"\d+\s*(?:m|h|d|w|min)\b", raw_text, re.I) and value <= 24:
        reasons.append("likely_from_timeframe_notation")
    return reasons


def run_technical_levels_audit(
    conn: Any,
    *,
    channel: str | None = "signalyp",
) -> TechnicalLevelsAuditReport:
    report = TechnicalLevelsAuditReport(channel=channel)
    ch_clause = ""
    params: list[Any] = []
    if channel:
        ch_clause = " AND p.channel_name = ?"
        params.append(channel)

    report.total_posts = conn.execute(
        f"""
        SELECT COUNT(*) AS n FROM futures_agent_trader_posts p
        WHERE p.content_type = 'TECHNICAL_LEVELS'{ch_clause}
        """,
        params,
    ).fetchone()["n"]

    report.theses_created = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_theses t
        JOIN futures_agent_trader_posts p ON p.id = t.post_id
        WHERE p.content_type = 'TECHNICAL_LEVELS'{ch_clause}
        """,
        params,
    ).fetchone()["n"]

    report.missing_theses = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM futures_agent_trader_posts p
        LEFT JOIN futures_agent_trader_theses t ON t.post_id = p.id
        WHERE t.id IS NULL AND p.content_type = 'TECHNICAL_LEVELS'{ch_clause}
        """,
        params,
    ).fetchone()["n"]

    posts = conn.execute(
        f"""
        SELECT p.id, p.raw_text, p.symbols_json, t.id AS thesis_id, t.symbol
        FROM futures_agent_trader_posts p
        LEFT JOIN futures_agent_trader_theses t ON t.post_id = p.id
        WHERE p.content_type = 'TECHNICAL_LEVELS'{ch_clause}
        ORDER BY p.id
        """,
        params,
    ).fetchall()

    for row in posts:
        syms = extract_research_symbols(row["raw_text"] or "")
        has_sym = bool(row["symbol"] or syms)
        if has_sym:
            report.posts_with_symbol += 1
        else:
            report.posts_without_symbol += 1

        if not row["thesis_id"]:
            continue

        levels = conn.execute(
            """
            SELECT level_type, price FROM futures_agent_trader_levels
            WHERE thesis_id = ? AND level_type IN ('SUPPORT', 'RESISTANCE')
            """,
            (row["thesis_id"],),
        ).fetchall()

        for lv in levels:
            ltype = lv["level_type"]
            val = float(lv["price"])
            if ltype == "SUPPORT":
                report.support_count += 1
            else:
                report.resistance_count += 1
            reasons = _audit_level_suspicious(
                post_id=row["id"],
                thesis_id=row["thesis_id"],
                symbol=row["symbol"],
                raw_text=row["raw_text"] or "",
                level_type=ltype,
                value=val,
            )
            if reasons:
                report.suspicious_levels.append(SuspiciousTechnicalLevel(
                    post_id=row["id"],
                    thesis_id=row["thesis_id"],
                    symbol=row["symbol"],
                    level_kind=ltype,
                    value=val,
                    reasons=reasons,
                    preview=(row["raw_text"] or "")[:300].replace("\n", " "),
                ))

    return report


def render_technical_levels_audit(report: TechnicalLevelsAuditReport) -> str:
    lines = [
        "TECHNICAL_LEVELS AUDIT",
        f"channel: {report.channel or 'all'}",
        "",
        f"A. total TECHNICAL_LEVELS posts: {report.total_posts:,}",
        f"B. theses created: {report.theses_created:,}",
        f"C. missing theses: {report.missing_theses:,}",
        f"D. posts with symbol: {report.posts_with_symbol:,}",
        f"E. posts without symbol: {report.posts_without_symbol:,}",
        f"F. SUPPORT levels: {report.support_count:,}",
        f"G. RESISTANCE levels: {report.resistance_count:,}",
        f"H. suspicious extracted levels: {len(report.suspicious_levels):,}",
        "",
    ]
    if not report.suspicious_levels:
        lines.append("No suspicious technical levels flagged.")
        return "\n".join(lines)

    lines.append("Suspicious examples:")
    for row in report.suspicious_levels[:50]:
        lines.append(
            f"  post={row.post_id} thesis={row.thesis_id} "
            f"symbol={row.symbol} {row.level_kind}={row.value}",
        )
        lines.append(f"    reasons: {', '.join(row.reasons)}")
        lines.append(f"    preview: {row.preview}")
    return "\n".join(lines)
