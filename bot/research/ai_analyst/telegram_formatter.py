"""S46.4 — Telegram HTML formatting (no raw markdown in chat)."""

from __future__ import annotations

import html
import re
from typing import Any


TELEGRAM_MAX = 4096
_SEPARATOR = "━━━━━━━━━━━━━━"


def escape(text: str) -> str:
    return html.escape(text or "", quote=False)


def section_header(title: str, emoji: str = "") -> str:
    label = f"{emoji} {title}".strip().upper()
    return f"<b>{escape(label)}</b>"


def format_separator() -> str:
    return escape(_SEPARATOR)


def truncate_telegram(text: str, *, limit: int = TELEGRAM_MAX) -> str:
    if len(text) <= limit:
        return text
    suffix = "\n\n… (truncated)"
    return text[: max(0, limit - len(suffix))] + suffix


def markdown_to_telegram_html(text: str) -> str:
    """Lightweight markdown → Telegram HTML (headings, bullets, bold)."""
    lines: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            lines.append("")
            continue
        if line.startswith("### "):
            title = line[4:].strip()
            lines.append(section_header(title))
            lines.append("")
            continue
        if line.startswith("## "):
            title = line[3:].strip()
            lines.append(section_header(title))
            lines.append("")
            continue
        if line.startswith("# "):
            title = line[2:].strip()
            lines.append(f"<b>{escape(title)}</b>")
            lines.append("")
            continue
        m = re.match(r"^\*\*(.+?)\*\*$", line.strip())
        if m:
            lines.append(f"<b>{escape(m.group(1))}</b>")
            continue
        if line.strip().startswith("- "):
            body = line.strip()[2:]
            body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", body)
            lines.append(f"• {body}")
            continue
        if line.strip().startswith("• "):
            body = line.strip()[2:]
            body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", body)
            lines.append(f"• {body}")
            continue
        # inline bold
        safe = escape(line)
        safe = re.sub(r"\*\*(.+?)\*\*", lambda m: f"<b>{m.group(1)}</b>", safe)
        lines.append(safe)
    return "\n".join(lines)


def extract_executive_summary(markdown: str) -> str:
    """Pull Executive Summary block from full report markdown."""
    if not markdown:
        return ""
    start = markdown.find("## Executive Summary")
    if start < 0:
        return ""
    rest = markdown[start + len("## Executive Summary") :]
    end = rest.find("\n## ")
    block = rest[:end] if end >= 0 else rest
    return block.strip()


def format_executive_summary_html(summary_md: str) -> str:
    """Bloomberg-style executive summary for Telegram."""
    if not summary_md:
        return section_header("Executive Summary", "📰") + "\n\n" + escape("Not available.")

    parts: list[str] = []
    labels = (
        ("Today's Theme:", "📰", "TODAY'S THEME"),
        ("Market Bias:", "📈", "MARKET BIAS"),
        ("Confidence:", "🎯", "CONFIDENCE"),
        ("Key Drivers:", "🔑", "KEY DRIVERS"),
        ("Main Risks:", "⚠", "MAIN RISKS"),
    )
    text = summary_md
    for label, emoji, header in labels:
        idx = text.find(label)
        if idx < 0:
            continue
        start = idx + len(label)
        next_idx = len(text)
        for other, _, _ in labels:
            if other == label:
                continue
            pos = text.find(other, start)
            if pos >= 0:
                next_idx = min(next_idx, pos)
        body = text[start:next_idx].strip()
        parts.append(section_header(header, emoji))
        parts.append("")
        parts.append(escape(body))
        parts.append("")
        parts.append(format_separator())
        parts.append("")

    if not parts:
        return markdown_to_telegram_html(summary_md)
    return "\n".join(parts).strip()


def format_report_body_html(markdown: str, *, title: str, emoji: str = "📊") -> str:
    body = markdown_to_telegram_html(markdown)
    header = section_header(title, emoji)
    return truncate_telegram(f"{header}\n\n{body}")


def format_error_html(reason: str) -> str:
    return (
        "❌ <b>Report generation failed.</b>\n\n"
        f"<b>Reason:</b>\n{escape(reason)}\n\n"
        "Try again later."
    )


def format_context_status_html(ctx: dict[str, Any]) -> str:
    quality = ctx.get("analysis_quality") or {}
    completeness = ctx.get("context_completeness") or quality.get("context_completeness")
    confidence = quality.get("confidence")
    gaps = ctx.get("data_gaps") or quality.get("data_gaps") or []
    sources = (ctx.get("completeness") or {}).get("sources") or ctx.get("sources") or []
    assets = (ctx.get("completeness") or {}).get("asset_coverage") or []

    lines = [
        section_header("Context Status", "🧠"),
        "",
        f"<b>Context completeness:</b> {escape(str(completeness))}%",
        f"<b>Confidence:</b> {escape(str(confidence))}%",
        "",
        "<b>Data gaps:</b>",
    ]
    if gaps:
        lines.extend(f"• {escape(str(g))}" for g in gaps[:12])
    else:
        lines.append("• none")
    lines.extend(["", "<b>Sources:</b>"])
    if sources:
        lines.extend(f"• {escape(str(s))}" for s in sources[:12])
    else:
        lines.append("• see live enrichment metadata in context")
    lines.extend(["", "<b>Asset coverage:</b>"])
    if assets:
        lines.extend(f"• {escape(str(a))}" for a in assets[:12])
    else:
        covered = []
        for key in ("btc", "sp500", "nasdaq", "vix", "macro", "etf"):
            if ctx.get(key):
                covered.append(key)
        lines.extend(f"• {escape(k)}" for k in covered) or lines.append("• limited")
    return truncate_telegram("\n".join(lines))
