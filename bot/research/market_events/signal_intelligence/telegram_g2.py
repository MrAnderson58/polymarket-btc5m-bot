"""Phase G.2 — short Research Agent Telegram block (max 6–8 lines)."""

from __future__ import annotations


def format_research_telegram_g2(
    *,
    reversal_factors: list[str],
    risks: list[str],
    summary_ru: str,
) -> str:
    lines = [
        "🧠 Research Agent",
        "",
        "За откат:",
    ]
    for s in reversal_factors[:3]:
        lines.append(f"• {s}")
    if not reversal_factors:
        lines.append("• —")

    lines.extend(["", "Риски:"])
    for s in risks[:2]:
        lines.append(f"• {s}")
    if not risks:
        lines.append("• —")

    lines.extend(["", "Итог:", summary_ru])
    # Cap at 8 content lines as per spec
    return "\n".join(lines[:10])
