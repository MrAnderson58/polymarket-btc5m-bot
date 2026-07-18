"""AI Research review DTOs (V7.0.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ReviewVerdict = Literal["confirm", "caution", "reject", "unknown"]


@dataclass(frozen=True)
class ResearchContext:
    """Packed Terminal context for Claude — never empty-slate analysis."""

    symbol: str
    decision_text: str
    scanner_text: str
    portfolio_text: str
    news_text: str
    learning_text: str
    extra: dict[str, Any] = field(default_factory=dict)

    def as_prompt_block(self) -> str:
        return (
            f"SYMBOL: {self.symbol}\n\n"
            f"=== DECISION CARD ===\n{self.decision_text}\n\n"
            f"=== SCANNER ===\n{self.scanner_text}\n\n"
            f"=== PORTFOLIO ===\n{self.portfolio_text}\n\n"
            f"=== NEWS ===\n{self.news_text}\n\n"
            f"=== LEARNING ===\n{self.learning_text}\n"
        )


@dataclass(frozen=True)
class ResearchReview:
    """Claude (or template) review of an already-formed DecisionCard."""

    symbol: str
    verdict: ReviewVerdict
    summary: str
    strengths: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    adjustments: tuple[str, ...] = ()
    provider: str = "template"
    model: str = ""
    raw_text: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


__all__ = ["ResearchContext", "ResearchReview", "ReviewVerdict"]
