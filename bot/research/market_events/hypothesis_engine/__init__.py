"""Research Hypothesis Engine V1 — auto hypotheses from analytics layers (research only)."""

from __future__ import annotations

from bot.research.market_events.hypothesis_engine.generate import (
    generate_hypothesis_candidates,
    score_hypothesis,
)
from bot.research.market_events.hypothesis_engine.report import (
    format_hypothesis_detail,
    format_hypothesis_show,
    write_hypotheses_report,
)
from bot.research.market_events.hypothesis_engine.schema import ensure_hypothesis_engine_schema
from bot.research.market_events.hypothesis_engine.validate import (
    decide_status,
    run_hypothesis_validate,
    validate_candidate,
)

__all__ = [
    "decide_status",
    "ensure_hypothesis_engine_schema",
    "format_hypothesis_detail",
    "format_hypothesis_show",
    "generate_hypothesis_candidates",
    "run_hypothesis_validate",
    "score_hypothesis",
    "validate_candidate",
    "write_hypotheses_report",
]
