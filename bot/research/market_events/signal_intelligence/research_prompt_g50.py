"""Phase G.5.0 — Quant Research Analyst prompt builder."""

from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT_G50 = """You are a quantitative researcher at a systematic crypto hedge fund.

Rules:
- Never invent facts.
- Use ONLY the supplied statistics and records.
- Do not predict future market prices.
- Only derive statistical observations from the data.
- Always provide confidence (0-1) in your overall conclusion.
- Always mention sample_size matching the dataset.
- Output ONLY valid JSON. No markdown. No free text outside JSON.

Required JSON keys:
top_factors, weak_factors, overestimated_factors, profitable_combinations,
loss_combinations, btc_vs_alt, symbol_specific, new_patterns, bad_patterns,
tomorrow_hypotheses, research_improvements, confidence, sample_size

Each list item should be an object with at least: label, evidence, sample_size where applicable.
profitable_combinations and loss_combinations items should include win_rate when computable."""

RESEARCH_QUESTIONS_G50 = (
    "Which 10 factors are currently most useful?",
    "Which factors are practically useless?",
    "Which factors are overestimated?",
    "Which factor combinations most often produce profit?",
    "Which combinations almost always lead to losses?",
    "Are there differences between BTC and altcoins?",
    "Which coins require their own thresholds?",
    "What new patterns were found?",
    "Which hypotheses should be tested tomorrow?",
    "What would you change in the research process?",
)


def build_research_prompt_g50(dataset: dict[str, Any]) -> str:
    """Compact user prompt with dataset + explicit questions."""
    stats = dataset.get("aggregate_stats") or {}
    compact = {
        "sample_size": dataset.get("sample_size", 0),
        "window_days": dataset.get("window_days"),
        "aggregate_stats": stats,
        "g4_factor_stats": dataset.get("g4_factor_stats") or [],
        "records": dataset.get("records") or [],
    }
    lines = [
        "Analyze this research dataset and answer all 10 research questions.",
        "",
        "Questions:",
    ]
    for i, q in enumerate(RESEARCH_QUESTIONS_G50, start=1):
        lines.append(f"{i}. {q}")
    lines.extend([
        "",
        "Dataset JSON:",
        json.dumps(compact, ensure_ascii=False, default=str),
    ])
    return "\n".join(lines)


def validate_research_json_g50(data: dict[str, Any]) -> dict[str, Any]:
    """Ensure required keys exist with sane defaults."""
    required_lists = (
        "top_factors", "weak_factors", "overestimated_factors",
        "profitable_combinations", "loss_combinations", "btc_vs_alt",
        "symbol_specific", "new_patterns", "bad_patterns",
        "tomorrow_hypotheses", "research_improvements",
    )
    out = dict(data)
    for key in required_lists:
        val = out.get(key)
        if not isinstance(val, list):
            out[key] = []
    out["confidence"] = float(out.get("confidence") or 0.5)
    out["sample_size"] = int(out.get("sample_size") or 0)
    return out
