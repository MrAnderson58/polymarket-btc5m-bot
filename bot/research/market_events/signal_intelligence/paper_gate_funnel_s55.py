"""Back-compat shim — unified funnel lives in gate_funnel_report."""

from __future__ import annotations

from bot.research.market_events.signal_intelligence.gate_funnel_report import (
    build_unified_gate_funnel as build_paper_gate_funnel,
    format_paper_gate_funnel,
    format_unified_gate_funnel,
)

__all__ = [
    "build_paper_gate_funnel",
    "format_paper_gate_funnel",
    "format_unified_gate_funnel",
]
