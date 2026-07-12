"""Phase F.4 — chart structure analysis from caption/OCR text (deterministic)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChartStructureAnalysis:
    demand_zones: list[str] = field(default_factory=list)
    supply_zones: list[str] = field(default_factory=list)
    liquidity_levels: list[str] = field(default_factory=list)
    forecast_direction: str | None = None
    structure_labels: list[str] = field(default_factory=list)
    author_scenario: str | None = None


def analyze_chart_text(text: str) -> ChartStructureAnalysis:
    out = ChartStructureAnalysis()
    if not text:
        return out
    low = text.lower()

    if re.search(r"demand|зона спроса|support|поддерж", low):
        out.demand_zones.append("demand")
    if re.search(r"supply|зона предлож|resist|сопротив", low):
        out.supply_zones.append("supply")
    if re.search(r"liquidity|ликвид|sweep|снятие", low):
        out.liquidity_levels.append("liquidity_sweep")
    if re.search(r"\bbos\b|break of structure|пробой структ", low):
        out.structure_labels.append("BOS")
    if re.search(r"\bchoch\b|change of character|смена характ", low):
        out.structure_labels.append("CHoCH")
    if re.search(r"\bhh\b|higher high", low):
        out.structure_labels.append("HH")
    if re.search(r"\bhl\b|higher low", low):
        out.structure_labels.append("HL")
    if re.search(r"\blh\b|lower high", low):
        out.structure_labels.append("LH")
    if re.search(r"\bll\b|lower low", low):
        out.structure_labels.append("LL")

    if re.search(r"long|buy|bull|↑|⬆", low):
        out.forecast_direction = "UP"
    elif re.search(r"short|sell|bear|↓|⬇", low):
        out.forecast_direction = "DOWN"

    if out.liquidity_levels and out.demand_zones:
        out.author_scenario = "ожидает снятие ликвидности в зоне спроса"
    elif out.liquidity_levels:
        out.author_scenario = "ожидает liquidity sweep"
    elif out.supply_zones:
        out.author_scenario = "реакция у зоны предложения"
    elif out.demand_zones:
        out.author_scenario = "реакция в зоне спроса"

    return out
